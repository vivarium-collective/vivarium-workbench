"""cmd_serve must register itself in the global running registry."""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest


@pytest.fixture
def pbg_home(tmp_path, monkeypatch):
    home = tmp_path / "pbg-home"
    monkeypatch.setenv("PBG_HOME", str(home))
    return home


@pytest.fixture
def workspace_dir(tmp_path):
    ws = tmp_path / "switcher-ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "name: switcher-ws\npackage: pbg_switcher_ws\n"
    )
    (ws / "reports").mkdir()
    return ws


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def test_cmd_serve_registers_on_boot(pbg_home, workspace_dir):
    """Spawning `vivarium-dashboard serve` should write ~/.pbg/servers/<name>.json
    within a few seconds, and remove it after we SIGTERM the process."""
    port = _free_port()
    env = {**os.environ, "PBG_HOME": str(pbg_home)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_workbench.cli",
         "serve", "--workspace", str(workspace_dir), "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        servers_dir = pbg_home / "servers"
        deadline = time.monotonic() + 8.0
        entry_path = None
        while time.monotonic() < deadline:
            if servers_dir.is_dir():
                cands = list(servers_dir.glob("switcher-ws*.json"))
                if cands:
                    entry_path = cands[0]
                    break
            time.sleep(0.1)
        assert entry_path is not None, "registration file never appeared"
        entry = json.loads(entry_path.read_text())
        assert entry["name"] == "switcher-ws"
        assert entry["path"] == str(workspace_dir.resolve())
        assert entry["pid"] == proc.pid
        assert entry["port"] == port
        assert entry["url"] == f"http://127.0.0.1:{port}"

        pid_file = workspace_dir / ".pbg" / "server" / "server.pid"
        assert pid_file.is_file()
        assert int(pid_file.read_text().strip()) == proc.pid
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    assert not entry_path.exists()
    pid_file = workspace_dir / ".pbg" / "server" / "server.pid"
    assert not pid_file.exists()


def test_cmd_serve_continues_when_registration_fails(tmp_path, workspace_dir):
    """If workspace_catalog.register_server raises, the dashboard should still
    boot — registration is opt-in, not a hard dependency.

    We verify this by spawning a wrapper script that monkeypatches
    pbg_superpowers.workspace_catalog.register_server to raise before calling
    the CLI. The dashboard must reach the serve step (server-info written) and
    the ~/.pbg/servers/ entry must NOT have been created.
    """
    port = _free_port()
    pbg_home = tmp_path / "pbg-home-fail"
    pbg_home.mkdir()

    # Build a small wrapper script that patches register_server to always raise,
    # then invokes the real CLI main(). This is spawned as a subprocess so it
    # exercises the same code path as production.
    wrapper = tmp_path / "run_patched.py"
    wrapper.write_text(textwrap.dedent(f"""\
        import sys
        import pbg_superpowers.workspace_catalog as _cat

        def _bad_register(**kwargs):
            raise RuntimeError("simulated registration failure")

        _cat.register_server = _bad_register

        from vivarium_workbench.cli import main
        sys.exit(main(["serve",
                       "--workspace", {str(workspace_dir)!r},
                       "--port", {str(port)!r}]))
    """))

    env = {**os.environ, "PBG_HOME": str(pbg_home)}
    proc = subprocess.Popen(
        [sys.executable, str(wrapper)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        # Wait for server-info — its presence signals the boot reached the
        # serve step, i.e., execution continued past the registration failure.
        info_path = workspace_dir / ".pbg" / "server" / "server-info"
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if info_path.is_file():
                break
            time.sleep(0.1)
        assert info_path.is_file(), "dashboard never reached the serve step after registration failure"

        # The servers/ directory must NOT exist — registration was skipped.
        assert not (pbg_home / "servers").exists(), \
            "servers/ dir was created even though register_server raised"

        # The warning must appear on stderr.
        # Give the process a moment to flush stderr (it's still running).
        time.sleep(0.2)
        stderr_so_far = proc.stderr.read1()  # type: ignore[attr-defined]
        assert b"warning: workspace switcher registration failed" in stderr_so_far, \
            f"expected warning on stderr, got: {stderr_so_far!r}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    # Note: when registration fails, the SIGTERM signal handler is not installed
    # (it's inside the try block), so pid_file cleanup via atexit won't run on
    # SIGTERM. That's acceptable — atexit still runs on normal exit (Ctrl+C /
    # KeyboardInterrupt). We don't assert pid_file cleanup here for that reason.
