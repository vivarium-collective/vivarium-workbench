"""The core and the backend plugin stay in lockstep (docs/backend-adapters.md step P4b).

Identical versions and ``==`` pins both ways; uv does not check the pins between workspace members, so
``scripts/check_release.py`` does -- the same checker release.yml runs on the tag. Files only, so this runs in the
core-only job too.
"""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MEMBER = Path("packages") / "vivarium-workbench-vivaapi" / "pyproject.toml"


def _checker():
    spec = importlib.util.spec_from_file_location("check_release", ROOT / "scripts" / "check_release.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_repo_is_in_lockstep():
    assert _checker().problems(ROOT) == []


@pytest.fixture
def copy(tmp_path):
    (tmp_path / MEMBER.parent).mkdir(parents=True)
    for rel in ("pyproject.toml", "uv.lock", str(MEMBER)):
        shutil.copy(ROOT / rel, tmp_path / rel)
    return tmp_path


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def test_a_member_version_that_misses_the_bump_is_caught(copy):
    check = _checker()
    version = check._load(copy / "pyproject.toml")["project"]["version"]
    _edit(copy / MEMBER, f'version = "{version}"', 'version = "9.9.9"')
    assert any("version is 9.9.9" in p for p in check.problems(copy))


def test_a_stale_pin_is_caught(copy):
    check = _checker()
    version = check._load(copy / "pyproject.toml")["project"]["version"]
    _edit(copy / MEMBER, f'"vivarium-workbench=={version}"', '"vivarium-workbench>=0.1"')
    _edit(copy / "pyproject.toml", f'"vivarium-workbench-vivaapi=={version}"', '"vivarium-workbench-vivaapi==0.0.1"')
    found = check.problems(copy)
    assert any("must require exactly" in p for p in found)
    assert any("vivaapi extra" in p for p in found)


def test_a_tag_that_is_not_the_version_is_caught(copy):
    assert any("expected 0.0.0" in p for p in _checker().problems(copy, "0.0.0"))
