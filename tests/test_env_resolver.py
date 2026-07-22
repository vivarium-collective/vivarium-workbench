"""EnvironmentResolver interpreter selection (in-place local adapter)."""
import sys

from vivarium_workbench.lib import env_resolver


def test_falls_back_to_running_interpreter_without_venv(tmp_path):
    assert env_resolver.resolve_interpreter(tmp_path) == sys.executable


def test_uses_the_workspace_venv_when_present(tmp_path):
    venv_py = tmp_path / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("#!/bin/sh\n")   # just needs to be a file
    assert env_resolver.resolve_interpreter(tmp_path) == str(venv_py)
