"""Tests for _resolve_composite_source_or_generate.

Covers the dual-lookup behaviour: legacy YAML composite documents AND
@composite_generator-registered functions both have to be resolvable
through the same call site so the investigation-create / composite-add
handlers can accept either kind of source.
"""
from __future__ import annotations

import sys
import types
import pytest
import yaml

from vivarium_dashboard.lib.investigation_migrate import (
    _resolve_composite_source_or_generate,
    materialize_generator_doc,
)


def test_returns_yaml_path_when_yaml_exists(tmp_path):
    """YAML wins when the file is on disk; is_generator stays False."""
    pkg_composites = tmp_path / "pkg" / "composites"
    pkg_composites.mkdir(parents=True)
    yaml_file = pkg_composites / "foo.composite.yaml"
    yaml_file.write_text(yaml.safe_dump({"state": {}}))

    path, is_generator, name = _resolve_composite_source_or_generate(
        "pkg.composites.foo", tmp_path,
    )

    assert path == yaml_file
    assert is_generator is False
    assert name == "foo"


def test_falls_back_to_generator_registry_when_yaml_missing(tmp_path, monkeypatch):
    """No YAML on disk → look up the dotted ref in the generator registry."""
    fake_entry = object()

    fake_mod = types.SimpleNamespace(
        _REGISTRY={"pkg.composites.module.func": fake_entry},
        discover_generators=lambda: None,
    )
    monkeypatch.setitem(
        sys.modules, "pbg_superpowers", types.SimpleNamespace(),
    )
    monkeypatch.setitem(
        sys.modules, "pbg_superpowers.composite_generator", fake_mod,
    )

    path, is_generator, name = _resolve_composite_source_or_generate(
        "pkg.composites.module.func", tmp_path,
    )

    assert path is None
    assert is_generator is True
    # Generator path picks the trailing segment (the function name),
    # not the full `<module>.<function>` stem.
    assert name == "func"


def test_raises_when_neither_yaml_nor_generator_resolves(tmp_path, monkeypatch):
    """Missing on disk AND missing from registry surfaces a FileNotFoundError."""
    fake_mod = types.SimpleNamespace(
        _REGISTRY={"some.other.id": object()},
        discover_generators=lambda: None,
    )
    monkeypatch.setitem(
        sys.modules, "pbg_superpowers", types.SimpleNamespace(),
    )
    monkeypatch.setitem(
        sys.modules, "pbg_superpowers.composite_generator", fake_mod,
    )

    with pytest.raises(FileNotFoundError, match="not registered as a"):
        _resolve_composite_source_or_generate(
            "pkg.composites.foo.bar", tmp_path,
        )


def test_materialize_generator_doc_runs_build_generator(tmp_path, monkeypatch):
    """materialize_generator_doc calls build_generator and normalizes the result."""
    fake_doc = {"state": {"x": 1}, "skip_initial_steps": []}
    fake_entry = object()
    fake_mod = types.SimpleNamespace(
        _REGISTRY={"pkg.composites.module.func": fake_entry},
        build_generator=lambda entry: fake_doc if entry is fake_entry else None,
        discover_generators=lambda: None,
    )
    monkeypatch.setitem(
        sys.modules, "pbg_superpowers", types.SimpleNamespace(),
    )
    monkeypatch.setitem(
        sys.modules, "pbg_superpowers.composite_generator", fake_mod,
    )

    doc = materialize_generator_doc("pkg.composites.module.func")
    assert doc == fake_doc
