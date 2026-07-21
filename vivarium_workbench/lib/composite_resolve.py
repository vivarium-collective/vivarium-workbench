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

from vivarium_workbench.lib.sms_api_client import SmsApiClient
from vivarium_workbench.lib.workspace_deps_views import _sms_api_base


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Ensure the workspace root is on ``sys.path`` so its package is importable."""
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def _prime_registry() -> None:
    """Best-effort: import bigraph-schema packages so decorator-registered
    generators populate the process-bigraph registry. Monkeypatched in tests."""
    try:
        from pbg_superpowers.composite_generator import discover_generators
        discover_generators()
    except Exception:
        pass


def declared_emit_paths(decls: "list[dict] | None") -> list:
    """Flatten a composite's declared ``emitters=[...]`` decl(s) into the
    ordered, deduped list of paths they emit (e.g. ``["global_time", "bulk",
    "listeners"]`` for v2ecoli's ``baseline``, matching ``spec.emitters`` /
    ``pbg_superpowers.composite_generator.emitter_defaults``'s shape).

    Each decl's ``paths`` entries are '.'-or-'/'-joined; segments are
    re-joined with ``/`` to match the client's ``emitSet`` path convention
    (mirrors ``_emitter_node_from_decl``'s own path-splitting in
    ``pbg_superpowers.composite_generator``, and loom's
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


def _artifact_base_dir(ws_root: "Path", spec: "CompositeSpec") -> "Path":
    """Where a generator's default-state artifact lives. Reuses the dashboard's
    existing snapshot dir if present, else the workspace root."""
    snap = Path(ws_root) / "api" / "composite-state"
    return snap if snap.is_dir() else Path(ws_root)


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
        "wiring_status": "unavailable",
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


def resolve_composite(
    ws_root: Path, spec_id: str, overrides: "dict | None" = None
) -> "dict | None":
    """Return the resolve payload dict for a single composite, or ``None`` on miss.

    Mirrors the data returned by ``GET /api/composite-resolve``.  The expensive
    SVG render is set to ``None``; it is only performed by the stdlib server's
    live handler.  Used by ``publish.build_bundle`` (via the server.py forwarder)
    to pre-build ``api/composite-state/<id>.json`` files.

    A generator whose default-state artifact is missing returns a 200 payload
    with ``wiring_status:"unavailable"`` and an honest ``notice``.  Only a
    genuinely-unregistered id returns ``None`` (→ 404).

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
        try:
            state = spec.default_state(base_dir=_artifact_base_dir(ws_root, spec))
        except Exception:
            state = None
        if state is None:
            # Generators that declare no default_state_ref still have a committed
            # artifact from the regen script (reports/composite-state/<id>.json) —
            # serve it so the wiring renders instead of "not generated yet".
            state = _committed_default_state(ws_root, spec_id)
        wiring_status = "ready" if state is not None else "unavailable"
        notice = None
        if wiring_status == "unavailable":
            if spec.kind == "generator":
                notice = (f"default state for generator '{spec.name}' is not generated yet — "
                          f"run it, or regenerate its default-state artifact to see the wiring.")
            else:
                notice = (f"static composite '{spec.name}' has no inline state to display.")
        if state is not None:
            try:
                from vivarium_workbench.lib.process_docs import attach_process_docs
                attach_process_docs(state)
            except Exception:
                pass
            # Embed the declared emit-all paths INSIDE `state` (not as a
            # sibling of it): the dashboard glue (walkthrough.js) and loom's
            # popup/static hydration paths all forward only `payload.state`
            # to the client (composite:load's `msg.state`, ?stateUrl='s
            # `data.state` unwrap) — a sibling key here would be silently
            # dropped before it ever reaches loom's `declaredEmitPaths`.
            if isinstance(state, dict):
                try:
                    declared = declared_emit_paths(spec.emitters)
                    if declared:
                        state["_declared_emit_paths"] = declared
                except Exception:
                    pass
        return {
            "id": spec_id, "name": spec.name, "description": spec.description,
            "parameters": spec.parameters, "state": state, "schema": spec.schema,
            "requires": spec.requires, "tags": spec.tags,
            "visualizations": spec.visualizations, "analyses": spec.analyses,
            "emitters": spec.emitters, "kind": spec.kind, "module": spec.module,
            "default_n_steps": spec.default_n_steps, "svg": None,
            "wiring_status": wiring_status, "notice": notice,
        }
    except Exception as e:
        # In-process import/discovery failures (e.g. a generator module whose
        # native deps — pymunk et al — are missing/broken in this interpreter)
        # degrade to the same honest-unavailable shape instead of propagating
        # to the app-wide 500 handler. `find_composite_path`/`from_file`/
        # `default_state` misses above already return/degrade before this
        # reaches here; this is the outer net for `_get_spec`/discovery itself.
        return _degraded_result(spec_id, e)


def resolve_composite_for_request(
    ws_root: "Path | str", spec_id: str, overrides: "dict | None" = None
) -> "dict | None":
    """Resolve a composite for a UI request, routing by source: a remote build
    (.viv-build.json) resolves on the deployment via sms-api; a local workspace
    resolves locally. Returns the resolve payload dict (or None on a local miss)."""
    from vivarium_workbench.lib.run_core import run_target_for
    from vivarium_workbench.lib.remote_simulations import _read_build_meta

    ws_root = Path(ws_root)
    if run_target_for(ws_root) == "deployment":
        meta = _read_build_meta(ws_root) or {}
        sim_id = meta.get("simulator_id")
        if sim_id is None:
            return {"error": "remote build has no simulator_id stamp"}
        return SmsApiClient(_sms_api_base()).composite_resolve(int(sim_id), spec_id, overrides or {})
    return resolve_composite(ws_root, spec_id, overrides)
