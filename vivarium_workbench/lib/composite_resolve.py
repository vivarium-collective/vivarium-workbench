"""Resolve a single composite spec/generator by ID.

Extracted from ``vivarium_workbench.server._composite_resolve_data`` so the
FastAPI seam (``api/app.py``) can call it without importing the stdlib server
module.  The single implementation is shared: ``server.py`` re-imports
``resolve_composite`` and keeps its old ``_composite_resolve_data`` name as a
thin wrapper.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from process_bigraph.composite_spec import CompositeSpec, get as _get_spec  # module-level for monkeypatch

from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError
from vivarium_workbench.lib.workspace_deps_views import _sms_api_base


from vivarium_workbench.lib.workspace_paths import add_ws_to_sys_path as _ws_add_to_sys_path


def _prime_registry() -> None:
    """Best-effort: import bigraph-schema packages so decorator-registered
    generators populate the process-bigraph registry. Monkeypatched in tests."""
    try:
        from process_bigraph.composite_generator import discover_generators
        discover_generators()
    except Exception:
        pass


def _prime_generator_module(spec_id: str) -> bool:
    """Best-effort: import the module a generator id names so its
    ``@composite_generator`` decorator runs and registers the generator.

    A generator id is ``"<dotted.module>.<generator_name>"`` (e.g.
    ``pbg_cpm_studies.composites.chemotaxis.recruitment``). The package-wide
    ``discover_generators()`` in ``_prime_registry`` walks a fixed set of
    bigraph packages and does NOT reach a local workspace's own
    ``<pkg>.composites.*`` modules, so a workspace generator stays unregistered
    and ``composite_spec.get`` returns ``None`` — the resolve then falls through
    to the static-file branch, misses (it is a generator, not a file), and the
    UI shows "Could not resolve …". Importing the module the id points at is the
    missing priming step: the id literally encodes where the generator lives, so
    this imports exactly that module and nothing broader.

    Returns ``True`` if a module was imported (caller should retry the registry
    lookup), ``False`` otherwise. Wrapped in a broad ``except`` — a workspace
    generator module whose native deps are missing/broken degrades to the same
    honest-unavailable path as before, never a 500. The workspace root must
    already be on ``sys.path`` (``_ws_add_to_sys_path``).
    """
    if not spec_id or "." not in spec_id:
        return False
    module_name = spec_id.rsplit(".", 1)[0]  # strip the trailing <generator_name>
    try:
        import importlib
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def declared_emit_paths(decls: "list[dict] | None") -> list:
    """Flatten a composite's declared ``emitters=[...]`` decl(s) into the
    ordered, deduped list of paths they emit (e.g. ``["global_time", "bulk",
    "listeners"]`` for v2ecoli's ``baseline``, matching ``spec.emitters`` /
    ``viva_superpowers.composite_generator.emitter_defaults``'s shape).

    Each decl's ``paths`` entries are '.'-or-'/'-joined; segments are
    re-joined with ``/`` to match the client's ``emitSet`` path convention
    (mirrors ``_emitter_node_from_decl``'s own path-splitting in
    ``viva_superpowers.composite_generator``, and loom's
    ``convert.ts: declaredEmitPaths``). Returns ``[]`` when nothing is
    declared or ``decls`` is falsy/malformed, so callers can embed the
    result unconditionally.
    """
    out: list = []
    for decl in decls or []:
        if not isinstance(decl, dict):
            continue
        for p in decl.get("paths") or []:
            segs = [seg for seg in str(p).replace(".", "/").split("/") if seg]
            if not segs:
                continue
            norm = "/".join(segs)
            if norm not in out:
                out.append(norm)
    return out


def _actual_emit_paths(state: "dict | None") -> list:
    """Emit paths derived from the composite's ACTUAL emitter nodes in the
    resolved ``state`` — the store paths each emitter step is wired to read.

    This surfaces selectable observables even for composites that build their
    emitter internally (e.g. ecoli_colony) rather than declaring ``emitters=``,
    so the Outputs panel shows what a composite emits, not just what it declares.
    An emitter node is a step whose address ends in 'Emitter' (RAMEmitter /
    ParquetEmitter / XArrayEmitter …); its emit paths are the '/'-joined targets
    of its ``inputs`` wires.
    """
    out: list = []
    if not isinstance(state, dict):
        return out
    for node in state.values():
        if not isinstance(node, dict):
            continue
        addr = str(node.get("address", ""))
        if not addr.split(":")[-1].endswith("Emitter"):
            continue
        wires = node.get("inputs")
        if not isinstance(wires, dict):
            continue
        for target in wires.values():
            segs = (target if isinstance(target, list) else [target])
            norm = "/".join(str(s) for s in segs if s not in (None, ""))
            if norm and norm not in out:
                out.append(norm)
    return out


def _artifact_base_dir(ws_root: "Path", spec: "CompositeSpec") -> "Path":
    """Where a generator's default-state artifact lives. Reuses the dashboard's
    existing snapshot dir if present, else the workspace root."""
    snap = Path(ws_root) / "api" / "composite-state"
    return snap if snap.is_dir() else Path(ws_root)


def classify_run_kind(state: "dict | None") -> str:
    """Classify a resolved composite as ``temporal`` / ``workflow`` / ``unknown``.

    A composite's Run form shows a "Steps" box, but "Steps" is only meaningful
    for a TEMPORAL composite — one with Processes that advance in simulated time
    (each step is a timestep). A WORKFLOW is a Step-only DAG (e.g. the ParCa
    pipeline) that runs its stages ONCE; a step count doesn't correspond to
    anything. The Run form uses this to label the control honestly.

    Rule: any ``_type == "process"`` node anywhere in the state tree → temporal;
    otherwise, if there are Step nodes → workflow; otherwise unknown (no wiring
    resolved, so nothing to judge). Emitter nodes are Steps too, so a lone
    emitter never makes an otherwise-empty composite read as a workflow — a real
    workflow always has non-emitter Steps, and any Process short-circuits first.
    """
    has_process = False
    has_step = False

    def _walk(node) -> None:
        nonlocal has_process, has_step
        if isinstance(node, dict):
            t = node.get("_type")
            if t == "process":
                has_process = True
            elif t == "step":
                has_step = True
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(state)
    if has_process:
        return "temporal"
    if has_step:
        return "workflow"
    return "unknown"


def _degraded_result(
    spec_id: str, error: "BaseException", *, kind: str = "spec",
    notice: "str | None" = None,
) -> dict:
    """Standard-shape 200 degrade payload for a composite that failed to resolve.

    Reused wherever an in-process failure (import error, parse error, ...)
    would otherwise propagate — the Composite Explorer already knows how to
    render ``wiring_status:"unavailable"`` + ``notice`` gracefully; this keeps
    that path the only one callers ever need to render, instead of a bare 500.
    ``notice`` may be overridden with a more specific message; defaults to a
    generic one built from ``error``.
    """
    return {
        "id": spec_id, "name": spec_id.rsplit(".", 1)[-1],
        "description": "", "parameters": {}, "state": None,
        "schema": {}, "requires": {}, "tags": [], "analyses": [],
        "visualizations": [], "emitters": [], "kind": kind,
        "module": "", "default_n_steps": None, "svg": None,
        "wiring_status": "unavailable", "run_kind": "unknown",
        "notice": notice if notice is not None else f"composite could not be resolved: {error}",
    }


def _committed_default_state(ws_root, spec_id: str) -> "dict | None":
    """Fallback default state for a generator that declares no ``default_state_ref``.

    The regen tooling (``scripts/regenerate_composite_states.py`` in a workspace)
    commits a generator's resolved state to ``reports/composite-state/<id>.json``.
    ``CompositeSpec.default_state`` only reads that artifact when the generator
    *declares* a ``default_state_ref``; most generators don't. This fallback reads
    the committed artifact directly by id, so every generator's wiring renders
    without per-generator annotation. Returns the state dict, or None when the
    artifact is absent, unreadable, or carries no usable ``state``."""
    art = Path(ws_root) / "reports" / "composite-state" / f"{spec_id}.json"
    if not art.is_file():
        return None
    try:
        data = json.loads(art.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a malformed artifact must not break resolve
        return None
    state = data.get("state") if isinstance(data, dict) else None
    return state if isinstance(state, dict) else None


def _live_generator_state(ws_root, spec_id: str, overrides: "dict | None" = None) -> "dict | None":
    """Last-resort default state: BUILD the generator instead of reading a file.

    ``spec.default_state()`` only yields state for a generator that declares a
    ``default_state_ref``, and :func:`_committed_default_state` only yields it
    for one whose artifact was regenerated and committed. A generator that has
    neither — every brand-new one — had no path to wiring at all here, even
    though ``GET /api/composite-state`` builds it happily via the env worker.
    That divergence is what made a new composite render "not generated yet" in
    the Explorer while its loom pop-out worked.

    Routes to the same seam (warm, workspace-interpreter, TTL-cached, so a
    pop-out and an Explorer open share one build). Returns the bare store
    mapping — unwrapping the ``{"state": {...}}`` document envelope the builder
    returns — or None when the build fails or no worker is available (in which
    case the caller's honest "unavailable" notice still stands).
    """
    doc, _err = _live_generator_build(ws_root, spec_id, overrides)
    return doc


def _live_generator_build(
    ws_root, spec_id: str, overrides: "dict | None" = None,
) -> "tuple[dict | None, str | None]":
    """Build a generator's state, returning ``(state, error)``.

    Like :func:`_live_generator_state` but also surfaces the build error string
    so a caller that supplied *overrides* can refuse to silently fall back to
    the default (unoverridden) wiring — an invalid Config → Apply must show its
    exception, not a stale default graph. ``error`` is None on success.
    """
    try:
        from vivarium_workbench.lib.composite_state_views import build_composite_state
        # With overrides (Config → Apply), bypass the TTL cache so the freshly
        # overridden wiring isn't shadowed by a default-params entry.
        body, status = build_composite_state(
            Path(ws_root), spec_id, overrides=overrides, fresh=bool(overrides),
        )
    except Exception as e:  # noqa: BLE001 — a failed build must never break resolve
        return None, str(e)
    if not isinstance(body, dict):
        return None, "generator build returned no document"
    if status != 200:
        return None, str(body.get("error") or f"generator build failed (HTTP {status})")
    doc = body.get("state")
    if (isinstance(doc, dict) and isinstance(doc.get("state"), dict)
            and set(doc) <= {"state", "schema", "composition", "bridge", "interface"}):
        doc = doc["state"]
    if isinstance(doc, dict) and doc:
        return doc, None
    return None, "generator build produced empty state"


def resolve_composite(
    ws_root: Path, spec_id: str, overrides: "dict | None" = None,
    *, allow_build: bool = True,
) -> "dict | None":
    """Return the resolve payload dict for a single composite, or ``None`` on miss.

    Mirrors the data returned by ``GET /api/composite-resolve``.  The expensive
    SVG render is set to ``None``; it is only performed by the stdlib server's
    live handler.  Used by ``publish.build_bundle`` (via the server.py forwarder)
    to pre-build ``api/composite-state/<id>.json`` files.

    State is sourced in order: the spec's declared ``default_state_ref``, the
    regen script's committed ``reports/composite-state/<id>.json`` artifact,
    then — for a generator with neither — a live build via the env worker
    (:func:`_live_generator_state`).  Only when all three miss does the payload
    come back 200 with ``wiring_status:"unavailable"`` and an honest ``notice``.
    Only a genuinely-unregistered id returns ``None`` (→ 404).

    Parameters
    ----------
    ws_root:
        Workspace root directory (must contain ``workspace.yaml``).
    spec_id:
        Dotted composite identifier (e.g. ``pbg_my_ws.composites.my_composite``
        for static specs, or ``<module>.<name>`` for generators).
    overrides:
        Optional parameter overrides (preserved in signature for callers;
        parameter substitution is applied by CompositeSpec.to_document at
        run-time; default_state returns the canonical stored state).
    allow_build:
        When False, skip the live-build fallback and report "unavailable"
        purely from declared/committed state — for callers that must stay
        file-only (tests, and any path that cannot afford a generator build).

    Returns
    -------
    dict | None
        Payload dict, or ``None`` on any failure (not found, import errors,
        missing packages).
    """
    ws_root = Path(ws_root)
    try:
        _ws_add_to_sys_path(ws_root)
        _prime_registry()
        spec = _get_spec(spec_id)                       # generator branch: "<module>.<name>"
        if spec is None and _prime_generator_module(spec_id):
            # The package-wide prime didn't reach this workspace's own
            # @composite_generator module; import the module the id names so its
            # decorator registers, then retry before the static-file fallback.
            spec = _get_spec(spec_id)
        if spec is None:                                # static branch: "<pkg>.composites.<stem>"
            from vivarium_workbench.lib.composite_lookup import find_composite_path
            ws_yaml = ws_root / "workspace.yaml"
            ws_data = yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) if ws_yaml.is_file() else {}
            pkg = ws_data.get("package_path") or ("pbg_" + str(ws_data.get("name", "")).replace("-", "_"))
            path = find_composite_path(ws_root, pkg, spec_id)
            if path is None:
                return None
            try:
                spec = CompositeSpec.from_file(path)
            except Exception as e:
                return _degraded_result(
                    spec_id, e,
                    notice=f"composite file could not be parsed: {e}",
                )
        state = None
        # Parameter overrides (Explore Config → Apply) only take effect via a
        # live build with the overridden params — the declared default_state and
        # the committed artifact are the CANONICAL (unoverridden) state. So when
        # overrides are given for a generator, build straight from them.
        if overrides and allow_build and getattr(spec, "kind", None) == "generator":
            state, override_err = _live_generator_build(ws_root, spec_id, overrides)
            if state is None and override_err:
                # The user supplied Config overrides and the build FAILED (e.g.
                # a single-value param given a comma-list). Surface the exception
                # — do NOT fall back to the default (unoverridden) wiring, which
                # would render as if the invalid config had been accepted.
                return {
                    "id": spec_id, "name": spec.name, "description": spec.description,
                    "parameters": spec.parameters, "state": None, "schema": spec.schema,
                    "requires": spec.requires, "tags": spec.tags,
                    "visualizations": spec.visualizations, "analyses": spec.analyses,
                    "emitters": spec.emitters, "kind": spec.kind, "module": spec.module,
                    "default_n_steps": spec.default_n_steps, "svg": None,
                    "wiring_status": "error", "run_kind": "unknown",
                    "notice": override_err, "error": override_err,
                }
        if state is None:
            try:
                state = spec.default_state(base_dir=_artifact_base_dir(ws_root, spec))
            except Exception:
                state = None
        if state is None:
            # Generators that declare no default_state_ref still have a committed
            # artifact from the regen script (reports/composite-state/<id>.json) —
            # serve it so the wiring renders instead of "not generated yet".
            state = _committed_default_state(ws_root, spec_id)
        if state is None and allow_build and getattr(spec, "kind", None) == "generator":
            # Neither declared nor committed: build it (see _live_generator_state).
            state = _live_generator_state(ws_root, spec_id)
        wiring_status = "ready" if state is not None else "unavailable"
        notice = None
        if wiring_status == "unavailable":
            if spec.kind == "generator":
                built = " (a live build was attempted and failed)" if allow_build else ""
                notice = (f"default state for generator '{spec.name}' is not generated yet{built} — "
                          f"run it, or regenerate its default-state artifact to see the wiring.")
            else:
                notice = (f"static composite '{spec.name}' has no inline state to display.")
        if state is not None:
            # Static specs with declared parameters: substitute ${param}
            # placeholders in the state — defaults, plus any Config→Apply
            # overrides — so the graph shows real values (not literal
            # "${capacity}") and Apply updates the preview. Generators already
            # bake overrides in via _live_generator_build above; only the
            # static-spec branch reaches here with raw placeholders.
            if getattr(spec, "kind", None) != "generator" and getattr(spec, "parameters", None):
                from vivarium_workbench.lib.composite_lookup import substitute_parameters
                state = substitute_parameters(state, spec.parameters, overrides or {})
            # Per-process docstrings via the env worker (no in-process workspace
            # import); best-effort — decoration never fails the resolve.
            from vivarium_workbench.lib.process_docs import attach_process_docs_via_worker
            # Pass spec_id so the worker can build the composite's core from its
            # core_extensions and resolve bare registry-name addresses
            # (local:EcoliWCM) — otherwise Composite Processes in a committed
            # artifact never get flagged (no inner-composite drill-in mini-map).
            state = attach_process_docs_via_worker(ws_root, state, spec_id=spec_id)
            # Embed the declared emit-all paths INSIDE `state` (not as a
            # sibling of it): the dashboard glue (walkthrough.js) and loom's
            # popup/static hydration paths all forward only `payload.state`
            # to the client (composite:load's `msg.state`, ?stateUrl='s
            # `data.state` unwrap) — a sibling key here would be silently
            # dropped before it ever reaches loom's `declaredEmitPaths`.
            if isinstance(state, dict):
                try:
                    # Union the DECLARED emitters= paths with the paths the
                    # composite's ACTUAL emitter nodes are wired to — so every
                    # composite surfaces its observables, including ones that
                    # build their emitter internally (e.g. ecoli_colony).
                    declared = declared_emit_paths(spec.emitters)
                    for p in _actual_emit_paths(state):
                        if p not in declared:
                            declared.append(p)
                    if declared:
                        state["_declared_emit_paths"] = declared
                except Exception:
                    pass
        return {
            "id": spec_id, "name": spec.name, "description": spec.description,
            "parameters": spec.parameters, "state": state, "schema": spec.schema,
            # Echo the caller's overrides so a study viewer opening its composite
            # with the study's real config (conditions.baseline.params) can show
            # them in the Configure panel — the panel merges these onto the
            # declared parameter defaults. Empty for a bare (default) resolve.
            "overrides": overrides or {},
            "requires": spec.requires, "tags": spec.tags,
            "visualizations": spec.visualizations, "analyses": spec.analyses,
            "emitters": spec.emitters, "kind": spec.kind, "module": spec.module,
            "default_n_steps": spec.default_n_steps, "svg": None,
            "wiring_status": wiring_status, "run_kind": classify_run_kind(state),
            "notice": notice,
        }
    except Exception as e:
        # In-process import/discovery failures (e.g. a generator module whose
        # native deps — pymunk et al — are missing/broken in this interpreter)
        # degrade to the same honest-unavailable shape instead of propagating
        # to the app-wide 500 handler. `find_composite_path`/`from_file`/
        # `default_state` misses above already return/degrade before this
        # reaches here; this is the outer net for `_get_spec`/discovery itself.
        return _degraded_result(spec_id, e)


def _local_generator_payload(ws_root: Path, spec_id: str) -> "dict | None":
    """item 63: resolve a generator's real declared parameters via the same
    safe, out-of-process env-worker discovery ``discover_all_composites``
    already uses for the registry-validation surface.

    ``resolve_composite``'s own generator lookup (``process_bigraph.
    composite_spec.get``, primed by an IN-PROCESS ``discover_generators()``
    import) structurally can't see a session-bound materialized build's
    composites: the generator module (e.g. ``v2ecoli.composites.
    ecoli_baseline``) is a pip/uv-installed DEPENDENCY of that session's own
    venv, never on the workbench SERVER's own ``sys.path``/site-packages —
    exactly the reason the rest of this codebase deliberately never imports
    ``@composite_generator`` modules in-process (see
    ``discover_all_composites``/``_discover_generators_via_worker``).

    Only returns the DECLARED shape (parameters, name, description, ...) —
    real and immediately useful for a config form. Does not attempt a live
    wiring/state build (``_live_generator_build``/``_live_generator_state``
    have the same in-process-import problem and aren't needed for a
    parameter form); ``state``/``svg`` stay ``None`` with an honest notice,
    same as this function's existing degraded-result shape elsewhere.
    Returns ``None`` when the id isn't a known generator here, so callers can
    fall back to whatever they'd otherwise do on a miss.
    """
    from vivarium_workbench.lib.composite_lookup import discover_all_composites
    ws_yaml = ws_root / "workspace.yaml"
    ws_data = yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) if ws_yaml.is_file() else {}
    pkg = ws_data.get("package_path") or ("pbg_" + str(ws_data.get("name", "")).replace("-", "_"))
    rec = discover_all_composites(ws_root, pkg).get(spec_id)
    if rec is None or rec.get("kind") != "generator":
        return None
    return {
        "id": spec_id, "name": rec.get("name") or spec_id.rsplit(".", 1)[-1],
        "description": rec.get("description", ""), "state": None,
        "parameters": rec.get("parameters") or {}, "schema": {},
        "requires": rec.get("requires") or {}, "tags": rec.get("tags") or [],
        "visualizations": rec.get("visualizations") or [],
        "analyses": rec.get("analyses") or [], "emitters": rec.get("emitters") or [],
        "kind": "generator", "module": rec.get("module") or "",
        "default_n_steps": rec.get("default_n_steps"), "svg": None,
        "wiring_status": "unavailable", "run_kind": "unknown",
        "notice": ("composite wiring preview is not available for remote-pinned "
                   "deployments yet (parameters above are real; the state/SVG "
                   "preview needs a live build, not supported for a materialized "
                   "session build)"),
    }


def resolve_composite_for_request(
    ws_root: "Path | str", spec_id: str, overrides: "dict | None" = None
) -> "dict | None":
    """Resolve a composite for a UI request, routing by source: a session-bound
    materialized build (.viv-build.json — real files on disk, same dir the env
    worker provisions a venv for, item 63) resolves LOCALLY, through the same
    general resolution any local workspace uses; a bare deployment-wide pin
    with no materialized clone resolves on the deployment via sms-api; a local
    workspace resolves locally. Returns the resolve payload dict (or None on
    a local miss)."""
    from vivarium_workbench.lib.run_core import run_target_for
    from vivarium_workbench.lib.remote_simulations import _read_build_meta

    ws_root = Path(ws_root)
    if run_target_for(ws_root) == "deployment":
        meta = _read_build_meta(ws_root) or {}
        sim_id = meta.get("simulator_id")
        if sim_id is None:
            return {"error": "remote build has no simulator_id stamp"}
        # item 63: `.viv-build.json` only ever gets stamped into a materialized
        # session clone (source_build_views.switch_build) — its presence means
        # ws_root has real source files on disk. Not special-cased to any one
        # composite kind or workspace: try the SAME general local resolution
        # ANY local (non-remote-pinned) workspace already uses below.
        #   1. resolve_composite — the canonical path. A static `.composite.
        #      yaml` spec resolves here natively (pure file read, no import,
        #      works regardless of which venv this process happens to run
        #      under). A generator resolves here too whenever this process's
        #      own environment happens to satisfy it.
        #   2. _local_generator_payload — a generator whose module needs THIS
        #      session's own materialized deps (not the workbench server's
        #      own) is invisible to step 1's in-process import; re-check via
        #      the same safe, out-of-process env-worker discovery
        #      discover_all_composites already uses elsewhere (and which the
        #      managed materialization path now provisions a real venv for).
        # Only when NEITHER general path can see it does this fall back to
        # sms-api — the one case with nothing local to introspect at all is a
        # bare deployment-wide pin (VIVARIUM_WORKBENCH_REMOTE_PINNED, no
        # materialized clone), which the same waterfall degrades through
        # cleanly (both local attempts miss, unconditionally, for any id).
        local = resolve_composite(ws_root, spec_id, overrides)
        if local is not None:
            return local
        local = _local_generator_payload(ws_root, spec_id)
        if local is not None:
            return local
        try:
            return SmsApiClient(_sms_api_base()).composite_resolve(int(sim_id), spec_id, overrides or {})
        except SmsApiError as e:
            # sms-api has no POST /core/v1/simulator/{id}/composite-resolve route —
            # this client method was added speculatively and the server side was
            # never built. Composite preview is a non-blocking convenience (actual
            # dispatch reads the composite ref directly and never calls this), so
            # degrade to the same honest-unavailable shape every other resolve
            # failure already uses, instead of a 500.
            return _degraded_result(
                spec_id, e,
                notice="composite preview is not available for remote-pinned deployments yet",
            )
    return resolve_composite(ws_root, spec_id, overrides)
