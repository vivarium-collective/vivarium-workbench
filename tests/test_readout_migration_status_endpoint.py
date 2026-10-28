"""Tests for ``GET /api/study-readout-migration-status``
(``lib.readout_migration_status_views.readout_migration_status_view``).

Phase 2.1d (rewire-first): the read-only STATUS sibling of
``/api/study-readout-migrate``. This endpoint wraps
``discovery_engine.readout_migration.readout_migration_status`` unchanged — the
plugin still computes the migration buckets, only the caller (the workbench, on
behalf of the ``/viva-report`` skill) moves. These tests exercise the lib
builder directly (the same "endpoint test calls the lib fn" idiom as
``test_study_readout_migrate_endpoint.py``) plus an equivalence check against
calling ``readout_migration_status`` directly, and confirm the read-only
contract (``study.yaml`` is never modified).
"""
from pathlib import Path

import pytest

from vivarium_workbench.lib import readout_migration_status_views as views

# Mirrors test_study_readout_migrate_endpoint.py's fixture: one migratable
# magic-index readout ("DnaA monomer total") and one unresolvable prose-·
# readout ("DnaA-form counts") that stays needs_human.
STUDY_YAML_TEXT = """\
# A hand-authored study with comments that MUST survive untouched.
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
    (ws / "workspace.yaml").write_text("name: readout-migration-status-test\n")
    (ws / ".pbg").mkdir()
    sy = sd / "study.yaml"
    sy.write_text(STUDY_YAML_TEXT)
    return ws, sy


def test_missing_study_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = views.readout_migration_status_view(ws, {})
    assert status == 400
    assert "study" in body["error"]


def test_unknown_study_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = views.readout_migration_status_view(
        ws, {"study": "does-not-exist"}
    )
    assert status == 404
    assert "does-not-exist" in body["error"]


def test_status_buckets_and_is_read_only(tmp_path):
    pytest.importorskip("discovery_engine.readout_migration")

    ws, sy = _study_ws(tmp_path)
    original = sy.read_text()

    body, status = views.readout_migration_status_view(
        ws, {"study": "dnaa-test"}
    )

    assert status == 200
    assert body["study"] == "dnaa-test"

    # The three buckets the plugin returns are all present.
    assert set(body) >= {"canonical", "migratable", "needs_human", "study"}

    # The magic-index readout is migratable; the prose-· readout needs a human.
    migratable_names = [r.get("name") for r in body["migratable"]]
    assert "DnaA monomer total" in migratable_names
    assert [h["name"] for h in body["needs_human"]] == ["DnaA-form counts"]

    # PURE read: study.yaml is left byte-for-byte identical.
    assert sy.read_text() == original


def test_equivalence_with_direct_readout_migration_status_call(tmp_path):
    """The endpoint's result must match calling
    ``discovery_engine.readout_migration.readout_migration_status`` directly —
    same buckets, same values."""
    pbg_readout_migration = pytest.importorskip(
        "discovery_engine.readout_migration"
    )

    ws, sy = _study_ws(tmp_path)
    study_dir = sy.parent

    endpoint_body, status = views.readout_migration_status_view(
        ws, {"study": "dnaa-test"}
    )
    assert status == 200

    direct = pbg_readout_migration.readout_migration_status(study_dir)
    for key in direct:
        assert endpoint_body[key] == direct[key], key
