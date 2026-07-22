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

from pathlib import Path
from typing import Any

import yaml

from vivarium_workbench.lib.registry import clear_registry_cache


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
    # fetch will rebuild from disk. (Workbench-process cache — stays here.)
    clear_registry_cache()

    # Import-verify + build_core() smoke-test + class discovery run in the
    # workspace's env worker (importing the generated module + build_core is
    # workspace Python — kept out of the HTTP process). HARD-fail on an
    # unavailable worker: a smoke-test that could not run must NOT report success.
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
    from vivarium_workbench.lib.env_worker_pool import get_pool
    try:
        res = get_pool().call(ws_root, "validate_generated_visualization",
                              {"pkg": pkg, "module": snake, "class_name": class_name})
    except EnvWorkerUnavailable:
        return {"error": "could not verify the generated visualization — "
                         "environment worker unavailable"}, 500
    if isinstance(res, dict) and res.get("error"):
        return {"error": res["error"]}, 500

    # Step 7 (the _active_branch_action git commit with a no-op action) is
    # DEFERRED to the FastAPI flip; on success return the success payload.
    return {"ok": True}, 200
