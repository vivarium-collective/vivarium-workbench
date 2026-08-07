"""Run-merging study-detail loader and slug-validation constant.

This is the **keystone** of the FastAPI strangler-fig migration's study side:
the per-study loader that resolves a study's spec, **merges the runs recorded in
``studies/<slug>/runs.db``** on top of any ``spec.runs`` persisted in
study.yaml, **reconciles ``simulation_set``** with what actually ran, and
**auto-discovers pre-rendered viz HTML**.  Several routes need exactly this
merged spec — most importantly rigor (``viva_superpowers.rigor`` reads
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

from vivarium_workbench.lib.workspace_paths import WorkspacePaths
from vivarium_workbench.lib import study_derivations as _study_derivations

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
# Normalized interface (composite/config/inputs/outputs/emitter)
# ---------------------------------------------------------------------------

def study_interface(spec: dict) -> dict:
    """Normalize a study spec's execution interface.

    Returns ``{composite, config, inputs: [{artifact, from, into?}],
    outputs: [str], emitter}``. ``inputs``/``outputs`` default to ``[]`` and
    ``config`` defaults to ``{}`` when absent (back-compat with legacy specs
    that don't declare an interface at all). ``composite``/``emitter``
    default to ``None`` when absent.

    Each ``inputs[]`` entry may optionally carry ``into``: the config key the
    consumer's generator should receive the producer's store path under (the
    generic mechanism ``pipeline._default_compute`` merges into
    ``overrides``). It's optional — an entry with only ``{artifact, from}``
    is still valid, and ``into`` is simply absent (``None``) from the
    returned dict; back-compat behavior (the ``sim_data``->``cache_dir``
    default) lives in ``pipeline._default_compute``, not here.

    Later tasks derive investigation-graph edges from ``inputs[].from`` and
    drive the pull-or-compute pipeline from this block, so a malformed
    ``inputs`` entry (missing ``artifact`` or ``from``) raises
    :class:`InvestigationSpecError` rather than being silently dropped.
    """
    from vivarium_workbench.lib.investigations import InvestigationSpecError

    raw_inputs = spec.get("inputs") or []
    inputs: list[dict] = []
    for i, entry in enumerate(raw_inputs):
        if not isinstance(entry, dict):
            raise InvestigationSpecError(f"interface.inputs[{i}] must be a mapping")
        artifact = entry.get("artifact")
        source = entry.get("from")
        if not artifact:
            raise InvestigationSpecError(
                f"interface.inputs[{i}] is missing required field 'artifact'"
            )
        if not source:
            raise InvestigationSpecError(
                f"interface.inputs[{i}] ({artifact!r}) is missing required field 'from'"
            )
        into = entry.get("into")
        item = {"artifact": str(artifact), "from": str(source)}
        if into is not None:
            item["into"] = str(into)
        inputs.append(item)

    raw_outputs = spec.get("outputs") or []
    outputs = [str(o) for o in raw_outputs]

    # Fall back to conditions.baseline for composite and config if not defined
    # at the top level. This handles Phase 2 migration where models moved under
    # conditions.baseline.{composite, params}.
    cond_baseline = ((spec.get("conditions") or {}).get("baseline")) or {}
    composite = spec.get("composite") or cond_baseline.get("composite")
    config = spec.get("config")
    if not config:
        config = dict(cond_baseline.get("params") or {})

    return {
        "composite": composite,
        "config": config,
        "inputs": inputs,
        "outputs": outputs,
        "emitter": spec.get("emitter"),
    }


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

    # Consolidation: fold the workspace-wide `.pbg/runs.jsonl` run-index (the
    # SAME canonical source the Simulations tab reads via
    # simulations_index.build_simulations_data) and add any runs tagged for this
    # study that the per-study runs.db / study.yaml didn't already surface —
    # e.g. bespoke pbg_runner or remote runs that only ever land in the JSONL.
    # Additive (setdefault): runs.db stays authoritative where both have a row.
    try:
        from vivarium_workbench.lib.run_log import fold_runs_jsonl
        for _rid, _rec in fold_runs_jsonl(ws_root).items():
            if _rec.get("study_slug") != name or _rid in rows_by_id:
                continue
            _params = _rec.get("params") if isinstance(_rec.get("params"), dict) else {}
            rows_by_id[_rid] = {
                "run_id":        _rid,
                "spec_id":       _rec.get("spec_id") or name,
                "label":         _rec.get("label") or _rec.get("sim_name") or _rid,
                "sim_name":      _rec.get("sim_name") or _rec.get("label") or _rid,
                "variant":       _params.get("variant"),
                "composite":     _params.get("composite") or _rec.get("spec_id"),
                "params":        _params,
                "n_steps":       _rec.get("n_steps"),
                "status":        _rec.get("status") or "completed",
                "started_at":    _rec.get("started_at"),
                "completed_at":  _rec.get("completed_at") or _rec.get("started_at"),
                "generation_id": _rec.get("generation_id"),
                "source":        "runs.jsonl",
            }
    except Exception:  # noqa: BLE001 — the JSONL index is a best-effort overlay
        pass

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
        from vivarium_workbench.lib import generation as _gen
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
        # Provenance (Fable §4.5, Task V3): a NON-STALE viz HTML file was
        # rendered from this study's runs.db AFTER the latest run completed,
        # so it genuinely represents that run — attach latest_run_id (below,
        # gated on `not stale`). A STALE file predates the latest run — it
        # did NOT derive from it, so it gets run_id=None rather than a
        # fabricated link to a run it never came from. Reuse study_charts'
        # existing run-lookup helper (also used by the native gallery)
        # rather than add a new run-scanning path.
        from vivarium_workbench.lib.study_charts import latest_run_row  # noqa: PLC0415
        _latest = latest_run_row(runs_db)
        latest_run_id = _latest.get("run_id") if _latest else None
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
                # A stale file predates the latest run — it did NOT derive
                # from it, so attributing latest_run_id here would be
                # fabricated provenance (V3 review, fix round 1). Only a
                # non-stale file genuinely represents the latest run.
                "run_id": None if stale else latest_run_id,
            })

    # Source 2: reports/figures/<name>/*.html (hand-authored cross-skill output).
    # No runs.db gate — these aren't auto-rendered. No genuine run association
    # either (Task V3): a hand-authored file isn't tied to a specific run, so
    # run_id stays null rather than fabricate one.
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
                "run_id": None,
            })

    return out


# ---------------------------------------------------------------------------
# The full run-merging study-detail loader
# ---------------------------------------------------------------------------

def _card_html_stub(html_path, verdict) -> bool:
    """Whether a report card's HTML is an unrendered stub (Phase 2c).

    A computed verdict (a ``<card>.verdict.json`` was read into ``verdict``)
    is authoritative: the card has a real, machine-readable result, so it is
    NOT a stub regardless of the HTML file's size. Only when there is NO
    computed verdict do we fall back to the ``size < 64`` heuristic (a tiny
    HTML file with no verdict is a placeholder the frontend should replace
    with the verdict table). A stat error is treated as a stub.
    """
    if verdict is not None:
        return False
    try:
        return html_path.stat().st_size < 64
    except OSError:
        return True


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
    from vivarium_workbench.lib.investigations import load_spec
    from vivarium_workbench.lib.study_enrichment import (  # noqa: PLC0415
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
        # Normalized execution interface (composite/config/inputs/outputs/
        # emitter). Legacy specs with no interface block get inputs=[]/
        # outputs=[] and never raise; a malformed input (missing 'from')
        # is an authoring error and SHOULD surface, so this is deliberately
        # not wrapped in a bare try/except like the best-effort blocks below.
        spec["interface"] = study_interface(spec)
        try:
            db_runs = read_runs_db_for_study(ws_root, name)
        except Exception:
            db_runs = []
        if db_runs:
            # Dedup by run_id. study.yaml `runs:` entries key their identity
            # under `name` (== runs_meta.run_id); db_runs key under `run_id`.
            # The old merge built existing_ids from `run_id` on the study.yaml
            # side — which is absent there — so the set collapsed to {None} and
            # EVERY db_run was appended even when the same run was already
            # present as a study.yaml entry, producing two rows per run (the
            # "name vs run_id" duplicate). db_runs is the canonical run-index
            # (runs.db + study.yaml mechanical + .pbg/runs.jsonl fold), so use it
            # as the run LIST and graft each study.yaml entry's AUTHORED fields
            # (outcomes/provenance — which the index doesn't carry) onto the
            # matching row by run_id.
            _authored_keys = ("outcomes", "computed_outcomes", "provenance",
                              "commit", "conclusion", "notes")
            yaml_by_id: dict = {}
            for _y in (spec.get("runs") or []):
                if isinstance(_y, dict):
                    _yid = _y.get("run_id") or _y.get("name")
                    if _yid:
                        yaml_by_id[_yid] = _y
            merged = []
            seen = set()
            for r in db_runs:
                rid = r.get("run_id")
                seen.add(rid)
                _y = yaml_by_id.get(rid)
                if _y:
                    for _k in _authored_keys:
                        if _k in _y and _k not in r:
                            r[_k] = _y[_k]
                merged.append(r)
            # study.yaml-only runs with no run-index row (defensive — normally
            # covered by _study_yaml_run_rows inside read_runs_db_for_study).
            for _yid, _y in yaml_by_id.items():
                if _yid not in seen:
                    merged.append(_y)
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
            from viva_superpowers.feedback_tracking import study_feedback_tracked
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
            from vivarium_workbench.lib.feedback_actions import study_feedback_actions
            spec["feedback_actions"] = study_feedback_actions(ws_root, name)
        except Exception:  # noqa: BLE001
            pass

        # Derive-on-read status (round-2 friction #2): compute the observable
        # status axes from runs.db so the report shows what actually ran, and
        # flag any stored axis (or legacy planning headline) that contradicts
        # execution state. Stops the "planning status after execution" drift.
        try:
            from viva_superpowers import study_status as _ss
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
                cgv = dict(persisted_ge)
            else:
                from viva_superpowers.study_verdict import roll_up_verdict
                cgv = roll_up_verdict(spec)
            # Attach the per-test counts the verdict was derived from so the SPA
            # can show "4 passed · 1 skipped" beside the badge (workbench#758) —
            # distinguishing needs_calibration-with-progress from a bare skip.
            # roll_up_verdict already carries `counts`; the persisted slot may
            # predate it, so backfill render-time (never persisted → no churn).
            if isinstance(cgv, dict) and "counts" not in cgv:
                try:
                    from viva_superpowers.study_status import count_test_outcomes
                    cgv["counts"] = count_test_outcomes(spec, spec.get("runs"))
                except Exception:  # noqa: BLE001
                    pass
            spec["computed_gate_verdict"] = cgv
        except Exception:  # noqa: BLE001
            pass

        # Wave 3a #18: pre-registration status — compare the study's declared
        # `preregistered` block (registered_at vs the canonical run's start;
        # thresholds vs behavior_tests[].pass_if) so the SPA / report can render
        # a "pre-registered ✓ / post-hoc ⚠" chip in the verdict area. Pure
        # function in pbg-superpowers; render-only, never modifies study.yaml.
        # Defensive: degrade silently if pbg-superpowers isn't importable.
        try:
            from viva_superpowers.study_verdict import preregistration_status
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
        # Defensive: a missing viva_superpowers.study_verdict.lifecycle_floor
        # leaves findings untouched (the chip then shows only the authored state).
        try:
            from viva_superpowers.study_verdict import lifecycle_floor as _lf
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

        # Per-study report-card artifacts (viz/report_card/<card>.{html,verdict.json})
        # surfaced so the SPA Tests section can embed a `kind: report_card` module
        # without cross-referencing the global saved-visualizations endpoint.
        # Resolve via study_dir() so NESTED investigations/<inv>/studies/<slug>/
        # layouts (e.g. the v2ecoli↔vEcoli comparison) are found too — the flat
        # ws_root/studies/<name> path misses them and left report_card_urls empty.
        rc_dir = study_dir(ws_root, name) / "viz" / "report_card"
        rc_urls: dict = {}
        if rc_dir.is_dir():
            for html in sorted(rc_dir.glob("*.html")):
                card = html.name[: -len(".html")]
                vf = html.with_name(card + ".verdict.json")
                verdict = None
                groups = None
                if vf.is_file():
                    try:
                        _vj = _json.loads(vf.read_text(encoding="utf-8"))
                        verdict = _vj.get("overall")
                        # Full per-group/per-axis detail so the Report Cards tab
                        # can render the observable-by-observable comparison
                        # (label / verdict / Δ meter) even when the HTML card is
                        # an unrendered stub.
                        groups = _vj.get("groups")
                    except Exception:  # noqa: BLE001
                        verdict = None
                # The card HTML is sometimes an unrendered stub (e.g. "<b>card</b>");
                # a computed verdict wins, else flag tiny files so the frontend
                # renders the verdict table rather than an empty iframe.
                html_stub = _card_html_stub(html, verdict)
                rc_urls[card] = {"url": "/" + html.relative_to(ws_root).as_posix(),
                                 "verdict": verdict, "groups": groups,
                                 "html_stub": html_stub}
        spec["report_card_urls"] = rc_urls
        # A rich report card (docs/report_cards/<set>/<card>/report_card.html)
        # belongs on the Report Cards tab, not embedded in Visualizations. Promote
        # any such embed into report_card_urls (preferring its richer render + its
        # own verdict over a thin/stale viz/report_card/<card>.html) and drop it
        # from embed_visualizations so Visualizations shows real figures.
        _promote_report_card_embeds(spec, ws_root)
        # Deterministic evidence chain: when the study has report cards but no
        # authored findings, DERIVE one finding per card from the card's own
        # verdict/subject so the study pillar (and the investigation graph) show
        # the evidence instead of "pending". Authored findings are never
        # clobbered (derive returns them unchanged). Also backfills each card's
        # verdict pill when it was None. Best-effort — never breaks the response.
        try:
            _existing = spec.get("findings")
            _rc = spec.get("report_card_urls")
            if (not _existing) and isinstance(_rc, dict) and _rc:
                _derived = derive_findings_from_report_cards(
                    ws_root, name, _rc, _existing)
                if _derived:
                    spec["findings"] = _derived
        except Exception:  # noqa: BLE001
            pass
        # Interactive plotly comparison (v2ecoli vs vEcoli overlays), rendered
        # into <study>/viz/comparison_plotly.html — surfaced above the scorecards
        # in the Report Cards tab. Absent for studies that don't have one.
        _plotly = study_dir(ws_root, name) / "viz" / "comparison_plotly.html"
        if _plotly.is_file():
            spec["comparison_plotly_url"] = "/" + _plotly.relative_to(ws_root).as_posix()
    if isinstance(spec, dict):
        # Single source of truth for per-test outcomes. The template (per-row
        # pills), the Tests-tab JS summary, and the Conclusions rollup all used to
        # re-derive "latest outcome per test" from runs[].outcomes independently;
        # compute it once here so they can't drift.
        latest, rollup = _latest_outcomes(spec)
        spec["latest_outcomes"] = latest
        spec["outcome_rollup"] = rollup
    from vivarium_workbench.lib.run_commands import study_run_commands
    spec["run_commands"] = study_run_commands(spec, name)
    spec["derived"] = _study_derivations.derived_block(spec)
    # Spine precedent (mirrors computed_gate_verdict / write_gate_evaluator
    # above): prefer the PERSISTED conclusion-card verdict — written by
    # conclusion_card.write_conclusion_card as part of the post-run flush —
    # over this render's live recompute of `derived.conclusion_verdicts`. Only
    # each track's computed `result` is taken from the frozen, disk-persisted
    # card; `basis` (the author's free-text rationale, sourced from
    # spec["conclusion_verdicts"]) stays the LIVE value so editing it in
    # study.yaml shows up immediately without needing a rerun. Falls back to
    # the live recompute entirely when no card has been persisted yet, or on
    # any read/parse failure — this must never break the study-detail render.
    try:
        _cv_path = study_dir(ws_root, name) / "viz" / "report_card" / "conclusion.verdict.json"
        if _cv_path.is_file():
            _persisted = _json.loads(_cv_path.read_text(encoding="utf-8"))
            # G8: the file's mere existence as a parsed dict IS the freeze
            # signal — write_conclusion_card is the ONLY writer, called once
            # per post-run flush, and there is no separate "frozen: true"
            # field anywhere to read. No timestamp is stored INSIDE the
            # payload either, so "when" is the file's own mtime. Surfaced as
            # spec["conclusion_card_frozen"] = {"when", "payload"} for the
            # Decide-tab frozen-record indicator (study_page.conclusion_digest
            # computes the digest from `payload` at render time); left absent
            # entirely when unparseable so no indicator is fabricated.
            if isinstance(_persisted, dict):
                try:
                    spec["conclusion_card_frozen"] = {
                        "when": _cv_path.stat().st_mtime,
                        "payload": _persisted,
                    }
                except OSError:
                    pass
            _ptracks = _persisted.get("tracks") if isinstance(_persisted, dict) else None
            if isinstance(_ptracks, dict):
                _live = spec["derived"].get("conclusion_verdicts")
                _merged = dict(_live) if isinstance(_live, dict) else {}
                for _track, _pt in _ptracks.items():
                    if not isinstance(_pt, dict):
                        continue
                    _lt_raw = _merged.get(_track)
                    _lt = _lt_raw if isinstance(_lt_raw, dict) else {}
                    _merged[_track] = {
                        "result": _pt.get("result", _lt.get("result", "")),
                        "basis": _lt.get("basis", _pt.get("basis", "")),
                    }
                if _merged:
                    spec["derived"]["conclusion_verdicts"] = _merged
    except Exception:  # noqa: BLE001 — render must never break on a bad card
        pass
    return spec


# Canonical machine marker a report card carries in its <head> so it is classified
# deterministically regardless of filename or which directory it was dropped into.
# Rendered report cards and hand-authored ones should both emit it; the classifier
# below prefers it over the fragile filename/title heuristics.
REPORT_CARD_MARKER = '<meta name="viv-artifact" content="report-card">'


def _html_self_identifies_as_report_card(path: Path) -> bool:
    """Content sniff: does this HTML self-identify as a report card?

    Deterministic marker first (``REPORT_CARD_MARKER`` in the head); falls back to a
    ``<title>… report card</title>`` for hand-authored cards predating the marker.
    Reads only a bounded prefix — report-card identity lives in the document head, so
    this never loads a multi-MB figure in full.
    """
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:8192]
    except OSError:
        return False
    low = head.lower()
    if 'name="viv-artifact"' in low and 'content="report-card"' in low:
        return True
    m = re.search(r"<title>(.*?)</title>", low, re.DOTALL)
    return bool(m and "report card" in m.group(1))


def _is_report_card_embed(entry: dict, ws_root: Optional[Path] = None) -> bool:
    """True when a Visualizations embed actually points at a rendered report card.

    Filename/path/display-name heuristics catch the conventional locations cheaply
    (a ReportCardStep output under ``docs/report_cards/.../report_card.html``). When a
    ``ws_root`` is given, a report card that was hand-dropped into ``reports/figures/``
    under a figure-style filename is *still* caught by sniffing the HTML for the
    report-card marker (or a ``… report card`` title) — so classification depends on
    what the artifact IS, not where it lives or what it is named."""
    url = str((entry or {}).get("url") or "")
    name = str((entry or {}).get("name") or "").lower()
    path = url.split("?", 1)[0]
    if (
        ("/report_cards/" in path and path.endswith("report_card.html"))
        or "report card" in name
        or "report_card" in path.rsplit("/", 1)[-1]
    ):
        return True
    if ws_root is not None and path.endswith(".html"):
        rel = path[1:] if path.startswith("/") else path
        return _html_self_identifies_as_report_card(ws_root / rel)
    return False


def _promote_report_card_embeds(spec: dict, ws_root: Path) -> None:
    """Move rich report-card embeds off the Visualizations tab and onto the
    Report Cards tab.

    A report card is the ``ReportCardStep`` subtype's output; when a study wired
    its *rich* render (``docs/report_cards/<set>/<card>/report_card.html``) into
    ``embed_visualizations``, it showed up under Visualizations while the Report
    Cards tab rendered a thinner/older ``viz/report_card/<card>.html``. Promote the
    rich render into ``report_card_urls`` (keyed by the ``<card>`` path segment),
    sourcing its verdict from the sibling ``report_card_verdict.json`` so the tab's
    verdict pill matches the card, and remove it from ``embed_visualizations``.
    Mutates *spec* in place; tolerant of missing files.
    """
    embeds = spec.get("embed_visualizations")
    if not isinstance(embeds, list) or not embeds:
        return
    rc_urls = spec.get("report_card_urls")
    if not isinstance(rc_urls, dict):
        rc_urls = {}
    kept: list = []
    for entry in embeds:
        if not (isinstance(entry, dict) and _is_report_card_embed(entry, ws_root)):
            kept.append(entry)
            continue
        url = str(entry.get("url") or "")
        path = url.split("?", 1)[0]
        # Canonical rich render is ``.../<card>/report_card.html`` → key by <card>.
        # A hand-authored card in ``reports/figures/<study>/<stem>.html`` must key by
        # its own <stem>, NOT the parent dir — otherwise every promoted card in the
        # same study collides on the study name and only the last survives.
        parts = [p for p in path.split("/") if p]
        stem = parts[-1].rsplit(".", 1)[0] if parts else "report_card"
        card = parts[-2] if (len(parts) >= 2 and parts[-1] == "report_card.html") else stem
        verdict = None
        groups = None
        # Rich bundle carries its verdict as report_card_verdict.json (sibling).
        try:
            rel = path[1:] if path.startswith("/") else path
            vj = (ws_root / rel).with_name("report_card_verdict.json")
            if vj.is_file():
                _vj = _json.loads(vj.read_text(encoding="utf-8"))
                verdict = _vj.get("overall")
                groups = _vj.get("groups")
        except Exception:  # noqa: BLE001
            pass
        # Prefer the rich render (override any thin viz/report_card/<card> entry).
        _prev = rc_urls.get(card)
        prev: dict = _prev if isinstance(_prev, dict) else {}
        rc_urls[card] = {
            "url": url,
            "verdict": verdict if verdict is not None else prev.get("verdict"),
            "groups": groups if groups is not None else prev.get("groups"),
            "html_stub": False,
            "rich": True,
            "label": entry.get("name") or prev.get("label"),
        }
    if len(kept) != len(embeds):
        spec["embed_visualizations"] = kept
        spec["report_card_urls"] = rc_urls


# ---------------------------------------------------------------------------
# Report-card evidence derivation
# ---------------------------------------------------------------------------
#
# When a study carries report cards but no authored ``findings``, its evidence
# chain would otherwise render blank ("pending evidence") in the investigation
# DAG and the study pillar. These helpers DETERMINISTICALLY derive one finding
# per report card from the card's own verdict/subject, so the graph shows
# "Finds: …" and the pillar reflects the evidence — without ever clobbering
# hand-authored findings.

# The verdict vocabulary the Report Cards tab pills understand
# (static/walkthrough.js ``_rcPill``): within_tol / drift / mismatch / ungraded.
_REPORT_CARD_VERDICTS = ("within_tol", "drift", "mismatch", "ungraded")

# Badge tokens (and JSON ``overall`` values) mapped to that canonical vocabulary.
# Hand-authored cards spell the overall verdict as PASS / MISMATCH in the badge;
# rendered cards use the canonical within_tol/drift/mismatch. Both normalise here.
_VERDICT_ALIASES = {
    "mismatch": "mismatch", "fail": "mismatch", "failed": "mismatch",
    "fails": "mismatch", "diverge": "mismatch", "divergent": "mismatch",
    "pass": "within_tol", "passed": "within_tol", "passes": "within_tol",
    "within_tol": "within_tol", "withintol": "within_tol", "within": "within_tol",
    "match": "within_tol", "matched": "within_tol", "ok": "within_tol",
    "drift": "drift", "partial": "drift", "marginal": "drift",
    "ungraded": "ungraded", "na": "ungraded", "n_a": "ungraded", "none": "ungraded",
}

# Map a card verdict to the finding ``status`` glyph the SPA renders
# (static/walkthrough.js: confirms ✓ / partial ◐ / contradicts ✗ / novel ◆).
_VERDICT_TO_STATUS = {
    "within_tol": "confirms",
    "drift": "partial",
    "mismatch": "contradicts",
    "ungraded": "novel",
}
_VERDICT_LABEL = {
    "within_tol": "within tolerance",
    "drift": "drift",
    "mismatch": "mismatch",
    "ungraded": "ungraded",
}
# Higher = more severe. Used to pick the DAG node's headline claim.
_VERDICT_SEVERITY = {"mismatch": 3, "drift": 2, "within_tol": 1, "ungraded": 0}


def _normalize_verdict(token: Optional[str]) -> Optional[str]:
    """Canonicalise a raw verdict token to the ``_REPORT_CARD_VERDICTS`` vocab.

    Lower-cases, collapses spaces/dashes to underscores, then maps through
    ``_VERDICT_ALIASES``. Returns the canonical string, or ``None`` when the
    token is empty/unrecognised (callers treat ``None`` as ungraded).
    """
    if not token or not isinstance(token, str):
        return None
    t = re.sub(r"[\s\-]+", "_", token.strip().lower())
    if not t:
        return None
    if t in _VERDICT_ALIASES:
        return _VERDICT_ALIASES[t]
    return t if t in _REPORT_CARD_VERDICTS else None


def _report_card_verdict_and_subject(path: Path):
    """Extract ``(verdict, subject, counts)`` from a report-card artifact.

    - **verdict**: canonical token (within_tol/drift/mismatch/ungraded) or None.
      A sibling ``<stem>.verdict.json`` / ``report_card_verdict.json`` ``overall``
      is PREFERRED (authoritative); otherwise the overall badge token in the HTML
      (``<… class="obadge …">MISMATCH · 1 ✓ 0 ≈ 3 ✗ 0 –</…>``) is parsed. Only the
      badge is read for the verdict — never the CSS/legend, which mention every
      verdict class and would poison a whole-document scan.
    - **subject**: the ``<title>`` segment before "… report card"
      (e.g. "v2ecoli baseline vs v2ecoli + Millard FBA-bridge — central-carbon
      exchange"), or None.
    - **counts**: ``{within_tol, drift, mismatch, ungraded}`` tallies parsed from
      the badge glyphs (✓ ≈ ✗ –), or None when unparsable.

    Tolerant: any read/parse failure yields ``None`` in that slot. Reads a bounded
    prefix so a multi-MB embedded figure is never loaded whole.
    """
    import html as _html

    verdict = None
    # 1) Sibling verdict.json wins (authoritative, machine-written).
    for sib in (path.with_name(path.stem + ".verdict.json"),
                path.with_name("report_card_verdict.json")):
        try:
            if sib.is_file():
                _vj = _json.loads(sib.read_text(encoding="utf-8"))
                verdict = _normalize_verdict(_vj.get("overall"))
                if verdict is not None:
                    break
        except Exception:  # noqa: BLE001
            pass

    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:400_000]
    except OSError:
        return verdict, None, None

    # Subject from <title>, dropping a trailing "… report card".
    subject = None
    mt = re.search(r"<title>(.*?)</title>", text, re.DOTALL | re.IGNORECASE)
    if mt:
        title = _html.unescape(re.sub(r"\s+", " ", mt.group(1))).strip()
        title = re.sub(r"\s*[—–-]\s*report card\s*$", "", title, flags=re.IGNORECASE).strip()
        subject = title or None

    # Overall verdict badge + glyph counts (only the obadge span, never the CSS).
    counts = None
    mb = re.search(r'class="[^"]*obadge[^"]*"[^>]*>(.*?)</', text, re.DOTALL | re.IGNORECASE)
    if mb:
        badge = _html.unescape(re.sub(r"<[^>]+>", " ", mb.group(1)))
        badge = badge.replace("\xa0", " ")
        badge = re.sub(r"\s+", " ", badge).strip()
        if verdict is None:
            mtok = re.match(r"[^A-Za-z]*([A-Za-z][A-Za-z _\-]*?)\s*(?:[·|]|&|\d|$)", badge)
            if mtok:
                verdict = _normalize_verdict(mtok.group(1))
        c = {}
        for key, glyph in (("within_tol", "✓"), ("drift", "≈"),
                           ("mismatch", "✗"), ("ungraded", "–")):
            gm = re.search(r"(\d+)\s*" + re.escape(glyph), badge)
            if gm:
                c[key] = int(gm.group(1))
        if c:
            counts = {k: c.get(k, 0) for k in _REPORT_CARD_VERDICTS}

    return verdict, subject, counts


def _resolve_report_card_path(ws_root: Path, url: Optional[str]) -> Optional[Path]:
    """Resolve a workspace-relative report-card URL to an on-disk file, or None."""
    if not url or not isinstance(url, str):
        return None
    p = url.split("?", 1)[0]
    rel = p[1:] if p.startswith("/") else p
    cand = Path(ws_root) / rel
    return cand if cand.is_file() else None


def _finding_from_report_card(card: str, url, verdict, subject, counts, label) -> dict:
    """Build ONE derived finding dict for a report card.

    The key set matches exactly what the SPA finding readers consume
    (static/walkthrough.js): ``id`` (anchor + fallback), ``statement`` and
    ``summary`` (DAG "Finds:" / claim / takeaways / synthesis), ``status``
    (glyph), ``kind`` (grouping), ``evidence`` (observed/summary). Plus
    provenance the readers ignore but downstream tooling can trust:
    ``verdict``, ``source: "report_card"``, ``url``.
    """
    v = verdict if verdict in _REPORT_CARD_VERDICTS else "ungraded"
    subj = (subject or label or card).strip()
    blurb = ""
    if counts:
        parts = []
        for key, glyph in (("within_tol", "✓"), ("drift", "≈"),
                           ("mismatch", "✗"), ("ungraded", "–")):
            if counts.get(key):
                parts.append(f"{counts[key]} {glyph}")
        blurb = " ".join(parts)
    summary = f"{subj}: {_VERDICT_LABEL.get(v, v)}"
    if blurb:
        summary += f" ({blurb})"
    fid = "report-card-" + re.sub(r"[^a-z0-9]+", "-", str(card).lower()).strip("-")
    evidence = {"summary": f"Report card verdict: {_VERDICT_LABEL.get(v, v)}"}
    if blurb:
        evidence["observed"] = blurb
    return {
        "id": fid,
        "summary": summary,
        "statement": summary,
        "status": _VERDICT_TO_STATUS.get(v, "novel"),
        "kind": "computational",
        "verdict": v,
        "source": "report_card",
        "url": url,
        "evidence": evidence,
    }


def derive_findings_from_report_cards(
    ws_root: Path,
    study_slug: str,
    report_card_urls: dict,
    existing_findings,
) -> list[dict]:
    """Deterministically derive findings from a study's report cards.

    - If ``existing_findings`` is a non-empty list → returned unchanged (authored
      findings are NEVER clobbered).
    - Otherwise, one finding per report card, in a STABLE order (sorted by card
      key). Each finding's verdict/subject/counts come from
      :func:`_report_card_verdict_and_subject` (verdict.json preferred over HTML).
      When a card's ``report_card_urls[card]["verdict"]`` is currently ``None`` it
      is BACKFILLED from the extracted verdict (improves the Report Cards tab
      pills). Pure and tolerant of missing/malformed files.
    """
    if isinstance(existing_findings, list) and existing_findings:
        return existing_findings
    if not isinstance(report_card_urls, dict) or not report_card_urls:
        return []
    ws_root = Path(ws_root)
    out: list[dict] = []
    for card in sorted(report_card_urls):
        # Feedback-loop guard: the `conclusion` card (written by
        # conclusion_card.write_conclusion_card) IS the study's verdict, not
        # evidence for it — study_derivations.conclusion_verdicts().explanatory_gain
        # reads findings[].tier == "interpretation", so letting this card feed a
        # derived finding back into `findings` would let it (indirectly) validate
        # its own explanatory_gain input on the next compute. Skip it here.
        if card == "conclusion":
            continue
        meta = report_card_urls.get(card)
        meta = meta if isinstance(meta, dict) else {}
        url = meta.get("url")
        verdict = _normalize_verdict(meta.get("verdict"))
        subject = None
        counts = None
        path = _resolve_report_card_path(ws_root, url)
        if path is not None:
            try:
                v2, subject, counts = _report_card_verdict_and_subject(path)
            except Exception:  # noqa: BLE001
                v2 = None
            if verdict is None:
                verdict = v2
        # Backfill the report-card entry's verdict when it was unset.
        if verdict is not None and meta.get("verdict") is None:
            meta["verdict"] = verdict
            report_card_urls[card] = meta
        out.append(_finding_from_report_card(
            card, url, verdict, subject, counts, meta.get("label")))
    return out


def discover_report_card_urls(ws_root: Path, slug: str) -> dict:
    """Assemble a study's ``report_card_urls`` WITHOUT the full detail loader.

    Mirrors the report-card discovery inside :func:`load_study_detail_spec` — the
    ``viz/report_card/<card>.{html,verdict.json}`` artifacts plus any rich card
    hand-dropped into ``reports/figures/<slug>/`` (folded in via
    :func:`_promote_report_card_embeds`, which is content-aware). Used by the
    investigation-graph node builder, which loads the raw study.yaml and so lacks
    the enriched ``report_card_urls``. Tolerant: returns ``{}`` on any failure.
    """
    rc_urls: dict = {}
    try:
        sdir = study_dir(ws_root, slug)
    except Exception:  # noqa: BLE001
        sdir = None
    rc_dir = (sdir / "viz" / "report_card") if sdir else None
    if rc_dir and rc_dir.is_dir():
        for html in sorted(rc_dir.glob("*.html")):
            card = html.name[: -len(".html")]
            vf = html.with_name(card + ".verdict.json")
            verdict = None
            groups = None
            if vf.is_file():
                try:
                    _vj = _json.loads(vf.read_text(encoding="utf-8"))
                    verdict = _vj.get("overall")
                    groups = _vj.get("groups")
                except Exception:  # noqa: BLE001
                    verdict = None
            html_stub = _card_html_stub(html, verdict)
            rc_urls[card] = {"url": "/" + html.relative_to(ws_root).as_posix(),
                             "verdict": verdict, "groups": groups,
                             "html_stub": html_stub}
    try:
        embeds = discover_viz_html_files(ws_root, slug)
    except Exception:  # noqa: BLE001
        embeds = []
    probe = {"embed_visualizations": list(embeds), "report_card_urls": rc_urls}
    try:
        _promote_report_card_embeds(probe, ws_root)
    except Exception:  # noqa: BLE001
        pass
    promoted = probe.get("report_card_urls")
    return promoted if isinstance(promoted, dict) else rc_urls


def has_graded_report_cards(ws_root: Path, slug: str) -> bool:
    """True only if a study carries a COMPLETED, graded report-card evaluation.

    Distinguishes a finalized-but-unpromoted study (e.g. metabolism_redux: 8
    per-card ``viz/report_card/*.verdict.json`` with graded overalls like
    ``drift``/``within_tol``) from an unrun/decision study whose only artifact is
    a placeholder ``tests.verdict.json`` with ``overall: ungraded``. Used by the
    investigation status safeguard so it never promotes a genuinely pre-eval
    study to "evaluated". Never raises.
    """
    import json as _json
    _UNGRADED = {"", "ungraded", "pending", "planned", "not_run", "n/a", "none"}
    try:
        from vivarium_workbench.lib.workspace_paths import WorkspacePaths
        rc_dir = WorkspacePaths(ws_root).study_dir(slug) / "viz" / "report_card"
    except Exception:  # noqa: BLE001
        return False
    if not rc_dir.is_dir():
        return False
    for vj in rc_dir.glob("*.verdict.json"):
        try:
            overall = str((_json.loads(vj.read_text(encoding="utf-8")) or {}).get("overall") or "").strip().lower()
        except Exception:  # noqa: BLE001
            continue
        if overall and overall not in _UNGRADED:
            return True
    return False


def report_card_findings_for_study(ws_root: Path, slug: str, existing_findings=None):
    """Discover a study's report cards and derive findings when none are authored.

    Returns ``(findings, report_card_urls)``. If ``existing_findings`` is a
    non-empty list it is returned unchanged (with the discovered urls). Used by
    the investigation-graph node builder so a DAG node's "Finds:"/claim reflect
    the report-card evidence chain.
    """
    rc_urls = discover_report_card_urls(ws_root, slug)
    if isinstance(existing_findings, list) and existing_findings:
        return existing_findings, rc_urls
    findings = derive_findings_from_report_cards(ws_root, slug, rc_urls, None)
    return findings, rc_urls


# Run statuses that count as "completed" when choosing the canonical run.
# Kept in sync with viva_superpowers.study_outcomes._COMPLETE (client side).
_COMPLETE_STATUSES = {"complete", "completed", "ran", "done"}


def _canonical_run(runs: list) -> dict | None:
    """The run whose outcomes are authoritative, matching the client's
    ``viva_superpowers.study_outcomes.canonical_run``:

    1. an explicit ``canonical: true`` run wins (last such run if several);
    2. else the newest *completed* run by ``timestamp``;
    3. else the last run;
    4. else ``None``.

    This deliberately does NOT do last-run-wins per-test aggregation — it picks
    ONE authoritative run so the study page and the report agree on pass/fail.
    """
    runs = [r for r in (runs or []) if isinstance(r, dict)]
    if not runs:
        return None
    flagged = [r for r in runs if r.get("canonical") is True]
    if flagged:
        return flagged[-1]
    completed = [
        r for r in runs
        if str(r.get("status", "")).lower() in _COMPLETE_STATUSES
    ]
    if completed:
        return max(completed, key=lambda r: r.get("timestamp") or "")
    return runs[-1]


def _latest_outcomes(spec: dict) -> tuple[dict, dict]:
    """Resolve the study's authoritative per-test outcomes from the single
    *canonical* run (see ``_canonical_run``), mirroring the client's
    ``viva_superpowers.study_outcomes.canonical_outcomes``.

    Previously this aggregated last-run-wins across ALL runs and ignored the
    ``canonical:`` flag, so the report (client, canonical-run) and the study
    page (server, this function) could disagree on pass/fail for the same YAML.
    Now both pick the one canonical run.

    Returns ``(outcomes_by_test, rollup)`` where ``outcomes_by_test`` maps test
    name -> the outcome dict from the canonical run and ``rollup`` counts
    PASS/FAIL/SKIP/PARTIAL/pending plus a ``total`` and how many runs
    contributed outcomes (``runs`` — 0 or 1 under canonical semantics).
    """
    run = _canonical_run(spec.get("runs", []) or [])
    outcomes = (run or {}).get("outcomes")
    latest: dict = dict(outcomes) if isinstance(outcomes, dict) else {}
    rollup = {"PASS": 0, "FAIL": 0, "SKIP": 0, "PARTIAL": 0, "pending": 0,
              "total": 0, "runs": 1 if latest else 0}
    for outcome in latest.values():
        res = (outcome or {}).get("result") if isinstance(outcome, dict) else None
        key = res if res in ("PASS", "FAIL", "SKIP", "PARTIAL") else "pending"
        rollup[key] += 1
        rollup["total"] += 1
    return latest, rollup
