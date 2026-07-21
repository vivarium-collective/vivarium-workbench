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


def fold_runs_jsonl(workspace: Path) -> dict[str, dict]:
    """Fold the log to the latest record per run_id (later events merge over earlier)."""
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
            folded.setdefault(rid, {}).update(ev)
    return folded
