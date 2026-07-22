"""Async materialization jobs (materialization-lifecycle.md §9c): the background
job registry — fast path, dedup, progress, and failure surfacing. ``materialize``
is stubbed for determinism (the real ``uv sync`` is covered in
test_materialization.py); these exercise the async state machine only.
"""
import threading
import time

import pytest

from vivarium_workbench.lib import materialization_jobs as mj
from vivarium_workbench.lib.materialization import MaterializationError


def _wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _project(root, *, lock="L"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    (root / "uv.lock").write_text(f"# {lock}\n")
    return root


@pytest.fixture
def reg():
    return mj.MaterializationRegistry()


def test_fast_path_ready_immediately_when_cached(reg, tmp_path, monkeypatch):
    """A coordinate whose venv already exists is READY at once — no uv sync."""
    src = _project(tmp_path / "ws")
    monkeypatch.setattr(mj, "cached_interpreter", lambda coord: "/cached/bin/python")

    def _boom(*a, **k):
        raise AssertionError("materialize must not run on the fast path")

    monkeypatch.setattr(mj, "materialize", _boom)
    job = reg.start(src)
    assert job.status == mj.READY
    assert job.snapshot()["interpreter"] == "/cached/bin/python"


def test_async_success_transitions_to_ready(reg, tmp_path, monkeypatch):
    src = _project(tmp_path / "ws")
    monkeypatch.setattr(mj, "cached_interpreter", lambda coord: None)
    monkeypatch.setattr(mj, "materialize", lambda source, **k: "/built/bin/python")

    job = reg.start(src)
    assert _wait_until(lambda: job.status == mj.READY)
    assert job.snapshot()["interpreter"] == "/built/bin/python"


def test_failure_surfaces_error_and_tail(reg, tmp_path, monkeypatch):
    src = _project(tmp_path / "ws")
    monkeypatch.setattr(mj, "cached_interpreter", lambda coord: None)

    def _fail(source, **k):
        raise MaterializationError("environment build failed", tail="uv: could not resolve foo")

    monkeypatch.setattr(mj, "materialize", _fail)
    job = reg.start(src)
    assert _wait_until(lambda: job.status == mj.FAILED)
    snap = job.snapshot()
    assert snap["status"] == "failed"
    assert "environment build failed" in snap["error"]
    assert "could not resolve foo" in snap["tail"]


def test_inflight_start_is_deduplicated(reg, tmp_path, monkeypatch):
    """Two starts of the same coordinate while syncing share ONE job (§5)."""
    src = _project(tmp_path / "ws")
    monkeypatch.setattr(mj, "cached_interpreter", lambda coord: None)
    release = threading.Event()

    def _block(source, **k):
        release.wait(timeout=5)
        return "/built/bin/python"

    monkeypatch.setattr(mj, "materialize", _block)
    job1 = reg.start(src)
    assert _wait_until(lambda: job1.status == mj.SYNCING)
    job2 = reg.start(src)           # attaches to the in-flight job
    assert job2 is job1
    assert reg.size() == 1
    release.set()
    assert _wait_until(lambda: job1.status == mj.READY)


def test_distinct_sources_get_distinct_jobs(reg, tmp_path, monkeypatch):
    a = _project(tmp_path / "a", lock="a")
    b = _project(tmp_path / "b", lock="b")
    monkeypatch.setattr(mj, "cached_interpreter", lambda coord: None)
    monkeypatch.setattr(mj, "materialize", lambda source, **k: "/x/bin/python")
    ja = reg.start(a)
    jb = reg.start(b)
    assert ja is not jb
    assert _wait_until(lambda: ja.status == mj.READY and jb.status == mj.READY)
    assert reg.size() == 2


def test_failed_coordinate_is_retried_on_explicit_start(reg, tmp_path, monkeypatch):
    """A FAILED job is not retried in a loop, but an explicit start retries (§4)."""
    src = _project(tmp_path / "ws")
    monkeypatch.setattr(mj, "cached_interpreter", lambda coord: None)
    monkeypatch.setattr(mj, "materialize",
                        lambda source, **k: (_ for _ in ()).throw(MaterializationError("boom")))
    job1 = reg.start(src)
    assert _wait_until(lambda: job1.status == mj.FAILED)

    monkeypatch.setattr(mj, "materialize", lambda source, **k: "/fixed/bin/python")
    job2 = reg.start(src)           # retry → a fresh job
    assert job2 is not job1
    assert _wait_until(lambda: job2.status == mj.READY)


def test_status_for_unknown_is_none(reg, tmp_path):
    src = _project(tmp_path / "ws")
    assert reg.status_for(src) is None


def test_status_for_reflects_the_job(reg, tmp_path, monkeypatch):
    src = _project(tmp_path / "ws")
    monkeypatch.setattr(mj, "cached_interpreter", lambda coord: "/c/bin/python")
    monkeypatch.setattr(mj, "materialize", lambda source, **k: "/c/bin/python")
    reg.start(src)
    snap = reg.status_for(src)
    assert snap is not None and snap["status"] == mj.READY


def test_get_registry_is_singleton():
    assert mj.get_registry() is mj.get_registry()
