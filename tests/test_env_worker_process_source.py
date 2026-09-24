"""Process-source read/write for the editable code rail.

The env-worker resolves a registry ``address`` to its class, reads the class's
source file, and (when the file lives inside the editable workspace tree) writes
edits back. Two layers are covered:

  * ``_source_path_editable`` — the pure safety gate. This is the sharp edge:
    a write must NEVER be allowed to overwrite an installed dependency
    (``.venv`` / ``site-packages``) or a file outside the workspace.
  * ``process_source`` / ``process_source_write`` end to end, over a real
    env-worker subprocess against a copied ``ws_increase_demo`` fixture.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import vivarium_workbench.env_worker as ew
from vivarium_workbench.lib.env_worker_client import EnvWorker

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


# --- the pure safety gate ----------------------------------------------------

def test_editable_true_for_file_inside_workspace(tmp_path):
    ws = tmp_path
    src = ws / "pbg_ws" / "processes.py"
    src.parent.mkdir(parents=True)
    src.write_text("x = 1\n")
    assert ew._source_path_editable(str(src), str(ws)) is True


def test_editable_false_for_file_outside_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "elsewhere" / "mod.py"
    outside.parent.mkdir()
    outside.write_text("x = 1\n")
    assert ew._source_path_editable(str(outside), str(ws)) is False


def test_editable_false_for_dependency_in_venv(tmp_path):
    """A dep installed into the workspace's own .venv is INSIDE the ws tree but
    must still be refused — this is the case the naive 'is it under ws?' check
    gets wrong."""
    ws = tmp_path
    dep = ws / ".venv" / "lib" / "python3.11" / "site-packages" / "process_bigraph" / "core.py"
    dep.parent.mkdir(parents=True)
    dep.write_text("x = 1\n")
    assert ew._source_path_editable(str(dep), str(ws)) is False


def test_editable_false_for_site_packages_anywhere(tmp_path):
    ws = tmp_path
    dep = ws / "site-packages" / "foo.py"
    dep.parent.mkdir(parents=True)
    dep.write_text("x = 1\n")
    assert ew._source_path_editable(str(dep), str(ws)) is False


# --- python-syntax validation ------------------------------------------------

def test_validate_python_accepts_valid():
    ok, err = ew._validate_python("def f():\n    return 1\n", "m.py")
    assert ok is True and err == ""


def test_validate_python_rejects_syntax_error():
    ok, err = ew._validate_python("def f(:\n", "m.py")
    assert ok is False and "line" in err.lower()


# --- end to end over a real worker ------------------------------------------

@pytest.fixture()
def ws_copy(tmp_path):
    """A writable copy of the increase-demo fixture so writes don't dirty the
    repo's checked-in fixture."""
    dst = tmp_path / "ws_increase_demo"
    shutil.copytree(_FIXTURE, dst)
    return dst


def test_process_source_reads_workspace_process(ws_copy):
    with EnvWorker(ws_copy) as w:
        out = w.call("process_source", {"address": "pbg_ws_increase_demo.processes.IncreaseProcess"})
    assert out["ok"] is True
    assert "class IncreaseProcess(Process)" in out["source"]
    assert out["editable"] is True
    assert out["path"].endswith("processes.py")
    assert out["package"] == "pbg_ws_increase_demo"


def test_process_source_write_round_trips(ws_copy):
    proc_file = ws_copy / "pbg_ws_increase_demo" / "processes.py"
    original = proc_file.read_text()
    edited = original.replace("linear-growth process", "EDITED linear-growth process")
    assert edited != original
    with EnvWorker(ws_copy) as w:
        out = w.call(
            "process_source_write",
            {"address": "pbg_ws_increase_demo.processes.IncreaseProcess", "source": edited},
        )
    assert out["ok"] is True
    assert proc_file.read_text() == edited


def test_process_source_write_rejects_syntax_error(ws_copy):
    proc_file = ws_copy / "pbg_ws_increase_demo" / "processes.py"
    original = proc_file.read_text()
    with EnvWorker(ws_copy) as w:
        out = w.call(
            "process_source_write",
            {"address": "pbg_ws_increase_demo.processes.IncreaseProcess", "source": "def broken(:\n"},
        )
    assert out["ok"] is False
    assert "syntax" in out["error"].lower()
    # disk untouched on a rejected write
    assert proc_file.read_text() == original


def test_process_source_unknown_address_is_structured_error(ws_copy):
    with EnvWorker(ws_copy) as w:
        out = w.call("process_source", {"address": "pbg_ws_increase_demo.processes.NoSuchProcess"})
    assert out["ok"] is False
    assert "not found" in out["error"].lower()
