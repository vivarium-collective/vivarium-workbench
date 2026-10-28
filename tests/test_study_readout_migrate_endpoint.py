"""Tests for ``POST /api/study-readout-migrate``
(``lib.readout_migrate_views.study_readout_migrate``).

Phase 2.1d (rewire-first): this endpoint wraps
``discovery_engine.readout_migration.migrate_study_file`` unchanged — the
plugin still computes the migration, only the caller (the workbench, on
behalf of the ``/viva-report`` skill) moves. These tests exercise the lib
builder directly (the same "endpoint test calls the lib fn" idiom as
``test_study_sync_runs_endpoint.py``) plus an equivalence check against
calling ``migrate_study_file`` directly, since the plan requires the
endpoint's output to match the plugin function's output.
"""
from pathlib import Path

import pytest

from vivarium_workbench.lib import readout_migrate_views

# Mirrors pbg-superpowers/tests/test_readout_migration.py's STUDY_YAML_TEXT:
# one migratable magic-index readout ("DnaA monomer total") and one
# unresolvable prose-· readout ("DnaA-form counts") that stays needs_human.
STUDY_YAML_TEXT = """\
# A hand-authored study with comments that MUST survive migration.
name: dnaa-test
objective: |
  Multi-line objective prose.
readouts:
  - name: DnaA monomer total  # the magic-index one
    status: primary
    identifier: listeners.monomer_counts[3861]
    units: molecules/cell
  - name: DnaA-form counts
    status: measured
    identifier: "bulk PD03831[c] (apo) · MONOMER0-160[c] (DnaA-ATP)"
    units: molecules/cell
# trailing comment
baselines: []
"""


def _study_ws(tmp_path: Path, slug: str = "dnaa-test") -> tuple[Path, Path]:
    ws = tmp_path / "ws"
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: readout-migrate-test\n")
    (ws / ".pbg").mkdir()
    sy = sd / "study.yaml"
    sy.write_text(STUDY_YAML_TEXT)
    return ws, sy


def test_missing_study_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = readout_migrate_views.study_readout_migrate(ws, {})
    assert status == 400
    assert "study" in body["error"]


def test_unknown_study_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = readout_migrate_views.study_readout_migrate(
        ws, {"study": "does-not-exist"}
    )
    assert status == 404
    assert "does-not-exist" in body["error"]


def test_dry_run_does_not_write_and_reports_migration(tmp_path):
    ws, sy = _study_ws(tmp_path)
    original = sy.read_text()

    body, status = readout_migrate_views.study_readout_migrate(
        ws, {"study": "dnaa-test"}
    )

    assert status == 200
    assert body["study"] == "dnaa-test"
    assert body["written"] is False
    assert sy.read_text() == original  # dry-run: untouched
    assert "DnaA monomer total" in body["migrated"]
    assert [h["name"] for h in body["needs_human"]] == ["DnaA-form counts"]


def test_write_true_rewrites_readouts_block(tmp_path):
    ws, sy = _study_ws(tmp_path)
    original = sy.read_text()

    body, status = readout_migrate_views.study_readout_migrate(
        ws, {"study": "dnaa-test", "write": True}
    )

    assert status == 200
    assert body["written"] is True
    assert body["changed"] is True
    text = sy.read_text()
    assert text != original
    # comments + hand-authored content survive the round-trip.
    assert "MUST survive migration" in text
    assert "trailing comment" in text
    # the magic index is migrated away; the unresolvable readout is kept.
    assert "monomer_counts[3861]" not in text
    assert "PD03831[c] (apo)" in text


def test_apply_alias_behaves_like_write(tmp_path):
    """``apply`` is accepted as a synonym for ``write`` (plan wording used
    "write/apply flag" ambiguously; support both so either caller works)."""
    ws, sy = _study_ws(tmp_path)
    body, status = readout_migrate_views.study_readout_migrate(
        ws, {"study": "dnaa-test", "apply": True}
    )
    assert status == 200
    assert body["written"] is True


def test_equivalence_with_direct_migrate_study_file_call(tmp_path):
    """The endpoint's report must match calling
    ``discovery_engine.readout_migration.migrate_study_file`` directly —
    same keys, same values, for both the dry-run and the write path."""
    pbg_readout_migration = pytest.importorskip(
        "discovery_engine.readout_migration"
    )

    ws, sy = _study_ws(tmp_path)
    study_dir = sy.parent

    endpoint_body, status = readout_migrate_views.study_readout_migrate(
        ws, {"study": "dnaa-test"}
    )
    assert status == 200
    direct_report = pbg_readout_migration.migrate_study_file(study_dir, write=False)
    for key in direct_report:
        assert endpoint_body[key] == direct_report[key], key

    # Fresh copy for the write path (dry-run above didn't touch the file).
    endpoint_body2, status2 = readout_migrate_views.study_readout_migrate(
        ws, {"study": "dnaa-test", "write": True}
    )
    assert status2 == 200
    assert endpoint_body2["written"] is True

    ws3, sy3 = _study_ws(tmp_path / "cmp2", slug="dnaa-test")
    direct_report2 = pbg_readout_migration.migrate_study_file(
        sy3.parent, write=True
    )
    for key in direct_report2:
        if key == "study_yaml":
            continue  # absolute paths differ by construction
        assert endpoint_body2[key] == direct_report2[key], key
