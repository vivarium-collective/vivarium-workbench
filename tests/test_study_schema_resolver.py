import json

import pytest

from vivarium_workbench.lib.study_schema import (
    map_execution_value,
    mechanical_exec_field,
)


def _ws(tmp_path, name, properties):
    root = tmp_path / name
    schemas = root / ".pbg" / "schemas"
    schemas.mkdir(parents=True)
    (root / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    (schemas / "study.schema.json").write_text(
        json.dumps({"properties": properties}), encoding="utf-8"
    )
    return root


@pytest.fixture
def canonical_ws(tmp_path):
    return _ws(tmp_path, "canon", {
        "execution_status": {
            "enum": ["proposed", "planned", "running", "completed", "failed"]},
        "simulation_status": {
            "deprecated": True, "x-replaced-by": "execution_status",
            "enum": ["not_run", "running", "ran", "failed"]},
    })


@pytest.fixture
def older_ws(tmp_path):
    return _ws(tmp_path, "older", {
        "simulation_status": {"enum": ["not_run", "running", "ran", "failed"]},
    })


def test_resolves_canonical_field(canonical_ws):
    field, enum = mechanical_exec_field(canonical_ws)
    assert field == "execution_status" and "completed" in enum


def test_resolves_older_simulation_status_field(older_ws):
    field, enum = mechanical_exec_field(older_ws)
    assert field == "simulation_status" and "completed" not in enum and "ran" in enum


def test_no_field_raises(tmp_path):
    with pytest.raises(ValueError):
        mechanical_exec_field(_ws(tmp_path, "none", {"title": {}}))


def test_maps_completed_to_ran_when_absent():
    assert map_execution_value("completed", frozenset({"not_run", "running", "ran", "failed"})) == "ran"


def test_maps_identity_when_present():
    assert map_execution_value("completed", frozenset({"planned", "running", "completed", "failed"})) == "completed"


def test_not_run_falls_back_to_planned():
    assert map_execution_value("not_run", frozenset({"planned", "completed"})) == "planned"


def test_no_safe_mapping_raises():
    with pytest.raises(ValueError):
        map_execution_value("failed", frozenset({"ran"}))
