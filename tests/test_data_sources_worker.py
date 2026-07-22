"""Tests for the worker-backed data-source provider (sys.path cleanup 4/4).

The ``dashboard.data_sources`` provider (a ``module:func`` spec that usually
resolves into the workspace's own package) is imported + invoked in the env
worker now, not the HTTP process. Two layers:

  * ``env_worker._data_sources_provider`` — the class/module-touching import + call,
    exercised directly with a fake module registered in ``sys.modules``;
  * ``data_sources.enumerate_data_sources`` — the HTTP-side orchestrator (cache,
    workspace.yaml read, normalization, degrade), with the worker seam stubbed.
"""
from __future__ import annotations

import sys
import types

from vivarium_workbench import env_worker
from vivarium_workbench.lib import data_sources


# ---------------------------------------------------------------------------
# worker internals
# ---------------------------------------------------------------------------

def _fake_provider_module(monkeypatch, name, fn):
    mod = types.ModuleType(name)
    mod.provide = fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, name, mod)


def test_worker_provider_happy(monkeypatch):
    _fake_provider_module(monkeypatch, "fake_ds_ok",
                          lambda: [{"key": "a", "path": "/p"}])
    out = env_worker._data_sources_provider({"provider": "fake_ds_ok:provide"})
    assert out == {"rows": [{"key": "a", "path": "/p"}], "error": None}


def test_worker_provider_bad_spec():
    out = env_worker._data_sources_provider({"provider": "no_colon_here"})
    assert out["rows"] == []
    assert out["error"].startswith("ValueError:")


def test_worker_provider_not_callable(monkeypatch):
    mod = types.ModuleType("fake_ds_notcallable")
    mod.provide = 123  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_ds_notcallable", mod)
    out = env_worker._data_sources_provider({"provider": "fake_ds_notcallable:provide"})
    assert out["rows"] == []
    assert out["error"].startswith("TypeError:")


def test_worker_provider_raises(monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")
    _fake_provider_module(monkeypatch, "fake_ds_boom", _boom)
    out = env_worker._data_sources_provider({"provider": "fake_ds_boom:provide"})
    assert out == {"rows": [], "error": "RuntimeError: kaboom"}


def test_worker_provider_none_result(monkeypatch):
    _fake_provider_module(monkeypatch, "fake_ds_none", lambda: None)
    out = env_worker._data_sources_provider({"provider": "fake_ds_none:provide"})
    assert out == {"rows": [], "error": None}


# ---------------------------------------------------------------------------
# HTTP-side orchestrator
# ---------------------------------------------------------------------------

def _ws(tmp_path, yaml_text):
    (tmp_path / "workspace.yaml").write_text(yaml_text, encoding="utf-8")
    data_sources.clear_cache()
    return tmp_path


def test_enumerate_no_provider_is_empty(tmp_path):
    ws = _ws(tmp_path, "name: x\n")
    out = data_sources.enumerate_data_sources(ws, bypass_cache=True)
    assert out == {"sources": []}


def test_enumerate_normalizes_worker_rows(tmp_path, monkeypatch):
    ws = _ws(tmp_path,
             "dashboard:\n  data_sources:\n    provider: pkg:fn\n    label: My Data\n")
    monkeypatch.setattr(
        data_sources, "_provider_rows_via_worker",
        lambda ws_root, provider: {"rows": [
            {"key": "k1", "path": "/x", "size_bytes": 5},
            {"no_key": "dropped"},  # missing "key" → filtered out
            "not-a-dict",           # non-dict → filtered out
        ], "error": None})
    out = data_sources.enumerate_data_sources(ws, bypass_cache=True)
    assert out["label"] == "My Data"
    assert out["sources"] == [{
        "key": "k1", "path": "/x", "category": "uncategorized",
        "kind": "inherited", "size_bytes": 5, "url": "",
    }]


def test_enumerate_provider_error_degrades(tmp_path, monkeypatch):
    ws = _ws(tmp_path,
             "dashboard:\n  data_sources:\n    provider: pkg:fn\n    label: My Data\n")
    monkeypatch.setattr(
        data_sources, "_provider_rows_via_worker",
        lambda ws_root, provider: {"rows": [], "error": "ValueError: boom"})
    out = data_sources.enumerate_data_sources(ws, bypass_cache=True)
    assert out == {"label": None, "sources": [], "error": "ValueError: boom"}


def test_enumerate_forwards_provider_spec(tmp_path, monkeypatch):
    ws = _ws(tmp_path,
             "dashboard:\n  data_sources:\n    provider: mypkg.data:list_it\n")
    seen = {}

    def _fake(ws_root, provider):
        seen["provider"] = provider
        return {"rows": [], "error": None}

    monkeypatch.setattr(data_sources, "_provider_rows_via_worker", _fake)
    data_sources.enumerate_data_sources(ws, bypass_cache=True)
    assert seen["provider"] == "mypkg.data:list_it"
