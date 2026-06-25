"""Tests for lib.events: workspace_state_payload + workspace_state_stream."""
from __future__ import annotations

import asyncio
import json

import pytest

from vivarium_dashboard.lib import events


# ---------------------------------------------------------------------------
# workspace_state_payload
# ---------------------------------------------------------------------------

def test_payload_valid_yaml(tmp_path):
    """Valid workspace.yaml → JSON-encoded dict."""
    (tmp_path / "workspace.yaml").write_text("name: my-ws\nversion: 1\n")
    result = events.workspace_state_payload(tmp_path)
    data = json.loads(result)
    assert data["name"] == "my-ws"
    assert data["version"] == 1


def test_payload_malformed_yaml(tmp_path):
    """Malformed workspace.yaml → _error sentinel."""
    # A bare tab + bad structure that PyYAML rejects
    (tmp_path / "workspace.yaml").write_text("key: [unclosed\n")
    result = events.workspace_state_payload(tmp_path)
    data = json.loads(result)
    assert data == {"_error": "yaml parse"}


def test_payload_missing_file_raises(tmp_path):
    """FileNotFoundError propagates when workspace.yaml is absent."""
    with pytest.raises(FileNotFoundError):
        events.workspace_state_payload(tmp_path)


# ---------------------------------------------------------------------------
# workspace_state_stream
# ---------------------------------------------------------------------------

def test_stream_emits_first_chunk_immediately(tmp_path):
    """First chunk fires before the first sleep when workspace.yaml exists."""
    (tmp_path / "workspace.yaml").write_text("key: value\n")

    async def run() -> bytes:
        gen = events.workspace_state_stream(tmp_path, poll_interval=0.01)
        chunk = await asyncio.wait_for(anext(gen), timeout=1.0)
        await gen.aclose()
        return chunk

    chunk = asyncio.run(run())
    assert chunk.startswith(b"event: state\ndata: ")
    assert chunk.endswith(b"\n\n")
    payload = json.loads(chunk[len(b"event: state\ndata: "):].rstrip())
    assert payload["key"] == "value"


def test_stream_emits_on_file_change(tmp_path):
    """A second chunk is emitted when workspace.yaml changes."""
    ws_yaml = tmp_path / "workspace.yaml"
    ws_yaml.write_text("key: v1\n")

    async def run() -> tuple[bytes, bytes]:
        gen = events.workspace_state_stream(tmp_path, poll_interval=0.01)
        first = await asyncio.wait_for(anext(gen), timeout=1.0)
        # Mutate the file between chunks
        ws_yaml.write_text("key: v2\n")
        second = await asyncio.wait_for(anext(gen), timeout=1.0)
        await gen.aclose()
        return first, second

    first, second = asyncio.run(run())
    assert json.loads(first[len(b"event: state\ndata: "):].rstrip())["key"] == "v1"
    assert json.loads(second[len(b"event: state\ndata: "):].rstrip())["key"] == "v2"


def test_stream_deduplication(tmp_path):
    """Unchanged file does not produce a second chunk (dedup by raw text)."""
    (tmp_path / "workspace.yaml").write_text("key: stable\n")

    async def run() -> tuple[bytes, bytes | None]:
        gen = events.workspace_state_stream(tmp_path, poll_interval=0.01)
        # First chunk comes immediately
        first = await asyncio.wait_for(anext(gen), timeout=1.0)
        # File unchanged: a second chunk must NOT arrive within a short window
        second: bytes | None = None
        try:
            second = await asyncio.wait_for(anext(gen), timeout=0.05)
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        finally:
            try:
                await gen.aclose()
            except Exception:  # generator may already be closed on timeout
                pass
        return first, second

    first, second = asyncio.run(run())
    assert first.startswith(b"event: state\ndata: ")
    assert second is None, "no second chunk when file is unchanged"


def test_stream_no_chunk_when_missing(tmp_path):
    """No chunk emitted when workspace.yaml does not exist."""

    async def run() -> bytes | None:
        gen = events.workspace_state_stream(tmp_path, poll_interval=0.01)
        chunk: bytes | None = None
        try:
            chunk = await asyncio.wait_for(anext(gen), timeout=0.05)
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass
        finally:
            try:
                await gen.aclose()
            except Exception:
                pass
        return chunk

    chunk = asyncio.run(run())
    assert chunk is None, "no chunk when workspace.yaml is absent"
