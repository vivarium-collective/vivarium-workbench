"""Test modules that need the SMS plugin say so (docs/backend-adapters.md step P3, K1).

The ``core-only`` CI job runs the suite with ``vivarium_workbench_sms`` removed. There, ``tests/conftest.py`` ignores
every test module that imports the plugin at module level (it cannot be collected) or is marked ``plugin`` whole, and
strict-xfails every other test marked ``plugin``. A module ignored that way silently drops any core tests it holds, so a module that imports the plugin at
module level must be a pure-plugin module and say so with ``pytestmark = pytest.mark.plugin``.

``UNMARKED_PLUGIN_IMPORTERS`` is a ratchet: the modules that imported the plugin at module level without the mark when
the core-only job arrived (K1: 53 modules). Step P3's K2 PRs empty it -- moving the import into the tests that need it
and marking those, or marking a pure-plugin module whole. It may only shrink: a new module that imports the plugin at
module level must be marked, and an entry that no longer applies must be removed.

This guard runs in the normal suite (it needs the plugin present to mean anything).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import (
    PLUGIN_PACKAGE,
    _test_modules,
    declares_plugin_mark,
    module_imports_plugin,
    module_needs_plugin_to_collect,
    plugin_imports_at_module_level,
)

TESTS = Path(__file__).parent

#: Empty since step P3 Z (the last two, test_source_branch and test_source_build_views_lib, were converted there).
UNMARKED_PLUGIN_IMPORTERS: frozenset[str] = frozenset()

#: The ratchet's ceiling: UNMARKED_PLUGIN_IMPORTERS's size when the core-only job arrived (K1). Each K2 PR that
#: shrinks the set lowers this to the new size in the same PR; NEVER raise it -- a new module-level importer gets the
#: mark instead.
UNMARKED_PLUGIN_IMPORTERS_CEILING = 0


def _name(path: Path) -> str:
    return path.relative_to(TESTS).as_posix()


def _scan() -> tuple[set[str], set[str]]:
    """(module-level importers without the whole-module mark, modules marked whole that never name the plugin)."""
    unmarked, hollow = set(), set()
    for path in _test_modules():
        source = path.read_text(encoding="utf-8")
        marked = declares_plugin_mark(source)
        if module_imports_plugin(path) and not marked:
            unmarked.add(_name(path))
        if marked and PLUGIN_PACKAGE not in source and not module_imports_plugin(path):
            hollow.add(_name(path))
    return unmarked, hollow


def test_the_ratchet_only_shrinks():
    assert len(UNMARKED_PLUGIN_IMPORTERS) <= UNMARKED_PLUGIN_IMPORTERS_CEILING, (
        f"UNMARKED_PLUGIN_IMPORTERS grew to {len(UNMARKED_PLUGIN_IMPORTERS)} (ceiling "
        f"{UNMARKED_PLUGIN_IMPORTERS_CEILING}); mark the new module-level plugin importer instead of listing it")


def test_module_level_plugin_importers_are_marked():
    unmarked, _ = _scan()
    new = sorted(unmarked - UNMARKED_PLUGIN_IMPORTERS)
    stale = sorted(UNMARKED_PLUGIN_IMPORTERS - unmarked)
    assert not new, (
        "these test modules import vivarium_workbench_sms at module level without `pytestmark = pytest.mark.plugin`; "
        "the core-only job cannot collect them, so their core tests would silently vanish. Move the import into the "
        f"tests that need it and mark those `@pytest.mark.plugin`, or mark a pure-plugin module whole: {new}")
    assert not stale, (
        f"these no longer import the plugin at module level unmarked; remove them from UNMARKED_PLUGIN_IMPORTERS: "
        f"{stale}")


def test_a_whole_module_plugin_mark_names_the_plugin():
    """A whole-module mark must not hide core tests: the module really references the plugin."""
    _, hollow = _scan()
    assert not hollow, f"marked `plugin` as a whole but never reference {PLUGIN_PACKAGE}: {sorted(hollow)}"


def test_the_plugin_marker_is_registered(pytestconfig):
    assert any(m.startswith("plugin:") for m in pytestconfig.getini("markers"))


@pytest.mark.parametrize(("source", "expected"), [
    ("import vivarium_workbench_sms\n", [1]),
    ("import os\nfrom vivarium_workbench_sms import provider\n", [2]),
    ("from vivarium_workbench_sms.sms_legacy import x as y\n", [1]),
    ("import vivarium_workbench_sms.sms_api_client as c\n", [1]),
    ("import importlib\nm = importlib.import_module('vivarium_workbench_sms.provider')\n", [2]),
    # not the plugin
    ("import vivarium_workbench\nfrom vivarium_workbench.lib import backends\n", []),
    ("import vivarium_workbench_smsx\n", []),
    ("from . import vivarium_workbench_sms\n", []),
    # inside a function: runs at test time, not collection
    ("def test_x():\n    from vivarium_workbench_sms import provider\n", []),
    ("async def f():\n    import vivarium_workbench_sms\n", []),
    # TYPE_CHECKING is not runtime; its else branch is
    ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import vivarium_workbench_sms\n", []),
    ("import typing\nif typing.TYPE_CHECKING:\n    import vivarium_workbench_sms\n", []),
    ("if TYPE_CHECKING:\n    pass\nelse:\n    import vivarium_workbench_sms\n", [4]),
    # other top-level compound statements run at import
    ("import sys\nif sys.version_info > (3,):\n    import vivarium_workbench_sms\n", [3]),
    ("with open('x'):\n    import vivarium_workbench_sms\n", [2]),
    ("class T:\n    from vivarium_workbench_sms import provider\n", [2]),
    # a try that survives a missing plugin does not break collection
    ("try:\n    import vivarium_workbench_sms\nexcept ImportError:\n    pass\n", []),
    ("try:\n    import vivarium_workbench_sms\nexcept (ModuleNotFoundError, KeyError):\n    pass\n", []),
    ("try:\n    import vivarium_workbench_sms\nexcept KeyError:\n    pass\n", [2]),
    ("try:\n    pass\nfinally:\n    import vivarium_workbench_sms\n", [4]),
])
def test_the_detector(source, expected):
    assert plugin_imports_at_module_level(source) == expected


_P = "def _p():\n    import vivarium_workbench_sms\n    return []\n"


@pytest.mark.parametrize(("source", "expected"), [
    # a sibling test module that needs the plugin brings it in transitively
    ("from test_needs import ws\n", [1]),
    ("import test_needs\n", [1]),
    ("from test_core import ws\n", []),
    ("def f():\n    from test_needs import ws\n", []),
    # a module function that imports the plugin, called at import time (directly or through another function)
    ("def _mods():\n    import vivarium_workbench_sms\n    return []\nMODULES = ['a'] + _mods()\n", [4]),
    ("def _a():\n    import vivarium_workbench_sms\ndef _b():\n    return _a()\nX = _b()\n", [5]),
    ("def _a():\n    from test_needs import ws\nX = _a()\n", [3]),
    # defined but not called at import time
    ("def _mods():\n    import vivarium_workbench_sms\n", []),
    ("def _mods():\n    import vivarium_workbench_sms\nF = lambda: _mods()\n", []),
    # import-time positions other than assignments: decorators (incl. parametrize ids), defaults, class headers,
    # compound-statement headers, assert / raise
    (_P + "@pytest.mark.parametrize('x', [1], ids=_p())\ndef test_x(x):\n    pass\n", [4]),
    (_P + "@pytest.mark.parametrize('x', _p())\ndef test_x(x):\n    pass\n", [4]),
    (_P + "def test_x(a=_p()):\n    pass\n", [4]),
    (_P + "def test_x(*, a=_p()):\n    pass\n", [4]),
    (_P + "@_p()\nclass T:\n    pass\n", [4]),
    (_P + "class T(*_p()):\n    pass\n", [4]),
    (_P + "class T:\n    @pytest.mark.parametrize('x', _p())\n    def test_x(self, x):\n        pass\n", [5]),
    (_P + "if _p():\n    pass\n", [4]),
    (_P + "while _p():\n    pass\n", [4]),
    (_P + "for x in _p():\n    pass\n", [4]),
    (_P + "with _p():\n    pass\n", [4]),
    (_P + "assert _p() == []\n", [4]),
    (_P + "match _p():\n    case _:\n        pass\n", [4]),
    # ... but not when only the body (run at call time) or a lambda uses it
    (_P + "def test_x(a=lambda: _p()):\n    pass\n", []),
    (_P + "@pytest.mark.parametrize('x', [1])\ndef test_x(x):\n    _p()\n", []),
])
def test_the_detector_follows_local_modules_and_calls(source, expected):
    assert plugin_imports_at_module_level(source, lambda name: name == "test_needs") == expected


def test_module_imports_plugin_follows_sibling_modules_and_survives_cycles(tmp_path):
    (tmp_path / "test_direct.py").write_text("import vivarium_workbench_sms\n")
    (tmp_path / "test_via.py").write_text("from test_direct import x\n")
    (tmp_path / "test_via_via.py").write_text("import test_via\n")
    (tmp_path / "_helper.py").write_text("from vivarium_workbench_sms import provider\n")
    (tmp_path / "test_helper_user.py").write_text("from _helper import provider\n")
    (tmp_path / "test_core.py").write_text("import os\nfrom test_cycle_b import y\n")
    (tmp_path / "test_cycle_a.py").write_text("from test_cycle_b import y\n")
    (tmp_path / "test_cycle_b.py").write_text("from test_cycle_a import x\n")
    (tmp_path / "test_cycle_plugin.py").write_text("from test_cycle_c import y\nimport vivarium_workbench_sms\n")
    (tmp_path / "test_cycle_c.py").write_text("from test_cycle_plugin import x\n")
    answers = {name: module_imports_plugin(tmp_path / f"{name}.py") for name in (
        "test_direct", "test_via", "test_via_via", "test_helper_user", "test_core", "test_cycle_a", "test_cycle_b",
        "test_cycle_c", "test_cycle_plugin")}
    assert answers == {
        "test_direct": True, "test_via": True, "test_via_via": True, "test_helper_user": True,
        "test_core": False, "test_cycle_a": False, "test_cycle_b": False,
        "test_cycle_c": True, "test_cycle_plugin": True,
    }


def test_a_whole_module_mark_is_enough_to_skip_collection(tmp_path):
    """Marking a pure-plugin module whole is always a valid fix, even for a plugin use the detector cannot see."""
    (tmp_path / "test_marked.py").write_text(
        "import pytest\npytestmark = pytest.mark.plugin\nX = __import__('vivarium_workbench_sms')\n")
    (tmp_path / "test_uses_marked.py").write_text("from test_marked import X\n")
    (tmp_path / "test_plain.py").write_text("import os\n")
    assert not module_imports_plugin(tmp_path / "test_marked.py")      # the detector does not see __import__
    assert module_needs_plugin_to_collect(tmp_path / "test_marked.py")
    assert module_needs_plugin_to_collect(tmp_path / "test_uses_marked.py")
    assert not module_needs_plugin_to_collect(tmp_path / "test_plain.py")


def test_the_mark_check():
    assert declares_plugin_mark("import pytest\npytestmark = pytest.mark.plugin\n")
    assert declares_plugin_mark("import pytest\npytestmark = [pytest.mark.slow, pytest.mark.plugin]\n")
    assert not declares_plugin_mark("import pytest\npytestmark = pytest.mark.slow\n")
    assert not declares_plugin_mark("import pytest\ndef f():\n    pytestmark = pytest.mark.plugin\n")
