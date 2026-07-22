"""Per-session environment preparation + materialization status — the wiring that
connects `materialization_jobs` (§9c) to a session `bind`/`switch`
(`docs/session-registry.md` §5, `docs/materialization-lifecycle.md` §4/§8).

**Eager-on-switch (§10).** When a session switches source, it eagerly *prepares*
that source's environment instead of waiting for the first env query:

- **In-place local** (§2a) — a checkout already on disk — is `ready` at once:
  resolve its interpreter (its `.venv`, else the running Python), **no `uv sync`**.
- **Managed** — a source that must be provisioned — starts an async materialization
  job (§9c) and is `materializing` until `ready` / `failed`. A status poll reflects
  the live job.

Today every `/api/source/switch` target is a registered local catalog entry, so
the in-place branch is the live one; the managed branch is wired + tested, dormant
until the `RepoSource` clone seam (§2) introduces managed sources.

Session-level status values (session-registry §5): `ready` | `materializing` |
`failed`. In-memory, per session key (the ephemeral tier — a restart re-prepares
lazily; the durable artifact is the coordinate-keyed venv on disk).
"""
from __future__ import annotations

import threading
from pathlib import Path

from vivarium_workbench.lib import env_resolver, materialization_jobs as _mj

READY = "ready"
MATERIALIZING = "materializing"
FAILED = "failed"

_SESSION_ENV: dict[str, dict] = {}
_LOCK = threading.Lock()


def _map_job(snap: dict) -> dict:
    """Map an async job snapshot (materialization_jobs) → the session-level view.
    The job's coarse phases (`queued`/`cloning`/`syncing`) all read as the
    session-level `materializing`; `phase` carries the fine detail."""
    st = snap.get("status")
    if st == _mj.READY:
        out = {"status": READY, "interpreter": snap.get("interpreter")}
    elif st == _mj.FAILED:
        out = {"status": FAILED, "error": snap.get("error"), "tail": snap.get("tail", "")}
    else:
        out = {"status": MATERIALIZING, "phase": snap.get("phase"),
               "elapsed_s": snap.get("elapsed_s")}
    # Staged managed sources expose the checkout path + commit as they progress.
    if snap.get("path") is not None:
        out["path"] = snap["path"]
        out["commit"] = snap.get("commit")
    return out


def prepare(session_key: str, source: Path | str, *,
            managed: bool = False, timeout: "float | None" = None) -> dict:
    """Eagerly prepare a session's environment on bind/switch; return its status.

    ``managed=False`` (in-place, §2a) → resolve the interpreter now → ``ready``.
    ``managed=True`` → start (or attach to) an async materialization job (§9c)."""
    src = Path(source)
    if not managed:
        state = {"status": READY, "source": str(src), "managed": False,
                 "interpreter": env_resolver.resolve_interpreter(src)}
    else:
        job = _mj.get_registry().start(src, timeout=timeout)
        state = {"source": str(src), "managed": True,
                 "coordinate": job.coordinate, **_map_job(job.snapshot())}
    with _LOCK:
        _SESSION_ENV[session_key] = state
    return state


def prepare_managed(session_key: str, repo: str, ref: str, *,
                    timeout: "float | None" = None) -> dict:
    """Start (or attach to) materialization of a managed ``(repo, ref)`` for this
    session — an async clone → sync job (§9c). Returns the initial status; the
    session's **active workspace is not switched** to the staged checkout by this
    call (that binding lifecycle is a deferred decision — see the doc §11 log).
    The client polls `status`/`GET /api/source/materialization` until ready."""
    job = _mj.get_registry().start_managed(repo, ref, timeout=timeout)
    state = {"managed": True, "repo": repo, "ref": ref,
             "coordinate": job.coordinate, **_map_job(job.snapshot())}
    with _LOCK:
        _SESSION_ENV[session_key] = state
    return state


def status(session_key: "str | None") -> "dict | None":
    """The session's current materialization status, refreshed from the live job
    for a managed session. ``None`` for a session with no prepared env (an unbound
    session on the in-place default — the caller treats that as ``ready``)."""
    if not session_key:
        return None
    with _LOCK:
        state = _SESSION_ENV.get(session_key)
    if state is None:
        return None
    if state.get("managed") and state.get("coordinate"):
        snap = _mj.get_registry().status(state["coordinate"])
        if snap is not None:
            state = {**state, **_map_job(snap)}
    return state


def clear() -> None:
    """Test helper: forget all prepared session envs."""
    with _LOCK:
        _SESSION_ENV.clear()
