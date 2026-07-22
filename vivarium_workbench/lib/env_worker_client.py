"""Env-worker client — the workbench side of the ``EnvironmentResolver`` local
transport.

Spawns the env worker (``vivarium_workbench/env_worker.py``) over a
``socket.socketpair()`` and speaks the length-prefixed JSON-RPC of
``docs/env-worker-protocol.md``. The HTTP process holds one ``EnvWorker`` per
session (a later slice wires it into ``WorkspaceContext``); here it is the
standalone, tested transport.

**Slice 1 scope:** transport + lifecycle (``spawn`` → ``call`` → ``close``),
crash/EOF handling, timeouts. The per-session pooling (protocol §17), the venv
interpreter selection (workspace-store §8), and the environment methods land in
later slices. ``interpreter`` defaults to the current Python so the transport is
provable today; it becomes the workspace venv's interpreter once
``EnvironmentResolver`` materializes venvs.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

# The worker program, shipped in the package (spec §4) and located by path so the
# workspace venv needs no vivarium-workbench dependency.
_WORKER_PATH = str(Path(__file__).resolve().parent.parent / "env_worker.py")
_MAX_FRAME = 64 * 1024 * 1024


class EnvWorkerError(Exception):
    """A structured error returned by the worker (spec §9, environment error)."""

    def __init__(self, message: str, *, code: int | None = None, data=None):
        super().__init__(message)
        self.code = code
        self.data = data


class EnvWorkerUnavailable(EnvWorkerError):
    """The worker crashed / closed the connection / timed out (spec §9)."""


class EnvWorker:
    """A live connection to one env-worker subprocess. Serial (spec §8)."""

    def __init__(self, workspace: Path | str, *, interpreter: str | None = None,
                 log_path: Path | str | None = None, timeout: float = 60.0):
        self.workspace = str(workspace)
        self.timeout = timeout
        interpreter = interpreter or sys.executable

        parent, child = socket.socketpair()
        os.set_inheritable(child.fileno(), True)   # portable POSIX fd-passing (spec §5)
        self._log = open(log_path, "ab") if log_path else subprocess.DEVNULL
        try:
            self._proc = subprocess.Popen(
                [interpreter, _WORKER_PATH,
                 "--socket-fd", str(child.fileno()),
                 "--workspace", self.workspace],
                pass_fds=[child.fileno()],
                stdout=self._log, stderr=self._log,
            )
        finally:
            child.close()  # the child holds its own inherited copy
        parent.settimeout(timeout)
        self._sock = parent
        self._id = 0
        self._lock = threading.Lock()

    # -- protocol -----------------------------------------------------------
    def call(self, method: str, params: dict | None = None) -> Any:
        """Send one request, return its ``result`` (or raise on error). Serial:
        holds the lock so the next frame read is unambiguously this call's reply."""
        with self._lock:
            self._id += 1
            rid = self._id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params or {}})
            resp = self._recv()
            if resp is None:
                raise EnvWorkerUnavailable("worker closed the connection")
            if resp.get("id") != rid:
                raise EnvWorkerUnavailable(
                    f"protocol desync: got id {resp.get('id')}, wanted {rid}")
            if "error" in resp:
                e = resp["error"] or {}
                raise EnvWorkerError(e.get("message", "worker error"),
                                     code=e.get("code"), data=e.get("data"))
            return resp.get("result")

    def close(self) -> None:
        """Graceful shutdown, then reap. Idempotent."""
        try:
            self.call("shutdown")
        except EnvWorkerError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._terminate()
        if not isinstance(self._log, int):   # a real file, not subprocess.DEVNULL
            try:
                self._log.close()
            except OSError:
                pass

    def _terminate(self) -> None:
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)

    def alive(self) -> bool:
        return self._proc.poll() is None

    # -- framing (spec §5) --------------------------------------------------
    def _send(self, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        try:
            self._sock.sendall(struct.pack(">I", len(body)) + body)
        except (OSError, socket.timeout) as e:
            raise EnvWorkerUnavailable(f"send failed: {e}")

    def _recv(self) -> dict | None:
        hdr = self._recv_exact(4)
        if hdr is None:
            return None
        (n,) = struct.unpack(">I", hdr)
        if n > _MAX_FRAME:
            raise EnvWorkerUnavailable(f"reply frame too large: {n} bytes")
        body = self._recv_exact(n)
        if body is None:
            return None
        return json.loads(body.decode("utf-8"))

    def _recv_exact(self, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except socket.timeout:
                raise EnvWorkerUnavailable(f"worker timed out after {self.timeout}s")
            except OSError:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    # -- context manager ----------------------------------------------------
    def __enter__(self) -> "EnvWorker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
