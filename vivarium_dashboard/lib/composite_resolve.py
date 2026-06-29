"""Resolve a single composite spec/generator by ID.

Extracted from ``vivarium_dashboard.server._composite_resolve_data`` so the
FastAPI seam (``api/app.py``) can call it without importing the stdlib server
module.  The single implementation is shared: ``server.py`` re-imports
``resolve_composite`` and keeps its old ``_composite_resolve_data`` name as a
thin wrapper.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from process_bigraph.composite_spec import CompositeSpec, get as _get_spec  # module-level for monkeypatch

from vivarium_dashboard.lib.sms_api_client import SmsApiClient
from vivarium_dashboard.lib.workspace_deps_views import _sms_api_base


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


def _artifact_base_dir(ws_root: "Path", spec: "CompositeSpec") -> "Path":
    """Where a generator's default-state artifact lives. Reuses the dashboard's
    existing snapshot dir if present, else the workspace root."""
    snap = Path(ws_root) / "api" / "composite-state"
    return snap if snap.is_dir() else Path(ws_root)


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
    _ws_add_to_sys_path(ws_root)
    _prime_registry()
    spec = _get_spec(spec_id)                       # generator branch: "<module>.<name>"
    if spec is None:                                # static branch: "<pkg>.composites.<stem>"
        from vivarium_dashboard.lib.composite_lookup import find_composite_path
        ws_yaml = ws_root / "workspace.yaml"
        ws_data = yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) if ws_yaml.is_file() else {}
        pkg = ws_data.get("package_path") or ("pbg_" + str(ws_data.get("name", "")).replace("-", "_"))
        path = find_composite_path(ws_root, pkg, spec_id)
        if path is None:
            return None
        try:
            spec = CompositeSpec.from_file(path)
        except Exception as e:
            return {
                "id": spec_id, "name": spec_id.rsplit(".", 1)[-1],
                "description": "", "parameters": {}, "state": None,
                "schema": {}, "requires": {}, "tags": [], "analyses": [],
                "visualizations": [], "emitters": [], "kind": "spec",
                "module": "", "default_n_steps": None, "svg": None,
                "wiring_status": "unavailable",
                "notice": f"composite file could not be parsed: {e}",
            }
    try:
        state = spec.default_state(base_dir=_artifact_base_dir(ws_root, spec))
    except Exception:
        state = None
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
            from vivarium_dashboard.lib.process_docs import attach_process_docs
            attach_process_docs(state)
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


def resolve_composite_for_request(
    ws_root: "Path | str", spec_id: str, overrides: "dict | None" = None
) -> "dict | None":
    """Resolve a composite for a UI request, routing by source: a remote build
    (.viv-build.json) resolves on the deployment via sms-api; a local workspace
    resolves locally. Returns the resolve payload dict (or None on a local miss)."""
    from vivarium_dashboard.lib.run_core import run_target_for
    from vivarium_dashboard.lib.remote_simulations import _read_build_meta

    ws_root = Path(ws_root)
    if run_target_for(ws_root) == "deployment":
        meta = _read_build_meta(ws_root) or {}
        sim_id = meta.get("simulator_id")
        if sim_id is None:
            return {"error": "remote build has no simulator_id stamp"}
        return SmsApiClient(_sms_api_base()).composite_resolve(int(sim_id), spec_id, overrides or {})
    return resolve_composite(ws_root, spec_id, overrides)
