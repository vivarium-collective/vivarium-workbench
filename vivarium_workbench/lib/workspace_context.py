"""`WorkspaceContext` — the per-request binding of a session to its workspace.

The single seam every request resolves through: session key → the workspace this
request targets (+ its ports, in later slices). Replaces reading the
process-global ``lib._root`` directly in the request path.

See `docs/session-registry.md` §7 and `docs/REFACTOR-PLAN.md` §2A.6.

**Slice 1 scope.** The context carries just ``ws_root`` + ``session_key``; the
``ScientificContent`` / ``EnvironmentResolver`` ports are threaded onto it in
later slices. Resolution is behavior-preserving: a bound session (via
``/api/source/switch``) resolves to its bound path; an unbound session and every
cookie-less client fall back to the process default workspace — exactly today's
behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vivarium_workbench.lib import active_workspace, session_registry


@dataclass
class WorkspaceContext:
    """What a request is operating on. Slice 1: the workspace root + the session
    it belongs to. Later slices add the resolved ports (science, env, handle)."""

    ws_root: Path
    session_key: str | None = None


def _default_ws_root() -> Path:
    """The process default workspace — today's global root, or CWD fallback.

    Mirrors the prior ``get_workspace`` resolution so unbound sessions and
    cookie-less clients are byte-identical to before.
    """
    root = active_workspace.get_workspace_root()
    if root is not None:
        return root
    from vivarium_workbench.lib.env_compat import get_env
    return Path(get_env("WORKSPACE", ".")).resolve()


def resolve(session_key: str | None) -> WorkspaceContext:
    """Resolve a request's ``WorkspaceContext`` from its session key.

    Bound session → its bound path; unbound/unknown → the process default
    (§ session-registry §5 UNBOUND).
    """
    entry = session_registry.get(session_key)
    ws_root = entry.source_path if entry is not None else _default_ws_root()
    return WorkspaceContext(ws_root=ws_root, session_key=session_key)
