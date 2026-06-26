"""Run-merging study-detail loader and slug-validation constant.

This is the **keystone** of the FastAPI strangler-fig migration's study side:
the per-study loader that resolves a study's spec, **merges the runs recorded in
``studies/<slug>/runs.db``** on top of any ``spec.runs`` persisted in
study.yaml, **reconciles ``simulation_set``** with what actually ran, and
**auto-discovers pre-rendered viz HTML**.  Several routes need exactly this
merged spec — most importantly rigor (``pbg_superpowers.rigor`` reads
``spec["runs"]`` for the replication + run-persistence dimensions), and
``GET /api/study/{slug}`` (Phase A, Batch 4).

The legacy ``server.py`` helpers now delegate to these functions (thin shims
passing the module-level ``WORKSPACE``), so the many existing call-sites keep
working unchanged.

Public functions
----------------
study_dir                 ← server._study_dir
study_spec_path           ← server._study_spec_path / _study_spec_file
read_runs_db_for_study    ← server._read_runs_db_for_study
discover_viz_html_files   ← server._discover_viz_html_files
load_study_detail_spec    ← server._study_detail_spec  (the full run-merging mirror)

``SLUG_RE`` — the compiled regex shared by server.py and api/app.py so neither
imports the other.  Study/investigation names are generated with underscores
(e.g. derived from composite names like ``monod_kinetics``), so the pattern
allows ``_`` alongside ``-``, anchored to alphanumerics at both ends.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import re
import sqlite3
from pathlib import Path
from typing import Optional

import yaml

from vivarium_dashboard.lib.workspace_paths import WorkspacePaths

# Slug pattern shared by server.py and api/app.py (neither imports the other).
# Study/investigation names are generated with underscores (e.g. derived from
# composite names like ``monod_kinetics``), so the pattern allows ``_``
# alongside ``-``, anchored to alphanumerics at both ends (keeps out path
# traversal: ``..``, ``/``, leading dots).
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")


# ---------------------------------------------------------------------------
# Observable collection (pure — driven entirely off the spec dict)
# ---------------------------------------------------------------------------

def collect_study_observables(spec: dict) -> list[str]:
    """Return slash-joined observable store paths declared by the study spec.

    Pure copy of ``server._collect_study_observables`` (server keeps its own
    copy, used by many study-run handlers; dedup at the flip). Sweeps the spec
    for every observable-shaped path declaration so a run handler can wire
    ``inject_emitter_for_paths`` automatically.

    Recognised sources (tolerant — drives off whatever the study author
    declared, in whatever shape):
      - readouts[*].store_path
      - behavior_tests[*].measure.path
      - behavior_tests[*].measure.{series_x,series_y,x,y,series_a,series_b}.path
      - simulation_set[*].observe   (list or str)
      - tests[*].measure.{path,series_x,...}   (v4 studies)
      - comparative_visualizations[*].observable_path   (v4 overlays)

    Paths come dot-joined ('agents.0.listeners.foo') or slash-joined
    ('agents/0/listeners/foo'); both normalise to slash-joined. Duplicates are
    dropped while preserving declaration order.
    """
    def _norm(p: str) -> str | None:
        if not isinstance(p, str) or not p.strip():
            return None
        # Accept either separator; output is slash-joined.
        parts = [seg for seg in p.replace(".", "/").split("/") if seg]
        return "/".join(parts) if parts else None

    out: list[str] = []
    seen: set[str] = set()
    def _push(p):
        n = _norm(p) if isinstance(p, str) else None
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    for r in spec.get("readouts", []) or []:
        if isinstance(r, dict):
            _push(r.get("store_path"))

    for bt in spec.get("behavior_tests", []) or []:
        m = (bt or {}).get("measure") if isinstance(bt, dict) else None
        if not isinstance(m, dict):
            continue
        _push(m.get("path"))
        for nested_key in ("series_x", "series_y", "x", "y", "series_a", "series_b"):
            n = m.get(nested_key)
            if isinstance(n, dict):
                _push(n.get("path"))

    for sim in spec.get("simulation_set", []) or []:
        if not isinstance(sim, dict):
            continue
        obs = sim.get("observe")
        if isinstance(obs, list):
            for p in obs:
                _push(p)
        elif isinstance(obs, str):
            _push(obs)

    # v4 studies declare their tests under `tests:` (not `behavior_tests:`)
    # with the same {measure: {path, series_x, ...}} shape, and their overlay
    # observables under `comparative_visualizations[].observable_path`.
    for t in spec.get("tests", []) or []:
        m = (t or {}).get("measure") if isinstance(t, dict) else None
        if not isinstance(m, dict):
            continue
        _push(m.get("path"))
        for nested_key in ("series_x", "series_y", "x", "y", "series_a", "series_b"):
            n = m.get(nested_key)
            if isinstance(n, dict):
                _push(n.get("path"))

    for cv in spec.get("comparative_visualizations", []) or []:
        if isinstance(cv, dict):
            _push(cv.get("observable_path"))

    return out


# ---------------------------------------------------------------------------
# Directory / spec-path resolution (ws_root-parameterised)
# ---------------------------------------------------------------------------

def study_dir(ws_root: Path, name: str) -> Path:
    """Resolve a study directory, preferring ``studies/`` over ``investigations/``.

    Uses ``WorkspacePaths.study_dir`` as the primary lookup (handles nested
    ``investigations/<inv>/studies/<slug>/`` layouts), falling back to the flat
    ``studies/<name>/`` dir (when it holds only ``spec.yaml`` so
    ``iter_study_dirs`` skipped it) and finally the legacy
    ``investigations/<name>/`` path.

    Mirrors ``server._study_dir`` parameterised on ``ws_root``.
    """
    wp = WorkspacePaths.load(ws_root)
    try:
        return wp.study_dir(name)
    except FileNotFoundError:
        pass
    flat_candidate = wp.studies / name
    if flat_candidate.is_dir():
        return flat_candidate
    return wp.investigations / name


def study_spec_file(study_dir_path: Path) -> Path:
    """Resolve a study's spec file given its directory.

    Prefers ``study.yaml`` (v3 convention) when present, falls back to legacy
    ``spec.yaml``. Returns ``study_dir_path / "study.yaml"`` as the not-found
    default so callers' ``is_file()`` checks behave the same as before.

    Mirrors ``server._study_spec_file``.
    """
    study_yaml = study_dir_path / "study.yaml"
    if study_yaml.is_file():
        return study_yaml
    spec_yaml = study_dir_path / "spec.yaml"
    if spec_yaml.is_file():
        return spec_yaml
    return study_yaml


def study_spec_path(ws_root: Path, name: str) -> Path:
    """Resolve a study's spec file: ``study.yaml`` (v3) or ``spec.yaml`` (legacy).

    Mirrors ``server._study_spec_path`` parameterised on ``ws_root``.
    """
    return study_spec_file(study_dir(ws_root, name))


# ---------------------------------------------------------------------------
# runs.db reading (ws_root-parameterised)
# ---------------------------------------------------------------------------

def _latest_run_timestamp(runs_db: Path) -> Optional[float]:
    """Return the most recent run's wall-clock time from ``runs_meta``.

    Prefers ``completed_at`` (when the run finished, hence when its viz could
    have been rendered), falling back to ``started_at``. Returns ``None`` if the
    table is unreadable or empty.

    Uses the recorded run timestamps (not the db file mtime), which are immune
    to the WAL-checkpoint race that bumps the mtime AFTER a read connection
    renders the viz.  Mirrors ``server._latest_run_timestamp``.
    """
    try:
        conn = sqlite3.connect(f"file:{runs_db}?mode=ro", uri=True, timeout=1.0)
        try:
            row = conn.execute(
                "SELECT MAX(COALESCE(completed_at, started_at)) FROM runs_meta"
            ).fetchone()
        finally:
            conn.close()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:  # noqa: BLE001 — best-effort freshness probe
        return None


def _study_yaml_run_rows(ws_root: Path, name: str) -> list[dict]:
    """Map a study's ``study.yaml`` ``runs:`` list to run-row dicts.

    Emitter-less workspaces record each run in the spec's ``runs:`` block rather
    than a per-step ``runs.db``. Uses a light direct YAML read to avoid recursing
    through :func:`load_study_detail_spec`.  Mirrors ``server._study_yaml_run_rows``.
    """
    try:
        path = study_spec_path(ws_root, name)
        if not path or not Path(path).is_file():
            return []
        spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — never let a malformed spec break the view
        return []
    if not isinstance(spec, dict):
        return []
    rows: list[dict] = []
    for r in spec.get("runs", []) or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("run_id") or r.get("name") or "").strip()
        if not rid:
            continue
        rows.append({
            "run_id":        rid,
            "spec_id":       name,
            "label":         r.get("name") or rid,
            "sim_name":      r.get("name") or rid,
            "variant":       None,
            "composite":     r.get("composite"),
            "params":        {"seed": r.get("seed")} if r.get("seed") is not None else {},
            "n_steps":       r.get("n_steps"),
            "status":        r.get("status") or "completed",
            "started_at":    r.get("started_at"),
            "completed_at":  r.get("completed_at") or r.get("started_at"),
            "generation_id": r.get("generation_id"),
            "source":        "study.yaml",
        })
    return rows


def read_runs_db_for_study(ws_root: Path, name: str) -> list[dict]:
    """Read all runs from ``studies/<name>/runs.db`` for the Runs tab.

    Merges the ``runs_meta`` and ``simulations`` tables on ``run_id`` /
    ``simulation_id`` (same string by convention), then merges runs recorded only
    in study.yaml. Returns one dict per run with the fields the template needs,
    newest-first. Returns ``[]`` if neither source has any runs.

    Mirrors ``server._read_runs_db_for_study`` parameterised on ``ws_root``.
    """
    runs_db = WorkspacePaths.load(ws_root).studies / name / "runs.db"
    # A runs.db is the canonical per-step source, but it's optional: emitter-less
    # workspaces record runs only in study.yaml (merged in below). So don't bail
    # when it's absent — fall through with an empty db result.
    conn = sqlite3.connect(str(runs_db)) if runs_db.is_file() else None
    if conn is not None:
        conn.row_factory = sqlite3.Row
    try:
        # Discover available tables; both should exist for pbg_runner-wrapped
        # runs, but older backfilled DBs may only have runs_meta.
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")} if conn is not None else set()
        # runs_meta.generation_id is a recently-added nullable column; older
        # DBs won't have it, so probe before selecting it.
        meta_cols = {row[1] for row in conn.execute("PRAGMA table_info(runs_meta)")} \
            if conn is not None and "runs_meta" in tables else set()
        _gen_col = "generation_id" if "generation_id" in meta_cols else "NULL AS generation_id"
        rows_by_id: dict[str, dict] = {}
        if conn is not None and "runs_meta" in tables:
            for r in conn.execute(
                "SELECT run_id, spec_id, label, params_json, started_at, "
                f"completed_at, n_steps, status, sim_name, {_gen_col} "
                "FROM runs_meta ORDER BY started_at DESC"
            ):
                try:
                    params = _json.loads(r["params_json"] or "{}")
                except Exception:
                    params = {}
                rows_by_id[r["run_id"]] = {
                    "run_id":        r["run_id"],
                    "spec_id":       r["spec_id"],
                    "label":         r["label"] or r["sim_name"] or "",
                    "sim_name":      r["sim_name"] or r["label"] or "",
                    "variant":       params.get("variant"),
                    "composite":     params.get("composite") or r["spec_id"],
                    "params":        params,
                    "n_steps":       r["n_steps"],
                    "status":        r["status"],
                    "started_at":    r["started_at"],
                    "completed_at":  r["completed_at"],
                    "generation_id": r["generation_id"],
                    "source":        "runs_meta",
                }
        if conn is not None and "simulations" in tables:
            for r in conn.execute(
                "SELECT simulation_id, name, started_at, completed_at "
                "FROM simulations ORDER BY started_at DESC"
            ):
                sid = r["simulation_id"]
                existing = rows_by_id.get(sid)
                if existing:
                    # Fall back to SQLiteEmitter values when runs_meta lacks
                    # a name / timestamp.
                    if not existing.get("sim_name"):
                        existing["sim_name"] = r["name"] or ""
                else:
                    rows_by_id[sid] = {
                        "run_id":       sid,
                        "spec_id":      name,
                        "label":        r["name"] or "",
                        "sim_name":     r["name"] or "",
                        "variant":      None,
                        "composite":    None,
                        "params":       {},
                        "n_steps":      None,
                        "status":       "ran",
                        "started_at":   r["started_at"],
                        "completed_at": r["completed_at"],
                        "source":       "simulations",
                    }
    finally:
        if conn is not None:
            conn.close()

    # Merge runs recorded in study.yaml `runs:` (emitter-less workspaces) — the
    # db is authoritative where present, so only add spec runs not already seen.
    for _r in _study_yaml_run_rows(ws_root, name):
        rows_by_id.setdefault(_r["run_id"], _r)

    def _iso(v):
        if v is None:
            return ""
        if isinstance(v, (int, float)):
            try:
                return _dt.datetime.fromtimestamp(
                    float(v), tz=_dt.timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                return str(v)
        return str(v)

    # Coordinated-generation staleness (expert-feedback A.2): a run from an
    # older generation than the workspace's current one is flagged so the
    # report/Runs tab can mark it instead of silently mixing it in.
    try:
        from pbg_superpowers import generation as _gen
        _cur_gen = _gen.current_generation_id(ws_root)
    except Exception:  # noqa: BLE001
        _cur_gen = None

    out = []
    for r in rows_by_id.values():
        r["started_at_iso"] = _iso(r.get("started_at"))
        try:
            r["stale"] = _gen.is_stale(r.get("generation_id"), _cur_gen)
        except Exception:  # noqa: BLE001
            r["stale"] = False
        # Compact params summary for the table cell (e.g., "seed=0, rida_rate=4.6").
        params = r.get("params") or {}
        if params:
            shown = {k: v for k, v in params.items() if not k.startswith("_")}
            r["params_summary"] = ", ".join(
                f"{k}={v}" for k, v in sorted(shown.items())
            )[:80]
        else:
            r["params_summary"] = ""
        out.append(r)

    def _sort_key(r):
        v = r.get("started_at")
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return _dt.datetime.fromisoformat(
                    v.replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0.0
        return 0.0
    out.sort(key=_sort_key, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Viz HTML auto-discovery (ws_root-parameterised)
# ---------------------------------------------------------------------------

def discover_viz_html_files(ws_root: Path, name: str) -> list[dict]:
    """Discover viz HTML files for a study from BOTH conventional locations.

    Sources:
      1. ``studies/<name>/viz/*.html`` — auto-rendered by render_visualizations
         from the study's runs.db. Gated on runs.db existing; stale-flagged when
         mtime predates the latest recorded run (WAL-immune freshness reference).
      2. ``reports/figures/<name>/*.html`` — hand-authored cross-skill output.
         NOT gated on runs.db; the author owns the file's currency.

    Returns one dict per HTML file: ``{name, url, description, stale}``; the URL
    is workspace-relative so the dashboard's static-file fallback serves it.

    Mirrors ``server._discover_viz_html_files`` parameterised on ``ws_root``.
    """
    wp = WorkspacePaths.load(ws_root)
    out: list[dict] = []

    # Source 1: studies/<name>/viz/*.html (auto-rendered from runs.db).
    viz_dir = wp.studies / name / "viz"
    runs_db = wp.studies / name / "runs.db"
    if viz_dir.is_dir() and runs_db.is_file():
        # Freshness reference: the latest recorded run time (WAL-immune), not the
        # db file mtime. A small grace absorbs sub-second render/commit ordering.
        fresh_ref = _latest_run_timestamp(runs_db)
        grace_s = 5.0
        for html_file in sorted(viz_dir.glob("*.html")):
            mtime = html_file.stat().st_mtime
            size_kb = max(1, html_file.stat().st_size // 1024)
            rel = html_file.relative_to(ws_root).as_posix()
            stale = fresh_ref is not None and mtime + grace_s < fresh_ref
            desc = (
                f"Auto-discovered Plotly viz ({size_kb} KB) rendered by "
                f"render_visualizations against the study's runs.db history."
            )
            if stale:
                desc = (
                    "⚠ May predate the latest run — this chart was "
                    "rendered before the most recent simulation completed; re-run "
                    "the study to refresh it. " + desc
                )
            out.append({
                "name": f"{html_file.stem} (auto)",
                "url": f"/{rel}",
                "description": desc,
                "stale": stale,
            })

    # Source 2: reports/figures/<name>/*.html (hand-authored cross-skill output).
    # No runs.db gate — these aren't auto-rendered.
    figures_dir = wp.reports / "figures" / name
    if figures_dir.is_dir():
        for html_file in sorted(figures_dir.glob("*.html")):
            size_kb = max(1, html_file.stat().st_size // 1024)
            rel = html_file.relative_to(ws_root).as_posix()
            out.append({
                "name": f"{html_file.stem}",
                "url": f"/{rel}",
                "description": (
                    f"Hand-authored figure ({size_kb} KB) from reports/figures/{name}/."
                ),
                "stale": False,
            })

    return out


# ---------------------------------------------------------------------------
# The full run-merging study-detail loader
# ---------------------------------------------------------------------------

def load_study_detail_spec(ws_root: Path, name: str) -> Optional[dict]:
    """Load a study's spec for the GET /studies/<name> detail page.

    Resolves ``studies/`` or ``investigations/``, ``study.yaml`` or ``spec.yaml``
    (via :func:`study_spec_path`), then runs it through ``load_spec`` so legacy
    v2 specs are migrated to the v3 shape. Returns ``None`` when no spec file
    exists for the name.

    Merges runs from ``studies/<name>/runs.db`` (canonical source of truth for
    CLI- and dashboard-launched runs) on top of any ``spec.runs`` persisted in
    study.yaml, reconciles ``simulation_set`` with the actual runs, and
    auto-discovers pre-rendered viz HTML.  The render-only enrichment tail
    (param-enforcement, expert feedback, derived status, gate verdict, …) mirrors
    the legacy handler step-for-step, delegating the few server-local helpers
    back to the stdlib ``server`` module via a lazy import.

    Mirrors ``server._study_detail_spec`` parameterised on ``ws_root``.
    """
    from vivarium_dashboard.lib.investigations import load_spec
    from vivarium_dashboard.lib.study_enrichment import (  # noqa: PLC0415
        reconcile_simset_with_runs,
        compute_param_enforcement,
        collect_study_feedback,
        study_acceptance_criterion,
    )
    spec_path = study_spec_path(ws_root, name)
    if not spec_path.is_file():
        return None
    spec = load_spec(spec_path)
    if isinstance(spec, dict):
        try:
            db_runs = read_runs_db_for_study(ws_root, name)
        except Exception:
            db_runs = []
        if db_runs:
            existing_ids = {(r or {}).get("run_id") for r in (spec.get("runs") or [])}
            merged = list(spec.get("runs") or [])
            for r in db_runs:
                if r.get("run_id") not in existing_ids:
                    merged.append(r)
            spec["runs"] = merged

        # Reconcile the simulation_set with the actual runs so the Simulations
        # tab reflects current status (seeds / duration / run-count / ran) rather
        # than the authored-or-synthesized plan's "? min / not set / ready".
        try:
            spec["simulation_set"] = reconcile_simset_with_runs(
                spec.get("simulation_set"), spec.get("runs"), ws_root=ws_root)
            # Fill the rest of each entry's promise: condition + tests applied.
            _cond = (spec.get("condition") or spec.get("media")
                     or (spec.get("model_change") or {}).get("condition"))
            _ntests = len(spec.get("tests") or spec.get("behavior_tests") or [])
            for _e in (spec.get("simulation_set") or []):
                if not isinstance(_e, dict):
                    continue
                if _cond and not _e.get("condition"):
                    _e["condition"] = _cond
                if _ntests and not _e.get("n_tests_applied"):
                    _e["n_tests_applied"] = _ntests
        except Exception:  # noqa: BLE001
            pass

        # Auto-discover any pre-rendered Plotly HTML files at
        # studies/<name>/viz/*.html (produced by render_visualizations
        # after a CLI- or dashboard-launched run). They get surfaced on
        # the Visualizations tab as embed_visualizations entries — no
        # manual study.yaml edit required.
        try:
            auto_embeds = discover_viz_html_files(ws_root, name)
        except Exception:
            auto_embeds = []
        if auto_embeds:
            existing_urls = {
                (e or {}).get("url")
                for e in (spec.get("embed_visualizations") or [])
            }
            merged_embeds = list(spec.get("embed_visualizations") or [])
            for e in auto_embeds:
                if e.get("url") not in existing_urls:
                    merged_embeds.append(e)
            spec["embed_visualizations"] = merged_embeds

        # Param-enforcement gate (expert-feedback D.2): if the study declares
        # `enforced_params`, verify the latest run actually applied them.
        # Surfaces "declared but not applied" as structured violations the
        # report renders as a banner, instead of the silent default-use the
        # reviewer caught. Best-effort — never breaks the study response.
        try:
            spec["param_enforcement"] = compute_param_enforcement(spec)
        except Exception:  # noqa: BLE001
            pass

        # Imported expert feedback (expert-feedback B.1): attach any
        # annotations a reviewer left on this study's sections so the report
        # shows them back in-context, closing the loop. Best-effort.
        try:
            fb = collect_study_feedback(ws_root, name)
            if fb:
                spec["expert_feedback"] = fb
        except Exception:  # noqa: BLE001
            pass

        # Stage-3c: tracked feedback index with per-item status
        # (open / addressed / dismissed).  Pure Python in pbg-superpowers —
        # no AI dependency.  Best-effort; empty result on any error.
        try:
            from pbg_superpowers.feedback_tracking import study_feedback_tracked
            ft = study_feedback_tracked(ws_root, name)
            # Always attach so the SPA can render the panel (empty → no items).
            spec["feedback_tracked"] = ft
        except Exception:  # noqa: BLE001
            pass

        # SP3b: tracked feedback ACTIONS — each open feedback item joined with
        # its proposed action (kind + proposed_text) and open/applied status.
        # Pure Python in pbg-superpowers (the dashboard never computes the
        # action — it renders this + applies via /api/feedback-apply-action).
        try:
            from pbg_superpowers.feedback_actions import study_feedback_actions
            spec["feedback_actions"] = study_feedback_actions(ws_root, name)
        except Exception:  # noqa: BLE001
            pass

        # Derive-on-read status (round-2 friction #2): compute the observable
        # status axes from runs.db so the report shows what actually ran, and
        # flag any stored axis (or legacy planning headline) that contradicts
        # execution state. Stops the "planning status after execution" drift.
        try:
            from pbg_superpowers import study_status as _ss
            runs = spec.get("runs") or []
            spec["derived_status"] = _ss.derive_status(spec, runs)
            diss = _ss.status_disagreements(spec, runs)
            if diss:
                spec["status_disagreements"] = diss
            # Single-sourced reviewer-facing run/test/verdict summary — the
            # downloadable report's per-study clarity strip renders from this so
            # the markers are derived once (here) and shown consistently.
            spec["clarity_summary"] = _ss.study_clarity_summary(spec, runs)
        except Exception:  # noqa: BLE001
            pass

        # Coded gate verdict (spine stage #2): surface the study verdict
        # alongside the authored gate_status so the SPA can render both and flag
        # divergence. PREFER the PERSISTED pipeline_gate.gate_evaluator written
        # by study_verdict.write_gate_evaluator — it carries result,
        # evaluated_by AND diverges_from_authored (the code-vs-authored signal).
        # Only fall back to roll_up_verdict (a render-only recompute that DROPS
        # diverges_from_authored) when no persisted slot exists. Does NOT modify
        # study.yaml; this is render-only.
        try:
            persisted_ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator")
            if isinstance(persisted_ge, dict) and persisted_ge.get("result"):
                spec["computed_gate_verdict"] = dict(persisted_ge)
            else:
                from pbg_superpowers.study_verdict import roll_up_verdict
                spec["computed_gate_verdict"] = roll_up_verdict(spec)
        except Exception:  # noqa: BLE001
            pass

        # Wave 3a #18: pre-registration status — compare the study's declared
        # `preregistered` block (registered_at vs the canonical run's start;
        # thresholds vs behavior_tests[].pass_if) so the SPA / report can render
        # a "pre-registered ✓ / post-hoc ⚠" chip in the verdict area. Pure
        # function in pbg-superpowers; render-only, never modifies study.yaml.
        # Defensive: degrade silently if pbg-superpowers isn't importable.
        try:
            from pbg_superpowers.study_verdict import preregistration_status
            ps = preregistration_status(spec)
            if isinstance(ps, dict) and ps.get("preregistered"):
                spec["preregistration_status"] = ps
        except Exception:  # noqa: BLE001
            pass

        # Spine C1a: surface the owning investigation's PERSISTED acceptance
        # criterion(s) covering THIS study so the "Spine at a glance" panel can
        # show the acceptance roll-up + link to the investigation. Pure disk
        # read of executive.computed_acceptance — NO recompute (the live
        # roll-up still happens in the investigation builder). Best-effort.
        try:
            sa = study_acceptance_criterion(ws_root, name)
            if sa:
                spec["spine_acceptance"] = sa
        except Exception:  # noqa: BLE001
            pass

        # Wave 3b #25 — attach the derived lifecycle floor to each finding (the
        # report-data path so the SPA renders the chip without a JS recompute).
        # Defensive: a missing pbg_superpowers.study_verdict.lifecycle_floor
        # leaves findings untouched (the chip then shows only the authored state).
        try:
            from pbg_superpowers.study_verdict import lifecycle_floor as _lf
            for _f in (spec.get("findings") or []):
                if not isinstance(_f, dict) or "_lifecycle_floor" in _f:
                    continue
                try:
                    _v = _lf(_f, spec)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(_v, str) and _v.strip():
                    _f["_lifecycle_floor"] = _v.strip()
        except Exception:  # noqa: BLE001
            pass
    return spec
