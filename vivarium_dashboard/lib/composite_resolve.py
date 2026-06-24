"""Resolve a single composite spec/generator by ID.

Extracted from ``vivarium_dashboard.server._composite_resolve_data`` so the
FastAPI seam (``api/app.py``) can call it without importing the stdlib server
module.  The single implementation is shared: ``server.py`` re-imports
``resolve_composite`` and keeps its old ``_composite_resolve_data`` name as a
thin wrapper.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Ensure the workspace root is on ``sys.path`` so its package is importable."""
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def resolve_composite(ws_root: Path, spec_id: str) -> "dict | None":
    """Return the resolve payload dict for a single composite, or ``None`` on miss.

    Mirrors the data returned by ``GET /api/composite-resolve``.  The expensive
    SVG render is set to ``None``; it is only performed by the stdlib server's
    live handler.  Used by ``publish.build_bundle`` (via the server.py forwarder)
    to pre-build ``api/composite-state/<id>.json`` files.

    Parameters
    ----------
    ws_root:
        Workspace root directory (must contain ``workspace.yaml``).
    spec_id:
        Dotted composite identifier (e.g. ``pbg_my_ws.composites.my_composite``).

    Returns
    -------
    dict | None
        Payload dict, or ``None`` on any failure (not found, import errors,
        missing packages).
    """
    import yaml

    _ws_add_to_sys_path(ws_root)
    try:
        from vivarium_dashboard.lib.composite_lookup import (
            substitute_parameters,
            find_composite_path,
        )
        ws_data = yaml.safe_load(
            (ws_root / "workspace.yaml").read_text(encoding="utf-8")
        )
        pkg = ws_data.get("package_path") or (
            "pbg_" + ws_data.get("name", "").replace("-", "_")
        )

        # Generator-kind branch (pbg-superpowers @composite_generator)
        try:
            from pbg_superpowers.composite_generator import (
                _REGISTRY, build_generator, discover_generators,
            )
            if not _REGISTRY:
                discover_generators()
            entry = _REGISTRY.get(spec_id)
        except ImportError:
            entry = None

        if entry is not None:
            try:
                doc = build_generator(entry, overrides={})
            except Exception:
                return None
            if isinstance(doc, dict) and "state" in doc and isinstance(doc["state"], dict):
                state = doc["state"]
            else:
                state = doc
            try:
                from vivarium_dashboard.lib.process_docs import attach_process_docs
                attach_process_docs(state)
            except Exception:
                pass
            return {
                "id": spec_id,
                "name": entry.name,
                "description": entry.description,
                "parameters": entry.parameters,
                "state": state,
                "svg": None,
                "kind": "generator",
                "module": entry.module,
                "default_n_steps": getattr(entry, "default_n_steps", None),
            }

        # Spec-file branch
        path = find_composite_path(ws_root, pkg, spec_id)
        if path is None:
            return None

        text = path.read_text(encoding="utf-8")
        spec = (
            json.loads(text) if path.suffix.lower() == ".json"
            else yaml.safe_load(text)
        )
        state = substitute_parameters(
            spec.get("state") or {},
            spec.get("parameters") or {},
            {},
        )
        try:
            from vivarium_dashboard.lib.composite_lookup import _derive_module_from_spec_id
            module = _derive_module_from_spec_id(spec_id)
        except Exception:
            module = ""
        try:
            from vivarium_dashboard.lib.process_docs import attach_process_docs
            attach_process_docs(state)
        except Exception:
            pass
        return {
            "id": spec_id,
            "name": spec.get("name", spec_id.rsplit(".composites.", 1)[-1]),
            "description": spec.get("description", ""),
            "parameters": spec.get("parameters") or {},
            "state": state,
            "svg": None,
            "kind": "spec",
            "module": module,
            "default_n_steps": None,
        }
    except Exception:
        return None
