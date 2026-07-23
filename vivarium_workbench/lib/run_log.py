"""Append-only JSONL run-metadata log — the single write path for run events.

Owns ``<workspace>/.pbg/runs.jsonl``. Each line is one event
(``started`` / ``completed`` / ``failed``). Readers fold the log to the
latest record per ``run_id``. This is the durable metadata store; live
progress/heartbeat is intentionally NOT logged here (it stays ephemeral).
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path

RUN_LOG_RELPATH = ".pbg/runs.jsonl"


def _log_path(workspace: Path) -> Path:
    return Path(workspace) / RUN_LOG_RELPATH


def append_run_event(workspace: Path, event: dict) -> None:
    """Atomically append one event as a JSON line. Stamps ``ts`` if absent."""
    ev = dict(event)
    ev.setdefault("ts", time.time())
    path = _log_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(ev, sort_keys=True) + "\n"
    # O_APPEND makes concurrent single-line writes atomic on POSIX.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def append_deleted_event(workspace: Path, run_id: str) -> None:
    """Tombstone a run so the fold stops resurrecting it.

    ``delete_simulation`` clears the sqlite rows, history, run dir and study
    refs — but the log is append-only, so without a tombstone the very next
    fold re-synthesises the run from its surviving ``started`` event and the
    row reappears in the Simulations DB. That made a deleted run undeletable
    through the UI, permanently.

    A tombstone (rather than rewriting the file) keeps the log append-only,
    which is what makes concurrent writes from detached run processes safe.
    """
    append_run_event(workspace, {"run_id": run_id, "event": "deleted"})


def tombstoned_run_ids(workspace: Path) -> set:
    """run_ids whose LATEST event is a deletion.

    Complements :func:`fold_runs_jsonl` (which simply omits tombstoned runs):
    lets a caller tell "deleted" apart from "never logged" — e.g. the sqlite
    backfill must not resurrect a run the user deleted. A later ``started`` for
    the same id un-tombstones it, matching the fold's revive semantics.
    """
    path = _log_path(workspace)
    if not path.exists():
        return set()
    last: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue
            rid = ev.get("run_id")
            if rid:
                last[rid] = ev.get("event")
    return {rid for rid, event in last.items() if event == "deleted"}


def fold_runs_jsonl(workspace: Path) -> dict[str, dict]:
    """Fold the log to the latest record per run_id (later events merge over earlier).

    A ``deleted`` event tombstones its run_id: the run is dropped from the
    result entirely, and — because the fold is ordered — a later ``started``
    for the SAME run_id (a re-run reusing the id) revives it, so deletion
    doesn't poison the id forever.
    """
    path = _log_path(workspace)
    if not path.exists():
        return {}
    folded: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                continue  # tolerate a torn final line
            rid = ev.get("run_id")
            if not rid:
                continue
            if ev.get("event") == "deleted":
                folded.pop(rid, None)
                continue
            folded.setdefault(rid, {}).update(ev)
    return folded
