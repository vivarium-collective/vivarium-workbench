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


def source_switch(body: dict, *, switch_active: bool = True) -> tuple[dict, int]:
    """Validate a workspace switch (and, by default, apply it). Returns ``(body, status)``.

      * missing ``path``        → ``({"error": "missing 'path'"}, 400)``
      * unregistered path       → ``({"error": f"{path!r} is not a registered workspace"}, 400)``
      * registered entry        → ``({"ok": True, "source": {...}}, 200)``

    ``switch_active`` (default ``True``) keeps the legacy behavior — re-point the
    **process-global** active workspace via ``active_workspace.switch_workspace``.
    The FastAPI ``/api/source/switch`` route passes ``switch_active=False`` so the
    switch is **per session** (the route binds the caller's session instead — see
    ``docs/session-registry.md`` §8): validate + resolve the source, but leave the
    global root and other sessions untouched.
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
    if switch_active:
        active_workspace.switch_workspace(entry["path"])
    return (
        {"ok": True,
         "source": {"path": str(entry["path"]), "name": entry.get("name")}},
        200,
    )
