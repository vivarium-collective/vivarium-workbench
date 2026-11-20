"""Every top-level Python package in the repo ships in the wheel (docs/backend-adapters.md §2.4, step P1b).

The editable install puts the repo root on ``sys.path``, so a package missing from hatch's ``packages`` list still
imports in local dev, the bare-clone smoke job and the Docker image -- and is absent only from a real wheel, which is
what a workspace gets from ``pip install git+...``. This catches the omission without building one.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: Top-level directories with an ``__init__.py`` that are not shipped on purpose.
NOT_SHIPPED = {"tests", "scripts", "docs", "demos", "deploy"}


def test_every_top_level_package_is_in_the_wheel():
    packages = set(tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]["wheel"]
                   ["packages"])
    on_disk = {p.parent.name for p in ROOT.glob("*/__init__.py")} - NOT_SHIPPED
    assert on_disk <= packages, f"top-level packages missing from [tool.hatch.build.targets.wheel].packages: " \
                                f"{sorted(on_disk - packages)}"
    assert "vivarium_workbench_sms" in packages
