"""Detached composite-run executor.

``execute(request_path)`` is the entry point the ``vivarium-dashboard
run-composite`` CLI calls in a detached process. It is pure: no HTTP, no
module globals — everything it needs comes from the run-request file. State
is loaded from that file, never from argv, which structurally eliminates the
``OSError: [Errno 7] Argument list too long`` failure mode.
"""
from __future__ import annotations

import json
import os
import sys
import tarfile
import tempfile
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
    steps: float   # a run length in composite time units; fractional for temporal
    emit_paths: list
    db_file: str
    log_path: str
    target: str = "local"
    # Loom save-point fork: a full bigraph STATE captured at some frame of a
    # prior run, overlaid onto the freshly-built composite so this run STARTS
    # from that state (branch-the-timeline / "rerun from here"). Empty = a
    # normal run from the generator's initial state. Overlaid store-wise in
    # `_apply_seed_state` (preserves each store's `_type`, replaces contents).
    seed_state: dict = None  # type: ignore[assignment]
    # reproducible-rerun-spine Task 3 / G4, Step 6: the ORIGINAL run_id this
    # run reproduces, when it was launched as a rerun. Optional/best-effort —
    # `.get()` so a request file written by a producer that doesn't set it
    # (every current producer, as of this task — see result_fingerprint
    # report) simply parses as None, not a KeyError. When present, execute()'s
    # completion tail runs lib.rerun.verify_reproduction(reran_from, run_id).
    reran_from: "str | None" = None
    # Config declaration surface (composite-auto-results Task 6): the
    # composite-run body's config-declared ``analyses``/``visualizations``
    # block, written into request.json by composite_test_run_views. Read by
    # composite_flush._dispatch_analyses/_dispatch_visualizations
    # (``getattr(req, "declared_results", None) or {}``) and merged with the
    # composite's own defaults via ephemeral_study.merge_declarations — config
    # wins on name collision. Empty/absent = a no-op merge (composite defaults
    # flow through unchanged), never a crash.
    declared_results: dict = None  # type: ignore[assignment]

    @classmethod
    def from_file(cls, path: Path) -> "RunRequest":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            run_id=data["run_id"],
            spec_id=data["spec_id"],
            pkg=data["pkg"],
            workspace=Path(data["workspace"]),
            overrides=data.get("overrides") or {},
            # float: a temporal composite may run a fractional duration
            # (e.g. 3.5 time units). int-only truncated the fraction.
            steps=float(data["steps"]),
            emit_paths=data.get("emit_paths") or [],
            db_file=data["db_file"],
            log_path=data["log_path"],
            target=data.get("target") or "local",
            reran_from=data.get("reran_from"),
            seed_state=data.get("seed_state") or {},
            declared_results=data.get("declared_results") or {
                "analyses": [], "visualizations": [],
            },
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
        from process_bigraph.composite_generator import (
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


def _apply_seed_state(base: dict, seed: dict) -> dict:
    """Overlay a save-point ``seed`` state onto a freshly-built ``base`` state.

    Mirrors the loom's per-frame overlay (App.tsx): for each top-level store the
    seed carries, keep the base store's ``_type`` but take the seed's contents
    wholesale — so a ``tree[node]`` store's TOPOLOGY (its child nodes) is
    replaced by the saved frame's, not unioned with the base's. Stores/keys the
    seed doesn't mention (e.g. process nodes) are left as the generator built
    them. Time keys are dropped (a fork restarts the clock).
    """
    if not seed or not isinstance(seed, dict) or not isinstance(base, dict):
        return base
    out = dict(base)
    for k, v in seed.items():
        if k in ("time", "global_time"):
            continue
        b = base.get(k)
        if isinstance(b, dict) and isinstance(b.get("_type"), str) and isinstance(v, dict):
            out[k] = {"_type": b["_type"],
                      **{kk: vv for kk, vv in v.items() if kk != "_type"}}
        else:
            out[k] = v
    return out


def _generator_entry(spec_id: str):
    """The registered GeneratorEntry for ``spec_id``, or ``None``.

    Mirrors the generator resolution every other ``spec_id`` lookup uses
    (``_REGISTRY.get(spec_id)`` after ``discover_generators()``). Never raises
    when viva_superpowers is unavailable or the spec_id isn't a registered
    generator.
    """
    try:
        from process_bigraph.composite_generator import (
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
    Returns ``[]`` (never raises) when viva_superpowers is unavailable or the
    spec_id is not a registered generator, so callers can treat it like the
    static-spec ``emitter_defaults(spec)`` path.
    """
    entry = _generator_entry(spec_id)
    if entry is None:
        return []
    from process_bigraph.composite_generator import emitter_defaults
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


# Declared-emitter address class → the workbench emitter NAME its kind maps to.
_EMITTER_CLASS_TO_NAME = {
    "ParquetEmitter": "parquet",
    "XArrayEmitter": "xarray",
    "SQLiteEmitter": "sqlite",
    "RAMEmitter": "ram",
}


def _declared_emitter_name(decls: "list | None") -> "str | None":
    """The workbench emitter NAME for a composite's declared emitter(s), from the
    first decl's ``address`` class (``local:XArrayEmitter`` → ``"xarray"``), or
    ``None`` when nothing recognizable is declared."""
    for decl in decls or []:
        if not isinstance(decl, dict):
            continue
        cls = str(decl.get("address", "")).split(":")[-1].split(".")[-1]
        name = _EMITTER_CLASS_TO_NAME.get(cls)
        if name:
            return name
    return None


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
    from process_bigraph.composite_generator import emitter_defaults
    declared = (emitter_defaults(spec) if spec is not None
                else _generator_emitter_defaults(spec_id))
    if declared:
        # Honor the KIND the composite declared (xarray → streaming zarr, which
        # avoids the parquet path's unbounded RAM history for whole-cell runs;
        # sqlite; …), not a blanket "parquet". Falls back to parquet only when the
        # declared address isn't a recognized emitter class.
        return _declared_emitter_name(declared) or "parquet"
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


def _state_has_process(state) -> bool:
    """True if the built ``state`` contains any ``_type: process`` node — i.e. a
    temporal composite (whole-cell etc.), for which an all-stores emit fallback
    is the dangerous, memory-unbounded case worth warning about (#754)."""
    if isinstance(state, dict):
        if state.get("_type") == "process":
            return True
        return any(_state_has_process(v) for v in state.values())
    if isinstance(state, list):
        return any(_state_has_process(v) for v in state)
    return False


def _emit_paths_from_state(state: dict) -> list[str]:
    """Declared emit paths recovered REGISTRY-FREE from the built ``state``.

    Walks the whole state tree (the composite's emitter step can be nested — e.g.
    ecoli_baseline's lives at ``agents/0/emitter``) and returns the '/'-joined
    store paths each emitter node is wired to read. This is the robustness net:
    it recovers what the composite actually emits without consulting the
    generator registry, so a run still honors the declared observables even when
    registry resolution silently returns nothing (e.g. the pbg→viva module skew
    that made #754's fix a no-op). Internal/layout ports (``_``-prefixed, e.g.
    loom ``_layer_in_*``) are skipped.
    """
    out: list[str] = []

    def _walk(node) -> None:
        if isinstance(node, dict):
            addr = str(node.get("address", ""))
            if addr.split(":")[-1].endswith("Emitter"):
                wires = node.get("inputs")
                if isinstance(wires, dict):
                    for port, target in wires.items():
                        if str(port).startswith("_"):
                            continue
                        segs = target if isinstance(target, list) else [target]
                        norm = "/".join(str(s) for s in segs if s not in (None, ""))
                        if norm and norm not in out:
                            out.append(norm)
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(state)
    return out


def _resolve_emit_paths(req: RunRequest, state: dict, *,
                        spec: "dict | None" = None,
                        spec_id: "str | None" = None) -> "tuple[list[str], str]":
    """Return ``(emit_paths, source)`` for a run. Priority chain:

      1. ``explicit``   — the wiring-view paths the user hand-picked.
      2. ``declared``   — the composite's ``emitters=[...]`` declaration, read
         from the generator registry / static spec.
      3. ``state``      — the same declaration recovered REGISTRY-FREE by walking
         the built state's emitter node(s) (rebrand-proof; see
         :func:`_emit_paths_from_state`).
      4. ``all-stores`` — last resort: every store. Emitting a whole-cell state
         into the stacked RAM+SQLite+Parquet sinks each tick is the #754 memory
         blow-up, so this is the path the caller warns about for temporal runs.

    ``source`` is returned so ``execute`` can log which rung resolved (observability
    — a silent fall-through to all-stores was exactly the gap that reintroduced
    the OOM).
    """
    if req.emit_paths:
        return list(req.emit_paths), "explicit"
    from vivarium_workbench.lib.composite_resolve import declared_emit_paths
    if spec is not None:
        from process_bigraph.composite_generator import emitter_defaults
        decls = emitter_defaults(spec)
    else:
        decls = _generator_emitter_defaults(spec_id)
    declared = declared_emit_paths(decls)
    if declared:
        return declared, "declared"
    from_state = _emit_paths_from_state(state)
    if from_state:
        return from_state, "state"
    return cr.all_store_paths(state), "all-stores"


def _emit_paths_for(req: RunRequest, state: dict, *,
                    spec: "dict | None" = None,
                    spec_id: "str | None" = None) -> list[str]:
    """Resolve which store paths the run should emit (paths only; see
    :func:`_resolve_emit_paths` for the priority chain and the ``source`` label).

    Defaulting to the composite's DECLARED paths — recovered from the generator
    declaration OR, as a rebrand-proof fallback, from the built state's own
    emitter node — mirrors what a direct ``composite.run()`` emits and keeps a
    whole-cell run from deep-copying its ENTIRE state into the RAM+SQLite+Parquet
    sinks every tick (issue #754). Only a composite with no emitter at all falls
    through to emit-all. An explicit selection always wins.
    """
    return _resolve_emit_paths(req, state, spec=spec, spec_id=spec_id)[0]


# The generic default-viz fallback skips any history-row state blob larger than
# this when gathering observables, so a whole-cell composite (~16k-species bulk
# array + listeners per row) can't be json.loads()'d into an unbounded set and
# hang the render (issue #784, layer 3). A normal composite's per-step state is
# well under this; a whole-cell blob is many times larger.
_DEFAULT_VIZ_MAX_STATE_BYTES = 512 * 1024  # 512 KiB


def _safe_link_registry_dict(core) -> dict:
    """Materialize ``core.link_registry`` into a plain dict, skipping any entry
    whose lazy lookup raises.

    A foreign package that fails at generator-discovery import time can leave a
    dangling key in the lazy link registry; ``dict(core.link_registry)`` then
    raises ``KeyError`` when it materializes that key, crashing viz rendering
    (issue #784, layer 1 — a stray ``vEcoli``/``genecoli`` install in the venv).
    Degrade gracefully instead: a broken third-party registry entry must not take
    down a run's visualization tail.
    """
    registry = getattr(core, "link_registry", None)
    if registry is None:
        return {}
    try:
        keys = list(registry.keys())
    except Exception:
        try:
            return dict(registry)
        except Exception:
            return {}
    out: dict = {}
    for key in keys:
        try:
            out[key] = registry[key]
        except Exception:
            continue
    return out


def _spec_declares_canonical_viz(spec_id: str | None) -> bool:
    """True when the composite generator for ``spec_id`` declares one or more
    canonical visualizations.

    Used to keep a whole-cell composite (which always declares its panels) out of
    the generic ``_render_default_viz`` fallback even when canonical rendering
    fails for some reason — a canonical-viz *failure* leaves ``viz_html`` empty,
    which must NOT be mistaken for "declares no visualizations" (issue #784,
    layer 2). Best-effort; returns False when the registry can't be read.
    """
    if not spec_id:
        return False
    try:
        from process_bigraph.composite_generator import (
            _REGISTRY, discover_generators)
    except ImportError:
        return False
    if not _REGISTRY:
        try:
            discover_generators()
        except Exception:
            return False
    entry = _REGISTRY.get(spec_id)
    return bool(getattr(entry, "visualizations", None))


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
         ``viva_superpowers.composite_generator._REGISTRY``, build a small
         viz composite per entry against the just-completed run's emitter
         output, and capture its rendered HTML.

    Inline entries win on key collision (they're scoped to a concrete
    state-tree path; canonical entries are bare names).
    """
    viz_html: dict = {}

    # 1. Inline viz steps.
    try:
        from process_bigraph.visualization import render_results
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

    # 2b. Topology trajectory figure: any run whose emitter captured a
    #     tree[node] store whose child nodes change over steps (cell division,
    #     biofilm colonization, lineage evolution) gets a saved "place-graph
    #     forming" figure — node count over time + a frame-by-frame filmstrip.
    #     No-op for runs without a topology-changing tree[node].
    if db_file and run_id:
        try:
            from vivarium_workbench.lib.topology_viz import render_topology_viz
            for name, html in render_topology_viz(db_file=db_file, run_id=run_id).items():
                viz_html.setdefault(name, html)
        except Exception:
            traceback.print_exc()

    # 3. Default figure ONLY when a composite declares no visualizations at all.
    #    A canonical-viz *failure* also leaves viz_html empty, but a whole-cell
    #    composite that DECLARES panels must not silently route into the generic
    #    (and expensive) fallback on that failure (issue #784, layer 2).
    if (not viz_html and db_file and run_id and core is not None
            and not _spec_declares_canonical_viz(spec_id)):
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
        from process_bigraph.composite_generator import _REGISTRY, discover_generators
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
    registry = _safe_link_registry_dict(core)
    try:
        from process_bigraph.visualizations import (
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
        from process_bigraph.visualization import Visualization
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
    # pick up trajectories from a different one. Scope the SQL scan to this run
    # so we don't json.loads every OTHER run's state history too (issue #784).
    gathered = gather_emitter_outputs(Path(db_file), run_id=run_id)
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
        from process_bigraph.visualizations import (
            TimeSeriesPlot, TimeSeriesFromObservables,
        )
        from process_bigraph import Composite
    except ImportError:
        return {}

    # Register viz classes the same way _render_canonical_viz does so
    # `local:<ClassName>` addresses resolve through core.link_registry.
    registry = _safe_link_registry_dict(core)
    try:
        from process_bigraph.visualizations import (
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
        from process_bigraph.visualization import Visualization
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

    # Scope to this run AND cap per-row blob size: the generic fallback must
    # never json.loads a whole-cell-scale state per row into an unbounded
    # observable set (issue #784, layer 3 — the actual hang).
    gathered = gather_emitter_outputs(
        Path(db_file), run_id=run_id,
        max_state_bytes=_DEFAULT_VIZ_MAX_STATE_BYTES)
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


def _remote_failure_reason(exc: Exception) -> str:
    """A clean, actionable one-line reason for a failed remote run.

    Maps the known failure modes (poll timeout, unreachable sms-api, dirty/unpushed
    workspace) to a hint an external user can act on, instead of surfacing only a
    raw Python traceback in the run's error excerpt.
    """
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, TimeoutError):
        return f"{msg}  [the run may still be executing on the deployment — check sms-api before retrying]"
    if "unreachable" in low or "is the tunnel up" in low or "still reachable" in low:
        return f"{msg}  [remote sms-api not reachable — is the tunnel/endpoint up? (SMS_API_BASE)]"
    if "uncommitted" in low or "untracked" in low or "not pushed" in low:
        return f"{msg}  [commit and push the workspace before running remotely — the remote build installs it from git]"
    return f"{type(exc).__name__}: {msg}"


def _remote_analysis_options(req: RunRequest, run_dir: Path) -> "dict | None":
    """Build v2ecoli-shaped ``analysis_options`` for a deployment-target submit.

    Composite-auto-results Task 8: the composite deployment path (this module)
    submitted no ``analysis_options`` at all, unlike the study path
    (``remote_run_views.py``'s ``build_analysis_options`` call). Mirrors that:
    merges the composite's own ``@composite_generator(analyses=[...])``
    declarations (``composite_flush._composite_analyses`` — returns ``[]`` when
    ``process_bigraph.composite_generator`` isn't importable, e.g. a
    non-editable install; ``req.declared_results`` still flows through in that
    case) with ``req.declared_results["analyses"]`` (a config-declared block —
    ``ephemeral_study.merge_declarations``, config wins by name), then resolves
    each entry's v2ecoli scale (``study_run_post.build_analysis_options``).

    Gated by the workspace's ``auto_results`` setting
    (``composite_flush._auto_results_enabled``, Task 7): ``False`` returns
    ``None`` — today's behavior (no analysis_options injected).

    Best-effort: any failure (bad path, unresolvable analysis names, missing
    v2ecoli) degrades to ``None`` rather than blocking the remote dispatch;
    unresolved-name errors are logged to the run's log.
    """
    from vivarium_workbench.lib import composite_flush, ephemeral_study, study_run_post

    try:
        if not composite_flush._auto_results_enabled(run_dir):
            return None
        composite_defaults = {
            "analyses": composite_flush._composite_analyses(req.spec_id, None)
        }
        config_declared = getattr(req, "declared_results", None) or {}
        declared = ephemeral_study.merge_declarations(composite_defaults, config_declared)
        analyses = declared.get("analyses") or []
        if not analyses:
            return None
        options, errors = study_run_post.build_analysis_options(analyses, req.workspace)
        for err in errors:
            _write_log(req, f"composite remote analysis config: {err.get('error')}")
        return options or None
    except Exception as exc:  # noqa: BLE001 — best-effort, never block remote dispatch
        _write_log(req, f"note: could not build remote analysis_options: {exc}")
        return None


def _execute_remote(req: RunRequest, run_dir: Path) -> int:
    """Dispatch a 'deployment'-target run to sms-api and land results. Returns 0/1.

    SP-D2: delegates to the already-built ``remote_run.run_remote`` (export .pbg →
    ``/compose/v1`` submit → poll → download results.tar.gz), writing the SAME
    ``composite-runs.db`` status rows the local path does so the browser's existing
    ``/api/composite-run/<id>/status`` polling works unchanged. Unpacking the landed
    tar.gz into a viewable emitter store (viz/chart rendering) is still a
    follow-on; this run DOES fold any ``analyses/<name>/analysis.json`` already
    present in the tar into ``.pbg/runs/<run_id>/analyses.json`` (see below) —
    establishing the run lifecycle end to end (running → completed/failed) on
    the deployment target.

    Composite-auto-results Task 8: also injects ``analysis_options`` into the
    submit (see ``_remote_analysis_options``) — added to the call only when
    non-empty, so a caller/test double built against the pre-Task-8 3-kwarg
    ``run_remote`` signature (``dest``/``n_steps``/``overrides``) keeps working
    unchanged when there's nothing to inject.

    compose-results-land-p0 T5c: once ``run_remote`` returns a landed
    tar.gz path, extract it and fold any analysis manifest into
    ``analyses.json`` via ``remote_run_landing.fold_analyses`` directly —
    deliberately NOT via ``land_remote_run``, which is study-shaped and mints
    its OWN ``run_id`` (would fork a second, disconnected run row vs
    ``req.run_id`` and break the browser's status polling). This call is
    unconditional (no separate ``auto_results`` check — the send-side gate in
    ``_remote_analysis_options`` above already fully controls whether a
    manifest exists at all; ``fold_analyses`` is a silent no-op when there are
    no manifests) and best-effort (never fails an otherwise-completed run —
    mirrors the local path's try/except in ``composite_flush.run_flush``).
    """
    from vivarium_workbench.lib import remote_run, remote_run_landing

    conn = cr.connect(req.db_file)
    try:
        try:
            analysis_options = _remote_analysis_options(req, run_dir)
            run_remote_kwargs: dict = dict(
                dest=run_dir, n_steps=req.steps, overrides=req.overrides,
            )
            if analysis_options:
                run_remote_kwargs["analysis_options"] = analysis_options
            results_path = remote_run.run_remote(req.workspace, req.spec_id, **run_remote_kwargs)
            if results_path is not None:
                try:
                    with tempfile.TemporaryDirectory() as td:
                        extract_root = Path(td)
                        with tarfile.open(results_path, "r:gz") as tar:
                            tar.extractall(extract_root, filter="data")
                        remote_run_landing.fold_analyses(extract_root, req.workspace, req.run_id)
                        # Persist PTools TSV exports locally so the Omics Viewer
                        # discovers this (e.g. GovCloud) run like a local study's.
                        remote_run_landing.land_ptools_tsvs(extract_root, req.workspace, req.run_id)
                except Exception as fold_exc:  # noqa: BLE001 — best-effort, never fail a completed run
                    _write_log(req, f"note: could not fold remote analyses/ptools for this run: {fold_exc}")
        except Exception as exc:
            tb = traceback.format_exc()
            reason = _remote_failure_reason(exc)
            print(reason, flush=True)
            print(tb, flush=True)
            # Write the traceback then the clean reason LAST, so it lands in the
            # status endpoint's last-2000-char error excerpt (composite_run_views)
            # and Chris sees "sms-api unreachable" / "push the workspace" instead of
            # only a raw Python traceback.
            _write_log(req, tb)
            _write_log(req, f"\nREMOTE RUN FAILED: {reason}\n")
            cr.complete_metadata(conn, run_id=req.run_id, n_steps=0, status="failed")
            return 1
        cr.complete_metadata(conn, run_id=req.run_id, n_steps=req.steps,
                             status="completed")
        print(f"remote run {req.run_id} completed: {req.steps} steps", flush=True)
        return 0
    finally:
        conn.close()


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

    # Tell the composite where THIS run lives. A composite that only emits
    # through the injected emitter needs none of this, but one that produces its
    # own on-disk artifacts (a workflow-driving composite writing a parquet
    # sweep, analysis HTML, exports) otherwise has no way to learn the run it is
    # part of — so it writes to a fixed path in the workspace, every run
    # overwrites the last, and the run's own viewers find nothing where they
    # look. SWEEP_DIR is deliberately the exact path _render_canonical_viz hands
    # ParquetAnalysisView, so artifacts written there are the ones the run
    # renders. Read them with os.environ.get(...) and fall back to your own
    # default: they are absent when the composite is built outside a run.
    os.environ["VIVARIUM_WORKBENCH_RUN_ID"] = str(req.run_id)
    os.environ["VIVARIUM_WORKBENCH_RUN_DIR"] = str(run_dir)
    os.environ["VIVARIUM_WORKBENCH_SWEEP_DIR"] = str(
        Path(run_dir) / "parquet" / req.run_id)

    # SP-D2: a 'deployment'-target run dispatches to sms-api /compose/v1 instead of
    # running the composite in this local subprocess. Same detached-runner model,
    # same composite-runs.db persistence + browser polling — only the compute moves.
    if req.target == "deployment":
        return _execute_remote(req, run_dir)

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

        # Loom save-point fork: start this run from a captured frame's state.
        if req.seed_state:
            state = _apply_seed_state(state, req.seed_state)

        # build_core lives in the workspace's own package (e.g.
        # pbg_ws_increase_demo.core). Import it dynamically by package name.
        core_mod = __import__(f"{req.pkg}.core", fromlist=["build_core"])
        core = core_mod.build_core()

        # A generator may register custom types/processes via ``core_extensions``
        # (e.g. the colony composite's ``pymunk_agent`` type). The workspace's
        # ``build_core`` does not know these, so realizing the Composite fails on
        # ``map[pymunk_agent]``. Apply the generator's declared extensions to the
        # run core before the Composite is realized. Never block the run on this.
        try:
            _gen_entry = _generator_entry(req.spec_id)
            if _gen_entry is not None:
                from process_bigraph.composite_generator import apply_core_extensions
                core = apply_core_extensions(_gen_entry, core) or core
        except Exception as _ext_exc:  # noqa: BLE001
            _write_log(req, f"note: could not apply generator core_extensions: {_ext_exc}")

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
        emit_paths, _emit_src = _resolve_emit_paths(
            req, state, spec=spec, spec_id=req.spec_id)
        # Observability (#754): a silent fall-through to emitting every store was
        # exactly how a whole-cell run's memory blew up. Log which rung resolved,
        # and WARN loudly when a temporal composite ends up emitting all stores.
        _write_log(req, f"emit: {len(emit_paths)} path(s) via '{_emit_src}'"
                        f"{': ' + ', '.join(emit_paths[:8]) if emit_paths else ''}")
        if _emit_src == "all-stores" and _state_has_process(state):
            _write_log(
                req,
                f"WARNING: no declared emitter found for temporal composite "
                f"'{req.spec_id}' — emitting ALL {len(emit_paths)} stores. This "
                f"deep-copies the full state every tick and can grow memory "
                f"without bound (#754); declare an emitter (emitters=[...]) or "
                f"pass explicit emit_paths.")

        # The progress callback both heartbeats and enforces the max-runtime
        # self-terminate: raising _RunTimeout aborts the broker's run loop and
        # is caught below, preserving the prior failed-status behavior.
        started = time.monotonic()

        def _progress(step: int) -> None:
            cr.update_progress(conn, run_id=req.run_id, progress_step=step,
                               heartbeat_at=time.time())
            if time.monotonic() - started > MAX_RUNTIME_SEC:
                raise _RunTimeout(step)

        cr.set_phase(conn, run_id=req.run_id, phase="simulating")
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

        cr.set_phase(conn, run_id=req.run_id, phase="rendering visualizations")
        _render_viz(
            composite, run_dir,
            spec_id=req.spec_id, db_file=req.db_file, run_id=req.run_id,
            core=core,
        )
        try:
            from vivarium_workbench.lib.composite_flush import run_flush
            cr.set_phase(conn, run_id=req.run_id, phase="analysis flush")
            run_flush(run_dir, req=req, spec_id=req.spec_id,
                      db_file=req.db_file, run_id=req.run_id, core=core)
        except Exception:
            traceback.print_exc()   # flush must never fail the run

        # reproducible-rerun-spine Task 3 (G4): compute + store a
        # result_fingerprint over this run's declared fields so a rerun can be
        # verified byte-for-byte rather than eyeballed. fingerprint_fields
        # comes from the manifest this run was launched with (Task 3 resolves
        # it at launch, defaulting to emit_paths — see
        # composite_runs.build_run_manifest); a legacy manifest-less run
        # falls back to this run's own resolved emit_paths directly. The
        # snapshot is read from `composite.state` (the just-completed run's
        # final state tree) rather than re-reading the emitter store, so it
        # works uniformly regardless of which emitter (sqlite/parquet/zarr)
        # persisted the run. Best-effort throughout: any failure here leaves
        # result_fingerprint NULL rather than failing an otherwise-successful
        # run.
        try:
            from vivarium_workbench.lib import result_fingerprint as rfp
            meta_row = cr.query_run_meta(conn, run_id=req.run_id)
            fields = None
            if meta_row and meta_row.get("manifest_json"):
                try:
                    fields = json.loads(meta_row["manifest_json"]).get("fingerprint_fields")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    fields = None
            if not fields:
                fields = emit_paths
            state = getattr(composite, "state", None) or {}
            rfp.write_snapshot(run_dir, state, fields)
            fingerprint = rfp.fingerprint_run(run_dir, fields)
            cr.set_result_fingerprint(conn, run_id=req.run_id, fingerprint=fingerprint)
        except Exception:
            traceback.print_exc()

        cr.complete_metadata(conn, run_id=req.run_id, n_steps=req.steps,
                             status="completed", workspace=req.workspace)

        # reproducible-rerun-spine Task 3, Step 6: if this run itself is a
        # recorded reproduction of an earlier run, verify the two
        # fingerprints now that both are stored. `reran_from` is threaded
        # through the request file IF the launcher set it — no current
        # producer does yet (see the task report's concern), so this is
        # normally a no-op; landing it here means a future one-line change to
        # rerun.run_rerun (passing `reran_from=run_id` into the launch) is
        # all that's needed to activate it, with no further run_runner change.
        if req.reran_from:
            try:
                from vivarium_workbench.lib import rerun as rerun_mod
                rerun_mod.verify_reproduction(req.workspace, req.reran_from, req.run_id)
            except Exception:
                traceback.print_exc()   # verification must never fail the run

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
