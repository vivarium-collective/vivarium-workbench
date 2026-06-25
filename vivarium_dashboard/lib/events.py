"""Workspace-state SSE helpers.

Provides the single-source payload builder (``workspace_state_payload``) and
the async generator (``workspace_state_stream``) that back both the legacy
stdlib handler (``server.py``) and the FastAPI SSE route (``api/app.py``).

No imports from ``server`` — this module is the clean seam.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator

import yaml


def payload_from_text(text: str) -> str:
    """Encode already-read ``workspace.yaml`` text as the SSE JSON payload.

    Returns ``json.dumps(yaml.safe_load(text))`` on success, or
    ``json.dumps({"_error": "yaml parse"})`` when the YAML cannot be parsed.
    Split out so a caller that has already read the file (the dedup loop)
    derives the payload from the SAME bytes it deduped on — no second read,
    no read-vs-emit TOCTOU divergence.
    """
    try:
        return json.dumps(yaml.safe_load(text))
    except Exception:  # noqa: BLE001
        return json.dumps({"_error": "yaml parse"})


def workspace_state_payload(ws_root: Path) -> str:
    """Encode ``<ws_root>/workspace.yaml`` as a JSON string.

    Reads the file and delegates to :func:`payload_from_text`. Callers are
    responsible for checking that the file exists before calling;
    ``FileNotFoundError`` propagates so the stream can skip a missing file.
    """
    return payload_from_text((ws_root / "workspace.yaml").read_text(encoding="utf-8"))


async def workspace_state_stream(
    ws_root: Path,
    *,
    poll_interval: float = 1.0,
) -> AsyncGenerator[bytes, None]:
    """Async generator that emits SSE ``event: state`` frames.

    Mirrors ``server.Handler._serve_events_sse``:

    - Tracks ``last_state`` (raw ``workspace.yaml`` text, starts ``None``).
    - Each tick: if ``workspace.yaml`` exists and its text differs from
      ``last_state``, yields::

          b"event: state\\ndata: <json>\\n\\n"

      and updates ``last_state``.
    - The first check fires before the first sleep, so a pre-existing
      ``workspace.yaml`` causes an event with no initial delay.
    - Sleeps ``poll_interval`` seconds between ticks (default 1.0 s,
      byte-identical cadence to the legacy handler).

    ``poll_interval`` is exposed so tests can pass ``0.01`` for fast
    iteration; the FastAPI route uses the default 1.0.
    """
    ws_file = ws_root / "workspace.yaml"
    last_state: str | None = None

    try:
        while True:
            if ws_file.exists():
                text = ws_file.read_text(encoding="utf-8")
                if text != last_state:
                    # Derive the payload from the SAME text we deduped on
                    # (single read — no read-vs-emit divergence).
                    yield b"event: state\ndata: " + payload_from_text(text).encode() + b"\n\n"
                    last_state = text
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        # Client disconnected — Starlette cancels the generator. Return quietly,
        # mirroring the legacy handler's BrokenPipeError/ConnectionResetError catch.
        return
