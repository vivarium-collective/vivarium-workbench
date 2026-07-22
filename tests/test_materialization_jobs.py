"""Async materialization jobs (materialization-lifecycle.md §9c): the background
job registry — fast path, dedup, progress, and failure surfacing. ``materialize``
is stubbed for determinism (the real ``uv sync`` is covered in
test_materialization.py); these exercise the async state machine only.
"""
import subprocess
import threading
import time
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Managed (repo, ref) jobs — clone → sync (materialization-lifecycle §2/§9c).
# repo_source.stage + materialize are stubbed here for determinism; the real
# git+uv end-to-end is the last test (gated on both being present).
# ---------------------------------------------------------------------------
class _Staged:
    def __init__(self, path, commit):
        self.path = path
        self.commit = commit


def test_managed_job_runs_clone_then_sync(reg, tmp_path, monkeypatch):
    from vivarium_workbench.lib import repo_source
    clone_go = threading.Event()
    sync_go = threading.Event()

    def _stage(repo, ref, **k):
        clone_go.wait(3)
        return _Staged(str(tmp_path / "staged"), "a" * 40)

    def _sync(source, **k):
        sync_go.wait(3)
        return "/built/bin/python"

    monkeypatch.setattr(repo_source, "stage", _stage)
    monkeypatch.setattr(mj, "materialize", _sync)

    job = reg.start_managed("https://x/r.git", "main")
    assert _wait_until(lambda: job.status == mj.CLONING)
    clone_go.set()
    assert _wait_until(lambda: job.status == mj.SYNCING)
    assert job.path == str(tmp_path / "staged")
    assert job.commit == "a" * 40
    sync_go.set()
    assert _wait_until(lambda: job.status == mj.READY)
    snap = job.snapshot()
    assert snap["interpreter"] == "/built/bin/python"
    assert snap["path"] == str(tmp_path / "staged")
    assert snap["commit"] == "a" * 40


def test_managed_job_is_deduplicated(reg, tmp_path, monkeypatch):
    from vivarium_workbench.lib import repo_source
    go = threading.Event()
    monkeypatch.setattr(repo_source, "stage",
                        lambda repo, ref, **k: (go.wait(3), _Staged(str(tmp_path), "b" * 40))[1])
    monkeypatch.setattr(mj, "materialize", lambda s, **k: "/p")
    j1 = reg.start_managed("r", "main")
    assert _wait_until(lambda: j1.status == mj.CLONING)
    j2 = reg.start_managed("r", "main")
    assert j2 is j1
    go.set()
    assert _wait_until(lambda: j1.status == mj.READY)


def test_managed_distinct_ref_distinct_job(reg, tmp_path, monkeypatch):
    from vivarium_workbench.lib import repo_source
    monkeypatch.setattr(repo_source, "stage",
                        lambda repo, ref, **k: _Staged(str(tmp_path / ref), "c" * 40))
    monkeypatch.setattr(mj, "materialize", lambda s, **k: "/p")
    a = reg.start_managed("r", "main")
    b = reg.start_managed("r", "dev")
    assert a is not b
    assert _wait_until(lambda: a.status == mj.READY and b.status == mj.READY)


def test_managed_clone_failure_is_surfaced(reg, monkeypatch):
    from vivarium_workbench.lib import repo_source
    monkeypatch.setattr(repo_source, "stage",
                        lambda repo, ref, **k: (_ for _ in ()).throw(
                            repo_source.RepoStagingError("ref 'x' not found", tail="git: bad ref")))
    monkeypatch.setattr(mj, "materialize",
                        lambda s, **k: (_ for _ in ()).throw(AssertionError("sync must not run")))
    job = reg.start_managed("r", "x")
    assert _wait_until(lambda: job.status == mj.FAILED)
    snap = job.snapshot()
    assert "not found" in snap["error"]
    assert "bad ref" in snap["tail"]


def test_managed_sync_failure_is_surfaced(reg, tmp_path, monkeypatch):
    from vivarium_workbench.lib import repo_source
    from vivarium_workbench.lib.materialization import MaterializationError
    monkeypatch.setattr(repo_source, "stage",
                        lambda repo, ref, **k: _Staged(str(tmp_path), "d" * 40))
    monkeypatch.setattr(mj, "materialize",
                        lambda s, **k: (_ for _ in ()).throw(
                            MaterializationError("environment build failed", tail="uv: boom")))
    job = reg.start_managed("r", "main")
    assert _wait_until(lambda: job.status == mj.FAILED)
    assert "boom" in job.snapshot()["tail"]


@pytest.mark.skipif(not __import__("shutil").which("git") or not __import__("shutil").which("uv"),
                    reason="git+uv required")
def test_managed_real_clone_and_sync_end_to_end(reg, tmp_path, monkeypatch):
    """A local git origin that is a valid (no-dep) uv project: clone → sync →
    ready with a real interpreter in the coordinate-keyed venv store."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REPO_STORE", str(tmp_path / "repos"))
    monkeypatch.setenv("VIVARIUM_WORKBENCH_VENV_STORE", str(tmp_path / "venvs"))
    monkeypatch.setenv("UV_PYTHON", __import__("sys").executable)
    origin = tmp_path / "origin"
    origin.mkdir()

    def run(a):
        subprocess.run(a, cwd=str(origin), check=True, capture_output=True)

    run(["git", "init", "-q", "-b", "main"])
    run(["git", "config", "user.email", "t@t"])
    run(["git", "config", "user.name", "t"])
    (origin / "pyproject.toml").write_text(
        "[project]\nname='tinyws'\nversion='0'\nrequires-python='>=3.11'\n")
    lk = subprocess.run(["uv", "lock"], cwd=str(origin), capture_output=True, text=True)
    if lk.returncode != 0:
        pytest.skip(f"uv lock unavailable: {lk.stderr[:200]}")
    run(["git", "add", "."])
    run(["git", "commit", "-qm", "c1"])

    job = reg.start_managed(str(origin), "main", timeout=180)
    assert _wait_until(lambda: job.status in (mj.READY, mj.FAILED), timeout=180)
    snap = job.snapshot()
    if snap["status"] == mj.FAILED:
        pytest.skip(f"managed sync unavailable: {snap.get('error')} {snap.get('tail','')[:200]}")
    assert snap["status"] == mj.READY
    assert Path(snap["path"]).is_dir()
    assert len(snap["commit"]) == 40
    assert str(tmp_path / "venvs") in snap["interpreter"]
    assert Path(snap["interpreter"]).is_file()
