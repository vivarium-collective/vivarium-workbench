"""Per-session workspace bindings — the `SessionRegistry`.

Maps an opaque **session key** (a cookie the browser carries) → the workspace
that session is bound to. This is the connective tissue that lets one backend
process route each request to *its* session's workspace instead of a single
process-global root.

See `docs/session-registry.md` for the full design and `docs/REFACTOR-PLAN.md`
§2A.6 for where it sits.

**Slice 1 scope (behavior-preserving).** In-memory registry + cookie plumbing.
An *unbound* session (no entry) resolves to the process default workspace — so
`serve --workspace /path` and every cookie-less client (curl, the CLI, the test
harness) behave exactly as before. Only an explicit `/api/source/switch` creates
a binding. Durable persistence of `session_key → source`, materialized handles,
and per-session cache invalidation are later slices.
"""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from pathlib import Path

SESSION_COOKIE = "vw_session"

# Per-tab identity header (session-per-tab, docs/session-binding.md §3). A browser
# tab carries its session id in ``sessionStorage`` and sends it as this request
# header; the server prefers it over the cookie and echoes the effective id back in
# the same-named response header when the request arrived without one. The cookie
# stays as the back-compat fallback for clients not yet running the fetch override.
SESSION_HEADER = "X-VW-Session"


@dataclass
class SessionEntry:
    """A session's binding. Slice 1 carries just the workspace path; later
    slices add the WorkspaceStore handle, env worker, and Principal."""

    source_path: Path


_REGISTRY: dict[str, SessionEntry] = {}
_LOCK = threading.Lock()


def mint_key() -> str:
    """A fresh, unguessable session key (CSPRNG, ~256 bits)."""
    return secrets.token_urlsafe(32)


def get(session_key: str | None) -> SessionEntry | None:
    """The binding for ``session_key``, or ``None`` when unbound/unknown.

    ``None`` is the *unbound* case — callers fall back to the process default
    workspace (this is what keeps a fresh session and cookie-less clients
    behaving exactly as today).
    """
    if not session_key:
        return None
    with _LOCK:
        return _REGISTRY.get(session_key)


def rebind(session_key: str, source_path: Path | str) -> SessionEntry:
    """Bind ``session_key`` to ``source_path`` (create or update). The write
    path for a per-session ``/api/source/switch``."""
    entry = SessionEntry(source_path=Path(source_path))
    with _LOCK:
        _REGISTRY[session_key] = entry
    return entry


def drop(session_key: str | None) -> None:
    """Forget a session's binding (session end / expiry). No-op if unknown."""
    if not session_key:
        return
    with _LOCK:
        _REGISTRY.pop(session_key, None)


def clear() -> None:
    """Test helper: drop all bindings."""
    with _LOCK:
        _REGISTRY.clear()
