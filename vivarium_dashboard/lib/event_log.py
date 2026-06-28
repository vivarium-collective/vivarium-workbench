"""Durable append-only event log writer (RFC-0002 Phase A).

Writes typed events to ``workspace/.pbg/events.jsonl`` AFTER a committed state
write (the drift guard). The canonical READER lives in
``investigation_contracts.read_log``; this module owns only the writer + the
``emit_event`` envelope builder.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from investigation_contracts import validate_envelope, SCHEMA_VERSION


def _pbg_dir(ws_root: Path) -> Path:
    d = Path(ws_root) / ".pbg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path(ws_root: Path) -> Path:
    return _pbg_dir(ws_root) / "events.jsonl"


def _next_event_id(ws_root: Path) -> str:
    seq = _pbg_dir(ws_root) / "events.seq"
    n = 0
    if seq.is_file():
        try:
            n = int(seq.read_text().strip() or "0")
        except ValueError:
            n = 0
    n += 1
    tmp = seq.with_suffix(".seq.tmp")
    tmp.write_text(str(n), encoding="utf-8")
    os.replace(tmp, seq)
    return f"{n:012d}"


def append(ws_root: Path, envelope: dict) -> str:
    ok, err = validate_envelope(envelope)
    if not ok:
        raise ValueError(f"invalid event envelope: {err}")
    line = json.dumps(envelope, separators=(",", ":")) + "\n"
    path = log_path(ws_root)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return envelope["event_id"]


def emit_event(ws_root: Path, *, type: str, subject: str, transition: dict,
               actor: str, provenance: dict, payload: dict) -> str:
    """Build + append a typed event. Call ONLY after the state write commits."""
    envelope = {
        "event_id": _next_event_id(ws_root),
        "type": type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "subject": subject,
        "transition": transition,
        "provenance": provenance,
        "payload": payload,
        "schema_version": SCHEMA_VERSION,
    }
    return append(ws_root, envelope)
