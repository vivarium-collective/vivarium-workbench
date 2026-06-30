import tomllib
from pathlib import Path


def test_vdash_console_script_declared():
    pp = tomllib.loads(
        (Path(__file__).parent.parent / "pyproject.toml").read_text())
    scripts = pp["project"]["scripts"]
    assert scripts["vdash"] == "vivarium_dashboard.cli:main"
    assert scripts["vivarium-dashboard"] == "vivarium_dashboard.cli:main"
