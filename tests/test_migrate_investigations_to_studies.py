"""Tests for the investigations → studies migration CLI."""
import yaml
from pathlib import Path

import pytest


@pytest.fixture
def _ws_with_v2_investigation(tmp_path):
    """Workspace with one v2 investigation directory."""
    ws = tmp_path / "ws"
    inv = ws / "investigations" / "old"
    inv.mkdir(parents=True)
    (inv / "spec.yaml").write_text(yaml.safe_dump({
        "schema_version": 2,
        "name": "old",
        "created": "2026-04-01",
        "composites": [
            {"name": "main", "source": "pkg.composites.foo", "parameters": {"x": 1}},
        ],
        "runs": [],
        "variants": [],
    }))
    (inv / "notes.md").write_text("hello")
    (inv / "composites").mkdir()
    (inv / "viz").mkdir()
    return ws


def test_migration_creates_studies_dir(_ws_with_v2_investigation):
    from vivarium_dashboard.cli import migrate_investigations_to_studies
    result = migrate_investigations_to_studies(_ws_with_v2_investigation, dry_run=False)
    sd = _ws_with_v2_investigation / "studies" / "old"
    assert sd.is_dir()
    assert (sd / "study.yaml").is_file()
    assert (sd / "notes.md").read_text() == "hello"
    assert not (_ws_with_v2_investigation / "investigations" / "old").exists()
    assert result["migrated"] == 1


def test_migration_dry_run_makes_no_changes(_ws_with_v2_investigation):
    from vivarium_dashboard.cli import migrate_investigations_to_studies
    result = migrate_investigations_to_studies(_ws_with_v2_investigation, dry_run=True)
    assert (_ws_with_v2_investigation / "investigations" / "old").is_dir()
    assert not (_ws_with_v2_investigation / "studies").exists()
    assert result["would_migrate"] == 1


def test_migration_rewrites_spec_to_v3(_ws_with_v2_investigation):
    """Plan 1 changed the v3 shape: ``baseline`` is now a list of
    ``{name, composite, params}`` mappings (not a single dict)."""
    from vivarium_dashboard.cli import migrate_investigations_to_studies
    migrate_investigations_to_studies(_ws_with_v2_investigation, dry_run=False)
    spec = yaml.safe_load(
        (_ws_with_v2_investigation / "studies" / "old" / "study.yaml").read_text()
    )
    assert spec["schema_version"] == 3
    assert "composites" not in spec
    assert isinstance(spec["baseline"], list)
    assert len(spec["baseline"]) == 1
    entry = spec["baseline"][0]
    assert entry["composite"] == "pkg.composites.foo"
    assert entry["params"] == {"x": 1}


def test_migration_idempotent(_ws_with_v2_investigation):
    from vivarium_dashboard.cli import migrate_investigations_to_studies
    migrate_investigations_to_studies(_ws_with_v2_investigation, dry_run=False)
    # Running again is a no-op (investigations/ is gone)
    result = migrate_investigations_to_studies(_ws_with_v2_investigation, dry_run=False)
    assert result["migrated"] == 0
