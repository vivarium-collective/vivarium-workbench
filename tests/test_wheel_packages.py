"""What each wheel ships (docs/backend-adapters.md §2.4, steps P1b and P4b).

The editable install puts the repo root on ``sys.path``, so a package missing from hatch's ``packages`` list still
imports in local dev, the bare-clone smoke job and the Docker image -- and is absent only from a real wheel, which is
what a workspace gets from ``pip install git+...``. This catches the omission without building one.

Since step P4b the SMS plugin is its own distribution, ``vivarium-workbench-vivaapi``, built from the uv workspace
member ``packages/vivarium-workbench-vivaapi/``. The core wheel ships neither its package nor its entry point nor its
boto3 dependency. (scripts/check_wheels.py checks the same against built wheels, in the ``wheels`` job and
release.yml.)
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEMBER = ROOT / "packages" / "vivarium-workbench-vivaapi"
GROUP = "vivarium_workbench.backends"
#: Top-level directories with an ``__init__.py`` that are not shipped on purpose.
NOT_SHIPPED = {"tests", "scripts", "docs", "demos", "deploy"}


def _root() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _member() -> dict:
    return tomllib.loads((MEMBER / "pyproject.toml").read_text())


def _names(reqs: list[str]) -> set[str]:
    import re

    return {re.split(r"[\s\[<>=!~;]", r, maxsplit=1)[0].lower() for r in reqs}


def test_every_top_level_package_is_in_the_core_wheel():
    packages = set(_root()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"])
    assert packages == {"vivarium_workbench", "vivarium_dashboard"}
    on_disk = {p.parent.name for p in ROOT.glob("*/__init__.py")} - NOT_SHIPPED
    assert on_disk <= packages, f"top-level packages missing from [tool.hatch.build.targets.wheel].packages: " \
                                f"{sorted(on_disk - packages)}"


def test_no_plugin_package_at_the_repo_root():
    """A real ``vivarium_workbench_sms`` package at the root would import through the core's editable ``.pth`` and
    defeat the core-only install. (A leftover ``__pycache__`` is only a namespace, which the registry and the test
    suite's ``CORE_MODE`` treat as absent, and which scripts/pytest_core_only.sh deletes.)"""
    assert not (ROOT / "vivarium_workbench_sms" / "__init__.py").exists()


def test_the_member_ships_the_plugin():
    assert _member()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["vivarium_workbench_sms"]
    assert (MEMBER / "vivarium_workbench_sms" / "provider.py").is_file()


def test_only_the_member_declares_the_backend_entry_point():
    """The wheel's ``entry_points.txt`` is generated from ``[project.entry-points]`` (step P4a). Core metadata that
    declares the group is a stale install (lib/backends/provider.py refuses it)."""
    assert GROUP not in _root()["project"].get("entry-points", {})
    assert _member()["project"]["entry-points"][GROUP] == {"sms": "vivarium_workbench_sms.provider:PROVIDER"}


def test_boto3_is_the_plugins_dependency_and_remote_is_gone():
    root = _root()["project"]
    assert "boto3" not in _names(root["dependencies"])
    assert "boto3" in _names(_member()["project"]["dependencies"])
    assert "remote" not in root["optional-dependencies"]


def test_the_core_sdist_excludes_the_member():
    assert "/packages" in _root()["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
