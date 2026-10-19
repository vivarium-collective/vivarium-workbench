"""Tests for the schema-driven deprecated-field migration (migrate-fields)."""
import json

import pytest

from vivarium_workbench.lib.field_migrate import (
    migrate_fields,
    rename_top_level_keys,
    safe_renames_from_schema,
)

# A study schema mirroring the real viva-template deprecations: four safe 1:1
# prose/list renames, the many-to-one status collapse, an enum-vocabulary
# mismatch, and a dotted (structural) replacement.
_SCHEMA = {
    "properties": {
        "objective": {"type": "string", "deprecated": True, "x-replaced-by": "experiment"},
        "experiment": {"type": "string"},
        "hypothesis": {"type": "string", "deprecated": True, "x-replaced-by": "claim"},
        "claim": {"type": "string"},
        "observables": {"type": "array", "deprecated": True, "x-replaced-by": "readouts"},
        "readouts": {"type": "array"},
        "expected_behavior": {"type": "array", "deprecated": True, "x-replaced-by": "behavior_tests"},
        "behavior_tests": {"type": "array"},
        # many-to-one collapse → excluded
        "design_status": {"enum": ["planned", "done"], "deprecated": True, "x-replaced-by": "execution_status"},
        "simulation_status": {"enum": ["planned", "done"], "deprecated": True, "x-replaced-by": "execution_status"},
        "execution_status": {"enum": ["planned", "running", "done"]},
        # enum vocabulary differs → excluded
        "expert_review_status": {"enum": ["unreviewed", "reviewed"], "deprecated": True, "x-replaced-by": "evidence_tier"},
        "evidence_tier": {"enum": ["T0", "T1", "T2"]},
        # dotted / structural replacement → excluded
        "runs": {"type": "array", "deprecated": True, "x-replaced-by": "runs.db"},
    }
}


def test_safe_renames_extracts_only_prose_list_renames():
    renames, excluded = safe_renames_from_schema(_SCHEMA)
    assert renames == {
        "objective": "experiment",
        "hypothesis": "claim",
        "observables": "readouts",
        "expected_behavior": "behavior_tests",
    }
    excluded_fields = {e["field"] for e in excluded}
    assert excluded_fields == {
        "design_status", "simulation_status", "expert_review_status", "runs",
    }


def test_rename_is_format_preserving_and_idempotent():
    text = (
        "name: s\n"
        "objective: |\n"
        "  Study the thing.  # keep this comment\n"
        "  Second line.\n"
        "status: planned\n"
    )
    renames = {"objective": "experiment"}
    out, changed, conflicts = rename_top_level_keys(text, renames)
    assert changed == ["objective->experiment"]
    assert conflicts == []
    # Only the key line changed; the value block + comment are byte-identical.
    assert out == text.replace("objective: |", "experiment: |", 1)
    # Idempotent: a second pass (now the new key exists, old gone) is a no-op.
    out2, changed2, conflicts2 = rename_top_level_keys(out, renames)
    assert out2 == out and changed2 == [] and conflicts2 == []


def test_rename_is_conflict_guarded():
    text = "objective: old wording\nexperiment: new wording\n"
    out, changed, conflicts = rename_top_level_keys(text, {"objective": "experiment"})
    assert out == text  # untouched
    assert changed == []
    assert conflicts == ["objective+experiment"]


def test_rename_only_touches_top_level_keys():
    # A nested `objective:` under a presentation block must NOT be renamed.
    text = "report:\n  objective: shown in the report card\nname: s\n"
    out, changed, _ = rename_top_level_keys(text, {"objective": "experiment"})
    assert out == text
    assert changed == []


@pytest.fixture
def _ws(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".pbg" / "schemas").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: t\n")
    (ws / ".pbg" / "schemas" / "study.schema.json").write_text(json.dumps(_SCHEMA))

    # flat authored study with two legacy fields + a comment
    s1 = ws / "studies" / "alpha"
    s1.mkdir(parents=True)
    (s1 / "study.yaml").write_text(
        "name: alpha\nobjective: measure X  # legacy\nhypothesis: X rises\nstatus: planned\n"
    )
    # flat study that is already canonical (no-op) but has a conflict on observables
    s2 = ws / "studies" / "beta"
    s2.mkdir(parents=True)
    (s2 / "study.yaml").write_text(
        "name: beta\nexperiment: measure Y\nobservables: [a]\nreadouts: [b]\n"
    )
    # nested authored study under an investigation
    s3 = ws / "investigations" / "inv1" / "studies" / "gamma"
    s3.mkdir(parents=True)
    (s3 / "study.yaml").write_text("name: gamma\nobjective: measure Z\n")
    # a regenerated/cache copy under .pbg — must be left untouched
    cache = ws / ".pbg" / "cache" / "studies" / "alpha"
    cache.mkdir(parents=True)
    (cache / "study.yaml").write_text("name: alpha\nobjective: measure X\n")
    return ws


def test_migrate_fields_flat_and_nested(_ws):
    result = migrate_fields(_ws, dry_run=False)
    assert result["migrated"] == 2  # alpha + gamma (beta is no-op/conflict)
    assert (_ws / "studies" / "alpha" / "study.yaml").read_text() == (
        "name: alpha\nexperiment: measure X  # legacy\nclaim: X rises\nstatus: planned\n"
    )
    assert "experiment: measure Z" in (_ws / "investigations" / "inv1" / "studies" / "gamma" / "study.yaml").read_text()
    # conflict surfaced, not clobbered
    assert result["conflicts"] == [{"study": "beta", "fields": ["observables+readouts"]}]
    assert (_ws / "studies" / "beta" / "study.yaml").read_text() == (
        "name: beta\nexperiment: measure Y\nobservables: [a]\nreadouts: [b]\n"
    )
    # the .pbg cache copy was never visited
    assert (_ws / ".pbg" / "cache" / "studies" / "alpha" / "study.yaml").read_text() == (
        "name: alpha\nobjective: measure X\n"
    )


def test_migrate_fields_dry_run_writes_nothing(_ws):
    before = (_ws / "studies" / "alpha" / "study.yaml").read_text()
    result = migrate_fields(_ws, dry_run=True)
    assert result["would_migrate"] == 2
    assert (_ws / "studies" / "alpha" / "study.yaml").read_text() == before


def test_migrate_fields_idempotent(_ws):
    migrate_fields(_ws, dry_run=False)
    second = migrate_fields(_ws, dry_run=False)
    assert second["migrated"] == 0
    assert second["conflicts"] == [{"study": "beta", "fields": ["observables+readouts"]}]


def test_missing_schema_is_a_reported_error(tmp_path):
    ws = tmp_path / "ws"
    (ws / "studies" / "a").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: t\n")
    result = migrate_fields(ws, dry_run=False)
    assert result["migrated"] == 0
    assert result["errors"] and "not found" in result["errors"][0]["error"]
