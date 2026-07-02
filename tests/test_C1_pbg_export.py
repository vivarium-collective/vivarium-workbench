"""Tests for vivarium_workbench.lib.pbg_export - C1: address rewriter."""
from __future__ import annotations
import collections
import pytest
from vivarium_workbench.lib.pbg_export import rewrite_local_addresses


class _FakeRegistry(dict):
    """A plain dict standing in for core.link_registry."""


class _FakeCore:
    def __init__(self, mapping: dict):
        self.link_registry = _FakeRegistry(mapping)


def test_rewrites_short_to_full_path():
    cls = collections.OrderedDict  # any importable class
    core = _FakeCore({"MyProc": cls})
    doc = {"state": {"p": {"address": "local:MyProc"}}}
    out = rewrite_local_addresses(doc, core)
    assert out["state"]["p"]["address"] == "local:!collections.OrderedDict"


def test_leaves_full_path_untouched():
    core = _FakeCore({})
    doc = {"state": {"a": {"address": "local:!x.Y"}}}
    out = rewrite_local_addresses(doc, core)
    assert out["state"]["a"]["address"] == "local:!x.Y"


def test_leaves_non_local_protocols_untouched():
    core = _FakeCore({})
    doc = {"state": {"b": {"address": "pkg:mod.Z"}}}
    out = rewrite_local_addresses(doc, core)
    assert out["state"]["b"]["address"] == "pkg:mod.Z"


def test_rewrites_nested_addresses():
    cls = collections.OrderedDict
    core = _FakeCore({"Proc": cls})
    doc = {
        "schema": {},
        "state": {
            "top": {
                "address": "local:Proc",
                "inputs": {"x": ["stores", "x"]},
                "config": {},
            },
            "stores": {"x": 1.0},
        },
    }
    out = rewrite_local_addresses(doc, core)
    assert out["state"]["top"]["address"] == "local:!collections.OrderedDict"


def test_does_not_mutate_original():
    cls = collections.OrderedDict
    core = _FakeCore({"P": cls})
    original = {"state": {"p": {"address": "local:P"}}}
    out = rewrite_local_addresses(original, core)
    assert original["state"]["p"]["address"] == "local:P"
    assert out["state"]["p"]["address"] == "local:!collections.OrderedDict"


def test_raises_for_main_module():
    """Classes in __main__ are not importable by module path."""
    # Create a fake class pretending to live in __main__
    class _FakeClass:
        pass
    _FakeClass.__module__ = "__main__"
    _FakeClass.__qualname__ = "_FakeClass"
    core = _FakeCore({"MainProc": _FakeClass})
    doc = {"state": {"p": {"address": "local:MainProc"}}}
    with pytest.raises(ValueError, match="__main__|not importable"):
        rewrite_local_addresses(doc, core)


def test_raises_for_locals_qualname():
    """Classes with <locals> in qualname cannot be imported by dotted path."""
    class _FakeClass:
        pass
    _FakeClass.__module__ = "some.module"
    _FakeClass.__qualname__ = "outer.<locals>._FakeClass"
    core = _FakeCore({"LocalProc": _FakeClass})
    doc = {"state": {"p": {"address": "local:LocalProc"}}}
    with pytest.raises(ValueError, match="<locals>|not importable"):
        rewrite_local_addresses(doc, core)


def test_rewrites_multiple_addresses_in_document():
    import collections
    core = _FakeCore({
        "ProcA": collections.OrderedDict,
        "ProcB": collections.OrderedDict,
    })
    doc = {
        "state": {
            "a": {"address": "local:ProcA", "config": {}},
            "b": {"address": "local:ProcB", "config": {}},
        }
    }
    out = rewrite_local_addresses(doc, core)
    assert out["state"]["a"]["address"] == "local:!collections.OrderedDict"
    assert out["state"]["b"]["address"] == "local:!collections.OrderedDict"
