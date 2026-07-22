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

    Resolves the target catalog entry by **path** (legacy) or by **name** (the
    session-per-tab spawn: a tab opens ``/?workspace=<name>``, whose bootstrap POSTs
    ``{"name": <catalog name>}`` — no filesystem path in the URL). ``path`` wins when
    both are present.

      * neither path nor name   → ``({"error": "missing 'path'"}, 400)``
      * unregistered path       → ``({"error": f"{path!r} is not a registered workspace"}, 400)``
      * unknown name            → ``({"error": f"{name!r} is not a registered workspace"}, 400)``
      * ambiguous name          → ``({"error": f"{name!r} is ambiguous …"}, 400)``
      * registered entry        → ``({"ok": True, "source": {...}}, 200)``

    ``switch_active`` (default ``True``) keeps the legacy behavior — re-point the
    **process-global** active workspace via ``active_workspace.switch_workspace``.
    The FastAPI ``/api/source/switch`` route passes ``switch_active=False`` so the
    switch is **per session** (the route binds the caller's session instead — see
    ``docs/session-registry.md`` §8): validate + resolve the source, but leave the
    global root and other sessions untouched.
    """
    from pbg_superpowers import workspace_catalog

    body = body or {}
    path = str(body.get("path") or "").strip()
    name = str(body.get("name") or "").strip()
    entries = workspace_catalog.list_workspaces()

    if path:
        target = str(Path(path).resolve())
        entry = next(
            (w for w in entries if str(Path(w["path"]).resolve()) == target),
            None,
        )
        if entry is None:
            return {"error": f"{path!r} is not a registered workspace"}, 400
    elif name:
        matches = [w for w in entries if str(w.get("name") or "") == name]
        if not matches:
            return {"error": f"{name!r} is not a registered workspace"}, 400
        if len(matches) > 1:
            return (
                {"error": f"{name!r} is ambiguous ({len(matches)} registered "
                          "workspaces share it) — open it by path"},
                400,
            )
        entry = matches[0]
    else:
        return {"error": "missing 'path'"}, 400

    if switch_active:
        active_workspace.switch_workspace(entry["path"])
    return (
        {"ok": True,
         "source": {"path": str(entry["path"]), "name": entry.get("name")}},
        200,
    )
