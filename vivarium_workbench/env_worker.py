"""Env worker — the per-session subprocess that holds a workspace's compute
environment out of the HTTP process.

Full contract: ``docs/env-worker-protocol.md``. This is the **worker program**
(spec §4): a single self-contained file, shipped by the workbench but run on the
workspace's interpreter by path, importing **only the standard library** (plus,
in later slices, what the workspace venv already has). It never imports
``vivarium_workbench``.

**Slice 1 scope:** the transport + lifecycle only — the JSON-RPC framing over the
inherited socket, and ``initialize`` / ``ping`` / ``shutdown``. The environment
methods (``registry_catalog``, ``resolve_composite_state`` …) that call
``build_core`` / the generator registry land in later slices; ``initialize`` here
does **not** build the core yet, so this file stays stdlib-only and the transport
can be proven against any workspace.

Invocation (spec §4/§5)::

    <python> <path>/env_worker.py --socket-fd <n> --workspace <dir>

``--socket-fd`` is the inherited end of a ``socket.socketpair()`` (passed via
``subprocess(pass_fds=...)``); ``stdout``/``stderr`` are for logs, never the
protocol (spec §5).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time
import traceback

PROTOCOL_VERSION = "1.0"
_MAX_FRAME = 64 * 1024 * 1024  # 64 MiB cap (spec §5) — over-cap is an error, not an OOM
_started = time.monotonic()
_workspace = ""


class _MethodError(Exception):
    """A structured JSON-RPC error (spec §9)."""

    def __init__(self, code: int, message: str, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _recv_exact(sock: socket.socket, n: int) -> "bytes | None":
    """Read exactly ``n`` bytes, or ``None`` on EOF (the parent went away)."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _read_frame(sock: socket.socket) -> "dict | None":
    """One length-prefixed JSON frame (uint32 BE length + UTF-8 JSON), or None on EOF."""
    hdr = _recv_exact(sock, 4)
    if hdr is None:
        return None
    (n,) = struct.unpack(">I", hdr)
    if n > _MAX_FRAME:
        raise _MethodError(-32600, f"frame too large: {n} bytes")
    body = _recv_exact(sock, n)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def _write_frame(sock: socket.socket, obj: dict) -> None:
    body = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


def _handle(method: str, params: dict) -> dict:
    """Dispatch one method (spec §11). Slice 1: lifecycle only."""
    if method == "ping":
        return {"ok": True, "uptime_s": time.monotonic() - _started}
    if method == "initialize":
        # Slice 1: handshake only — no build_core yet (that's a later slice, and
        # it keeps this file stdlib-only).
        return {
            "protocol_version": PROTOCOL_VERSION,
            "workspace": _workspace,
            "python": sys.version.split()[0],
            "pid": os.getpid(),
            "capabilities": ["initialize", "ping", "shutdown"],
        }
    if method == "shutdown":
        return {"ok": True}
    raise _MethodError(-32601, f"unknown method: {method!r}")


def _serve(sock: socket.socket) -> None:
    """Serial request loop (spec §8): one request at a time, FIFO."""
    while True:
        req = _read_frame(sock)
        if req is None:  # parent closed the connection
            return
        rid = req.get("id")
        method = req.get("method")
        try:
            result = _handle(method, req.get("params") or {})
            _write_frame(sock, {"jsonrpc": "2.0", "id": rid, "result": result})
            if method == "shutdown":
                return
        except _MethodError as e:
            _write_frame(sock, {"jsonrpc": "2.0", "id": rid, "error": {
                "code": e.code, "message": e.message, "data": e.data}})
        except Exception as e:  # noqa: BLE001 — surface as a structured env error (spec §9)
            _write_frame(sock, {"jsonrpc": "2.0", "id": rid, "error": {
                "code": 2000, "message": str(e),
                "data": {"exc_type": type(e).__name__,
                         "traceback_tail": traceback.format_exc()[-2000:]}}})


def main(argv=None) -> int:
    global _workspace
    parser = argparse.ArgumentParser(prog="env_worker")
    parser.add_argument("--socket-fd", type=int, required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)
    _workspace = args.workspace

    # Wrap the inherited fd as an AF_UNIX stream socket (the socketpair peer).
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, fileno=args.socket_fd)
    try:
        _serve(sock)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
