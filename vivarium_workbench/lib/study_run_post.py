"""Post-run side-effect stages extracted from server.py.

These are the ``ws_root``-parameterized post-run helpers for the study-run
engine (study-run engine extraction, phase E3) — the side-effect stage that
fires AFTER a study run's simulation subprocess completes: render the study's
declared visualizations, invoke its ``post_run_scripts``, and run its
``analyses`` steps over the parquet output. The legacy server.py module-level
helpers (``_render_study_visualizations``, ``_run_post_run_scripts``,
``_run_study_analyses``) now delegate to the corresponding functions here via
thin name-shims, keeping their existing call-sites + test imports intact and the
live path byte-identical.

None of these functions import ``server``. ``render_study_visualizations`` takes
the workspace root as an explicit ``ws_root`` argument (replacing the server
``WORKSPACE`` global and replicating ``_ws_add_to_sys_path`` inline) so the
module stays importable standalone and flip-ready. The simulation itself is NOT
run here — these stages only render/shell-out over an already-completed run.

Functions
---------
render_study_visualizations → render canonical + study-declared viz after a run
run_post_run_scripts        → invoke spec.post_run_scripts[] as subprocesses
run_study_analyses          → run spec.analyses[] over the run's parquet output
build_analysis_options      → map spec.analyses entries → v2ecoli analysis_options
purge_stale_viz             → delete stale viz HTML older than the latest run
latest_run_timestamp        → most-recent run wall-clock time from runs_meta
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import yaml


def latest_run_timestamp(runs_db: Path) -> float | None:
    """Return the most recent run's wall-clock time from ``runs_meta``.

    Prefers ``completed_at`` (when the run finished, hence when its viz
    could have been rendered), falling back to ``started_at``. Returns
    ``None`` if the table is unreadable or empty.

    Why not ``runs.db`` file mtime: the db is opened in WAL mode, and any
    *read* connection (including the one render_visualizations uses to draw
    the charts) can trigger a checkpoint that bumps the file mtime AFTER the
    viz HTML was written. That made freshly-rendered viz look "older" than
    the db and get silently dropped. The recorded run timestamps are real
    data and immune to that race.
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


def run_post_run_scripts(spec: dict, ws_root: Path) -> tuple[list[str], list[dict]]:
    """Invoke each ``spec.post_run_scripts[]`` entry as a subprocess.

    Each entry: ``{path: <rel-to-ws>, args: [...], timeout_s: 1800}``.
    Scripts run with cwd=ws_root using the same Python interpreter as the
    dashboard. Stdout/stderr are captured but discarded unless the script
    fails (script's own viz writes go straight to disk). Returns
    ``(written_files, errors)`` — written_files lists the HTML files under
    studies/<slug>/viz/ that were created or changed by this batch (for
    response surfacing), sorted for determinism.
    """
    entries = spec.get("post_run_scripts") or []
    if not entries:
        return [], []
    import sys as _sys
    import subprocess as _subprocess

    written: list[str] = []
    errors: list[dict] = []

    def _viz_html_snapshot() -> dict:
        """Map of study-viz HTML path -> (mtime, size), for before/after diffing."""
        snap: dict = {}
        studies = ws_root / "studies"
        if not studies.is_dir():
            return snap
        for study_dir in studies.iterdir():
            viz_dir = study_dir / "viz"
            if not viz_dir.is_dir():
                continue
            for html in viz_dir.glob("*.html"):
                try:
                    st = html.stat()
                    snap[html] = (st.st_mtime, st.st_size)
                except OSError:
                    continue
        return snap

    # Diff a before/after snapshot rather than comparing st_mtime against a
    # wall-clock reading. The old approach (`st_mtime >= time.time()` captured
    # at start) compares two different clocks: filesystem mtime granularity can
    # be coarser than time.time(), so a file written AFTER t_start can carry an
    # mtime BELOW it and be silently missed. That made this a race — it happened
    # to pass serially on CI and fail under parallel scheduling — and in
    # production it means post-run viz files sometimes never get surfaced.
    before = _viz_html_snapshot()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rel_path = entry.get("path")
        if not rel_path:
            continue
        script_path = ws_root / rel_path
        if not script_path.is_file():
            errors.append({"script": rel_path, "error": "script not found"})
            continue
        args = [str(a) for a in (entry.get("args") or [])]
        timeout_s = int(entry.get("timeout_s") or 1800)
        try:
            result = _subprocess.run(
                [_sys.executable, str(script_path), *args],
                cwd=str(ws_root), timeout=timeout_s,
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                # surface stderr tail for debugging; full stdout/stderr stay
                # in the script's own logs if it wrote any.
                tail = (result.stderr or result.stdout or "")[-500:]
                errors.append({
                    "script": rel_path, "args": args,
                    "returncode": result.returncode, "stderr_tail": tail,
                })
        except _subprocess.TimeoutExpired:
            errors.append({"script": rel_path, "error": f"timed out after {timeout_s}s"})
        except Exception as e:  # noqa: BLE001 — keep other scripts running
            errors.append({"script": rel_path, "error": f"{type(e).__name__}: {e}"})
    # Files created, or whose (mtime, size) changed, during this batch.
    for html, stamp in _viz_html_snapshot().items():
        if before.get(html) != stamp:
            written.append(str(html.relative_to(ws_root)))
    written.sort()          # deterministic order (iterdir/glob are not)
    return written, errors


def build_analysis_options(entries: list[dict]) -> tuple[dict, list[dict]]:
    """Translate ``spec.analyses`` entries into v2ecoli ``analysis_options``.

    Looks up each entry's ``name`` in ``v2ecoli.workflow.analysis.ANALYSIS_REGISTRY``
    to discover its ``scale``, then groups it into
    ``{scale: {name: params}}``.

    Returns ``(analysis_options, errors)`` where ``errors`` lists dicts for
    unknown analysis names.  Importable as a pure helper so it is unit-testable
    without a workspace.
    """
    try:
        from v2ecoli.workflow.analysis import ANALYSIS_REGISTRY  # type: ignore[import]
    except ImportError:
        return {}, [{"error": "v2ecoli not installed; cannot resolve analysis scales"}]

    analysis_options: dict[str, dict] = {}
    errors: list[dict] = []
    for entry in entries:
        name = entry.get("name")
        if not name:
            continue
        step_cls = ANALYSIS_REGISTRY.get(name)
        if step_cls is None:
            errors.append({"analysis": name, "error": f"unknown analysis {name!r} (not in ANALYSIS_REGISTRY)"})
            continue
        scale = getattr(step_cls, "scale", None)
        if not scale:
            errors.append({"analysis": name, "error": f"analysis {name!r} has no scale attribute"})
            continue
        analysis_options.setdefault(scale, {})[name] = entry.get("params") or {}
    return analysis_options, errors


def run_study_analyses(study_dir: Path, spec: dict, run_id: str,
                       ws_root: Path) -> tuple[list[str], list[dict]]:
    """Run the study's configured ``analyses:`` steps over the run's parquet output.

    Mirrors ``_run_post_run_scripts`` in structure.  Collects the written
    ``ptools/*.tsv``, ``viz/*.html``, and ``analysis.json`` into
    ``written_files``, and per-analysis errors into ``errors``.
    Returns ``(written_files, errors)`` — never raises.

    Requires:
      - v2ecoli installed in the same venv (guarded import).
      - A parquet emitter run under ``study_dir/parquet-runs/``.
      - A sim_data pickle somewhere in the workspace (searched under ws_root).
        Analyses that don't need sim_data will still run even if none is found.
    """
    try:
        entries = list(spec.get("analyses") or [])
        if not entries:
            return [], []

        # 1. Locate the most-recent parquet sweep dir (workbench-side FS).
        from vivarium_workbench.lib.study_charts import _latest_parquet_for_study
        hive_root = _latest_parquet_for_study(study_dir)
        if hive_root is None:
            return [], [{"error": "no parquet run found under study dir; analyses need parquet emitter output"}]
        # run_analyses globs history parquet under sweep_dir; the hive root is
        # <exp>/history so its parent <exp> is the sweep_dir.
        sweep_dir = hive_root.parent

        # 2. Resolve workspace sim_data (optional — analyses that don't need it still run).
        sim_data_path: str | None = None
        for pat in ("out/**/simData*.cPickle", "out/**/sim_data*.cPickle",
                    "simData*.cPickle", "sim_data*.cPickle",
                    "**/simData*.cPickle", "**/sim_data*.cPickle"):
            import glob as _glob
            hits = _glob.glob(str(ws_root / pat), recursive=True)
            if hits:
                sim_data_path = hits[0]
                break

        # 3. The v2ecoli scale lookup (ANALYSIS_REGISTRY) + run_analyses run in the
        # env worker (importing/executing v2ecoli is workspace Python, kept out of
        # the HTTP process). Soft-degrade: a post-run analysis pass that can't run
        # returns an error note, never crashes the run handler.
        from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
        from vivarium_workbench.lib.env_worker_pool import get_pool
        try:
            res = get_pool().call(ws_root, "run_study_analyses", {
                "entries": entries, "sweep_dir": str(sweep_dir),
                "sim_data_path": sim_data_path})
        except EnvWorkerUnavailable:
            return [], [{"error": "environment worker unavailable; analyses not run"}]
        return list(res.get("written") or []), list(res.get("errors") or [])

    except Exception as exc:  # noqa: BLE001 — never crash the run handler
        import traceback
        return [], [{"error": f"_run_study_analyses failed: {type(exc).__name__}: {exc}",
                     "traceback": traceback.format_exc()}]


def render_study_visualizations(ws_root, study_dir, spec, spec_id):
    """Render canonical + Study-declared visualizations after a completed run.

    Merges the composite's ``@composite_generator(visualizations=...)``
    defaults (from ``pbg_superpowers._REGISTRY``) with
    ``spec.visualizations`` (Study entries win on name collision), then
    delegates to ``vivarium_workbench.lib.investigations.render_visualizations``
    to render against ``study_dir/runs.db``.

    Returns ``(viz_files, viz_errors)`` — viz_files lists paths relative
    to ``study_dir`` of HTML files written; viz_errors is a list of
    ``{error: <msg>}`` for global failures (per-viz failures are handled
    inside ``render_visualizations`` and surface as error-stub HTML).
    """
    from vivarium_workbench.lib.investigations import render_visualizations

    # The composite generator's default visualizations come from the env worker's
    # generator discovery (composite_lookup), not an in-process _REGISTRY import.
    from vivarium_workbench.lib.composite_lookup import _discover_generators_via_worker
    gen_entry = _discover_generators_via_worker(ws_root).get(spec_id)
    default_viz = list((gen_entry or {}).get("visualizations") or [])
    study_viz = list(spec.get("visualizations") or [])
    by_name: dict[str, dict] = {}
    for v in default_viz + study_viz:
        if isinstance(v, dict) and v.get("name"):
            by_name[v["name"]] = v
    merged = list(by_name.values())
    if not merged:
        return [], []

    # mem3dg-readdy friction #29: study.yaml needed `address:` (caught by
    # the report linter in pbg-superpowers / friction #26), but the same
    # gap existed on the @composite_generator(visualizations=[...]) side
    # and silently won on name collisions. Single source of truth fix:
    # default any unaddressed entry from workspace.yaml.visualizations[].class
    # by name, before render_visualizations gets a chance to KeyError.
    name_to_class: dict[str, str] = {}
    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
        for ws_viz in ws_data.get("visualizations", []) or []:
            if isinstance(ws_viz, dict) and ws_viz.get("name") and ws_viz.get("class"):
                name_to_class[ws_viz["name"]] = ws_viz["class"]
    except Exception:  # noqa: BLE001 — defaulting is best-effort
        pass
    for v in merged:
        if not v.get("address"):
            cls = name_to_class.get(v.get("name", ""))
            if cls:
                v["address"] = f"local:{cls}"

    effective_spec = dict(spec)
    effective_spec["visualizations"] = merged

    # The core build + viz-class registration + the per-viz Composite.run all
    # happen in the env worker (live viz classes + core, kept out of the HTTP
    # process). `viz_render_hooks` gives render_visualizations the two seams it
    # needs: an inputs-by-class map (so build_viz_composite needs no live class)
    # and a worker-backed build_and_run.
    from vivarium_workbench.lib.viz_render import viz_render_hooks
    inputs_by_class, build_and_run = viz_render_hooks(ws_root)

    try:
        paths = render_visualizations(
            effective_spec,
            study_dir,
            spec.get("name", ""),
            inputs_by_class=inputs_by_class,
            build_and_run=build_and_run,
        )
        written = [str(Path(p).relative_to(study_dir)) for p in paths]
        # Auto-purge stale viz: after rendering, delete any *.html in
        # studies/<slug>/viz/ whose mtime is older than the latest run's
        # started_at AND not in the just-written set. Keeps the report
        # showing only current-run output without manual cleanup.
        # `comparative_*` viz are excluded — those are owned by the
        # investigation-end hook (_render_investigation_comparative_visualisations)
        # which fires on a different schedule; purging them on a per-study
        # run would delete legitimately-current cross-run overlays.
        purge_stale_viz(study_dir, written)
        return written, []
    except Exception as e:  # noqa: BLE001
        return [], [{"error": f"render_visualizations failed: "
                     f"{type(e).__name__}: {e}"}]


def purge_stale_viz(study_dir: Path, just_written: list[str]) -> None:
    """Delete *.html in study_dir/viz/ whose mtime is older than the
    latest run's started_at AND not in the just-written set AND not
    a comparative_ viz (those are owned by a separate dispatch).

    No-op on any error — viz cleanup is best-effort, not load-bearing.
    """
    try:
        viz_dir = study_dir / "viz"
        runs_db = study_dir / "runs.db"
        if not viz_dir.is_dir() or not runs_db.is_file():
            return
        cutoff = latest_run_timestamp(runs_db)
        if cutoff is None:
            return
        kept_names = {Path(p).name for p in just_written}
        for html in viz_dir.glob("*.html"):
            if html.name in kept_names:
                continue
            if html.name.startswith("comparative_"):
                continue
            try:
                if html.stat().st_mtime < cutoff:
                    html.unlink()
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass
