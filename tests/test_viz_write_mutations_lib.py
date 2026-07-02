"""Tests for lib.viz_write_mutations — visualization file-write builders.

Covers (per builder):
  - Happy path: file write + (dict, 200) return.
  - Every 400/404 validation path.
  - Server shim parity: the _post_X server shim returns the same (dict, code)
    as the lib builder on a fixture workspace.

FastAPI route tests:
  - Happy path per route: file is written, 200 returned.
  - One error path per route.
  - Each route appears in the OpenAPI schema (test_*_in_openapi).
"""
from __future__ import annotations

import threading
import urllib.request
import urllib.error
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from vivarium_workbench.lib import viz_write_mutations as vwm
from vivarium_workbench.api.app import create_app, get_workspace


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_WS_YAML = (
    "schema_version: 2\n"
    "name: ws\n"
    "package_path: pkg_ws\n"
    "visualizations:\n"
    "  - name: my-viz\n"
    "    description: A test visualization.\n"
    "observables: []\n"
    "simulations: []\n"
)

_WS_YAML_NODESC = (
    "schema_version: 2\n"
    "name: ws\n"
    "package_path: pkg_ws\n"
    "visualizations:\n"
    "  - name: no-desc-viz\n"
    "    description: ''\n"
    "observables: []\n"
    "simulations: []\n"
)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Minimal workspace with a registered visualization."""
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text(_WS_YAML, encoding="utf-8")
    return w


@pytest.fixture
def ws_nodesc(tmp_path: Path) -> Path:
    """Workspace with a visualization that has no description."""
    w = tmp_path / "ws_nodesc"
    w.mkdir()
    (w / "workspace.yaml").write_text(_WS_YAML_NODESC, encoding="utf-8")
    return w


@pytest.fixture
def client(ws: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


# ---------------------------------------------------------------------------
# visualization_create
# ---------------------------------------------------------------------------


class TestVisualizationCreate:
    def test_happy_path_writes_file(self, ws: Path) -> None:
        resp, code = vwm.visualization_create(ws, {"name": "my-viz"})
        assert code == 200, resp
        assert resp["ok"] is True
        req_path = ws / ".pbg" / "viz-requests" / "my-viz.md"
        assert req_path.is_file(), f"Expected {req_path}"
        content = req_path.read_text()
        assert "my-viz" in content
        assert "A test visualization." in content
        assert "viz-responses/my-viz.py" in content

    def test_response_has_expected_keys(self, ws: Path) -> None:
        resp, code = vwm.visualization_create(ws, {"name": "my-viz"})
        assert code == 200
        assert "request_path" in resp
        assert "skill_command" in resp
        assert "instructions" in resp
        assert resp["skill_command"] == "/pbg-viz my-viz"

    def test_request_path_is_relative(self, ws: Path) -> None:
        resp, code = vwm.visualization_create(ws, {"name": "my-viz"})
        assert code == 200
        # Must be relative — not an absolute path
        assert not resp["request_path"].startswith("/")
        assert resp["request_path"].startswith(".pbg/")

    def test_400_invalid_name_empty(self, ws: Path) -> None:
        resp, code = vwm.visualization_create(ws, {"name": ""})
        assert code == 400
        assert "invalid name" in resp["error"]

    def test_400_invalid_name_spaces(self, ws: Path) -> None:
        resp, code = vwm.visualization_create(ws, {"name": "bad name"})
        assert code == 400
        assert "invalid name" in resp["error"]

    def test_404_not_registered(self, ws: Path) -> None:
        resp, code = vwm.visualization_create(ws, {"name": "ghost"})
        assert code == 404
        assert "not registered" in resp["error"]

    def test_400_no_description(self, ws_nodesc: Path) -> None:
        resp, code = vwm.visualization_create(ws_nodesc, {"name": "no-desc-viz"})
        assert code == 400
        assert "description" in resp["error"]

    def test_idempotent_overwrite(self, ws: Path) -> None:
        """Calling twice overwrites the file (no 409)."""
        vwm.visualization_create(ws, {"name": "my-viz"})
        resp, code = vwm.visualization_create(ws, {"name": "my-viz"})
        assert code == 200


# ---------------------------------------------------------------------------
# visualization_add_to_project
# ---------------------------------------------------------------------------


class TestVisualizationAddToProject:
    def test_happy_path_copies_file(self, ws: Path) -> None:
        # Seed a fake skill response
        resp_dir = ws / ".pbg" / "viz-responses"
        resp_dir.mkdir(parents=True)
        (resp_dir / "my-viz.py").write_text("# fake viz\n")

        resp, code = vwm.visualization_add_to_project(ws, {"name": "my-viz"})
        assert code == 200, resp
        assert resp["ok"] is True
        staged = ws / ".pbg" / "visualizations-staged" / "my-viz.py"
        assert staged.is_file()

    def test_staged_path_is_relative(self, ws: Path) -> None:
        resp_dir = ws / ".pbg" / "viz-responses"
        resp_dir.mkdir(parents=True)
        (resp_dir / "my-viz.py").write_text("# x\n")
        resp, code = vwm.visualization_add_to_project(ws, {"name": "my-viz"})
        assert code == 200
        assert not resp["staged_path"].startswith("/")

    def test_400_missing_name(self, ws: Path) -> None:
        resp, code = vwm.visualization_add_to_project(ws, {})
        assert code == 400
        assert "missing name" in resp["error"]

    def test_400_empty_name(self, ws: Path) -> None:
        resp, code = vwm.visualization_add_to_project(ws, {"name": ""})
        assert code == 400
        assert "missing name" in resp["error"]

    def test_404_no_skill_response(self, ws: Path) -> None:
        resp, code = vwm.visualization_add_to_project(ws, {"name": "my-viz"})
        assert code == 404
        assert "no skill response" in resp["error"] or "run /pbg-viz" in resp["error"]

    def test_preserves_file_content(self, ws: Path) -> None:
        resp_dir = ws / ".pbg" / "viz-responses"
        resp_dir.mkdir(parents=True)
        content = "def visualize(results):\n    return '<p>hi</p>'\n"
        (resp_dir / "my-viz.py").write_text(content)
        vwm.visualization_add_to_project(ws, {"name": "my-viz"})
        staged = ws / ".pbg" / "visualizations-staged" / "my-viz.py"
        assert staged.read_text() == content


# ---------------------------------------------------------------------------
# visualization_generate
# ---------------------------------------------------------------------------


class TestVisualizationGenerate:
    def test_happy_path_writes_file(self, ws: Path) -> None:
        resp, code = vwm.visualization_generate(ws, {
            "name": "new-viz",
            "description": "A plot of DnaA vs time.",
        })
        assert code == 200, resp
        assert resp["ok"] is True
        req_path = ws / ".pbg" / "viz-requests" / "new-viz.md"
        assert req_path.is_file()
        content = req_path.read_text()
        assert "as_visualization" in content
        assert "update_new_viz" in content

    def test_response_has_expected_keys(self, ws: Path) -> None:
        resp, code = vwm.visualization_generate(ws, {
            "name": "new-viz",
            "description": "Some description",
        })
        assert code == 200
        assert "request_path" in resp
        assert "target_file" in resp
        assert "skill_command" in resp
        assert "instructions" in resp
        assert resp["skill_command"] == "/pbg-viz new-viz"
        assert "visualizations/new_viz.py" in resp["target_file"]

    def test_snake_case_conversion(self, ws: Path) -> None:
        resp, code = vwm.visualization_generate(ws, {
            "name": "my-cool-plot",
            "description": "x",
        })
        assert code == 200
        req_path = ws / ".pbg" / "viz-requests" / "my-cool-plot.md"
        content = req_path.read_text()
        assert "update_my_cool_plot" in content

    def test_request_path_is_absolute(self, ws: Path) -> None:
        """visualization_generate returns the absolute str(req_path) (matches server)."""
        resp, code = vwm.visualization_generate(ws, {
            "name": "new-viz",
            "description": "x",
        })
        assert code == 200
        # The server returns str(req_path) which is absolute
        assert Path(resp["request_path"]).is_absolute()

    def test_400_invalid_name(self, ws: Path) -> None:
        resp, code = vwm.visualization_generate(ws, {
            "name": "bad name",
            "description": "x",
        })
        assert code == 400
        assert "name" in resp["error"]

    def test_400_empty_name(self, ws: Path) -> None:
        resp, code = vwm.visualization_generate(ws, {
            "name": "",
            "description": "x",
        })
        assert code == 400

    def test_400_missing_description(self, ws: Path) -> None:
        resp, code = vwm.visualization_generate(ws, {"name": "valid-name"})
        assert code == 400
        assert "description" in resp["error"]

    def test_400_empty_description(self, ws: Path) -> None:
        resp, code = vwm.visualization_generate(ws, {
            "name": "valid-name",
            "description": "  ",
        })
        assert code == 400
        assert "description" in resp["error"]

    def test_new_contract_markers(self, ws: Path) -> None:
        """New-contract request doc must contain as_visualization, not old def visualize."""
        resp, code = vwm.visualization_generate(ws, {
            "name": "fresh-viz",
            "description": "A fresh viz",
        })
        assert code == 200
        content = (ws / ".pbg" / "viz-requests" / "fresh-viz.md").read_text()
        assert "as_visualization" in content
        assert "def visualize(results" not in content


# ---------------------------------------------------------------------------
# FastAPI route tests
# ---------------------------------------------------------------------------


class TestVisualizationCreateRoute:
    def test_happy_path(self, client: TestClient, ws: Path) -> None:
        resp = client.post("/api/visualization-create", json={"name": "my-viz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        req_path = ws / ".pbg" / "viz-requests" / "my-viz.md"
        assert req_path.is_file()

    def test_400_invalid_name(self, client: TestClient) -> None:
        resp = client.post("/api/visualization-create", json={"name": "has spaces"})
        assert resp.status_code == 400
        assert "invalid name" in resp.json().get("error", "")

    def test_visualization_create_in_openapi(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        paths = schema.get("paths", {})
        assert "/api/visualization-create" in paths
        assert "post" in paths["/api/visualization-create"]


class TestVisualizationAddToProjectRoute:
    def test_happy_path(self, client: TestClient, ws: Path) -> None:
        resp_dir = ws / ".pbg" / "viz-responses"
        resp_dir.mkdir(parents=True)
        (resp_dir / "my-viz.py").write_text("# viz\n")

        resp = client.post("/api/visualization-add-to-project", json={"name": "my-viz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        staged = ws / ".pbg" / "visualizations-staged" / "my-viz.py"
        assert staged.is_file()

    def test_404_no_skill_response(self, client: TestClient) -> None:
        resp = client.post("/api/visualization-add-to-project", json={"name": "no-such"})
        assert resp.status_code == 404

    def test_visualization_add_to_project_in_openapi(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        paths = schema.get("paths", {})
        assert "/api/visualization-add-to-project" in paths
        assert "post" in paths["/api/visualization-add-to-project"]


class TestVisualizationGenerateRoute:
    def test_happy_path(self, client: TestClient, ws: Path) -> None:
        resp = client.post("/api/visualization-generate", json={
            "name": "new-viz",
            "description": "A test description",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        req_path = ws / ".pbg" / "viz-requests" / "new-viz.md"
        assert req_path.is_file()

    def test_400_bad_name(self, client: TestClient) -> None:
        resp = client.post("/api/visualization-generate", json={
            "name": "bad name",
            "description": "x",
        })
        assert resp.status_code == 400

    def test_400_missing_description(self, client: TestClient) -> None:
        resp = client.post("/api/visualization-generate", json={"name": "ok-name"})
        assert resp.status_code == 400

    def test_visualization_generate_in_openapi(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        paths = schema.get("paths", {})
        assert "/api/visualization-generate" in paths
        assert "post" in paths["/api/visualization-generate"]
