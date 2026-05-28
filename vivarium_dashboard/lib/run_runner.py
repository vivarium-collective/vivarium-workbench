"""Detached composite-run executor.

``execute(request_path)`` is the entry point the ``vivarium-dashboard
run-composite`` CLI calls in a detached process. It is pure: no HTTP, no
module globals — everything it needs comes from the run-request file. State
is loaded from that file, never from argv, which structurally eliminates the
``OSError: [Errno 7] Argument list too long`` failure mode.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from vivarium_dashboard.lib import composite_runs as cr

# A run exceeding this self-terminates with status='failed'. Matches the
# "tens of minutes" target from the design spec.
MAX_RUNTIME_SEC = 1800


@dataclass
class RunRequest:
    run_id: str
    spec_id: str
    pkg: str
    workspace: Path
    overrides: dict
    steps: int
    emit_paths: list
    db_file: str
    log_path: str

    @classmethod
    def from_file(cls, path: Path) -> "RunRequest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            run_id=data["run_id"],
            spec_id=data["spec_id"],
            pkg=data["pkg"],
            workspace=Path(data["workspace"]),
            overrides=data.get("overrides") or {},
            steps=int(data["steps"]),
            emit_paths=data.get("emit_paths") or [],
            db_file=data["db_file"],
            log_path=data["log_path"],
        )


def _resolve_state(req: RunRequest) -> dict:
    """Resolve the composite state — generator entry first, then file spec.

    Mirrors the resolution the old _post_composite_test_run handler did.
    Raises a clear error if neither path yields a state.
    """
    # Generator-kind branch.
    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, build_generator, discover_generators,
        )
        if not _REGISTRY:
            discover_generators()
        entry = _REGISTRY.get(req.spec_id)
        if entry is not None:
            doc = build_generator(entry, overrides=req.overrides)
            if isinstance(doc, dict) and isinstance(doc.get("state"), dict):
                return doc["state"]
            return doc
    except ImportError:
        pass

    # File-based spec branch.
    from vivarium_dashboard.lib.composite_lookup import (
        find_composite_path, substitute_parameters,
    )
    path = find_composite_path(req.workspace, req.pkg, req.spec_id)
    if path is None:
        raise FileNotFoundError(
            f"composite spec not found: {req.spec_id} "
            f"(not a registered generator, no spec file)"
        )
    text = path.read_text(encoding="utf-8")
    spec = json.loads(text) if path.suffix.lower() == ".json" else __import__(
        "yaml").safe_load(text)
    return substitute_parameters(spec.get("state") or {},
                                 spec.get("parameters") or {},
                                 req.overrides)


def _emit_paths_for(req: RunRequest, state: dict) -> list[str]:
    """Resolve which store paths the run should emit.

    The wiring-view paths the user hand-picked, or — when they picked none —
    every store in the composite. This makes "emit all" the Composite Explorer
    Run tab's default; an explicit selection always wins.
    """
    return req.emit_paths or cr.all_store_paths(state)


def _render_viz(composite, run_dir: Path, *,
                spec_id: str | None = None,
                db_file: str | None = None,
                run_id: str | None = None,
                core=None) -> None:
    """Render the run's visualizations to ``run_dir/viz.json``. Best-effort
    — never raises.

    Two sources contribute:
      1. Inline ``Visualization`` Step instances embedded in the running
         composite (the spatio-flux pattern).  ``render_results(composite)``
         picks these up directly from the live state tree.
      2. Canonical visualizations declared on the
         ``@composite_generator(visualizations=[...])`` decorator (the
         v2ecoli pattern).  These are metadata, not state, so they are not
         visible to ``render_results`` and must be materialized after the
         fact: read ``entry.visualizations`` from
         ``pbg_superpowers.composite_generator._REGISTRY``, build a small
         viz composite per entry against the just-completed run's emitter
         output, and capture its rendered HTML.

    Inline entries win on key collision (they're scoped to a concrete
    state-tree path; canonical entries are bare names).
    """
    viz_html: dict = {}

    # 1. Inline viz steps.
    try:
        from pbg_superpowers.visualization import render_results
        rendered = render_results(composite)
        for path_tuple, payload in rendered.items():
            key = ".".join(str(p) for p in path_tuple)
            viz_html[key] = payload
    except Exception:
        traceback.print_exc()

    # 2. Canonical viz from the @composite_generator decorator.
    if spec_id and db_file and run_id and core is not None:
        try:
            canonical = _render_canonical_viz(
                spec_id=spec_id, db_file=db_file, run_id=run_id, core=core,
            )
            for name, html in canonical.items():
                viz_html.setdefault(name, html)
        except Exception:
            traceback.print_exc()

    try:
        (run_dir / "viz.json").write_text(json.dumps(viz_html, default=str))
    except Exception:
        traceback.print_exc()


def _render_canonical_viz(*, spec_id: str, db_file: str, run_id: str, core) -> dict:
    """Render @composite_generator(visualizations=...) entries for this run.

    Returns ``{viz_name: html_string}``. Per-viz errors surface as an
    error-stub HTML string (mirroring ``render_visualizations``); the
    function itself never raises.
    """
    try:
        from pbg_superpowers.composite_generator import _REGISTRY, discover_generators
        from vivarium_dashboard.lib.investigations import (
            build_viz_composite, gather_emitter_outputs,
        )
    except ImportError:
        return {}

    if not _REGISTRY:
        discover_generators()
    entry = _REGISTRY.get(spec_id)
    if entry is None:
        return {}
    canonical = list(getattr(entry, "visualizations", []) or [])
    if not canonical:
        return {}

    # Build the Visualization class registry the same way
    # _render_study_visualizations does, so `local:<ClassName>`
    # addresses resolve through core.link_registry.
    registry = dict(core.link_registry)
    try:
        from pbg_superpowers.visualizations import (
            TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap,
        )
        for cls in (TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap):
            try:
                core.register_link(cls.__name__, cls)
                registry[cls.__name__] = cls
            except Exception:
                pass
    except ImportError:
        pass

    try:
        from pbg_superpowers.visualization import Visualization
        def _walk(cls):
            for sub in cls.__subclasses__():
                yield sub
                yield from _walk(sub)
        for sub in _walk(Visualization):
            if sub.__name__ in registry:
                continue
            try:
                core.register_link(sub.__name__, sub)
                registry[sub.__name__] = sub
            except Exception:
                pass
    except Exception:
        pass

    # Pull emitter output for this run only — the workspace-level
    # composite-runs.db can hold many CE runs and we don't want the viz to
    # pick up trajectories from a different one.
    gathered = gather_emitter_outputs(Path(db_file))
    by_sim_filtered: dict = {}
    for sim_name, runs in (gathered.get("by_sim") or {}).items():
        keep = [r for r in runs if r.get("run_id") == run_id]
        if keep:
            by_sim_filtered[sim_name] = keep
    gathered_filtered = {
        "schemas": gathered.get("schemas") or {},
        "by_sim": by_sim_filtered,
    }

    from process_bigraph import Composite

    out: dict = {}
    for viz_spec in canonical:
        if not isinstance(viz_spec, dict):
            continue
        name = viz_spec.get("name") \
            or viz_spec.get("address", "?").rsplit(":", 1)[-1]
        try:
            doc = build_viz_composite(viz_spec, gathered_filtered, registry)
            viz_composite = Composite({"state": doc}, core=core)
            viz_composite.run(1)
            state = viz_composite.state
            html = state.get("output_store")
            if isinstance(html, dict):
                html = html.get("value") or html.get("_value") or ""
            if isinstance(html, str) and html:
                out[name] = html
        except Exception as e:  # noqa: BLE001
            out[name] = (
                f'<p style="color:#991b1b">Failed to render '
                f'<code>{name}</code>: '
                f'<code>{type(e).__name__}: {e}</code></p>'
            )
    return out


def execute(request_path: Path) -> int:
    """Run one composite to completion. Returns 0 on success, 1 on failure.

    All progress and results are written to the shared SQLite DB; stdout/stderr
    (captured by the spawning process into run.log) carries diagnostics.
    """
    request_path = Path(request_path)
    req = RunRequest.from_file(request_path)
    run_dir = request_path.parent

    if str(req.workspace) not in sys.path:
        sys.path.insert(0, str(req.workspace))

    conn = cr.connect(req.db_file)
    try:
        try:
            state = _resolve_state(req)
        except FileNotFoundError as e:
            # Most common: the ParCa cache (out/cache/initial_state.json) is
            # missing. Fail fast with a legible message rather than a crash.
            msg = f"composite build failed: {e}"
            print(msg, flush=True)
            _write_log(req, msg)
            cr.complete_metadata(conn, run_id=req.run_id, n_steps=0,
                                 status="failed")
            return 1

        emit_paths = _emit_paths_for(req, state)
        if emit_paths:
            state = cr.inject_emitter_for_paths(state, emit_paths)
        state = cr.inject_sqlite_emitter(state, run_id=req.run_id,
                                         db_file=req.db_file)

        # build_core lives in the workspace's own package (e.g.
        # pbg_ws_increase_demo.core). Import it dynamically by package name.
        core_mod = __import__(f"{req.pkg}.core", fromlist=["build_core"])
        from process_bigraph import Composite
        from process_bigraph.emitter import SQLiteEmitter

        core = core_mod.build_core()
        core.register_link("SQLiteEmitter", SQLiteEmitter)
        composite = Composite({"state": state}, core=core)

        started = time.monotonic()
        for step in range(1, req.steps + 1):
            composite.run(1)
            cr.update_progress(conn, run_id=req.run_id, progress_step=step,
                               heartbeat_at=time.time())
            if time.monotonic() - started > MAX_RUNTIME_SEC:
                msg = (f"run exceeded max runtime ({MAX_RUNTIME_SEC}s) — "
                       f"terminating at step {step}")
                print(msg, flush=True)
                _write_log(req, msg)
                cr.complete_metadata(conn, run_id=req.run_id, n_steps=step,
                                     status="failed")
                return 1

        _render_viz(
            composite, run_dir,
            spec_id=req.spec_id, db_file=req.db_file, run_id=req.run_id,
            core=core,
        )
        cr.complete_metadata(conn, run_id=req.run_id, n_steps=req.steps,
                             status="completed")
        print(f"run {req.run_id} completed: {req.steps} steps", flush=True)
        return 0
    except Exception:
        tb = traceback.format_exc()
        print(tb, flush=True)
        _write_log(req, tb)
        cr.complete_metadata(conn, run_id=req.run_id, n_steps=0, status="failed")
        return 1
    finally:
        conn.close()


def _write_log(req: RunRequest, text: str) -> None:
    """Append diagnostic text to the run's log file. Best-effort.

    The spawning process normally redirects stdout/stderr into run.log, but
    execute() also writes failure diagnostics here directly so the log is
    populated even when called in-process (e.g. by tests).
    """
    try:
        log_full = req.workspace / req.log_path
        log_full.parent.mkdir(parents=True, exist_ok=True)
        with open(log_full, "a") as fh:
            fh.write(text + "\n")
    except Exception:
        pass
