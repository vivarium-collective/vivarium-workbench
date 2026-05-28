"""Request/response file convention for Claude-suggested dashboard inputs.

Dashboard writes a request to .pbg/agent-requests/<id>.json containing
context (workspace state, workstream commits). A skill in Claude Code
(`/pbg-suggest <id>`) reads it and writes .pbg/agent-responses/<id>.json
with {suggestion, rationale?}. Dashboard polls for the response file.
"""
from __future__ import annotations
import json
import time
from pathlib import Path


REQUEST_DIR = ".pbg/agent-requests"
RESPONSE_DIR = ".pbg/agent-responses"

VALID_KINDS = ("repo-name", "pr-title", "pr-body")


def write_request(ws_root: Path, kind: str, context: dict) -> str:
    """Write a request file; return its id (kind-<ts>)."""
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind: {kind} (must be one of {VALID_KINDS})")
    ts = int(time.time())
    req_id = f"{kind}-{ts}"
    req_dir = ws_root / REQUEST_DIR
    req_dir.mkdir(parents=True, exist_ok=True)
    req_path = req_dir / f"{req_id}.json"
    req_path.write_text(json.dumps({
        "id": req_id,
        "kind": kind,
        "timestamp": ts,
        "context": context,
    }, indent=2, default=str))
    return req_id


def read_response(ws_root: Path, req_id: str) -> dict | None:
    """Return parsed response dict if present, else None."""
    resp_path = ws_root / RESPONSE_DIR / f"{req_id}.json"
    if not resp_path.exists():
        return None
    try:
        return json.loads(resp_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
