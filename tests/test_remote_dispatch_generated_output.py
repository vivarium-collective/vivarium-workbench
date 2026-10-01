"""A remote dispatch installs the workspace from git, so the tree must be clean and HEAD pushed — but the
workbench's *own* generated output (the rendered report assets and registry catalog that ``serve`` rewrites on every
start) is not workspace code and must not make a clean workspace look dirty.

Real here: git (a real repo cloned from a real bare remote) and the real guards. Nothing is stubbed.
"""
import subprocess
from pathlib import Path

import pytest

from vivarium_workbench.lib import remote_run


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                        "HOME": str(cwd)})


@pytest.fixture
def ws(tmp_path) -> Path:
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare))
    work = tmp_path / "ws"
    _git(tmp_path, "clone", "-q", str(bare), str(work))
    (work / "reports" / "assets").mkdir(parents=True)
    (work / ".pbg" / "registry-catalog").mkdir(parents=True)
    (work / "workspace.yaml").write_text("name: ws\n")
    (work / "pyproject.toml").write_text("[project]\nname='ws'\n")
    (work / "reports" / "assets" / "walkthrough.js").write_text("old\n")
    (work / "reports" / "index.html").write_text("<old>\n")
    (work / "reports" / "BIOMD1_compare.html").write_text("<science>\n")          # a committed result, NOT generated
    (work / ".pbg" / "registry-catalog" / "c.json").write_text("{}\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "origin", "HEAD:main")
    _git(work, "fetch", "-q", "origin")
    return work


def test_a_clean_pushed_workspace_passes(ws):
    assert remote_run.remote_dispatch_preflight(ws)["ok"] is True


def test_what_serve_regenerates_does_not_make_the_workspace_dirty(ws):
    (ws / "reports" / "assets" / "walkthrough.js").write_text("new render\n")         # tracked, rewritten
    (ws / "reports" / "assets" / "chat.js").write_text("untracked new asset\n")       # untracked, new
    (ws / "reports" / "index.html").write_text("<new>\n")
    (ws / ".pbg" / "registry-catalog" / "c.json").write_text('{"n": 2}\n')
    (ws / ".pbg" / "server").mkdir(parents=True)
    (ws / ".pbg" / "server" / "server-info").write_text("{}\n")
    pf = remote_run.remote_dispatch_preflight(ws)
    assert pf["ok"] is True, pf
    assert remote_run.git_pip_url(ws)                                                # the install URL path agrees


@pytest.mark.parametrize("path", ["pyproject.toml", "workspace.yaml", "reports/BIOMD1_compare.html"])
def test_a_real_change_still_blocks_the_dispatch(ws, path):
    (ws / path).write_text("changed\n")
    pf = remote_run.remote_dispatch_preflight(ws)
    assert pf["ok"] is False and pf["reason"] == "dirty" and path in pf["dirty_files"], pf
    with pytest.raises(RuntimeError):
        remote_run.git_pip_url(ws)


def test_a_new_untracked_source_file_still_blocks_the_dispatch(ws):
    (ws / "newmodule.py").write_text("x = 1\n")
    assert remote_run.remote_dispatch_preflight(ws)["reason"] == "dirty"


# -- the workbench's run records and per-session state are generated too (a dispatch must not block the next one) --


@pytest.mark.parametrize("path", [
    ".pbg/runs.jsonl",                              # the run registry the workbench appends to
    "studies/s1/runs.db",                           # a study's run database
    "investigations/i1/runs.db",
    "studies/s1/runs.db-wal",                       # SQLite sidecars
    ".pbg/composite-state-cache/c.json",
    ".pbg/loom-layouts/l.json",
    ".pbg/ai-actions.jsonl",                        # the chat's audit log
])
def test_run_records_and_session_state_do_not_make_the_workspace_dirty(ws, path):
    f = ws / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("generated\n")
    pf = remote_run.remote_dispatch_preflight(ws)
    assert pf["ok"] is True, pf
    assert remote_run.git_pip_url(ws)


def test_a_tracked_run_database_that_was_rewritten_does_not_block_either(ws):
    db = ws / "studies" / "s1" / "runs.db"
    db.parent.mkdir(parents=True)
    db.write_text("v1\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "commit a run db")
    _git(ws, "push", "-q", "origin", "HEAD:main")
    _git(ws, "fetch", "-q", "origin")
    db.write_text("v2 rewritten by the next run\n")
    assert remote_run.remote_dispatch_preflight(ws)["ok"] is True


def test_a_study_definition_next_to_a_run_database_still_blocks(ws):
    (ws / "studies" / "s1").mkdir(parents=True)
    (ws / "studies" / "s1" / "runs.db").write_text("generated\n")
    (ws / "studies" / "s1" / "study.yaml").write_text("name: s1\n")     # real workspace content
    pf = remote_run.remote_dispatch_preflight(ws)
    # git reports an untracked directory as one entry; what matters is that the real file keeps it blocking
    assert pf["ok"] is False and pf["reason"] == "dirty" and "studies/" in pf["dirty_files"], pf
    (ws / "studies" / "s1" / "study.yaml").unlink()                       # only the generated database left: clean again
    assert remote_run.remote_dispatch_preflight(ws)["ok"] is True
