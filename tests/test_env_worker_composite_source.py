"""Composite-source read/write for the code rail.

Composites come in two kinds and the panel shows both:

  * ``spec``      — a declarative ``*.composite.yaml`` file (the ``source`` path).
  * ``generator`` — an ``@composite_generator`` Python function; the panel shows
    its whole module file ("all the composite code in that module").

Writes reuse the same editability gate as process source, but validate by
language: YAML via ``yaml.safe_load``, Python via ``compile``.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from vivarium_workbench.lib.env_worker_client import EnvWorker

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
_GEN = {
    "id": "pbg_ws_increase_demo.composites.hint_test",
    "module": "pbg_ws_increase_demo.composites",
}
_SPEC = {
    "id": "pbg_ws_increase_demo.composites.increase-demo",
    "source_path": "pbg_ws_increase_demo/composites/increase-demo.composite.yaml",
}


@pytest.fixture()
def ws_copy(tmp_path):
    dst = tmp_path / "ws_increase_demo"
    shutil.copytree(_FIXTURE, dst)
    return dst


def test_generator_source_reads_module(ws_copy):
    with EnvWorker(ws_copy) as w:
        out = w.call("composite_source", _GEN)
    assert out["ok"] is True
    assert out["lang"] == "python"
    assert "def hint_test" in out["source"]
    assert out["editable"] is True
    assert out["path"].endswith(".py")


def test_spec_source_reads_yaml(ws_copy):
    with EnvWorker(ws_copy) as w:
        out = w.call("composite_source", _SPEC)
    assert out["ok"] is True
    assert out["lang"] == "yaml"
    assert "name: increase-demo" in out["source"]
    assert out["editable"] is True
    assert out["path"].endswith(".composite.yaml")


def test_spec_source_write_round_trips(ws_copy):
    yaml_file = ws_copy / _SPEC["source_path"]
    edited = yaml_file.read_text().replace("default: 2.0", "default: 3.0")
    assert "default: 3.0" in edited
    with EnvWorker(ws_copy) as w:
        out = w.call("composite_source_write", dict(_SPEC, source=edited, lang="yaml"))
    assert out["ok"] is True
    assert yaml_file.read_text() == edited


def test_spec_source_write_rejects_bad_yaml(ws_copy):
    yaml_file = ws_copy / _SPEC["source_path"]
    original = yaml_file.read_text()
    with EnvWorker(ws_copy) as w:
        out = w.call("composite_source_write",
                     dict(_SPEC, source="key: [unclosed\n", lang="yaml"))
    assert out["ok"] is False
    assert "yaml" in out["error"].lower()
    assert yaml_file.read_text() == original


def test_generator_source_write_rejects_bad_python(ws_copy):
    gen_file = ws_copy / "pbg_ws_increase_demo" / "composites" / "__init__.py"
    original = gen_file.read_text()
    with EnvWorker(ws_copy) as w:
        out = w.call("composite_source_write",
                     dict(_GEN, source="def broken(:\n", lang="python"))
    assert out["ok"] is False
    assert "syntax" in out["error"].lower()
    assert gen_file.read_text() == original
