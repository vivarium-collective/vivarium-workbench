"""Tests for the normalized study `interface` block (composite/config/inputs/
outputs/emitter) — `study_interface()` and its wiring into
`load_study_detail_spec`.
"""
from __future__ import annotations

import pytest
import yaml

from vivarium_workbench.lib.study_spec import study_interface, load_study_detail_spec
from vivarium_workbench.lib.investigations import InvestigationSpecError


def test_full_interface_normalizes():
    spec = {
        "composite": "baseline",
        "config": {"seed": 0},
        "inputs": [{"artifact": "sim_data", "from": "parca"}],
        "outputs": ["run_zarr"],
        "emitter": "parquet",
    }
    result = study_interface(spec)
    assert result["composite"] == "baseline"
    assert result["config"] == {"seed": 0}
    assert result["inputs"] == [{"artifact": "sim_data", "from": "parca"}]
    assert result["outputs"] == ["run_zarr"]
    assert result["emitter"] == "parquet"


def test_legacy_spec_defaults_empty():
    spec = {"name": "old_study"}
    result = study_interface(spec)
    assert result["inputs"] == []
    assert result["outputs"] == []
    assert result["config"] == {}
    assert result["composite"] is None
    assert result["emitter"] is None


def test_input_missing_from_raises():
    with pytest.raises(InvestigationSpecError):
        study_interface({"inputs": [{"artifact": "sim_data"}]})


def test_loader_attaches_interface(tmp_path):
    ws = tmp_path
    (ws / "workspace.yaml").write_text(
        yaml.safe_dump({"name": "wstest"}), encoding="utf-8"
    )
    study_dir = ws / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump({
            "name": "s1",
            "composite": "baseline",
            "config": {"seed": 0},
            "inputs": [{"artifact": "sim_data", "from": "parca"}],
            "outputs": ["run_zarr"],
        }),
        encoding="utf-8",
    )

    result = load_study_detail_spec(ws, "s1")
    assert result is not None
    assert result["interface"]["inputs"] == [{"artifact": "sim_data", "from": "parca"}]
    assert result["interface"]["composite"] == "baseline"
