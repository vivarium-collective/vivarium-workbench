"""reconcile_study_status / reconcile_investigation: write evidence-derived
execution state back into study.yaml (schema-mapped, one field only)."""
import json

import pytest
import yaml

from vivarium_workbench.lib.composite_runs import (
    complete_metadata, connect, save_metadata,
)
from vivarium_workbench.lib.study_reconcile import (
    reconcile_investigation, reconcile_study_status,
)

SLUG = "s3-protrusions-to-enclosure"


def _runs(ws, slug, rows):
    sd = ws / "studies" / slug
    sd.mkdir(parents=True, exist_ok=True)
    conn = connect(sd / "runs.db")
    for run_id, status, t in rows:
        save_metadata(conn, spec_id="demo", run_id=run_id, params={}, label="",
                      started_at=t, n_steps=5)
        complete_metadata(conn, run_id=run_id, n_steps=5, status=status)
    conn.close()


def _study(ws, slug, **fields):
    sd = ws / "studies" / slug
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "study.yaml").write_text(
        yaml.safe_dump({"name": slug, **fields}, sort_keys=False), encoding="utf-8")
    return sd / "study.yaml"


@pytest.fixture
def ws(tmp_path):
    schemas = tmp_path / ".pbg" / "schemas"
    schemas.mkdir(parents=True)
    (tmp_path / "workspace.yaml").write_text("name: t\n")
    (schemas / "study.schema.json").write_text(json.dumps({"properties": {
        "simulation_status": {"enum": ["not_run", "running", "ran", "failed"]},
    }}))
    _runs(tmp_path, SLUG, [("r1", "completed", 100.0)])
    _study(tmp_path, SLUG, simulation_status="not_run",
           verdict={"label": "supported", "note": "authored"})
    _study(tmp_path, "idle", simulation_status="not_run")
    return tmp_path


def test_writes_mapped_value(ws):
    out = reconcile_study_status(ws, SLUG)
    assert out["changed"] and out["field"] == "simulation_status"
    assert out["before"] == "not_run" and out["after"] == "ran"
    assert yaml.safe_load((ws / "studies" / SLUG / "study.yaml").read_text())[
        "simulation_status"] == "ran"


def test_verdict_untouched(ws):
    before = yaml.safe_load((ws / "studies" / SLUG / "study.yaml").read_text())["verdict"]
    reconcile_study_status(ws, SLUG)
    after = yaml.safe_load((ws / "studies" / SLUG / "study.yaml").read_text())
    assert after["verdict"] == before and after["name"] == SLUG


def test_idempotent(ws):
    reconcile_study_status(ws, SLUG)
    out = reconcile_study_status(ws, SLUG)
    assert out["changed"] is False and out["skipped"] == "already fresh"


def test_only_target_file_changes(ws):
    def snap():
        return {p: p.read_bytes() for p in ws.rglob("*")
                if p.is_file() and not p.name.endswith(("-wal", "-shm"))}  # sqlite read artifacts
    before = snap()
    reconcile_study_status(ws, SLUG)
    after = snap()
    changed = {p for p in before if before[p] != after.get(p)}
    assert changed == {ws / "studies" / SLUG / "study.yaml"}
    assert set(after) == set(before)


def test_not_run_is_skipped_and_unchanged(ws):
    p = ws / "studies" / "idle" / "study.yaml"
    raw = p.read_bytes()
    out = reconcile_study_status(ws, "idle")
    assert out["changed"] is False and out["skipped"] == "no run evidence"
    assert p.read_bytes() == raw


def test_no_enum_mapping_skips(ws):
    (ws / ".pbg" / "schemas" / "study.schema.json").write_text(json.dumps(
        {"properties": {"simulation_status": {"enum": ["a", "b"]}}}))
    raw = (ws / "studies" / SLUG / "study.yaml").read_bytes()
    out = reconcile_study_status(ws, SLUG)
    assert out["changed"] is False and out["skipped"].startswith("no schema mapping")
    assert (ws / "studies" / SLUG / "study.yaml").read_bytes() == raw


def test_reconcile_investigation(ws):
    d = ws / "investigations" / "inv"
    d.mkdir(parents=True)
    (d / "investigation.yaml").write_text(
        yaml.safe_dump({"studies": [SLUG, "idle", "ghost"]}))
    out = reconcile_investigation(ws, "inv")
    by = {r["slug"]: r for r in out["studies"]}
    assert by[SLUG]["changed"] is True
    assert by["idle"]["skipped"] == "no run evidence"
    assert "error" in by["ghost"]
