"""Tests for the G5 worktree-aware cycle runner (`lib/cycle_runner.run`)."""
import sys

from vivarium_workbench.lib.cycle_runner import run, _study_slug_from_command

PY = sys.executable


def test_run_ok_result_shape(tmp_path):
    res = run(f"{PY} -c \"print('hello')\"", tmp_path)
    assert res["ok"] is True
    assert res["returncode"] == 0
    assert "hello" in res["tail"]
    assert res["run_id"] is None  # no runs.db in this bare workspace


def test_nonzero_returncode_is_not_ok(tmp_path):
    res = run(f"{PY} -c \"import sys; sys.exit(3)\"", tmp_path)
    assert res["ok"] is False
    assert res["returncode"] == 3
    assert res["run_id"] is None


def test_worktree_prepended_to_pythonpath(tmp_path):
    wt = tmp_path / "build-worktree"
    wt.mkdir()
    cmd = f"{PY} -c \"import os; print(os.environ.get('PYTHONPATH',''))\""
    res = run(cmd, tmp_path, worktree=wt)
    assert res["ok"] is True
    # the worktree is the FIRST entry on PYTHONPATH, so its composite resolves first
    assert res["tail"].strip().split(":")[0] == str(wt.resolve())


def test_no_worktree_leaves_pythonpath_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/some/existing/path")
    cmd = f"{PY} -c \"import os; print(os.environ.get('PYTHONPATH',''))\""
    res = run(cmd, tmp_path)  # worktree=None
    assert res["tail"].strip() == "/some/existing/path"


def test_timeout_reported(tmp_path):
    res = run(f"{PY} -c \"import time; time.sleep(5)\"", tmp_path, timeout=1)
    assert res["ok"] is False
    assert res["returncode"] == 124
    assert "timeout" in res["tail"].lower()


def test_slug_parsed_from_run_study_command():
    assert _study_slug_from_command("vivarium-workbench run study s01-birth --workspace /ws") == "s01-birth"
    # compound seed-then-run (branch) — the run-study tail still yields the slug
    assert _study_slug_from_command(
        "viva-study seed-from-followup parent child && vivarium-workbench run study child --workspace /ws"
    ) == "child"
    assert _study_slug_from_command("echo no-study-here") is None
