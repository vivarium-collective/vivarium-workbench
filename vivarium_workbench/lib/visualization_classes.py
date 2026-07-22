"""List registered Visualization / Analysis classes for a workspace.

Originally extracted from ``vivarium_workbench.server._visualization_classes_data``.
The introspection (build the workspace core, snapshot its ``link_registry``,
discover viz/analysis classes) now runs in the **env worker** — the workspace's
compute environment out of the HTTP process (``docs/env-worker-protocol.md``) —
so this process never imports the workspace package. The worker builds the JSON;
this function just routes the call through the warm worker pool.
"""

from __future__ import annotations

from pathlib import Path


def list_visualization_classes(ws_root: Path) -> dict:
    """Return ``{"classes": [...]}`` for all registered Visualization / Analysis classes.

    Mirrors ``GET /api/visualization-classes``.  The build_core + class discovery
    runs in the workspace's env worker (``env_worker._list_visualizations``); this
    function routes to it through the warm pool. Tolerant: if the worker is
    unavailable (can't spawn / crashed / timed out) it degrades to an empty list,
    matching the pre-worker "build_core failure → empty" contract. Used by
    ``publish.build_bundle`` to export ``api/visualization-classes.json``.

    Parameters
    ----------
    ws_root:
        Workspace root directory (must contain ``workspace.yaml``).

    Returns
    -------
    dict
        ``{"classes": [...]}`` where each entry has ``address``, ``name``,
        ``doc``, and ``kind`` keys.
    """
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
    from vivarium_workbench.lib.env_worker_pool import get_pool

    try:
        return get_pool().call(ws_root, "viz_classes")
    except EnvWorkerUnavailable:
        return {"classes": []}
