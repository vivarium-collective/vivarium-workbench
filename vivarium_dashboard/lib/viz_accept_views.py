"""Visualization-accept finalize builder (pure, no ``import server``).

Pure builder for ``POST /api/visualization-accept`` — finalize a generated
visualization file:

    visualization_accept(ws_root: Path, body: dict) -> tuple[dict, int]

It reproduces the live ``_post_visualization_accept`` handler's PRE-WRAPPER
validation/verification byte-identically (steps 1-6): name check, generated-file
lookup, registry-cache invalidation, in-process import-verify, ``build_core()``
smoke-test, and class discovery.  The handler's step 7 — the
``_active_branch_action`` git commit whose ``action()`` is a NO-OP — is DEFERRED
to the FastAPI flip; on success this builder returns ``({"ok": True}, 200)``.

The import / reload / ``build_core`` / class-walk operate on the running process
exactly as the handler does (importing the workspace's generated module into the
process is the point of the verification).  ``_ws_add_to_sys_path``'s effect is
replicated inline (insert ``ws_root`` on ``sys.path``) rather than importing the
stdlib server.

Part of the FastAPI strangler-fig migration (POST phase, Phase C-state).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from vivarium_dashboard.lib.registry import clear_registry_cache


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Ensure the workspace root is on ``sys.path`` so its package is importable.

    Replicates the stdlib server's ``_ws_add_to_sys_path`` helper inline.
    """
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def visualization_accept(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/visualization-accept — finalize a generated visualization file.

    Body: ``{name, class_name?}``.

    Reproduces steps 1-6 of ``_post_visualization_accept`` with ``ws_root`` in
    place of ``WORKSPACE``.  The git commit (step 7) is deferred to the flip.

    Returns:
      200  ``{ok: True}``                    happy path (commit deferred)
      400  name missing
      404  generated file not found
      500  generated file failed to import
      500  workspace build_core() failed after importing the generated file
      500  class <class_name> not found in generated file after import
    """
    name = (body.get("name") or "").strip()
    class_name = (body.get("class_name") or "").strip()
    if not name:
        return {"error": "name is required"}, 400

    snake = name.lower().replace("-", "_")
    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
    target_rel = f"{pkg}/visualizations/{snake}.py"
    target_abs = ws_root / target_rel
    if not target_abs.is_file():
        return {"error": f"generated file not found at {target_rel}"}, 404

    # Invalidate the module-level registry cache so the next registry
    # fetch will rebuild from disk.
    clear_registry_cache()

    # Attempt a fresh in-process import to verify the file loads cleanly.
    try:
        _ws_add_to_sys_path(ws_root)
        sys.path.insert(0, str(ws_root))
        import importlib
        mod_name = f"{pkg}.visualizations.{snake}"
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
        else:
            __import__(mod_name)
        # Also reload the visualizations package itself so the new module
        # is picked up by subsequent _list_visualization_classes calls.
        pkg_viz_mod = f"{pkg}.visualizations"
        if pkg_viz_mod in sys.modules:
            importlib.reload(sys.modules[pkg_viz_mod])
    except Exception as e:
        return {
            "error": f"generated file failed to import: {type(e).__name__}: {e}"
        }, 500

    # Smoke-test the workspace's build_core() so a generated class that
    # breaks bigraph-schema discovery (e.g. malformed inputs type strings,
    # circular imports, type registration errors) surfaces here rather
    # than at first investigation run. Invalidate the cached base core so
    # the rebuild walks the new module too.
    try:
        import bigraph_schema.core as _bsc
        _bsc._cached_base_core = None
    except Exception:
        pass
    try:
        core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
        core_module.build_core()
    except Exception as e:
        return {
            "error": (
                f"workspace build_core() failed after importing the generated file: "
                f"{type(e).__name__}: {e}"
            )
        }, 500

    # Verify the class is discoverable when class_name is supplied.
    # We walk the imported module's attributes directly (using the
    # is_visualization() marker) rather than relying on core.link_registry,
    # because non-installed workspace packages are not discovered by
    # discover_packages() / importlib.metadata.
    if class_name:
        found = False
        mod = sys.modules.get(f"{pkg}.visualizations.{snake}")
        if mod is not None:
            for attr_val in vars(mod).values():
                if not isinstance(attr_val, type):
                    continue
                if getattr(attr_val, "__name__", None) != class_name:
                    continue
                marker = getattr(attr_val, "is_visualization", None)
                if callable(marker):
                    try:
                        if marker() is True:
                            found = True
                            break
                    except Exception:
                        pass
                # Fallback: check subclass of Visualization base
                if not found:
                    try:
                        from pbg_superpowers.visualization import Visualization as _VizBase
                        if issubclass(attr_val, _VizBase) and attr_val is not _VizBase:
                            found = True
                            break
                    except ImportError:
                        pass
        if not found:
            return {
                "error": (
                    f"class {class_name!r} not found in generated file after import; "
                    f"check the @as_visualization name= argument matches"
                )
            }, 500

    # Step 7 (the _active_branch_action git commit with a no-op action) is
    # DEFERRED to the FastAPI flip; on success return the success payload.
    return {"ok": True}, 200
