"""Per-session workspace bindings — the `SessionRegistry`.

Maps an opaque **session key** (a cookie the browser carries) → the workspace
that session is bound to. This is the connective tissue that lets one backend
process route each request to *its* session's workspace instead of a single
process-global root.

See `docs/session-registry.md` for the full design and `docs/REFACTOR-PLAN.md`
§2A.6 for where it sits.

An *unbound* session (no entry) resolves to the process default workspace — so
`serve --workspace /path` and every cookie-less client (curl, the CLI, the test
harness) behave exactly as before. Only an explicit `/api/source/switch` creates
a binding. Materialized handles and per-session cache invalidation are later
slices.

**Durable bindings (item 3, Phase 1 — `docs/session-registry.md` §6, "Durable"
tier).** The ``session_key → source`` map survives a restart. It is one small JSON
file, :data:`STORE_FILENAME`, under ``remote_build_source.build_cache_root()`` — the
directory that already holds each session's materialized clone (``sessions/<key>/…``)
and that the deployed workbench mounts from its PVC — written through
``atomic_io.atomic_write_text`` (§10) and loaded lazily on first use. Only the map is
durable; the env worker and ``session_env`` state stay ephemeral (§6). It is durable only
if the build cache is on a persistent volume: the temp-dir fallback ``build_cache_root()``
uses when HOME is not writable is not.

* **Writes.** :func:`rebind` / :func:`drop` write before they return (their handlers
  run on worker threads). :func:`get` never touches the disk: the event-loop thread
  calls it on every request (§10: lookups are cheap), so it only advances ``last_seen``
  in memory and hands the write to a short-lived daemon thread (the pattern of
  ``remote_simulations._kick_remote_refresh``). A slow volume delays a flush, not every
  request.
* **``last_seen``** advances at most once per :data:`LAST_SEEN_FLUSH_INTERVAL_S` and
  reaches the store with the next write, so while the volume is writable the durable
  record lags by about one interval; if writes fail it lags until one succeeds (each
  failure is logged). It is the record a future reaper derives "orphaned" from — never
  process state, which a ``Recreate`` rollout resets (every rollout would look like a
  mass orphaning).
* **A store that cannot be read or understood is never overwritten** — an I/O error,
  invalid JSON, the wrong shape, or another ``version``. The registry then runs
  memory-only, as before Phase 1, logs one error, and retries the read at most once per
  :data:`STORE_RETRY_INTERVAL_S`; the file is left for an operator. Records this version cannot parse (or whose path is
  relative, or whose ``last_seen`` is not a finite number) are kept and written back
  verbatim.
* **Locks.** ``_LOCK`` guards the in-memory state and is never held across store I/O (the
  read, the write, or resolving the store path); ``_IO_LOCK`` serialises the readers and
  writers of the file. Order: ``_IO_LOCK`` before ``_LOCK``, never the reverse.
* **A failed persist degrades to memory-only.** If a write fails, or the store cannot be read
  (above), the binding still holds in this process and is retried; :func:`rebind` does not
  raise. The signal is the logged error, and the binding is lost if the process restarts
  before a write succeeds.
* **Nothing is expired or deleted here — on purpose.** Dropping a binding, let alone
  reaping the clone behind it, before ``WorkspaceStore.persist()`` exists would strand
  or destroy a session's unpushed work (vivarium-workbench#944; the accumulation this
  leaves is #1142). The sweep is a separate, later slice.
* **What this is not: concurrent multi-user access.** It buys durability across
  restarts. The registry assumes one backend process per build cache (§12) — two
  processes sharing one lose each other's updates (the last writer wins) — and a binding
  belongs to a browser TAB (the session key lives in ``sessionStorage``), so it survives
  a restart for the same tab, not for a new tab or another browser.
"""
from __future__ import annotations

import functools
import json
import logging
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from vivarium_workbench.lib.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

SESSION_COOKIE = "vw_session"

# Per-tab identity header (session-per-tab, docs/session-binding.md §3). A browser
# tab carries its session id in ``sessionStorage`` and sends it as this request
# header; the server prefers it over the cookie and echoes the effective id back in
# the same-named response header when the request arrived without one. The cookie
# stays as the back-compat fallback for clients not yet running the fetch override.
SESSION_HEADER = "X-VW-Session"

#: The durable store's file name, under ``build_cache_root()``.
STORE_FILENAME = "session-bindings.json"
STORE_VERSION = 1

#: ``last_seen`` is advanced (in memory) only when it is at least this stale, so a busy
#: session costs one small write per interval rather than one per request.
LAST_SEEN_FLUSH_INTERVAL_S = 300.0

#: After a failed read of the store the read is not retried for this long: a broken volume
#: then costs one failed read per interval, not one per request (and a request may be on
#: the event-loop thread).
STORE_RETRY_INTERVAL_S = 30.0


def _now() -> float:
    return time.time()


@dataclass
class SessionEntry:
    """A session's binding. Carries the workspace path and when the session was last
    seen; later slices add the WorkspaceStore handle, env worker, and Principal."""

    source_path: Path
    last_seen: float = field(default_factory=lambda: _now())


_REGISTRY: dict[str, SessionEntry] = {}
# Records this version could not parse, by session key — kept so a rewrite never deletes them.
_UNPARSED: dict[str, object] = {}
_LOCK = threading.Lock()  # guards _REGISTRY, _UNPARSED and the flags below; never held across store I/O
_IO_LOCK = threading.Lock()  # serialises store readers and writers; always taken BEFORE _LOCK, never while holding it
_LOADED_FROM: Path | None = None  # the store the map was last read from successfully
_DIRTY = False  # the map differs from what was last written to that store
_RETRY_AT: dict[Path, float] = {}  # per store path: when a failed read may be retried


class _StoreUnusable(Exception):
    """The store exists but cannot be read or understood. It is never overwritten."""


@functools.lru_cache(maxsize=128)
def _log_once(level: int, message: str) -> None:
    """Log ``message`` the first time only: these run on the request path, where the same
    failure would otherwise repeat on every request."""
    logger.log(level, "%s", message)


def _store_path() -> Path | None:
    """Where the durable store lives, or ``None`` when the build-cache root cannot be
    resolved (e.g. ``Path.home()`` with no ``HOME`` and no passwd entry) — memory-only,
    never a 500 on every request."""
    from vivarium_workbench.lib.remote_build_source import build_cache_root

    try:
        return build_cache_root() / STORE_FILENAME
    except (RuntimeError, OSError) as exc:
        _log_once(logging.ERROR,
                  f"session bindings are memory-only: cannot resolve the build-cache root ({exc})")
        return None


def _parse_entry(record: object) -> SessionEntry | None:
    """A ``SessionEntry`` from a stored record, or ``None`` if this version cannot trust it:
    missing or mistyped fields, a relative path (it would resolve against the process CWD),
    or a ``last_seen`` that is not a finite, non-negative number."""
    if not isinstance(record, dict):
        return None
    try:
        source_path = Path(record["source_path"])
        last_seen = float(record["last_seen"])
    except (KeyError, TypeError, ValueError):
        return None
    if not source_path.is_absolute() or not math.isfinite(last_seen) or last_seen < 0:
        return None
    return SessionEntry(source_path=source_path, last_seen=last_seen)


def _read_store(path: Path) -> tuple[dict[str, SessionEntry], dict[str, object]]:
    """The bindings in ``path`` and, separately, the records this version cannot parse. A
    missing file is an empty registry; anything else that cannot be read *and understood*
    raises :class:`_StoreUnusable`."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, {}
    except (OSError, ValueError) as exc:
        raise _StoreUnusable(f"cannot read it ({exc})") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), dict):
        raise _StoreUnusable("it has no 'sessions' mapping")
    if raw.get("version") != STORE_VERSION:
        raise _StoreUnusable(
            f"it is version {raw.get('version')!r}; this build understands {STORE_VERSION}")
    entries: dict[str, SessionEntry] = {}
    unparsed: dict[str, object] = {}
    for key, record in raw["sessions"].items():
        entry = _parse_entry(record)
        if entry is None:
            unparsed[key] = record
        else:
            entries[key] = entry
    return entries, unparsed


def _ensure_loaded() -> bool:
    """Read the durable store the first time it is needed. Returns True when the in-memory
    map is backed by a store that was read successfully (a missing file counts: an empty
    registry), False when the registry is memory-only. Caller holds no lock: the read runs
    under ``_IO_LOCK`` only, so ``_LOCK`` (the request path's) is never held across it."""
    global _LOADED_FROM, _DIRTY
    path = _store_path()
    if path is None:
        return False
    with _LOCK:
        if _LOADED_FROM == path:
            return True
        if _now() < _RETRY_AT.get(path, 0.0):
            return False  # a read failed a moment ago: memory-only until the retry is due
    with _IO_LOCK:
        with _LOCK:
            if _LOADED_FROM == path:  # another thread loaded it while this one waited
                return True
            now = _now()
            if now < _RETRY_AT.get(path, 0.0):
                return False
            if _LOADED_FROM is not None:
                # A different store than the one held in memory (tests re-point the env
                # var): never mix two.
                _REGISTRY.clear()
                _UNPARSED.clear()
                _LOADED_FROM = None
                _DIRTY = False
        try:
            entries, unparsed = _read_store(path)
        except _StoreUnusable as exc:
            with _LOCK:
                _RETRY_AT[path] = now + STORE_RETRY_INTERVAL_S
            _log_once(logging.ERROR,
                      f"session bindings are memory-only; {path} is left untouched: {exc}")
            return False
        _log_once.cache_clear()  # recovered: a later outage is news again
        with _LOCK:
            _RETRY_AT.pop(path, None)
            # A binding made while the store was unreadable is newer than the disk: keep
            # it, and get it written.
            unsaved = any(entries.get(key) != entry for key, entry in _REGISTRY.items())
            entries.update(_REGISTRY)
            _REGISTRY.clear()
            _REGISTRY.update(entries)
            _UNPARSED.clear()
            _UNPARSED.update(unparsed)
            _LOADED_FROM = path
            if unsaved:
                _DIRTY = True
    if unsaved:
        _kick_flush()
    return True


def _snapshot() -> dict[str, object]:
    """The store's content. Caller holds ``_LOCK``."""
    sessions: dict[str, object] = dict(_UNPARSED)
    sessions.update({
        key: {"source_path": str(entry.source_path), "last_seen": entry.last_seen}
        for key, entry in _REGISTRY.items()
    })
    return {"version": STORE_VERSION, "sessions": sessions}


def flush() -> None:
    """Write the map to the store if it changed since the last write. Blocking I/O — call
    it from a worker or background thread, never from the event-loop thread.

    Writers run one at a time and each takes its snapshot only after it holds the writer
    lock, so an older snapshot can never overwrite a newer one. Nothing is written unless
    the store was read successfully (see the module docstring). A failed write keeps the
    map marked unsaved, so the next flush retries it — the binding itself still holds in
    memory, as it always did."""
    global _DIRTY
    with _IO_LOCK:
        path = _store_path()
        with _LOCK:
            if not _DIRTY or path is None or _LOADED_FROM != path:
                return
            payload = json.dumps(_snapshot(), indent=2, sort_keys=True) + "\n"
            _DIRTY = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, payload)
        except Exception:  # not only OSError: an unsaved change must never die silently
            logger.warning("could not persist session bindings to %s; they stay in memory only",
                           path, exc_info=True)
            with _LOCK:
                _DIRTY = True


def _kick_flush() -> None:
    """Write pending changes on a short-lived daemon thread, so the caller — usually the
    event-loop thread — never waits on the disk."""
    try:
        threading.Thread(target=flush, name="session-registry-flush", daemon=True).start()
    except RuntimeError:  # e.g. thread exhaustion: the change stays marked unsaved, the next flush writes it
        logger.warning("could not start the session-bindings writer thread", exc_info=True)


def mint_key() -> str:
    """A fresh, unguessable session key (CSPRNG, ~256 bits)."""
    return secrets.token_urlsafe(32)


def get(session_key: str | None) -> SessionEntry | None:
    """The binding for ``session_key``, or ``None`` when unbound/unknown.

    ``None`` is the *unbound* case — callers fall back to the process default
    workspace (this is what keeps a fresh session and cookie-less clients
    behaving exactly as today).

    A bound session's ``last_seen`` is advanced here, but only when it is at least
    :data:`LAST_SEEN_FLUSH_INTERVAL_S` stale, and only in memory: the write is handed to a
    background thread. The first call after a start also reads the (small) store.
    """
    global _DIRTY
    if not session_key:
        return None
    advanced = False
    _ensure_loaded()
    with _LOCK:
        entry = _REGISTRY.get(session_key)
        if entry is not None:
            now = _now()
            if now - entry.last_seen >= LAST_SEEN_FLUSH_INTERVAL_S:
                entry.last_seen = now
                _DIRTY = True
                advanced = True
    if advanced:
        _kick_flush()
    return entry


def rebind(session_key: str, source_path: Path | str) -> SessionEntry:
    """Bind ``session_key`` to ``source_path`` (create or update) and persist it before
    returning. The write path for a per-session ``/api/source/switch``."""
    global _DIRTY
    entry = SessionEntry(source_path=Path(source_path))
    _ensure_loaded()
    with _LOCK:
        _REGISTRY[session_key] = entry
        _DIRTY = True
    flush()
    return entry


def drop(session_key: str | None) -> None:
    """Forget a session's binding, durably (session end / explicit end). No-op if
    unknown. Nothing calls this on idle expiry — see the module docstring. While the store
    is unreadable there is nothing in memory to drop, so a ``drop`` then is a no-op and the
    binding returns once the store can be read again."""
    global _DIRTY
    if not session_key:
        return
    _ensure_loaded()
    with _LOCK:
        if _REGISTRY.pop(session_key, None) is None:
            return
        _UNPARSED.pop(session_key, None)
        _DIRTY = True
    flush()


def clear() -> None:
    """Test helper: leave the registry empty — in memory AND on disk, so a later lazy load
    cannot resurrect what was cleared (many tests share one store per worker and rely on
    this). Production never calls it."""
    global _LOADED_FROM, _DIRTY
    store = _store_path()
    with _IO_LOCK, _LOCK:
        _REGISTRY.clear()
        _UNPARSED.clear()
        _RETRY_AT.clear()
        _LOADED_FROM = None
        _DIRTY = False
        # Only ever a regular file — never trip on a directory squatting the path.
        if store is not None and store.is_file():
            store.unlink(missing_ok=True)
