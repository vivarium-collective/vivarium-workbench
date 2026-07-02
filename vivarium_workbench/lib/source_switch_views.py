"""Pure builder for ``POST /api/source/switch`` (in-process workspace re-point).

Behaviour-preserving port of the catalog-validation half of the stdlib
``server.Handler._post_source_switch``: a ``{"path": <dir>}`` body must resolve
to a path that is a REGISTERED workspace-catalog entry (no arbitrary paths), and
the matched entry re-points the active workspace via
``active_workspace.switch_workspace`` (sets ``lib._root`` + invalidates the
lib caches — the lib-shareable half of the switch).

The stdlib handler additionally updates its own ``WORKSPACE`` global +
server-local caches; that part stays in ``server`` (this builder is pure and
never imports ``server``).  ``workspace_catalog`` is imported lazily, mirroring
the handler, so importing this module never pulls in pbg_superpowers.
"""

from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib import active_workspace


def source_switch(body: dict) -> tuple[dict, int]:
    """Validate + apply a workspace switch. Returns ``(body, status)``.

      * missing ``path``        → ``({"error": "missing 'path'"}, 400)``
      * unregistered path       → ``({"error": f"{path!r} is not a registered workspace"}, 400)``
      * registered entry        → re-points + ``({"ok": True, "source": {...}}, 200)``
    """
    from pbg_superpowers import workspace_catalog

    path = str((body or {}).get("path") or "").strip()
    if not path:
        return {"error": "missing 'path'"}, 400
    target = str(Path(path).resolve())
    entry = next(
        (w for w in workspace_catalog.list_workspaces()
         if str(Path(w["path"]).resolve()) == target),
        None,
    )
    if entry is None:
        return {"error": f"{path!r} is not a registered workspace"}, 400
    active_workspace.switch_workspace(entry["path"])
    return (
        {"ok": True,
         "source": {"path": str(entry["path"]), "name": entry.get("name")}},
        200,
    )
