"""Tests for ``lib.workspace_heal.heal_workspace_imports`` — repairing a
legacy-corrupted ``workspace.yaml`` so one malformed ``imports`` entry can't
permanently 500 every catalog install.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from vivarium_workbench.lib import _root
from vivarium_workbench.lib import workspace_deps_views
from vivarium_workbench.lib import workspace_heal


# A minimal stand-in for the viva-template schema: every imports entry value
# requires non-empty source/ref/mode (matches the field the deployment cleared).
_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "imports": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["source", "ref", "mode"],
                "properties": {
                    "source": {"type": "string", "minLength": 1},
                    "ref": {"type": "string", "minLength": 1},
                    "mode": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


def _setup_ws(tmp_path: Path, ws_data: dict) -> Path:
    schema_dir = tmp_path / ".pbg" / "schemas"
    schema_dir.mkdir(parents=True, exist_ok=True)
    (schema_dir / "workspace.schema.json").write_text(json.dumps(_SCHEMA), encoding="utf-8")
    (tmp_path / "workspace.yaml").write_text(
        yaml.safe_dump(ws_data, sort_keys=False), encoding="utf-8")
    _root.set_workspace_root(tmp_path)
    return tmp_path


def _catalog(monkeypatch, modules: list[dict]) -> None:
    monkeypatch.setattr(workspace_deps_views, "module_registry", lambda ws: modules)


def test_backfills_missing_ref_from_catalog(tmp_path, monkeypatch):
    # A pre-existing entry with a blank ref (the exact corruption the old
    # provisioning-skip tooling produced) is healed from the catalog.
    _setup_ws(tmp_path, {
        "name": "ws",
        "imports": {"foo": {"source": "https://x/foo.git", "ref": "", "mode": "reference"}},
    })
    _catalog(monkeypatch, [{"name": "foo", "source": "https://x/foo.git",
                            "ref": "main", "mode": "reference"}])

    healed = workspace_heal.heal_workspace_imports(tmp_path)

    assert healed == ["foo"]
    saved = yaml.safe_load((tmp_path / "workspace.yaml").read_text())
    assert saved["imports"]["foo"]["ref"] == "main"


def test_backfills_missing_source_and_mode(tmp_path, monkeypatch):
    _setup_ws(tmp_path, {
        "name": "ws",
        "imports": {"bar": {"source": "", "ref": "v1"}},  # source blank, mode absent
    })
    _catalog(monkeypatch, [{"name": "bar", "source": "https://x/bar.git",
                            "ref": "v1", "mode": "in-place"}])

    healed = workspace_heal.heal_workspace_imports(tmp_path)

    assert healed == ["bar"]
    entry = yaml.safe_load((tmp_path / "workspace.yaml").read_text())["imports"]["bar"]
    assert entry["source"] == "https://x/bar.git"
    assert entry["mode"] == "in-place"


def test_valid_file_is_noop(tmp_path, monkeypatch):
    _setup_ws(tmp_path, {
        "name": "ws",
        "imports": {"foo": {"source": "https://x/foo.git", "ref": "main", "mode": "reference"}},
    })
    _catalog(monkeypatch, [{"name": "foo", "source": "OTHER", "ref": "OTHER", "mode": "reference"}])

    healed = workspace_heal.heal_workspace_imports(tmp_path)

    assert healed == []
    # Untouched — a valid entry is never rewritten from the catalog.
    saved = yaml.safe_load((tmp_path / "workspace.yaml").read_text())
    assert saved["imports"]["foo"]["source"] == "https://x/foo.git"


def test_unhealable_entry_leaves_file_untouched(tmp_path, monkeypatch):
    # Malformed entry whose name is NOT in the catalog can't be authentically
    # backfilled — the file is left as-is so validation surfaces a precise error.
    original = {
        "name": "ws",
        "imports": {"ghost": {"source": "", "ref": "", "mode": "reference"}},
    }
    _setup_ws(tmp_path, original)
    _catalog(monkeypatch, [])  # empty catalog

    healed = workspace_heal.heal_workspace_imports(tmp_path)

    assert healed == []
    saved = yaml.safe_load((tmp_path / "workspace.yaml").read_text())
    assert saved == original


def test_partial_heal_not_saved_if_still_invalid(tmp_path, monkeypatch):
    # One entry is healable, another is not → the doc still wouldn't validate,
    # so nothing is written (all-or-nothing; no half-repaired file on disk).
    original = {
        "name": "ws",
        "imports": {
            "foo": {"source": "https://x/foo.git", "ref": "", "mode": "reference"},
            "ghost": {"source": "", "ref": "", "mode": "reference"},
        },
    }
    _setup_ws(tmp_path, original)
    _catalog(monkeypatch, [{"name": "foo", "source": "https://x/foo.git",
                            "ref": "main", "mode": "reference"}])

    healed = workspace_heal.heal_workspace_imports(tmp_path)

    assert healed == []
    saved = yaml.safe_load((tmp_path / "workspace.yaml").read_text())
    assert saved == original


def test_non_dict_imports_is_noop(tmp_path, monkeypatch):
    _setup_ws(tmp_path, {"name": "ws", "imports": []})
    _catalog(monkeypatch, [])
    assert workspace_heal.heal_workspace_imports(tmp_path) == []


def test_missing_schema_is_best_effort_noop(tmp_path, monkeypatch):
    # No .pbg/schemas present → validation can't run → heal bails without raising.
    (tmp_path / "workspace.yaml").write_text(
        yaml.safe_dump({"name": "ws", "imports": {"foo": {"source": "", "ref": "", "mode": "reference"}}}),
        encoding="utf-8")
    _root.set_workspace_root(tmp_path)
    _catalog(monkeypatch, [{"name": "foo", "source": "s", "ref": "r", "mode": "reference"}])

    assert workspace_heal.heal_workspace_imports(tmp_path) == []
