"""Behavioural parity tests for ``lib.workspaces_process_views`` (2 builders).

Each builder is a pure port of a stdlib workspace process-management handler
(``_post_workspaces_start`` / ``_post_workspaces_stop``).  Every test
monkeypatches the module-level seams reached via ``workspaces_process_views``
(``workspace_catalog`` / ``subprocess.Popen`` / ``os.kill`` / ``time``) so NO
real process is ever spawned or killed, NO real ``~/.pbg`` catalog is touched,
and the polls return immediately.  Each asserts the exact ``(body, status)`` the
legacy handlers returned (incl. the Popen spawn argv + the self-stop em-dash
message + the 504 timeout bodies).
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from vivarium_workbench.lib import workspaces_process_views as wp


def _fake_catalog(workspaces=None, running=None, entry=None, **overrides):
    """A stand-in ``workspace_catalog`` module.

    ``workspaces`` → list_workspaces() result; ``running`` → find_running()
    result (callable or value); ``entry`` → find_entry() result (callable or
    value).  Records calls in ``.calls``.
    """
    calls: list[tuple] = []

    def list_workspaces():
        calls.append(("list_workspaces",))
        return workspaces or []

    def find_running(path):
        calls.append(("find_running", path))
        return running(path) if callable(running) else running

    def find_entry(path):
        calls.append(("find_entry", path))
        return entry(path) if callable(entry) else entry

    ns = types.SimpleNamespace(
        list_workspaces=overrides.get("list_workspaces", list_workspaces),
        find_running=overrides.get("find_running", find_running),
        find_entry=overrides.get("find_entry", find_entry),
        calls=calls,
    )
    return ns


def _no_sleep(monkeypatch):
    """Make polls deterministic + instant: time.sleep is a no-op and
    time.monotonic advances 1s per call so any deadline trips after one pass."""
    ticks = iter(range(0, 100000))
    monkeypatch.setattr(wp.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(wp.time, "monotonic", lambda: next(ticks))


# ===========================================================================
# workspaces_start
# ===========================================================================
class TestWorkspacesStart:
    def test_non_absolute_path_400(self, monkeypatch):
        monkeypatch.setattr(wp, "workspace_catalog", object())  # never reached
        assert wp.workspaces_start(Path("/ws_root"), {"path": "relative/x"}) == (
            {"error": "path must be an absolute string"}, 400,
        )

    def test_missing_path_400(self, monkeypatch):
        monkeypatch.setattr(wp, "workspace_catalog", object())
        assert wp.workspaces_start(Path("/ws_root"), {}) == (
            {"error": "path must be an absolute string"}, 400,
        )

    def test_no_workspace_yaml_400(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wp, "workspace_catalog", object())  # not reached
        d = tmp_path / "empty"
        d.mkdir()
        assert wp.workspaces_start(Path("/ws_root"), {"path": str(d)}) == (
            {"error": "not a workspace (no workspace.yaml)"}, 400,
        )

    def test_not_in_catalog_400(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "workspace.yaml").write_text("name: ws\n")
        monkeypatch.setattr(wp, "workspace_catalog", _fake_catalog(workspaces=[]))
        assert wp.workspaces_start(Path("/ws_root"), {"path": str(d)}) == (
            {"error": "workspace not in catalog — Add it first"}, 400,
        )

    def test_idempotent_live_200(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "workspace.yaml").write_text("name: ws\n")
        target = str(d.resolve())
        cat = _fake_catalog(
            workspaces=[{"path": target}],
            running={"url": "http://127.0.0.1:8001", "pid": 4242},
        )
        monkeypatch.setattr(wp, "workspace_catalog", cat)
        # Popen must NEVER be called on the idempotent path.
        monkeypatch.setattr(
            wp.subprocess, "Popen",
            lambda *a, **k: pytest.fail("Popen must not be called when live"),
        )
        assert wp.workspaces_start(Path("/ws_root"), {"path": target}) == (
            {"url": "http://127.0.0.1:8001", "pid": 4242}, 200,
        )

    def test_happy_spawn_then_register_200(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "workspace.yaml").write_text("name: ws\n")
        target = str(d.resolve())
        # find_running: None first (idempotent check + nothing yet), then a live
        # entry once the spawned child "registers".
        seq = [None, None, {"url": "http://127.0.0.1:9001", "pid": 777}]

        def _running(_path):
            return seq.pop(0) if seq else {"url": "http://127.0.0.1:9001", "pid": 777}

        cat = _fake_catalog(workspaces=[{"path": target}], running=_running)
        monkeypatch.setattr(wp, "workspace_catalog", cat)

        spawned = {}

        def _fake_popen(argv, **kwargs):
            spawned["argv"] = argv
            spawned["kwargs"] = kwargs
            return object()

        monkeypatch.setattr(wp.subprocess, "Popen", _fake_popen)
        _no_sleep(monkeypatch)

        body, status = wp.workspaces_start(Path("/ws_root"), {"path": target})
        assert (body, status) == (
            {"url": "http://127.0.0.1:9001", "pid": 777}, 200,
        )
        # The spawn argv + kwargs reproduce the legacy handler byte-for-byte.
        assert spawned["argv"] == [
            wp.sys.executable, "-m", "vivarium_workbench.cli",
            "serve", "--workspace", str(Path(target).expanduser().resolve()),
        ]
        kw = spawned["kwargs"]
        assert kw["stdin"] is wp.subprocess.DEVNULL
        assert kw["start_new_session"] is True
        assert kw["close_fds"] is True
        assert kw["cwd"] == str(Path(target).expanduser().resolve())
        # stdout/stderr are the opened log file handle.
        assert kw["stdout"] is kw["stderr"]
        # The start.log was created under <target>/.pbg/server/.
        assert (d / ".pbg" / "server" / "start.log").is_file()

    def test_timeout_504(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        (d / "workspace.yaml").write_text("name: ws\n")
        target = str(d.resolve())
        cat = _fake_catalog(workspaces=[{"path": target}], running=None)
        monkeypatch.setattr(wp, "workspace_catalog", cat)
        monkeypatch.setattr(wp.subprocess, "Popen", lambda *a, **k: object())
        _no_sleep(monkeypatch)

        log_path = d / ".pbg" / "server" / "start.log"
        assert wp.workspaces_start(Path("/ws_root"), {"path": target}) == (
            {
                "error": "start_timeout",
                "log_path": str(log_path),
                "hint": f"tail {log_path}",
            },
            504,
        )


# ===========================================================================
# workspaces_stop
# ===========================================================================
class TestWorkspacesStop:
    def test_non_absolute_path_400(self, monkeypatch):
        monkeypatch.setattr(wp, "workspace_catalog", object())
        assert wp.workspaces_stop(Path("/ws_root"), {"path": "x"}) == (
            {"error": "path must be an absolute string"}, 400,
        )

    def test_not_in_catalog_400(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        monkeypatch.setattr(wp, "workspace_catalog", _fake_catalog(workspaces=[]))
        assert wp.workspaces_stop(Path("/ws_root"), {"path": str(d)}) == (
            {"error": "workspace not in catalog"}, 400,
        )

    def test_self_stop_400_uses_running_pid(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        target = d.resolve()
        cat = _fake_catalog(
            workspaces=[{"path": str(target)}],
            running={"url": "http://x", "pid": 31337},
        )
        monkeypatch.setattr(wp, "workspace_catalog", cat)
        # ws_root == target → self-stop; pid comes from the running entry.
        assert wp.workspaces_stop(target, {"path": str(target)}) == (
            {"error": "refusing to stop self — use the terminal: kill 31337"}, 400,
        )

    def test_self_stop_400_falls_back_to_getpid(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        target = d.resolve()
        cat = _fake_catalog(workspaces=[{"path": str(target)}], running=None)
        monkeypatch.setattr(wp, "workspace_catalog", cat)
        monkeypatch.setattr(wp.os, "getpid", lambda: 9999)
        assert wp.workspaces_stop(target, {"path": str(target)}) == (
            {"error": "refusing to stop self — use the terminal: kill 9999"}, 400,
        )

    def test_not_running_400(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        target = d.resolve()
        cat = _fake_catalog(workspaces=[{"path": str(target)}], running=None)
        monkeypatch.setattr(wp, "workspace_catalog", cat)
        assert wp.workspaces_stop(Path("/other_root"), {"path": str(target)}) == (
            {"error": "not running"}, 400,
        )

    def test_happy_sigterm_then_deregister_200(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        target = d.resolve()
        cat = _fake_catalog(
            workspaces=[{"path": str(target)}],
            running={"pid": 555},
            entry=None,  # deregistered immediately
        )
        monkeypatch.setattr(wp, "workspace_catalog", cat)
        killed = {}
        monkeypatch.setattr(
            wp.os, "kill",
            lambda pid, sig: killed.update(pid=pid, sig=sig),
        )
        _no_sleep(monkeypatch)
        assert wp.workspaces_stop(Path("/other_root"), {"path": str(target)}) == (
            {"ok": True}, 200,
        )
        assert killed == {"pid": 555, "sig": wp.signal.SIGTERM}

    def test_process_lookup_error_200(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        target = d.resolve()
        cat = _fake_catalog(workspaces=[{"path": str(target)}], running={"pid": 42})
        monkeypatch.setattr(wp, "workspace_catalog", cat)

        def _raise(pid, sig):
            raise ProcessLookupError()

        monkeypatch.setattr(wp.os, "kill", _raise)
        assert wp.workspaces_stop(Path("/other_root"), {"path": str(target)}) == (
            {"ok": True}, 200,
        )

    def test_timeout_504(self, monkeypatch, tmp_path):
        d = tmp_path / "ws"
        d.mkdir()
        target = d.resolve()
        cat = _fake_catalog(
            workspaces=[{"path": str(target)}],
            running={"pid": 808},
            entry={"pid": 808},  # never deregisters
        )
        monkeypatch.setattr(wp, "workspace_catalog", cat)
        monkeypatch.setattr(wp.os, "kill", lambda *a, **k: None)
        _no_sleep(monkeypatch)
        assert wp.workspaces_stop(Path("/other_root"), {"path": str(target)}) == (
            {
                "error": "stop_timeout",
                "hint": "PID 808 still alive; SIGKILL it manually if stuck",
            },
            504,
        )
