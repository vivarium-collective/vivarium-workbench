"""Tests for lib.compare_group_mutations — investigation comparison & group builders.

Covers (per builder):
  - Happy path: file mutation + (dict, 200) return.
  - Every 400/404 validation path (ALL error messages + order).
  - 409 conflict (comparison_add, group_add).
  - 404 not-found (comparison_update, group_update).

Behavioral commit-path tests (TestServerCommitPath):
  - Monkeypatches server._commit_or_run to a recorder.
  - Drives the REAL _post_* handler (not the lib builder).
  - Asserts exact commit_msg string (verbatim from the brief).
  - Asserts validation 400/404 short-circuits BEFORE the wrapper.
  - Asserts do_action() re-raises on a lib non-200 (batch-18 lesson).

FastAPI route tests (TestApiRoutes):
  - Happy path per route via TestClient.
  - Error paths (400/404/409).
  - Each route appears in the OpenAPI schema.
"""
from __future__ import annotations

import yaml
import pytest
from fastapi.testclient import TestClient

from vivarium_workbench.lib import compare_group_mutations as cgm
from vivarium_workbench.api.app import create_app, get_workspace
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_WS_YAML = "schema_version: 2\nname: ws\ncreated: '2026-01-01'\nplugin_version: 0.6.1\npackage_path: pkg\n"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Workspace with one investigation that has variants, comparisons, and groups."""
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text(_WS_YAML, encoding="utf-8")
    inv_dir = w / "investigations" / "demo"
    inv_dir.mkdir(parents=True)
    (inv_dir / "spec.yaml").write_text(yaml.safe_dump({
        "name": "demo",
        "variants": [
            {"name": "baseline", "source": "pkg.x"},
            {"name": "high-rate", "extends": "baseline"},
        ],
        "comparisons": [],
        "groups": [],
    }, sort_keys=False), encoding="utf-8")
    return w


@pytest.fixture
def client(ws: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def _read_spec(ws: Path) -> dict:
    return yaml.safe_load(
        (ws / "investigations" / "demo" / "spec.yaml").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# comparison_add
# ---------------------------------------------------------------------------


class TestComparisonAdd:
    def test_happy_path(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "investigation": "demo",
            "name": "rate-cmp",
            "variants": ["baseline", "high-rate"],
            "observables": ["DnaA_count"],
            "description": "rate doubling",
        })
        assert code == 200, resp
        assert resp == {"ok": True}
        spec = _read_spec(ws)
        assert len(spec["comparisons"]) == 1
        c = spec["comparisons"][0]
        assert c["name"] == "rate-cmp"
        assert c["description"] == "rate doubling"
        assert c["variants"] == ["baseline", "high-rate"]
        assert c["observables"] == ["DnaA_count"]

    def test_study_alias_accepted(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "study": "demo",
            "name": "cmp-a",
            "variants": ["baseline"],
            "observables": ["x"],
        })
        assert code == 200, resp

    def test_description_defaults_empty(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "investigation": "demo",
            "name": "cmp-b",
            "variants": ["baseline"],
            "observables": ["y"],
        })
        assert code == 200, resp
        spec = _read_spec(ws)
        assert spec["comparisons"][0]["description"] == ""

    def test_400_missing_investigation(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "name": "cmp", "variants": ["baseline"], "observables": ["x"],
        })
        assert code == 400
        assert resp["error"] == "investigation required"

    def test_400_missing_name(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "investigation": "demo", "variants": ["baseline"], "observables": ["x"],
        })
        assert code == 400
        assert resp["error"] == "name required"

    def test_400_variants_empty(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "investigation": "demo", "name": "c",
            "variants": [], "observables": ["x"],
        })
        assert code == 400
        assert resp["error"] == "variants must be a non-empty list"

    def test_400_variants_not_a_list(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "investigation": "demo", "name": "c",
            "variants": "baseline", "observables": ["x"],
        })
        assert code == 400
        assert "variants must be a non-empty list" in resp["error"]

    def test_400_observables_empty(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "investigation": "demo", "name": "c",
            "variants": ["baseline"], "observables": [],
        })
        assert code == 400
        assert resp["error"] == "observables must be a non-empty list"

    def test_400_observables_not_a_list(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "investigation": "demo", "name": "c",
            "variants": ["baseline"], "observables": "x",
        })
        assert code == 400
        assert "observables must be a non-empty list" in resp["error"]

    def test_400_description_not_a_string(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "investigation": "demo", "name": "c",
            "variants": ["baseline"], "observables": ["x"],
            "description": 42,
        })
        assert code == 400
        assert resp["error"] == "description must be a string"

    def test_404_investigation_not_found(self, ws: Path) -> None:
        resp, code = cgm.comparison_add(ws, {
            "investigation": "ghost", "name": "c",
            "variants": ["baseline"], "observables": ["x"],
        })
        assert code == 404
        assert resp["error"] == "investigation not found"

    def test_409_duplicate_name(self, ws: Path) -> None:
        cgm.comparison_add(ws, {
            "investigation": "demo", "name": "dup",
            "variants": ["baseline"], "observables": ["x"],
        })
        resp, code = cgm.comparison_add(ws, {
            "investigation": "demo", "name": "dup",
            "variants": ["baseline"], "observables": ["y"],
        })
        assert code == 409
        assert "already exists" in resp["error"]


# ---------------------------------------------------------------------------
# comparison_update
# ---------------------------------------------------------------------------


class TestComparisonUpdate:
    @pytest.fixture(autouse=True)
    def seed(self, ws: Path) -> None:
        cgm.comparison_add(ws, {
            "investigation": "demo", "name": "cmp-1",
            "variants": ["baseline"], "observables": ["DnaA_count"],
            "description": "original",
        })

    def test_happy_path_update_description(self, ws: Path) -> None:
        resp, code = cgm.comparison_update(ws, {
            "investigation": "demo",
            "name": "cmp-1",
            "fields_to_update": {"description": "updated"},
        })
        assert code == 200, resp
        assert resp == {"ok": True}
        c = _read_spec(ws)["comparisons"][0]
        assert c["description"] == "updated"
        assert c["variants"] == ["baseline"]  # unchanged
        assert c["observables"] == ["DnaA_count"]  # unchanged

    def test_happy_path_update_variants(self, ws: Path) -> None:
        resp, code = cgm.comparison_update(ws, {
            "investigation": "demo",
            "name": "cmp-1",
            "fields_to_update": {"variants": ["baseline", "high-rate"]},
        })
        assert code == 200, resp
        assert _read_spec(ws)["comparisons"][0]["variants"] == ["baseline", "high-rate"]

    def test_happy_path_no_fields(self, ws: Path) -> None:
        """Empty fields_to_update is accepted (no-op)."""
        resp, code = cgm.comparison_update(ws, {
            "investigation": "demo",
            "name": "cmp-1",
            "fields_to_update": {},
        })
        assert code == 200, resp

    def test_400_missing_investigation(self, ws: Path) -> None:
        resp, code = cgm.comparison_update(ws, {"name": "cmp-1", "fields_to_update": {}})
        assert code == 400
        assert resp["error"] == "investigation required"

    def test_400_missing_name(self, ws: Path) -> None:
        resp, code = cgm.comparison_update(ws, {
            "investigation": "demo", "fields_to_update": {},
        })
        assert code == 400
        assert resp["error"] == "name required"

    def test_400_fields_not_a_mapping(self, ws: Path) -> None:
        resp, code = cgm.comparison_update(ws, {
            "investigation": "demo", "name": "cmp-1",
            "fields_to_update": ["description"],
        })
        assert code == 400
        assert resp["error"] == "fields_to_update must be a mapping"

    def test_404_investigation_not_found(self, ws: Path) -> None:
        resp, code = cgm.comparison_update(ws, {
            "investigation": "ghost", "name": "cmp-1", "fields_to_update": {},
        })
        assert code == 404
        assert resp["error"] == "investigation not found"

    def test_404_comparison_not_found(self, ws: Path) -> None:
        resp, code = cgm.comparison_update(ws, {
            "investigation": "demo", "name": "no-such", "fields_to_update": {},
        })
        assert code == 404
        assert "not found" in resp["error"]


# ---------------------------------------------------------------------------
# group_add
# ---------------------------------------------------------------------------


class TestGroupAdd:
    def test_happy_path(self, ws: Path) -> None:
        resp, code = cgm.group_add(ws, {
            "investigation": "demo",
            "name": "control",
            "variants": ["baseline"],
            "description": "Baseline condition.",
        })
        assert code == 200, resp
        assert resp == {"ok": True}
        spec = _read_spec(ws)
        assert len(spec["groups"]) == 1
        g = spec["groups"][0]
        assert g["name"] == "control"
        assert g["description"] == "Baseline condition."
        assert g["variants"] == ["baseline"]

    def test_description_defaults_empty(self, ws: Path) -> None:
        resp, code = cgm.group_add(ws, {
            "investigation": "demo",
            "name": "g-x",
            "variants": ["baseline"],
        })
        assert code == 200, resp
        assert _read_spec(ws)["groups"][0]["description"] == ""

    def test_400_missing_investigation(self, ws: Path) -> None:
        resp, code = cgm.group_add(ws, {"name": "g", "variants": ["baseline"]})
        assert code == 400
        assert resp["error"] == "investigation required"

    def test_400_missing_name(self, ws: Path) -> None:
        resp, code = cgm.group_add(ws, {"investigation": "demo", "variants": ["baseline"]})
        assert code == 400
        assert resp["error"] == "name required"

    def test_400_variants_empty(self, ws: Path) -> None:
        resp, code = cgm.group_add(ws, {
            "investigation": "demo", "name": "g", "variants": [],
        })
        assert code == 400
        assert resp["error"] == "variants must be a non-empty list"

    def test_400_description_not_a_string(self, ws: Path) -> None:
        resp, code = cgm.group_add(ws, {
            "investigation": "demo", "name": "g",
            "variants": ["baseline"], "description": 99,
        })
        assert code == 400
        assert resp["error"] == "description must be a string"

    def test_404_investigation_not_found(self, ws: Path) -> None:
        resp, code = cgm.group_add(ws, {
            "investigation": "ghost", "name": "g", "variants": ["baseline"],
        })
        assert code == 404
        assert resp["error"] == "investigation not found"

    def test_400_unknown_variant(self, ws: Path) -> None:
        resp, code = cgm.group_add(ws, {
            "investigation": "demo", "name": "g",
            "variants": ["ghost-variant"],
        })
        assert code == 400
        assert "unknown variant" in resp["error"].lower()
        assert "ghost-variant" in resp["error"]

    def test_409_duplicate_name(self, ws: Path) -> None:
        cgm.group_add(ws, {
            "investigation": "demo", "name": "dup", "variants": ["baseline"],
        })
        resp, code = cgm.group_add(ws, {
            "investigation": "demo", "name": "dup", "variants": ["high-rate"],
        })
        assert code == 409
        assert "already exists" in resp["error"]


# ---------------------------------------------------------------------------
# group_update
# ---------------------------------------------------------------------------


class TestGroupUpdate:
    @pytest.fixture(autouse=True)
    def seed(self, ws: Path) -> None:
        cgm.group_add(ws, {
            "investigation": "demo", "name": "ctrl",
            "variants": ["baseline"], "description": "original",
        })

    def test_happy_path_update_description(self, ws: Path) -> None:
        resp, code = cgm.group_update(ws, {
            "investigation": "demo",
            "name": "ctrl",
            "fields_to_update": {"description": "updated"},
        })
        assert code == 200, resp
        assert resp == {"ok": True}
        g = _read_spec(ws)["groups"][0]
        assert g["description"] == "updated"
        assert g["variants"] == ["baseline"]  # unchanged

    def test_happy_path_update_variants(self, ws: Path) -> None:
        resp, code = cgm.group_update(ws, {
            "investigation": "demo",
            "name": "ctrl",
            "fields_to_update": {"variants": ["baseline", "high-rate"]},
        })
        assert code == 200, resp
        assert _read_spec(ws)["groups"][0]["variants"] == ["baseline", "high-rate"]

    def test_happy_path_no_fields(self, ws: Path) -> None:
        resp, code = cgm.group_update(ws, {
            "investigation": "demo", "name": "ctrl", "fields_to_update": {},
        })
        assert code == 200, resp

    def test_400_missing_investigation(self, ws: Path) -> None:
        resp, code = cgm.group_update(ws, {"name": "ctrl", "fields_to_update": {}})
        assert code == 400
        assert resp["error"] == "investigation required"

    def test_400_missing_name(self, ws: Path) -> None:
        resp, code = cgm.group_update(ws, {
            "investigation": "demo", "fields_to_update": {},
        })
        assert code == 400
        assert resp["error"] == "name required"

    def test_400_fields_not_a_mapping(self, ws: Path) -> None:
        resp, code = cgm.group_update(ws, {
            "investigation": "demo", "name": "ctrl",
            "fields_to_update": "description",
        })
        assert code == 400
        assert resp["error"] == "fields_to_update must be a mapping"

    def test_404_investigation_not_found(self, ws: Path) -> None:
        resp, code = cgm.group_update(ws, {
            "investigation": "ghost", "name": "ctrl", "fields_to_update": {},
        })
        assert code == 404
        assert resp["error"] == "investigation not found"

    def test_404_group_not_found(self, ws: Path) -> None:
        resp, code = cgm.group_update(ws, {
            "investigation": "demo", "name": "no-such", "fields_to_update": {},
        })
        assert code == 404
        assert "not found" in resp["error"]

    def test_400_variants_empty_in_update(self, ws: Path) -> None:
        """Nested 400: variants in fields_to_update must be a non-empty list."""
        resp, code = cgm.group_update(ws, {
            "investigation": "demo", "name": "ctrl",
            "fields_to_update": {"variants": []},
        })
        assert code == 400
        assert resp["error"] == "variants must be a non-empty list"

    def test_400_unknown_variant_in_update(self, ws: Path) -> None:
        resp, code = cgm.group_update(ws, {
            "investigation": "demo", "name": "ctrl",
            "fields_to_update": {"variants": ["ghost"]},
        })
        assert code == 400
        assert "unknown variant" in resp["error"].lower()


# ---------------------------------------------------------------------------
# FastAPI route tests
# ---------------------------------------------------------------------------


class TestApiRoutes:
    # ---- investigation-comparison-add ----

    def test_comparison_add_200(self, client: TestClient, ws: Path) -> None:
        r = client.post("/api/investigation-comparison-add", json={
            "investigation": "demo",
            "name": "rate-cmp",
            "variants": ["baseline"],
            "observables": ["DnaA_count"],
            "description": "rate doubling",
        })
        assert r.status_code == 200, r.json()
        assert r.json() == {"ok": True}
        assert _read_spec(ws)["comparisons"][0]["name"] == "rate-cmp"

    def test_comparison_add_400_missing_investigation(self, client: TestClient) -> None:
        r = client.post("/api/investigation-comparison-add", json={
            "name": "c", "variants": ["baseline"], "observables": ["x"],
        })
        assert r.status_code == 400

    def test_comparison_add_400_missing_name(self, client: TestClient) -> None:
        r = client.post("/api/investigation-comparison-add", json={
            "investigation": "demo", "variants": ["baseline"], "observables": ["x"],
        })
        assert r.status_code == 400

    def test_comparison_add_400_empty_variants(self, client: TestClient) -> None:
        r = client.post("/api/investigation-comparison-add", json={
            "investigation": "demo", "name": "c", "variants": [], "observables": ["x"],
        })
        assert r.status_code == 400

    def test_comparison_add_404_not_found(self, client: TestClient) -> None:
        r = client.post("/api/investigation-comparison-add", json={
            "investigation": "ghost", "name": "c",
            "variants": ["baseline"], "observables": ["x"],
        })
        assert r.status_code == 404

    def test_comparison_add_409_duplicate(self, client: TestClient) -> None:
        payload = {
            "investigation": "demo", "name": "dup",
            "variants": ["baseline"], "observables": ["x"],
        }
        client.post("/api/investigation-comparison-add", json=payload)
        r = client.post("/api/investigation-comparison-add", json=payload)
        assert r.status_code == 409

    def test_comparison_add_in_openapi(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert "/api/investigation-comparison-add" in schema["paths"]

    # ---- investigation-comparison-update ----

    def test_comparison_update_200(self, client: TestClient, ws: Path) -> None:
        cgm.comparison_add(ws, {
            "investigation": "demo", "name": "c1",
            "variants": ["baseline"], "observables": ["x"],
        })
        r = client.post("/api/investigation-comparison-update", json={
            "investigation": "demo",
            "name": "c1",
            "fields_to_update": {"description": "via route"},
        })
        assert r.status_code == 200, r.json()
        assert r.json() == {"ok": True}
        assert _read_spec(ws)["comparisons"][0]["description"] == "via route"

    def test_comparison_update_400_missing_investigation(self, client: TestClient) -> None:
        r = client.post("/api/investigation-comparison-update", json={
            "name": "c1", "fields_to_update": {},
        })
        assert r.status_code == 400

    def test_comparison_update_404_not_found(self, client: TestClient) -> None:
        r = client.post("/api/investigation-comparison-update", json={
            "investigation": "demo", "name": "ghost", "fields_to_update": {},
        })
        assert r.status_code == 404

    def test_comparison_update_in_openapi(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert "/api/investigation-comparison-update" in schema["paths"]

    # ---- investigation-group-add ----

    def test_group_add_200(self, client: TestClient, ws: Path) -> None:
        r = client.post("/api/investigation-group-add", json={
            "investigation": "demo",
            "name": "control",
            "variants": ["baseline"],
            "description": "Baseline condition.",
        })
        assert r.status_code == 200, r.json()
        assert r.json() == {"ok": True}
        assert _read_spec(ws)["groups"][0]["name"] == "control"

    def test_group_add_400_missing_investigation(self, client: TestClient) -> None:
        r = client.post("/api/investigation-group-add", json={
            "name": "g", "variants": ["baseline"],
        })
        assert r.status_code == 400

    def test_group_add_400_unknown_variant(self, client: TestClient) -> None:
        r = client.post("/api/investigation-group-add", json={
            "investigation": "demo", "name": "g", "variants": ["ghost"],
        })
        assert r.status_code == 400
        assert "unknown variant" in r.json()["error"].lower()

    def test_group_add_404_not_found(self, client: TestClient) -> None:
        r = client.post("/api/investigation-group-add", json={
            "investigation": "ghost", "name": "g", "variants": ["baseline"],
        })
        assert r.status_code == 404

    def test_group_add_409_duplicate(self, client: TestClient) -> None:
        payload = {"investigation": "demo", "name": "dup", "variants": ["baseline"]}
        client.post("/api/investigation-group-add", json=payload)
        r = client.post("/api/investigation-group-add", json=payload)
        assert r.status_code == 409

    def test_group_add_in_openapi(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert "/api/investigation-group-add" in schema["paths"]

    # ---- investigation-group-update ----

    def test_group_update_200(self, client: TestClient, ws: Path) -> None:
        cgm.group_add(ws, {
            "investigation": "demo", "name": "ctrl", "variants": ["baseline"],
        })
        r = client.post("/api/investigation-group-update", json={
            "investigation": "demo",
            "name": "ctrl",
            "fields_to_update": {"description": "via route"},
        })
        assert r.status_code == 200, r.json()
        assert r.json() == {"ok": True}
        assert _read_spec(ws)["groups"][0]["description"] == "via route"

    def test_group_update_400_missing_investigation(self, client: TestClient) -> None:
        r = client.post("/api/investigation-group-update", json={
            "name": "ctrl", "fields_to_update": {},
        })
        assert r.status_code == 400

    def test_group_update_400_empty_variants_in_fields(
        self, client: TestClient, ws: Path
    ) -> None:
        cgm.group_add(ws, {
            "investigation": "demo", "name": "ctrl", "variants": ["baseline"],
        })
        r = client.post("/api/investigation-group-update", json={
            "investigation": "demo",
            "name": "ctrl",
            "fields_to_update": {"variants": []},
        })
        assert r.status_code == 400

    def test_group_update_404_not_found(self, client: TestClient) -> None:
        r = client.post("/api/investigation-group-update", json={
            "investigation": "demo", "name": "ghost", "fields_to_update": {},
        })
        assert r.status_code == 404

    def test_group_update_in_openapi(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert "/api/investigation-group-update" in schema["paths"]
