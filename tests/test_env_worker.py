"""M2 slice 1: the env-worker transport + lifecycle, end to end.

Spawns the real ``env_worker.py`` subprocess over a socketpair and exercises the
JSON-RPC contract (docs/env-worker-protocol.md §5-9). Runs on whatever OS the
suite runs on — so macOS locally and Linux in CI both cover the fd-passing +
framing transport (spec §2, platform support).
"""
import socket
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Slice 3: registry_catalog — build_core + introspection, faithful to the
# existing embedded-subprocess path in registry.build_registry.
# ---------------------------------------------------------------------------
_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_registry_catalog_matches_build_registry(monkeypatch):
    """Strong port check: the worker's registry_catalog reproduces
    build_registry's introspection (name/address/kind/source) exactly."""
    pytest.importorskip("pbg_superpowers")
    from vivarium_workbench.lib import registry

    expected = registry.build_registry(_FIXTURE, bypass_cache=True)
    if expected.get("error"):
        pytest.skip(f"build_registry unavailable in this env: {expected['error']}")

    with EnvWorker(_FIXTURE) as w:
        got = w.call("registry_catalog")
    assert not got.get("error"), got.get("error")

    def _core(entries):
        # compare on the introspection fields (ignore workbench post-processing
        # like emitter is_workspace_default that build_registry adds on top).
        return sorted((p["name"], p["address"], p["kind"], p["source"])
                      for p in entries)

    assert _core(got["processes"]) == _core(expected["processes"])
    assert [t["name"] for t in got["types"]] == [t["name"] for t in expected["types"]]


# ---------------------------------------------------------------------------
# Opt-in real-workspace check: run the worker against a real v2ecoli checkout
# on ITS OWN venv interpreter. Skips unless ../v2ecoli/.venv exists (build it
# with `cd ../v2ecoli && uv sync`). This is the e2e that the minimal fixture
# can't give: a heavy workspace, its own 3.12.12 interpreter, real generators.
# ---------------------------------------------------------------------------
_V2ECOLI = Path(__file__).resolve().parent.parent.parent / "v2ecoli"
_V2ECOLI_VENV = _V2ECOLI / ".venv" / "bin" / "python"


@pytest.mark.skipif(not _V2ECOLI_VENV.is_file(),
                    reason="no ../v2ecoli/.venv (build with `cd ../v2ecoli && uv sync`)")
def test_env_worker_against_real_v2ecoli():
    with EnvWorker(_V2ECOLI, interpreter=str(_V2ECOLI_VENV), timeout=600) as w:
        info = w.call("initialize")
        assert info["python"].startswith("3.12"), info["python"]  # the venv's, not the workbench's

        gens = w.call("list_generators")["generators"]
        assert "v2ecoli.composites.baseline" in gens
        assert len(gens) > 10   # a real, heavy workspace

        cat = w.call("registry_catalog")
        assert not cat.get("error"), cat.get("error")
        assert len(cat["processes"]) > 50 and len(cat["types"]) > 20
        assert "v2ecoli" in cat["workspace_pkgs"]
        assert any(p["source"] == "in_workspace" for p in cat["processes"])
