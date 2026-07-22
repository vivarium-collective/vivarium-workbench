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

from vivarium_workbench.lib import composite_runs as cr

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


def _resolve_state(req: RunRequest) -> tuple[dict, dict | None]:
    """Resolve the composite state — generator entry first, then file spec.

    Returns ``(state, spec)`` where ``spec`` is the parsed static-spec dict for
    the file-based branch (so the caller can read its ``emitters:`` default-emitter
    declaration via the convention) or ``None`` for a generator (generators carry
    their own emitter resolution internally; the convention isn't applied to them
    here). Mirrors the resolution the old _post_composite_test_run handler did.
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
                return doc["state"], None
            return doc, None
    except ImportError:
        pass

    # File-based spec branch.
    from vivarium_workbench.lib.composite_lookup import (
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
    state = substitute_parameters(spec.get("state") or {},
                                  spec.get("parameters") or {},
                                  req.overrides)
    return state, spec


def _generator_entry(spec_id: str):
    """The registered GeneratorEntry for ``spec_id``, or ``None``.

    Mirrors the generator resolution every other ``spec_id`` lookup uses
    (``_REGISTRY.get(spec_id)`` after ``discover_generators()``). Never raises
    when pbg_superpowers is unavailable or the spec_id isn't a registered
    generator.
    """
    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, discover_generators,
        )
    except ImportError:
        return None
    if not _REGISTRY:
        discover_generators()
    return _REGISTRY.get(spec_id)


def _generator_emitter_defaults(spec_id: str) -> list:
    """Declared default emitter(s) for a GENERATOR composite, or ``[]``.

    Reads the decorator's ``emitters=[...]`` via ``emitter_defaults(entry)``.
    Returns ``[]`` (never raises) when pbg_superpowers is unavailable or the
    spec_id is not a registered generator, so callers can treat it like the
    static-spec ``emitter_defaults(spec)`` path.
    """
    entry = _generator_entry(spec_id)
    if entry is None:
        return []
    from pbg_superpowers.composite_generator import emitter_defaults
    return emitter_defaults(entry)


def _emitter_decl_source(spec: dict | None, spec_id: str):
    """The object carrying the composite's emitter declaration.

    ``emitter_defaults`` and ``install_default_emitters`` both accept EITHER a
    static-spec dict or a ``GeneratorEntry``. ``_resolve_state`` returns
    ``spec=None`` for a generator, so passing ``spec`` straight through to
    ``install_default_emitters`` makes it a no-op for exactly the composites
    whose declaration we just honored in ``_select_emitter_name`` — the
    selection reads the registry entry while the injection reads ``spec``.
    That mismatch means no ParquetEmitter is installed AND, because the run no
    longer takes the xarray branch, the ``.zarr`` store that used to be written
    is gone too: a silent regression to no durable output at all, still
    reported as ``output_kind="parquet"``.

    Resolving the declaration source once, here, keeps the two in lockstep.
    """
    return spec if spec is not None else _generator_entry(spec_id)


def _select_emitter_name(*, spec: dict | None, spec_id: str, db_file: str) -> str:
    """Pick the emitter NAME for a run, honoring the composite's DECLARED sink.

    R1: honor the declared emitter for BOTH static specs and generators. For a
    static spec (``spec is not None``) the declaration comes from the spec's
    ``emitters:`` key; for a generator (``spec is None``) it comes from the
    registered entry's ``emitters=[...]`` decorator. Any declaration routes to
    ``"parquet"`` (the composite carries its own ParquetEmitter step — the
    parquet branch persists it in one place). When nothing is declared, fall
    back to the workspace ``default_emitter`` — unchanged from before.

    Pure and side-effect-free so R1 is unit-testable without a full ``execute``.
    """
    from vivarium_workbench.lib import emitters
    from pbg_superpowers.composite_generator import emitter_defaults
    declared = (emitter_defaults(spec) if spec is not None
                else _generator_emitter_defaults(spec_id))
    if declared:
        return "parquet"
    return emitters.default_emitter(spec, Path(db_file))


def _record_run_emitter(workspace, run_id: str, name: str) -> None:
    """Append a JSONL run event recording the resolved emitter kind (R3).

    Folds (by ``run_id``) into the run's Sims-DB record so the Emitter column
    shows the sink that actually persisted the run. Best-effort — a logging
    failure must never fail the run.
    """
    try:
        from vivarium_workbench.lib import run_log
        run_log.append_run_event(workspace, {"run_id": run_id, "emitter": name})
    except Exception:
        traceback.print_exc()


class _RunTimeout(Exception):
    """Raised by the progress callback when a run exceeds ``MAX_RUNTIME_SEC``.

    Carries the step at which the limit tripped so ``execute`` can record an
    accurate ``n_steps`` on the failed run. Raising from the progress callback
    lets the broker own the run loop while ``execute`` keeps the self-terminate
    semantics it had before the broker existed.
    """


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
                run_dir=run_dir,
            )
            for name, html in canonical.items():
                viz_html.setdefault(name, html)
        except Exception:
            traceback.print_exc()

    # 3. Default figure when a composite declares no visualizations.
    if not viz_html and db_file and run_id and core is not None:
        try:
            for k, html in _render_default_viz(
                    db_file=db_file, run_id=run_id, core=core).items():
                viz_html.setdefault(k, html)
        except Exception:
            traceback.print_exc()

    try:
        (run_dir / "viz.json").write_text(json.dumps(viz_html, default=str), encoding="utf-8")
    except Exception:
        traceback.print_exc()


def _resolve_sim_data_path(run_dir) -> str:
    """Locate a ParCa ``parca_state.pkl.gz`` in the run's workspace for the
    native-analysis views to hydrate sim_data from. ``run_dir`` is
    ``<ws>/.pbg/runs/<run_id>``; returns "" when none is found (the view then
    renders analyses that don't need sim_data and notes the rest)."""
    if run_dir is None:
        return ""
    try:
        ws = Path(run_dir).parents[2]
    except IndexError:
        return ""
    for rel in ("out/sim_data_full/parca_state.pkl.gz",
                "out/sim_data-showcase/parca_state.pkl.gz",
                "models/parca/parca_state.pkl.gz"):
        cand = ws / rel
        if cand.is_file():
            return str(cand)
    return ""


def _render_canonical_viz(*, spec_id: str, db_file: str, run_id: str, core,
                          run_dir=None) -> dict:
    """Render @composite_generator(visualizations=...) entries for this run.

    Returns ``{viz_name: html_string}``. Per-viz errors surface as an
    error-stub HTML string (mirroring ``render_visualizations``); the
    function itself never raises.
    """
    try:
        from pbg_superpowers.composite_generator import _REGISTRY, discover_generators
        from vivarium_workbench.lib.investigations import (
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

    all_obs = _numeric_observables(gathered_filtered)
    out: dict = {}
    for viz_spec in canonical:
        if not isinstance(viz_spec, dict):
            continue
        name = viz_spec.get("name") \
            or viz_spec.get("address", "?").rsplit(":", 1)[-1]
        # Inject the per-run db path (TimeSeriesFromObservables reads runs.db
        # directly, self-contained) and resolve observables for time-series
        # specs: an empty list means "all numeric observables"; an
        # `observable_match` substring selects a subset (e.g. every mass field).
        spec = dict(viz_spec)
        cfg = dict(spec.get("config") or {})
        cfg.setdefault("runs_db_path", db_file)
        cfg.setdefault("run_id", run_id)  # scope the figure to THIS run
        if spec.get("address", "").endswith("ParquetAnalysisView"):
            # Point the native-analysis adapter at THIS run's on-disk parquet
            # sweep + a ParCa sim_data pickle so the analysis can hydrate.
            if run_dir is not None:
                cfg.setdefault("sweep_dir",
                               str(Path(run_dir) / "parquet" / run_id))
            cfg.setdefault("sim_data_path", _resolve_sim_data_path(run_dir))
        if spec.get("address", "").endswith("TimeSeriesFromObservables"):
            match = cfg.pop("observable_match", None)
            if not cfg.get("observables"):
                cfg["observables"] = (
                    [o for o in all_obs if match in o] if match else list(all_obs)
                )
        spec["config"] = cfg
        try:
            doc = build_viz_composite(spec, gathered_filtered, registry)
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


def _numeric_observables(gathered_filtered: dict) -> list[str]:
    """Derive observable names from gathered emitter output.

    Returns keys that appear in at least one run's observables dict,
    are not "time", and have at least one numeric (int or float, but NOT
    bool) value. Booleans are excluded because ``bool`` is a subclass of
    ``int`` and a boolean observable is not a meaningful time series.
    The returned list is sorted for determinism.
    """
    numeric_keys: set[str] = set()
    for runs in (gathered_filtered.get("by_sim") or {}).values():
        for run in runs:
            for key, vals in (run.get("observables") or {}).items():
                if key == "time":
                    continue
                if any(isinstance(v, (int, float)) and not isinstance(v, bool)
                       for v in (vals or [])):
                    numeric_keys.add(key)
    return sorted(numeric_keys)


def _render_default_viz(*, db_file: str, run_id: str, core) -> dict:
    """A default 'observables over time' figure for composites that declare
    no visualizations.

    Uses TimeSeriesFromObservables, which reads runs.db directly and plots
    every numeric leaf found in this run's emitter output. The observable
    names are derived from the gathered output's non-"time" numeric keys.
    Best-effort; returns {} on any failure.
    """
    try:
        from vivarium_workbench.lib.investigations import (
            build_viz_composite, gather_emitter_outputs,
        )
        from pbg_superpowers.visualizations import (
            TimeSeriesPlot, TimeSeriesFromObservables,
        )
        from process_bigraph import Composite
    except ImportError:
        return {}

    # Register viz classes the same way _render_canonical_viz does so
    # `local:<ClassName>` addresses resolve through core.link_registry.
    registry = dict(core.link_registry)
    try:
        from pbg_superpowers.visualizations import (
            ParamVsObservable, Distribution, PhaseSpace, Heatmap,
        )
        for cls in (
            TimeSeriesPlot, TimeSeriesFromObservables,
            ParamVsObservable, Distribution, PhaseSpace, Heatmap,
        ):
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

    gathered = gather_emitter_outputs(Path(db_file))
    by_sim_filtered: dict = {}
    for sim_name, runs in (gathered.get("by_sim") or {}).items():
        keep = [r for r in runs if r.get("run_id") == run_id]
        if keep:
            by_sim_filtered[sim_name] = keep
    if not by_sim_filtered:
        return {}
    gathered_filtered = {
        "schemas": gathered.get("schemas") or {},
        "by_sim": by_sim_filtered,
    }

    # Derive numeric observable names from the gathered output, then hand
    # them plus the db path to TimeSeriesFromObservables which reads
    # runs.db itself and plots all requested series.
    obs_names = _numeric_observables(gathered_filtered)
    if not obs_names:
        return {}

    viz_spec = {
        "name": "observables_over_time",
        "address": "local:TimeSeriesFromObservables",
        "config": {
            "title": "Observables over time",
            "observables": obs_names,
            "runs_db_path": db_file,
            "run_id": run_id,
        },
    }
    try:
        doc = build_viz_composite(viz_spec, gathered_filtered, registry)
        viz_composite = Composite({"state": doc}, core=core)
        viz_composite.run(1)
        html = viz_composite.state.get("output_store")
        if isinstance(html, dict):
            html = html.get("value") or html.get("_value") or ""
        return {"observables_over_time": html} if isinstance(html, str) and html else {}
    except Exception:
        traceback.print_exc()
        return {}


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
            state, spec = _resolve_state(req)
        except FileNotFoundError as e:
            # Most common: the ParCa cache (out/cache/initial_state.json) is
            # missing. Fail fast with a legible message rather than a crash.
            msg = f"composite build failed: {e}"
            print(msg, flush=True)
            _write_log(req, msg)
            cr.complete_metadata(conn, run_id=req.run_id, n_steps=0,
                                 status="failed", workspace=req.workspace)
            return 1

        # build_core lives in the workspace's own package (e.g.
        # pbg_ws_increase_demo.core). Import it dynamically by package name.
        core_mod = __import__(f"{req.pkg}.core", fromlist=["build_core"])
        core = core_mod.build_core()

        # Uniform write path: pick the emitter NAME, then let the broker inject
        # it as a Step, build the Composite, run, and flush. A static spec that
        # declares an `emitters:` default sink still routes to the parquet
        # convention (install_default_emitters); otherwise the workspace's
        # `runtime.default_emitter` (default "sqlite", Task 6 flips it) selects
        # the sink. The broker's sqlite branch reuses the same
        # inject_emitter_for_paths + inject_sqlite_emitter + per-tick run(1)
        # loop this function used inline, so default runs are byte-identical.
        from vivarium_workbench.lib import emitters
        name = _select_emitter_name(
            spec=spec, spec_id=req.spec_id, db_file=req.db_file)
        # For a generator `spec` is None; hand the parquet branch the registry
        # entry instead so install_default_emitters sees the same declaration
        # _select_emitter_name just routed on (see _emitter_decl_source).
        decl_source = _emitter_decl_source(spec, req.spec_id)
        # R3: record the resolved emitter kind so the Sims DB Emitter column
        # reflects the sink that actually persisted this run.
        _record_run_emitter(req.workspace, req.run_id, name)
        emit_paths = _emit_paths_for(req, state)

        # The progress callback both heartbeats and enforces the max-runtime
        # self-terminate: raising _RunTimeout aborts the broker's run loop and
        # is caught below, preserving the prior failed-status behavior.
        started = time.monotonic()

        def _progress(step: int) -> None:
            cr.update_progress(conn, run_id=req.run_id, progress_step=step,
                               heartbeat_at=time.time())
            if time.monotonic() - started > MAX_RUNTIME_SEC:
                raise _RunTimeout(step)

        try:
            prov = emitters.run_with_emitter(
                name=name, state=state, run_id=req.run_id, emit_paths=emit_paths,
                out_dir=str(run_dir), core=core, steps=req.steps,
                db_file=req.db_file, progress_cb=_progress, spec=decl_source,
                also_sqlite_history=True)
        except _RunTimeout as exc:
            step = exc.args[0] if exc.args else req.steps
            msg = (f"run exceeded max runtime ({MAX_RUNTIME_SEC}s) — "
                   f"terminating at step {step}")
            print(msg, flush=True)
            _write_log(req, msg)
            cr.complete_metadata(conn, run_id=req.run_id, n_steps=step,
                                 status="failed", workspace=req.workspace)
            return 1

        composite = prov.get("composite")

        # Surface any broker warning (e.g. an xarray run whose buffer never
        # filled → empty-store fall-back to sqlite) into the run's log so the
        # changed output_kind is diagnosable, not a silent swallow.
        warning = prov.get("warning")
        if warning:
            print(warning, flush=True)
            _write_log(req, warning)

        _render_viz(
            composite, run_dir,
            spec_id=req.spec_id, db_file=req.db_file, run_id=req.run_id,
            core=core,
        )
        try:
            from vivarium_workbench.lib.composite_flush import run_flush
            run_flush(run_dir, req=req, spec_id=req.spec_id,
                      db_file=req.db_file, run_id=req.run_id, core=core)
        except Exception:
            traceback.print_exc()   # flush must never fail the run
        cr.complete_metadata(conn, run_id=req.run_id, n_steps=req.steps,
                             status="completed", workspace=req.workspace)
        print(f"run {req.run_id} completed: {req.steps} steps", flush=True)
        return 0
    except Exception:
        tb = traceback.format_exc()
        print(tb, flush=True)
        _write_log(req, tb)
        cr.complete_metadata(conn, run_id=req.run_id, n_steps=0, status="failed",
                             workspace=req.workspace)
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
