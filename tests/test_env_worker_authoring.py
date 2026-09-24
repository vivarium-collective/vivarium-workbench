"""Authoring new processes / steps / composites from the code rail.

Three env-worker methods back the "+ New" flow:
  * scaffold_template — a starter template + the conventional target path (no write).
  * authoring_validate — parse + import & register checks, returned as a checklist.
  * authoring_create — write the file into the workspace and auto-register it.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from vivarium_workbench.lib.env_worker_client import EnvWorker

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


@pytest.fixture()
def ws_copy(tmp_path):
    dst = tmp_path / "ws_increase_demo"
    shutil.copytree(_FIXTURE, dst)
    return dst


def _checks(out):
    return {c["label"]: c["ok"] for c in out.get("checks", [])}


# ── scaffold_template ────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,needle,lang", [
    ("process", "class Foo(Process)", "python"),
    ("step", "class Foo(Step)", "python"),
    ("generator", "@composite_generator", "python"),
    ("spec", "name: foo", "yaml"),
])
def test_scaffold_template(ws_copy, kind, needle, lang):
    with EnvWorker(ws_copy) as w:
        out = w.call("scaffold_template", {"kind": kind, "name": "Foo" if kind != "spec" else "foo"})
    assert out["ok"] is True
    assert out["lang"] == lang
    assert needle in out["source"]
    assert out["target"]  # a conventional relative path


# ── authoring_validate ───────────────────────────────────────────────────────

def test_validate_fresh_process_template_is_valid(ws_copy):
    with EnvWorker(ws_copy) as w:
        tpl = w.call("scaffold_template", {"kind": "process", "name": "Foo"})
        out = w.call("authoring_validate", {"kind": "process", "name": "Foo", "source": tpl["source"]})
    assert out["valid"] is True
    assert all(c["ok"] for c in out["checks"])


def test_validate_non_process_class_fails_subclass(ws_copy):
    src = "class Foo:\n    def inputs(self): return {}\n    def outputs(self): return {}\n    def update(self, state, interval): return {}\n"
    with EnvWorker(ws_copy) as w:
        out = w.call("authoring_validate", {"kind": "process", "name": "Foo", "source": src})
    assert out["valid"] is False
    checks = _checks(out)
    assert checks.get("Subclasses Process") is False


def test_validate_missing_update_fails(ws_copy):
    src = "from process_bigraph import Process\nclass Foo(Process):\n    def inputs(self): return {}\n    def outputs(self): return {}\n"
    with EnvWorker(ws_copy) as w:
        out = w.call("authoring_validate", {"kind": "process", "name": "Foo", "source": src})
    assert out["valid"] is False
    assert _checks(out).get("Has update()") is False


def test_validate_bad_yaml_spec_fails(ws_copy):
    with EnvWorker(ws_copy) as w:
        out = w.call("authoring_validate", {"kind": "spec", "name": "x", "source": "a: [unclosed\n"})
    assert out["valid"] is False
    assert _checks(out).get("Parses") is False


def test_validate_generator_name_mismatch_flags(ws_copy):
    src = ('from process_bigraph.composite_generator import composite_generator\n'
           '@composite_generator(name="other", parameters={})\n'
           'def other(core=None):\n    return {}\n')
    with EnvWorker(ws_copy) as w:
        out = w.call("authoring_validate", {"kind": "generator", "name": "myco", "source": src})
    assert out["valid"] is False


# ── authoring_create ─────────────────────────────────────────────────────────

def test_create_process_writes_and_registers(ws_copy):
    with EnvWorker(ws_copy) as w:
        tpl = w.call("scaffold_template", {"kind": "process", "name": "GrowthProc"})
        res = w.call("authoring_create", {"kind": "process", "name": "GrowthProc", "source": tpl["source"]})
    assert res["ok"] is True
    assert res["registered"] is True
    procs = (ws_copy / "pbg_ws_increase_demo" / "processes.py").read_text()
    assert "class GrowthProc(Process)" in procs
    core = (ws_copy / "pbg_ws_increase_demo" / "core.py").read_text()
    assert "GrowthProc" in core and "register_link" in core
    # a FRESH worker builds core from disk and now sees the new class registered
    with EnvWorker(ws_copy) as w2:
        cat = w2.call("registry_catalog")
    addrs = [p.get("address", "") for p in (cat.get("processes") or [])]
    assert any("GrowthProc" in a for a in addrs)


def test_create_refuses_collision(ws_copy):
    with EnvWorker(ws_copy) as w:
        tpl = w.call("scaffold_template", {"kind": "process", "name": "IncreaseProcess"})
        res = w.call("authoring_create", {"kind": "process", "name": "IncreaseProcess", "source": tpl["source"]})
    assert res["ok"] is False
    assert "exist" in res["error"].lower()


def test_create_spec_writes_yaml(ws_copy):
    with EnvWorker(ws_copy) as w:
        tpl = w.call("scaffold_template", {"kind": "spec", "name": "my-new-comp"})
        res = w.call("authoring_create", {"kind": "spec", "name": "my-new-comp", "source": tpl["source"]})
    assert res["ok"] is True
    assert (ws_copy / "pbg_ws_increase_demo" / "composites" / "my_new_comp.composite.yaml").is_file()
