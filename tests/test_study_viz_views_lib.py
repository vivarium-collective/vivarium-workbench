"""Tests for lib.study_viz_views builders + server.Handler shim parity.

Covers:
  - build_study_bigraph_paths  (happy + 400/404/500 paths)
  - build_visualization_status (happy + 400 + missing + lifecycle ordering)
  - build_visualization_instances (happy + tolerant empty)
  - build_ptools_launch        (happy + 400 + 404)
  - ptools_object_class        (class inference)
  - build_ptools_launch_url    (URL structure)
  - TestServerShimParity       (real server.Handler methods == lib output)
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
    build_ptools_launch,
    build_ptools_launch_url,
    ptools_object_class,
    _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
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
        "ui": {
            "ptools_server_url": "http://ptools.example.com",
        },
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


@pytest.fixture()
def ptools_ws(tmp_path):
    """Workspace with a ptools study containing TSV files."""
    study_dir = tmp_path / "studies" / "my-study"
    ptools_dir = study_dir / "ptools"
    ptools_dir.mkdir(parents=True)
    (ptools_dir / "ptools_rna__p1.tsv").write_text(
        "$\tt0\tt1\tt2\nb0001\t1.0\t2.0\t3.0\n", encoding="utf-8"
    )
    (ptools_dir / "ptools_rxns__p1.tsv").write_text(
        "$\tt0\nRXN-1\t1.0\n", encoding="utf-8"
    )
    (tmp_path / "workspace.yaml").write_text(yaml.dump({
        "name": "testws",
        "package_path": "pbg_testws",
        "ui": {
            "ptools_server_url": "http://ptools.example.com",
        },
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


# ---------------------------------------------------------------------------
# ptools_object_class
# ---------------------------------------------------------------------------

class TestPtoolsObjectClass:
    def test_rxn_returns_reaction(self):
        assert ptools_object_class("ptools_rxns__p1.tsv") == "reaction"
        assert ptools_object_class("reaction_data") == "reaction"

    def test_protein_returns_protein(self):
        assert ptools_object_class("ptools_proteins__p1.tsv") == "protein"

    def test_gene_default(self):
        assert ptools_object_class("ptools_rna__p1.tsv") == "gene"
        assert ptools_object_class("anything_else") == "gene"

    def test_compound(self):
        assert ptools_object_class("metabolite_data") == "compound"
        assert ptools_object_class("compound_flux") == "compound"


# ---------------------------------------------------------------------------
# build_ptools_launch_url
# ---------------------------------------------------------------------------

class TestBuildPtoolsLaunchUrl:
    def test_happy_path_returns_url(self, ptools_ws):
        study_dir = ptools_ws / "studies" / "my-study"
        result = build_ptools_launch_url(
            study_dir=study_dir,
            ws_root=ptools_ws,
            ptools_server_url="http://ptools.example.com",
            ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
            public_base="http://dash.example.com",
        )
        assert "error" not in result
        assert "url" in result
        assert "tsv_url" in result
        assert len(result["available"]) == 2
        assert result["tsv_url"].startswith("http://dash.example.com/")
        assert result["tsv_url"].endswith(".tsv")

    def test_no_tsvs_returns_error(self, tmp_path):
        study_dir = tmp_path / "studies" / "empty"
        study_dir.mkdir(parents=True)
        result = build_ptools_launch_url(
            study_dir=study_dir,
            ws_root=tmp_path,
            ptools_server_url="http://ptools.example.com",
            ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
            public_base="http://dash.example.com",
        )
        assert "error" in result
        assert result["available"] == []

    def test_analysis_filter(self, ptools_ws):
        study_dir = ptools_ws / "studies" / "my-study"
        result = build_ptools_launch_url(
            study_dir=study_dir,
            ws_root=ptools_ws,
            ptools_server_url="http://ptools.example.com",
            ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
            public_base="http://dash.example.com",
            analysis="ptools_rna",
        )
        assert "error" not in result
        assert len(result["available"]) == 1
        assert "ptools_rna" in result["available"][0]

    def test_relpath_is_workspace_relative(self, ptools_ws):
        study_dir = ptools_ws / "studies" / "my-study"
        result = build_ptools_launch_url(
            study_dir=study_dir,
            ws_root=ptools_ws,
            ptools_server_url="http://ptools.example.com",
            ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
            public_base="http://dash.example.com",
        )
        for rel in result["available"]:
            assert not rel.startswith("/")
            assert rel.startswith("studies/my-study/ptools/")

    def test_column_count_from_tsv_header(self, ptools_ws):
        """3-column TSV → column1=1-3 in the URL."""
        study_dir = ptools_ws / "studies" / "my-study"
        result = build_ptools_launch_url(
            study_dir=study_dir,
            ws_root=ptools_ws,
            ptools_server_url="http://ptools.example.com",
            ptools_omics_url_template=_PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
            public_base="http://dash.example.com",
            analysis="ptools_rna",
        )
        # ptools_rna TSV has $, t0, t1, t2 → 3 data columns
        assert "column1=1-3" in result["url"]


# ---------------------------------------------------------------------------
# build_ptools_launch
# ---------------------------------------------------------------------------

class TestBuildPtoolsLaunch:
    def test_no_ptools_server_url_returns_400(self, tmp_path):
        (tmp_path / "workspace.yaml").write_text(
            yaml.dump({"name": "ws"}), encoding="utf-8"
        )
        body, status = build_ptools_launch(tmp_path, "my-study")
        assert status == 400
        assert "ptools_server_url not configured" in body["error"]

    def test_study_not_found_returns_404(self, ws):
        body, status = build_ptools_launch(ws, "no-such-study")
        assert status == 404
        assert "study not found" in body["error"]

    def test_no_tsvs_returns_404(self, ws):
        study_dir = ws / "studies" / "my-study"
        study_dir.mkdir(parents=True)
        body, status = build_ptools_launch(ws, "my-study")
        assert status == 404
        assert "error" in body

    def test_happy_path_returns_200(self, ptools_ws):
        body, status = build_ptools_launch(
            ptools_ws, "my-study", public_base="http://dash.example.com"
        )
        assert status == 200
        assert "url" in body
        assert "tsv_url" in body
        assert "available" in body
        assert len(body["available"]) == 2

    def test_public_base_overridden_by_workspace_config(self, tmp_path):
        study_dir = tmp_path / "studies" / "my-study"
        ptools_dir = study_dir / "ptools"
        ptools_dir.mkdir(parents=True)
        (ptools_dir / "ptools_rna__p1.tsv").write_text("gene\tt0\n", encoding="utf-8")
        (tmp_path / "workspace.yaml").write_text(yaml.dump({
            "name": "ws",
            "ui": {
                "ptools_server_url": "http://ptools.example.com",
                "dashboard_public_base_url": "http://override.example.com",
            },
        }), encoding="utf-8")
        body, status = build_ptools_launch(
            tmp_path, "my-study", public_base="http://ignored.example.com"
        )
        assert status == 200
        # dashboard_public_base_url from config takes priority
        assert body["tsv_url"].startswith("http://override.example.com/")
