"""Per-session env preparation (materialization-lifecycle §9c wiring): eager
in-place READY, managed → materializing → ready, and status polling. The managed
branch stubs the job registry's `materialize` (the real `uv sync` lives in
test_materialization.py)."""
import threading
import time

import pytest

from vivarium_workbench.lib import materialization_jobs as mj
from vivarium_workbench.lib import session_env as se


@pytest.fixture(autouse=True)
def _clean():
    se.clear()
    yield
    se.clear()


def _project(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    (root / "uv.lock").write_text("# L\n")
    return root


def _wait_ready(key, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = se.status(key)
        if s and s["status"] == se.READY:
            return True
        time.sleep(0.01)
    return False


# -- in-place (the live path today) ------------------------------------------
def test_prepare_in_place_is_ready_with_interpreter(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    st = se.prepare("k1", ws)
    assert st["status"] == se.READY
    assert st["managed"] is False
    assert st["interpreter"]                       # sys.executable — no .venv


def test_prepare_in_place_prefers_the_checkout_venv(tmp_path):
    ws = tmp_path / "ws"
    b = ws / ".venv" / "bin"
    b.mkdir(parents=True)
    py = b / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    st = se.prepare("k", ws)
    assert st["interpreter"] == str(py)            # §2a: use the dev's own venv


# -- managed (wired, dormant until the clone seam) ---------------------------
def test_prepare_managed_goes_materializing_then_ready(tmp_path, monkeypatch):
    reg = mj.MaterializationRegistry()
    monkeypatch.setattr(mj, "get_registry", lambda: reg)
    monkeypatch.setattr(mj, "cached_interpreter", lambda c: None)
    release = threading.Event()
    monkeypatch.setattr(mj, "materialize",
                        lambda source, **k: (release.wait(3), "/built/bin/python")[1])

    proj = _project(tmp_path / "p")
    st = se.prepare("k", proj, managed=True)
    assert st["managed"] is True
    assert st["status"] in (se.MATERIALIZING, se.READY)
    assert "coordinate" in st
    release.set()
    assert _wait_ready("k")
    assert se.status("k")["interpreter"] == "/built/bin/python"


def test_prepare_managed_failure_is_surfaced(tmp_path, monkeypatch):
    from vivarium_workbench.lib.materialization import MaterializationError
    reg = mj.MaterializationRegistry()
    monkeypatch.setattr(mj, "get_registry", lambda: reg)
    monkeypatch.setattr(mj, "cached_interpreter", lambda c: None)
    monkeypatch.setattr(mj, "materialize",
                        lambda source, **k: (_ for _ in ()).throw(
                            MaterializationError("environment build failed", tail="uv: boom")))
    proj = _project(tmp_path / "p")
    se.prepare("k", proj, managed=True)
    end = time.monotonic() + 5
    while time.monotonic() < end and se.status("k")["status"] != se.FAILED:
        time.sleep(0.01)
    s = se.status("k")
    assert s["status"] == se.FAILED
    assert "environment build failed" in s["error"]
    assert "boom" in s["tail"]


# -- status polling ----------------------------------------------------------
def test_status_none_for_unknown_or_missing_key(tmp_path):
    assert se.status("never-prepared") is None
    assert se.status(None) is None


def test_status_reflects_prepared_in_place(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    se.prepare("k", ws)
    assert se.status("k")["status"] == se.READY


# -- managed (repo, ref) preparation -----------------------------------------
class _Staged:
    def __init__(self, path, commit):
        self.path = path
        self.commit = commit


def test_prepare_managed_records_repo_ref_and_polls_to_ready(tmp_path, monkeypatch):
    from vivarium_workbench.lib import repo_source
    reg = mj.MaterializationRegistry()
    monkeypatch.setattr(mj, "get_registry", lambda: reg)
    go = threading.Event()
    monkeypatch.setattr(repo_source, "stage",
                        lambda repo, ref, **k: (go.wait(3), _Staged(str(tmp_path / "s"), "e" * 40))[1])
    monkeypatch.setattr(mj, "materialize", lambda s, **k: "/built/bin/python")

    st = se.prepare_managed("k", "https://x/r.git", "main")
    assert st["managed"] is True
    assert st["repo"] == "https://x/r.git" and st["ref"] == "main"
    assert st["status"] == se.MATERIALIZING
    go.set()
    assert _wait_ready("k")
    final = se.status("k")
    assert final["interpreter"] == "/built/bin/python"
    assert final["path"] == str(tmp_path / "s")
    assert final["commit"] == "e" * 40
