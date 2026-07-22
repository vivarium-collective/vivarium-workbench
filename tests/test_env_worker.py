"""M2 slice 1: the env-worker transport + lifecycle, end to end.

Spawns the real ``env_worker.py`` subprocess over a socketpair and exercises the
JSON-RPC contract (docs/env-worker-protocol.md §5-9). Runs on whatever OS the
suite runs on — so macOS locally and Linux in CI both cover the fd-passing +
framing transport (spec §2, platform support).
"""
import socket

import pytest

from vivarium_workbench.lib.env_worker_client import (
    EnvWorker,
    EnvWorkerError,
    EnvWorkerUnavailable,
)


def test_initialize_handshake(tmp_path):
    with EnvWorker(tmp_path) as w:
        info = w.call("initialize")
        assert info["protocol_version"] == "1.0"
        assert info["workspace"] == str(tmp_path)
        assert "ping" in info["capabilities"]
        assert info["pid"] > 0


def test_ping(tmp_path):
    with EnvWorker(tmp_path) as w:
        r1 = w.call("ping")
        assert r1["ok"] is True
        assert r1["uptime_s"] >= 0
        # serial, multiple calls on one worker
        r2 = w.call("ping")
        assert r2["uptime_s"] >= r1["uptime_s"]


def test_unknown_method_is_structured_error(tmp_path):
    with EnvWorker(tmp_path) as w:
        with pytest.raises(EnvWorkerError) as ei:
            w.call("does_not_exist")
        assert ei.value.code == -32601
        # the worker stays alive after an error (spec §9: env error != crash)
        assert w.call("ping")["ok"] is True


def test_crash_is_reported_not_hung(tmp_path):
    w = EnvWorker(tmp_path)
    try:
        assert w.call("ping")["ok"] is True
        w._proc.kill()          # simulate a worker crash
        w._proc.wait(timeout=5)
        with pytest.raises(EnvWorkerUnavailable):
            w.call("ping")      # EOF -> unavailable, never a hang
    finally:
        w.close()


def test_close_is_clean_and_idempotent(tmp_path):
    w = EnvWorker(tmp_path)
    assert w.call("ping")["ok"] is True
    w.close()
    assert not w.alive()
    w.close()  # idempotent


def test_timeout_surfaces_as_unavailable(tmp_path):
    """A worker that never replies must raise, not hang, once the timeout elapses."""
    w = EnvWorker(tmp_path, timeout=0.5)
    try:
        # Suspend the worker so it stops reading/replying, then a call must time out.
        import os
        import signal
        os.kill(w._proc.pid, signal.SIGSTOP)
        with pytest.raises(EnvWorkerUnavailable):
            w.call("ping")
        os.kill(w._proc.pid, signal.SIGCONT)
    finally:
        w._proc.kill()
        w.close()


def test_transport_is_socketpair_no_filesystem(tmp_path):
    """The channel is a socketpair (spec §5) — no named UDS path to leak/limit."""
    w = EnvWorker(tmp_path)
    try:
        assert w._sock.family == socket.AF_UNIX
        assert w.call("ping")["ok"] is True
    finally:
        w.close()


# ---------------------------------------------------------------------------
# Slice 2: list_generators — the worker holds the workspace env in ITS process
# ---------------------------------------------------------------------------
def _make_ws(root, pkg, gen_name):
    """A minimal workspace whose package registers one @composite_generator."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(f"name: {pkg}\npackage_path: {pkg}\n")
    comp = root / pkg / "composites"
    comp.mkdir(parents=True)
    (root / pkg / "__init__.py").write_text("from . import composites\n")
    (comp / "__init__.py").write_text(
        "from pbg_superpowers.composite_generator import composite_generator\n"
        f"@composite_generator(name='{gen_name}', description='')\n"
        "def g(core=None):\n    return {}\n"
    )
    return root


def test_list_generators_finds_the_workspace_package_generator(tmp_path):
    pytest.importorskip("pbg_superpowers")
    ws = _make_ws(tmp_path / "wsA", "pbg_wa", "gen_a")
    with EnvWorker(ws) as w:
        gens = w.call("list_generators")["generators"]
        assert "pbg_wa.composites.gen_a" in gens


def test_two_workers_have_isolated_registries(tmp_path):
    """The load-bearing M2 property: process isolation. Each worker holds only
    its own workspace's generators — one process cannot do this in-place."""
    pytest.importorskip("pbg_superpowers")
    a = _make_ws(tmp_path / "a", "pbg_iso_a", "gen_a")
    b = _make_ws(tmp_path / "b", "pbg_iso_b", "gen_b")
    with EnvWorker(a) as wa, EnvWorker(b) as wb:
        ga = wa.call("list_generators")["generators"]
        gb = wb.call("list_generators")["generators"]
        assert "pbg_iso_a.composites.gen_a" in ga
        assert "pbg_iso_b.composites.gen_b" in gb
        assert "pbg_iso_b.composites.gen_b" not in ga   # A never sees B's env
        assert "pbg_iso_a.composites.gen_a" not in gb   # and vice versa


def test_list_generators_tolerates_a_workspace_with_no_package(tmp_path):
    pytest.importorskip("pbg_superpowers")
    (tmp_path / "workspace.yaml").write_text("name: bare\n")
    with EnvWorker(tmp_path) as w:
        assert isinstance(w.call("list_generators")["generators"], list)  # no crash
