"""Generic post-run flush for composite runs: analyses + a report card.

Called by run_runner.execute after visualizations render. Best-effort:
never raises into the run loop; a failure is logged and reflected in the
returned has_* flags."""
from __future__ import annotations

import html as _html
import json
import sqlite3
import traceback
from pathlib import Path

from viva_superpowers import diff_reports, build_report
from viva_superpowers.study_verdict import severity_gate
from vivarium_workbench.lib.conclusion_card import _CANON_SEVERITY
from vivarium_workbench.lib.ephemeral_study import merge_declarations

_RUN_VERDICT_SCHEMA = "run_verdict/v1"


def rollup_run_verdict(verdict_json_paths) -> dict:
    """Roll up a run's report-card ``*.verdict.json`` files into ONE verdict.

    Each path's ``overall`` is canonicalized (unknown / missing / unreadable
    -> ``"ungraded"``); the run's ``overall`` is the WORST (max
    ``_CANON_SEVERITY``) card verdict, ``"ungraded"`` when there are no cards.
    ``cards`` is sorted by name for a stable content hash. Never raises — a
    broken file contributes an ``ungraded`` card, not an exception.

    Returns ``{"schema": "run_verdict/v1", "overall": <canon>,
    "cards": [{"name", "overall"}, ...]}``.
    """
    cards = []
    for p in verdict_json_paths:
        p = Path(p)
        name = p.name[: -len(".verdict.json")] if p.name.endswith(
            ".verdict.json") else p.stem
        overall = "ungraded"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            cand = data.get("overall")
            if cand in _CANON_SEVERITY:
                overall = cand
        except Exception:  # noqa: BLE001 — unreadable/invalid -> ungraded card
            pass
        cards.append({"name": name, "overall": overall})
    cards.sort(key=lambda c: c["name"])
    overall = max(
        (c["overall"] for c in cards),
        key=lambda v: _CANON_SEVERITY.get(v, 0),
        default="ungraded",
    )
    return {"schema": _RUN_VERDICT_SCHEMA, "overall": overall, "cards": cards}


def _load_verdict_cards(run_dir) -> dict:
    """``{card_name: full verdict doc}`` for every ``*.verdict.json`` under
    ``run_dir``. Same name-recovery as ``rollup_run_verdict`` (:30-38), but
    keeps the FULL parsed doc (not just ``overall``) since ``diff_reports``
    needs each axis's ``groups[...].axes[...]`` detail. A missing/unreadable
    file is skipped, not fatal — mirrors ``rollup_run_verdict``'s tolerance."""
    cards: dict = {}
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        return cards
    for p in run_dir.glob("**/*.verdict.json"):
        name = p.name[: -len(".verdict.json")] if p.name.endswith(
            ".verdict.json") else p.stem
        try:
            cards[name] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — unreadable/invalid -> skip this card
            continue
    return cards


def _find_prev_run_id(db_file, run_id: str) -> str | None:
    """Newest ``runs_meta`` row that is NOT ``run_id``, or ``None``.

    Read-only + tolerant: missing db file / table / columns all yield
    ``None`` rather than raising (matches ``study_charts.latest_run_row``'s
    contract) so a first run or a bare composite-test-run (no runs.db row
    yet) degrades to "no prev run" instead of erroring. Excludes ``run_id``
    directly in SQL rather than via a post-hoc guard, since the CURRENT run's
    row is typically already present (inserted at run start, before this
    flush stage runs) with the most recent ``started_at``.
    """
    db_file = Path(db_file)
    if not db_file.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=1.0)
        try:
            have = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
            if "run_id" not in have:
                return None
            order_col = ("COALESCE(completed_at, started_at)"
                         if {"completed_at", "started_at"} & have else "run_id")
            row = conn.execute(
                f"SELECT run_id FROM runs_meta WHERE run_id != ? "
                f"ORDER BY {order_col} DESC LIMIT 1", (run_id,)
            ).fetchone()
        finally:
            conn.close()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _write_test_diff(run_dir, prev_run_dir, *, diff_fn=diff_reports) -> bool:
    """Write ``run_dir/test_diff.json = diff_fn(prev_cards, curr_cards)``.

    ``prev_run_dir`` may be ``None`` or non-existent (first run / prev run's
    cards missing) — ``prev_cards`` then degrades to ``{}`` and every axis in
    ``curr_cards`` diffs as ``"new"``, which is still a useful signal. Pure
    and best-effort in the same style as the rest of this module: on ANY
    failure (bad ``diff_fn``, unwritable ``run_dir``, ...) this returns
    ``False`` and leaves no partial ``test_diff.json`` behind, rather than
    raising into the run loop.
    """
    run_dir = Path(run_dir)
    try:
        curr_cards = _load_verdict_cards(run_dir)
        prev_cards = _load_verdict_cards(prev_run_dir) if prev_run_dir else {}
        diff = diff_fn(prev_cards, curr_cards)
        # allow_nan=False matches repo convention (_write_json/verdict writes) —
        # browser JSON.parse rejects NaN/Infinity; margins are sanitized
        # upstream so this is a belt-and-suspenders tightening, not a fix for
        # an observed failure.
        (run_dir / "test_diff.json").write_text(
            json.dumps(diff, allow_nan=False), encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 — best-effort, never raises into the run loop
        traceback.print_exc()
        return False


def _write_report_gate(run_dir, study_name, run_id, *,
                       build_fn=build_report, gate_fn=severity_gate):
    """Write ``run_dir/report.json`` = ``build_fn(cards)`` with a severity-aware
    ``gate`` injected, and return the gate status (``pass``/``fail``/``warn``) or
    ``None`` on failure.

    Aggregates this run's per-card verdicts (``_load_verdict_cards``) into a
    ``test_report/v1`` and gates it on axis severity: only ``hard``-severity axis
    mismatches FAIL; a soft mismatch or drift WARNs; directional axes never gate
    (see ``viva_superpowers.study_verdict.severity_gate``). Pure + best-effort in
    the same style as ``_write_test_diff`` — never raises into the run loop.
    """
    run_dir = Path(run_dir)
    try:
        cards = _load_verdict_cards(run_dir)
        report = build_fn(study_name or "", run_id, cards)
        report["gate"] = gate_fn(report)
        (run_dir / "report.json").write_text(
            json.dumps(report, allow_nan=False), encoding="utf-8")
        return report["gate"]["status"]
    except Exception:  # noqa: BLE001 — best-effort, never raises into the run loop
        traceback.print_exc()
        return None


def _composite_analyses(spec_id: str, core) -> list:
    """Return this composite's ``@composite_generator(analyses=[...])``
    declarations (each a dict with at least ``name``, optionally ``params``).
    [] when the generator is unregistered or declares none."""
    try:
        from vivarium_workbench.lib.composite_lookup import resolve_generator_entry
    except ImportError:
        return []
    entry = resolve_generator_entry(spec_id)
    return list(getattr(entry, "analyses", []) or []) if entry else []


def _render_analysis(*, name: str, params: dict, db_file: str, run_id: str,
                      run_dir, core) -> dict:
    """Render one declared analysis over this run's emitter output.

    Reuses the same env-worker ``run_study_analyses`` capability
    ``study_run_post.run_study_analyses`` dispatches to for the study path
    (see env_worker.py's ``_run_study_analyses``), scoped to this run's own
    fixed sweep dir — ``run_dir/parquet/run_id``, the same path
    ``run_runner.execute`` exports as ``VIVARIUM_WORKBENCH_SWEEP_DIR`` and
    ``_render_canonical_viz`` hands ``ParquetAnalysisView`` — and this run's
    ParCa sim_data pickle (``run_runner._resolve_sim_data_path``).

    Raises on failure; the caller (``_dispatch_analyses``) catches per-entry
    so one bad analysis never breaks the flush.
    """
    if run_dir is None:
        raise RuntimeError("run_dir required to resolve this run's sweep dir")
    run_dir = Path(run_dir)
    # run_dir is <ws>/.pbg/runs/<run_id> (see run_runner._resolve_sim_data_path).
    ws_root = run_dir.parents[2]
    sweep_dir = run_dir / "parquet" / run_id

    from vivarium_workbench.lib.run_runner import _resolve_sim_data_path
    sim_data_path = _resolve_sim_data_path(run_dir) or None

    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
    from vivarium_workbench.lib.env_worker_pool import get_pool
    try:
        res = get_pool().call(ws_root, "run_study_analyses", {
            "entries": [{"name": name, "params": params}],
            "sweep_dir": str(sweep_dir),
            "sim_data_path": sim_data_path,
        })
    except EnvWorkerUnavailable as exc:
        raise RuntimeError(f"environment worker unavailable: {exc}") from exc

    written = list(res.get("written") or [])
    errors = list(res.get("errors") or [])
    if errors and not written:
        raise RuntimeError(f"analysis {name!r} failed: {errors}")
    return {"name": name, "written": written, "errors": errors}


def _dispatch_analyses(*, spec_id: str, db_file: str, run_id: str, core,
                        run_dir=None, req=None) -> list:
    """Render every declared analysis over this run's emitter output.

    The declaration is ``merge_declarations(composite_defaults,
    config_declared)`` — the composite's own
    ``@composite_generator(analyses=[...])`` entries (``_composite_analyses``)
    overlaid by ``req.declared_results["analyses"]`` (a config-declared block;
    ``{}`` until a future task populates it, so composite defaults flow
    through unchanged today). Config wins on name collision. Returns the list
    of rendered-artifact dicts; [] when nothing is declared (graceful no-op).
    Best-effort: each entry is rendered in isolation — a failing analysis is
    logged and skipped, never breaks the flush."""
    composite_defaults = {"analyses": _composite_analyses(spec_id, core)}
    config_declared = getattr(req, "declared_results", None) or {}
    analyses = merge_declarations(composite_defaults, config_declared)["analyses"]
    if not analyses:
        return []
    out = []
    for a in analyses:
        name = a.get("name") if isinstance(a, dict) else str(a)
        params = (a.get("params") or {}) if isinstance(a, dict) else {}
        try:
            rendered = _render_analysis(
                name=name, params=params, db_file=db_file, run_id=run_id,
                run_dir=run_dir, core=core)
        except Exception:
            traceback.print_exc()
            continue
        if rendered:
            out.append(rendered)
    return out


def render_report_card(*, req, viz_names: list, analyses: list) -> str:
    steps = getattr(req, "steps", "?")
    spec_id = getattr(req, "spec_id", "") or ""
    name = spec_id.rsplit(".", 1)[-1] if spec_id else "composite"
    rows = "".join(
        f"<li><code>{_html.escape(str(n))}</code></li>" for n in viz_names
    ) or "<li><em>none</em></li>"
    an = "".join(
        f"<li>{_html.escape(str(a.get('name', a)))}</li>" for a in analyses
    ) or "<li><em>none</em></li>"
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<div style='font-family:system-ui;max-width:720px;margin:24px auto'>"
        f"<h2>Run report — <code>{_html.escape(name)}</code></h2>"
        f"<p><strong>Composite:</strong> <code>{_html.escape(spec_id)}</code><br>"
        f"<strong>Steps:</strong> {_html.escape(str(steps))}</p>"
        f"<h3>Figures ({len(viz_names)})</h3><ul>{rows}</ul>"
        f"<h3>Analyses ({len(analyses)})</h3><ul>{an}</ul>"
        "</div>"
    )


def _auto_results_enabled(run_dir: Path) -> bool:
    """Read ``ui.auto_results`` (default True) for the workspace owning
    ``run_dir``.  Mirrors ``_render_analysis``'s ``ws_root = run_dir.parents[2]``
    (``run_dir`` is ``<ws>/.pbg/runs/<run_id>``). Best-effort: any failure
    (bad path, unreadable workspace.yaml, ...) degrades to the default
    ``True`` rather than blocking the flush."""
    try:
        ws_root = Path(run_dir).parents[2]
        from vivarium_workbench.lib.system_info import build_ui_config
        return bool(build_ui_config(ws_root).get("auto_results", True))
    except Exception:  # noqa: BLE001 — default True, never block the flush
        return True


def run_flush(run_dir: Path, *, req, spec_id: str, db_file: str,
              run_id: str, core) -> dict:
    run_dir = Path(run_dir)
    analyses: list = []
    has_analyses = False
    if _auto_results_enabled(run_dir):
        try:
            analyses = _dispatch_analyses(
                spec_id=spec_id, db_file=db_file, run_id=run_id, core=core,
                run_dir=run_dir, req=req)
            has_analyses = bool(analyses)
        except Exception:
            traceback.print_exc()
    try:
        (run_dir / "analyses.json").write_text(
            json.dumps(analyses, default=str), encoding="utf-8")
    except Exception:
        traceback.print_exc()

    # Report card — always attempt; read viz names from the already-written viz.json.
    viz_names: list = []
    try:
        vj = run_dir / "viz.json"
        if vj.is_file():
            viz_names = list(json.loads(vj.read_text()).keys())
    except Exception:
        pass
    has_report = False
    try:
        (run_dir / "report.html").write_text(
            render_report_card(req=req, viz_names=viz_names, analyses=analyses),
            encoding="utf-8")
        has_report = True
    except Exception:
        traceback.print_exc()

    # Computed verdict (Phase 2c): roll up the report-card *.verdict.json files
    # this run's analyses just wrote into ONE run_dir/verdict.json artifact.
    # Best-effort — a verdict error never fails the run.
    has_verdict = False
    try:
        vpaths = [Path(w) for a in analyses
                  for w in (a.get("written") or [])
                  if str(w).endswith(".verdict.json")]
        (run_dir / "verdict.json").write_text(
            json.dumps(rollup_run_verdict(vpaths)), encoding="utf-8")
        has_verdict = True
    except Exception:
        traceback.print_exc()

    # Cross-iteration diff (Slice 3, §8/§9): test_diff.json = diff_reports
    # (prev_cards, curr_cards) vs the run immediately before this one — the
    # agent-feedback signal a model-building agent reads between iterations.
    # There is no history/ in the workbench (study-level
    # viz/report_card/*.verdict.json is overwritten each run), so prev_cards
    # is read from the PRIOR run's own run_dir. Best-effort — first run / no
    # runs.db row yet -> _find_prev_run_id returns None -> an all-"new" diff
    # is still written (still useful), never a hard failure.
    has_diff = False
    try:
        prev_run_id = _find_prev_run_id(db_file, run_id)
        prev_run_dir = None
        if prev_run_id:
            # The prev run shares the CURRENT run's runs-root — layout-agnostic
            # (no hardcoded ".pbg"/"runs": a layout:-remapped workspace resolves
            # run paths differently, e.g. via WorkspacePaths.load(ws_root).pbg
            # as study_spec.py does; run_dir.parent already IS that runs-root
            # for this run, whatever it's named).
            prev_run_dir = run_dir.parent / prev_run_id
        has_diff = _write_test_diff(run_dir, prev_run_dir)
    except Exception:
        traceback.print_exc()

    # Severity-aware study gate over this run's graded cards -> run_dir/report.json.
    # Only hard-severity axis mismatches fail; soft/drift warn; directional never
    # gates. Best-effort, same has_* contract — never fails the run.
    gate_status = None
    try:
        gate_status = _write_report_gate(run_dir, spec_id, run_id)
    except Exception:
        traceback.print_exc()

    # Auto-refresh declared visualizations (self-driving fix): previously a
    # study's `visualizations:` only got re-rendered via a manual "Refresh"
    # button in the UI, so every study with declared viz went stale after
    # each run unless someone remembered to click it. `db_file` is always
    # `<study_dir>/runs.db` for a study-owned run (see study_runs.py), so its
    # parent locates the study; a bare composite-test-run's db_file won't
    # have a sibling study.yaml and is skipped. Best-effort, matching the
    # has_* contract above — a refresh failure never fails the run.
    has_viz_refresh = False
    try:
        study_dir = Path(db_file).parent
        spec_path = study_dir / "study.yaml"
        if spec_path.is_file():
            import yaml
            spec_data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            if isinstance(spec_data, dict) and spec_data.get("visualizations"):
                from vivarium_workbench.lib.refresh_viz import refresh_study_viz
                from vivarium_workbench.lib.study_charts import latest_run_row
                latest = latest_run_row(study_dir / "runs.db")
                refresh_study_viz(study_dir, spec_data, latest)
                has_viz_refresh = True
    except Exception:
        traceback.print_exc()

    return {"has_analyses": has_analyses, "has_report": has_report,
            "has_verdict": has_verdict, "has_viz_refresh": has_viz_refresh,
            "has_diff": has_diff, "gate": gate_status}
