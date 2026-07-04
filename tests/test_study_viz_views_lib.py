"""Tests for lib.study_viz_views builders.

Covers:
  - build_study_bigraph_paths  (happy + 400/404/500 paths)
  - build_visualization_status (happy + 400 + missing + lifecycle ordering)
  - build_visualization_instances (happy + tolerant empty)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from vivarium_workbench.lib.study_viz_views import (
    build_study_bigraph_paths,
    build_visualization_status,
    build_visualization_instances,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def ws(tmp_path):
    """Minimal workspace.yaml with a named viz + a class-backed viz."""
    (tmp_path / "workspace.yaml").write_text(yaml.dump({
        "name": "testws",
        "package_path": "pbg_testws",
        "visualizations": [
            {"name": "my_viz", "description": "A test viz"},
            {"name": "my_class_viz", "class": "TimeSeriesPlot",
             "description": "Class-backed viz",
             "config": {"color": "blue"}},
        ],
    }), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def study_ws(tmp_path):
    """Workspace with a study that has a baseline + a serialized composite state."""
    study_dir = tmp_path / "studies" / "my-study"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(yaml.dump({
        "name": "my-study",
        "baseline": [
            {"name": "default", "composite": "pbg_testws.composites.baseline"},
        ],
    }), encoding="utf-8")
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    state = {"store": {"bulk": {"_value": [1.0, 2.0], "_type": "numpy"}}}
    (models_dir / "baseline.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "workspace.yaml").write_text(yaml.dump({
        "name": "testws",
        "package_path": "pbg_testws",
    }), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# build_visualization_status
# ---------------------------------------------------------------------------

class TestBuildVisualizationStatus:
    def test_missing_name_returns_400(self, ws):
        body, status = build_visualization_status(ws, "")
        assert status == 400
        assert "error" in body

    def test_name_not_in_workspace_returns_missing(self, ws):
        body, status = build_visualization_status(ws, "nonexistent")
        assert status == 200
        assert body == {"status": "missing", "name": "nonexistent"}

    def test_described_when_no_files(self, ws):
        body, status = build_visualization_status(ws, "my_viz")
        assert status == 200
        assert body["status"] == "described"
        assert body["name"] == "my_viz"
        assert body["has_request"] is False
        assert body["has_response"] is False
        assert body["has_staged"] is False
        assert body["has_committed"] is False

    def test_requested_when_request_file_exists(self, ws):
        req_dir = ws / ".pbg" / "viz-requests"
        req_dir.mkdir(parents=True)
        (req_dir / "my_viz.md").write_text("# request", encoding="utf-8")
        body, status = build_visualization_status(ws, "my_viz")
        assert status == 200
        assert body["status"] == "requested"
        assert body["has_request"] is True

    def test_created_when_response_file_exists(self, ws):
        resp_dir = ws / ".pbg" / "viz-responses"
        resp_dir.mkdir(parents=True)
        (resp_dir / "my_viz.py").write_text("# response", encoding="utf-8")
        body, status = build_visualization_status(ws, "my_viz")
        assert status == 200
        assert body["status"] == "created"
        assert body["has_response"] is True

    def test_added_when_staged_file_exists(self, ws):
        staged_dir = ws / ".pbg" / "visualizations-staged"
        staged_dir.mkdir(parents=True)
        (staged_dir / "my_viz.py").write_text("# staged", encoding="utf-8")
        body, status = build_visualization_status(ws, "my_viz")
        assert status == 200
        assert body["status"] == "added"
        assert body["has_staged"] is True

    def test_committed_when_pkg_file_exists(self, ws):
        viz_dir = ws / "pbg_testws" / "visualizations"
        viz_dir.mkdir(parents=True)
        (viz_dir / "my_viz.py").write_text("# committed", encoding="utf-8")
        body, status = build_visualization_status(ws, "my_viz")
        assert status == 200
        assert body["status"] == "committed"
        assert body["has_committed"] is True

    def test_committed_takes_priority_over_staged(self, ws):
        """committed > added: if both exist, status is committed."""
        staged_dir = ws / ".pbg" / "visualizations-staged"
        staged_dir.mkdir(parents=True)
        (staged_dir / "my_viz.py").write_text("# staged", encoding="utf-8")
        viz_dir = ws / "pbg_testws" / "visualizations"
        viz_dir.mkdir(parents=True)
        (viz_dir / "my_viz.py").write_text("# committed", encoding="utf-8")
        body, status = build_visualization_status(ws, "my_viz")
        assert body["status"] == "committed"


# ---------------------------------------------------------------------------
# build_visualization_instances
# ---------------------------------------------------------------------------

class TestBuildVisualizationInstances:
    def test_returns_class_backed_entries_only(self, ws):
        result = build_visualization_instances(ws)
        assert "instances" in result
        instances = result["instances"]
        # Only the class-backed entry should appear
        assert len(instances) == 1
        inst = instances[0]
        assert inst["name"] == "my_class_viz"
        assert inst["class"] == "TimeSeriesPlot"
        assert inst["address"] == "local:TimeSeriesPlot"
        assert inst["config"] == {"color": "blue"}
        assert inst["description"] == "Class-backed viz"

    def test_empty_on_missing_workspace_yaml(self, tmp_path):
        result = build_visualization_instances(tmp_path)
        assert result == {"instances": []}

    def test_empty_when_no_class_entries(self, tmp_path):
        (tmp_path / "workspace.yaml").write_text(
            yaml.dump({"name": "ws", "visualizations": [
                {"name": "my_viz", "description": "no class"}
            ]}), encoding="utf-8"
        )
        result = build_visualization_instances(tmp_path)
        assert result == {"instances": []}


# ---------------------------------------------------------------------------
# build_study_bigraph_paths
# ---------------------------------------------------------------------------

class TestBuildStudyBigraphPaths:
    def test_missing_slug_returns_400(self, study_ws):
        body, status = build_study_bigraph_paths(study_ws, "")
        assert status == 400
        assert "error" in body

    def test_no_study_yaml_returns_404(self, tmp_path):
        (tmp_path / "workspace.yaml").write_text("{}", encoding="utf-8")
        body, status = build_study_bigraph_paths(tmp_path, "no-study")
        assert status == 404
        assert "error" in body

    def test_no_baseline_entries_returns_400(self, tmp_path):
        study_dir = tmp_path / "studies" / "s"
        study_dir.mkdir(parents=True)
        (study_dir / "study.yaml").write_text(
            yaml.dump({"name": "s", "baseline": []}), encoding="utf-8"
        )
        (tmp_path / "workspace.yaml").write_text("{}", encoding="utf-8")
        body, status = build_study_bigraph_paths(tmp_path, "s")
        assert status == 400
        assert "no baseline entries" in body["error"]

    def test_baseline_name_not_found_returns_404(self, study_ws):
        body, status = build_study_bigraph_paths(study_ws, "my-study", baseline_name="missing")
        assert status == 404
        assert "not found" in body["error"]

    def test_no_serialized_state_returns_404(self, tmp_path):
        study_dir = tmp_path / "studies" / "s"
        study_dir.mkdir(parents=True)
        (study_dir / "study.yaml").write_text(yaml.dump({
            "name": "s",
            "baseline": [{"name": "default", "composite": "pbg.baseline"}],
        }), encoding="utf-8")
        (tmp_path / "workspace.yaml").write_text("{}", encoding="utf-8")
        body, status = build_study_bigraph_paths(tmp_path, "s")
        assert status == 404
        assert "no serialized composite state found" in body["error"]

    def test_happy_path_returns_200(self, study_ws):
        body, status = build_study_bigraph_paths(study_ws, "my-study")
        assert status == 200
        assert "composite" in body
        assert "source_file" in body
        assert "max_depth" in body
        assert body["max_depth"] == 8
        assert "node_count" in body
        assert "nodes" in body

    def test_invalid_json_in_state_file_returns_500(self, tmp_path):
        study_dir = tmp_path / "studies" / "s"
        study_dir.mkdir(parents=True)
        (study_dir / "study.yaml").write_text(yaml.dump({
            "name": "s",
            "baseline": [{"name": "default", "composite": "pbg.baseline"}],
        }), encoding="utf-8")
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "baseline.json").write_text("not valid json!!!", encoding="utf-8")
        (tmp_path / "workspace.yaml").write_text("{}", encoding="utf-8")
        body, status = build_study_bigraph_paths(tmp_path, "s")
        assert status == 500
        assert "failed to parse" in body["error"]
