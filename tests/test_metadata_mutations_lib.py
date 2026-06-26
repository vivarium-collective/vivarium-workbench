"""Parity tests for vivarium_dashboard.lib.metadata_mutations.

Tests are in three sections:
1. Direct lib builder tests — fixture workspace, assert file mutations and
   (dict, status) returns including 400/404 error paths.
2. Server shim parity — construct Handler.__new__, patch WORKSPACE, call the
   real _post_* method, assert output matches the lib builder.
3. FastAPI route tests — client.post(...) → assert mutation visible + 200/4xx.
"""
from __future__ import annotations

import yaml
import pytest
from fastapi.testclient import TestClient

from vivarium_dashboard.lib import metadata_mutations as mm
from vivarium_dashboard.api.app import create_app, get_workspace


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path):
    """Workspace with one investigation and one study."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "workspace.yaml").write_text("name: test\n")

    # Investigation under investigations/
    inv_dir = ws_root / "investigations" / "dnaa-test"
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(
        "name: dnaa-test\ntitle: DnaA Test\nstatus: in-progress\nstudies: []\n",
        encoding="utf-8",
    )
    (inv_dir / "study.yaml").write_text(yaml.safe_dump({
        "name": "dnaa-test",
        "status": "draft",
        "question": "Original question",
    }))

    # Study under studies/
    study_dir = ws_root / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": "s1",
        "status": "draft",
        "objective": "Pre-existing objective.",
        "conditions": {
            "model_settings": [
                {"name": "kS", "current": 1.0, "range": [0.1, 10.0]},
                {"name": "kD", "current": 0.5},
            ]
        },
    }))

    return ws_root


def _read_inv_spec(ws_root):
    return yaml.safe_load(
        (ws_root / "investigations" / "dnaa-test" / "study.yaml").read_text()
    )


def _read_inv_yaml(ws_root):
    return yaml.safe_load(
        (ws_root / "investigations" / "dnaa-test" / "investigation.yaml").read_text()
    )


def _read_study(ws_root):
    return yaml.safe_load(
        (ws_root / "studies" / "s1" / "study.yaml").read_text()
    )


# ---------------------------------------------------------------------------
# 1. Direct lib builder tests
# ---------------------------------------------------------------------------


class TestSetInvestigationObservables:
    def test_sets_paths(self, ws):
        resp, code = mm.set_investigation_observables(ws, {
            "investigation": "dnaa-test",
            "paths": [["path", "to", "obs"]],
        })
        assert code == 200
        assert resp == {"ok": True}
        spec = _read_inv_spec(ws)
        assert spec["observables"] == [{"path": ["path", "to", "obs"]}]

    def test_emit_all_flag(self, ws):
        resp, code = mm.set_investigation_observables(ws, {
            "investigation": "dnaa-test",
            "paths": [],
            "emit_all": True,
        })
        assert code == 200
        spec = _read_inv_spec(ws)
        assert spec["observables"] == [{"path": []}]

    def test_missing_investigation_400(self, ws):
        resp, code = mm.set_investigation_observables(ws, {"paths": []})
        assert code == 400
        assert "investigation required" in resp["error"]

    def test_paths_not_list_400(self, ws):
        resp, code = mm.set_investigation_observables(ws, {
            "investigation": "dnaa-test",
            "paths": "not-a-list",
        })
        assert code == 400

    def test_missing_investigation_404(self, ws):
        resp, code = mm.set_investigation_observables(ws, {
            "investigation": "no-such",
            "paths": [],
        })
        assert code == 404


class TestSetInvestigationConclusions:
    def test_sets_markdown(self, ws):
        resp, code = mm.set_investigation_conclusions(ws, {
            "investigation": "dnaa-test",
            "markdown": "# Conclusion\nDnaA cycles.",
        })
        assert code == 200
        spec = _read_inv_spec(ws)
        assert spec["conclusions"] == "# Conclusion\nDnaA cycles."

    def test_missing_investigation_400(self, ws):
        resp, code = mm.set_investigation_conclusions(ws, {"markdown": "x"})
        assert code == 400

    def test_markdown_not_string_400(self, ws):
        resp, code = mm.set_investigation_conclusions(ws, {
            "investigation": "dnaa-test",
            "markdown": 123,
        })
        assert code == 400

    def test_markdown_over_limit_400(self, ws):
        big = "x" * (256 * 1024 + 1)
        resp, code = mm.set_investigation_conclusions(ws, {
            "investigation": "dnaa-test",
            "markdown": big,
        })
        assert code == 400
        assert "256KB" in resp["error"]

    def test_not_found_404(self, ws):
        resp, code = mm.set_investigation_conclusions(ws, {
            "investigation": "no-such",
            "markdown": "x",
        })
        assert code == 404

    def test_accepts_name_alias(self, ws):
        """name= key is accepted (legacy alias for investigation)."""
        resp, code = mm.set_investigation_conclusions(ws, {
            "name": "dnaa-test",
            "markdown": "Via name key.",
        })
        assert code == 200
        spec = _read_inv_spec(ws)
        assert spec["conclusions"] == "Via name key."


class TestSetInvestigationOverview:
    def test_sets_question(self, ws):
        resp, code = mm.set_investigation_overview(ws, {
            "investigation": "dnaa-test",
            "fields": {"question": "New question?"},
        })
        assert code == 200
        spec = _read_inv_spec(ws)
        assert spec["question"] == "New question?"

    def test_sets_multiple_fields(self, ws):
        resp, code = mm.set_investigation_overview(ws, {
            "investigation": "dnaa-test",
            "fields": {
                "question": "Q?",
                "hypothesis": "H.",
                "topic": "DnaA",
            },
        })
        assert code == 200
        spec = _read_inv_spec(ws)
        assert spec["question"] == "Q?"
        assert spec["hypothesis"] == "H."
        assert spec["topic"] == "DnaA"

    def test_invalid_status_400(self, ws):
        resp, code = mm.set_investigation_overview(ws, {
            "investigation": "dnaa-test",
            "fields": {"status": "bogus"},
        })
        assert code == 400
        assert "status must be one of" in resp["error"]

    def test_valid_status_accepted(self, ws):
        for s in ("draft", "in-progress", "completed", "archived"):
            resp, code = mm.set_investigation_overview(ws, {
                "investigation": "dnaa-test",
                "fields": {"status": s},
            })
            assert code == 200, (s, resp)

    def test_missing_investigation_400(self, ws):
        resp, code = mm.set_investigation_overview(ws, {"fields": {}})
        assert code == 400

    def test_fields_not_dict_400(self, ws):
        resp, code = mm.set_investigation_overview(ws, {
            "investigation": "dnaa-test",
            "fields": "bad",
        })
        assert code == 400

    def test_not_found_404(self, ws):
        resp, code = mm.set_investigation_overview(ws, {
            "investigation": "no-such",
            "fields": {"question": "Q?"},
        })
        assert code == 404


class TestSetInvestigationStatus:
    def test_sets_status(self, ws):
        resp, code = mm.set_investigation_status(ws, {
            "investigation": "dnaa-test",
            "status": "archived",
        })
        assert code == 200
        assert resp == {"ok": True, "status": "archived"}
        spec = _read_inv_yaml(ws)
        assert spec["status"] == "archived"

    def test_invalid_status_400(self, ws):
        resp, code = mm.set_investigation_status(ws, {
            "investigation": "dnaa-test",
            "status": "bogus",
        })
        assert code == 400

    def test_missing_investigation_400(self, ws):
        resp, code = mm.set_investigation_status(ws, {"status": "archived"})
        assert code == 400

    def test_not_found_404(self, ws):
        resp, code = mm.set_investigation_status(ws, {
            "investigation": "no-such",
            "status": "archived",
        })
        assert code == 404


class TestSetStudyObjective:
    def test_sets_objective(self, ws):
        resp, code = mm.set_study_objective(ws, {
            "study": "s1",
            "text": "New objective text.",
        })
        assert code == 200
        assert resp == {"ok": True}
        spec = _read_study(ws)
        assert spec["objective"] == "New objective text."

    def test_preserves_other_keys(self, ws):
        mm.set_study_objective(ws, {"study": "s1", "text": "X"})
        spec = _read_study(ws)
        assert spec["schema_version"] == 4
        assert spec["name"] == "s1"

    def test_missing_study_400(self, ws):
        resp, code = mm.set_study_objective(ws, {"text": "x"})
        assert code == 400

    def test_not_found_404(self, ws):
        resp, code = mm.set_study_objective(ws, {"study": "no-such", "text": "x"})
        assert code == 404


class TestSetStudyNarrative:
    """Delegated to metadata_mutations; core behaviour covered in
    test_study_narrative_set.py. Only smoke-test the dispatch here."""

    def test_sets_biological_summary(self, ws):
        resp, code = mm.set_study_narrative(ws, {
            "study": "s1",
            "path": "biological_summary",
            "value": "DnaA cycles.",
        })
        assert code == 200
        spec = _read_study(ws)
        assert spec["biological_summary"] == "DnaA cycles."

    def test_missing_value_key_400(self, ws):
        resp, code = mm.set_study_narrative(ws, {
            "study": "s1",
            "path": "biological_summary",
        })
        assert code == 400
        assert "missing value" in resp["error"]

    def test_forbidden_root_400(self, ws):
        resp, code = mm.set_study_narrative(ws, {
            "study": "s1",
            "path": "baseline.0",
            "value": "x",
        })
        assert code == 400


class TestSetStudyExpertInput:
    def test_sets_current(self, ws):
        resp, code = mm.set_study_expert_input(ws, {
            "study": "s1",
            "name": "kS",
            "current": 3.0,
        })
        assert code == 200
        assert resp == {"study": "s1", "name": "kS", "current": 3.0}
        spec = _read_study(ws)
        ms = spec["conditions"]["model_settings"]
        target = next(e for e in ms if e["name"] == "kS")
        assert target["current"] == 3.0

    def test_null_current_resets(self, ws):
        resp, code = mm.set_study_expert_input(ws, {
            "study": "s1",
            "name": "kD",
            "current": None,
        })
        assert code == 200
        assert resp["current"] is None

    def test_out_of_range_400(self, ws):
        resp, code = mm.set_study_expert_input(ws, {
            "study": "s1",
            "name": "kS",
            "current": 99.0,  # outside [0.1, 10.0]
        })
        assert code == 400
        assert "outside declared range" in resp["error"]

    def test_missing_study_and_name_400(self, ws):
        resp, code = mm.set_study_expert_input(ws, {"study": "s1"})
        assert code == 400

    def test_missing_current_key_400(self, ws):
        resp, code = mm.set_study_expert_input(ws, {
            "study": "s1",
            "name": "kS",
        })
        assert code == 400
        assert "current is required" in resp["error"]

    def test_not_found_404(self, ws):
        resp, code = mm.set_study_expert_input(ws, {
            "study": "no-such",
            "name": "kS",
            "current": 1.0,
        })
        assert code == 404

    def test_setting_not_found_404(self, ws):
        resp, code = mm.set_study_expert_input(ws, {
            "study": "s1",
            "name": "no-such-setting",
            "current": 1.0,
        })
        assert code == 404


# ---------------------------------------------------------------------------
# 2. Server shim parity
# ---------------------------------------------------------------------------


class TestServerShimParity:
    """Verify that the server's _post_* shims produce the same result as
    the lib builders. Construct a handler with __new__ (no socket needed),
    patch WORKSPACE, capture _json calls."""

    def _make_handler(self, ws_root):
        import vivarium_dashboard.server as srv
        handler = object.__new__(srv.Handler)
        handler._json_calls = []
        original_ws = srv.WORKSPACE

        def fake_json(body, code=200):
            handler._json_calls.append((body, code))
            return body, code

        handler._json = fake_json
        srv.WORKSPACE = ws_root
        return handler, srv, original_ws

    def test_investigation_set_observables_shim(self, ws):
        handler, srv, orig = self._make_handler(ws)
        try:
            handler._post_investigation_set_observables({
                "investigation": "dnaa-test",
                "paths": [["a", "b"]],
            })
        finally:
            srv.WORKSPACE = orig
        assert handler._json_calls[0][1] == 200

    def test_investigation_set_status_shim(self, ws):
        handler, srv, orig = self._make_handler(ws)
        try:
            handler._post_investigation_set_status({
                "investigation": "dnaa-test",
                "status": "completed",
            })
        finally:
            srv.WORKSPACE = orig
        body, code = handler._json_calls[0]
        assert code == 200
        assert body.get("ok") is True

    def test_study_set_objective_shim(self, ws):
        handler, srv, orig = self._make_handler(ws)
        try:
            handler._post_study_set_objective({
                "study": "s1",
                "text": "Via shim.",
            })
        finally:
            srv.WORKSPACE = orig
        body, code = handler._json_calls[0]
        assert code == 200
        spec = _read_study(ws)
        assert spec["objective"] == "Via shim."

    def test_study_narrative_set_shim(self, ws):
        handler, srv, orig = self._make_handler(ws)
        try:
            handler._post_study_narrative_set({
                "study": "s1",
                "path": "biological_summary",
                "value": "Shim test.",
            })
        finally:
            srv.WORKSPACE = orig
        body, code = handler._json_calls[0]
        assert code == 200

    def test_study_expert_input_set_shim(self, ws):
        handler, srv, orig = self._make_handler(ws)
        try:
            handler._post_study_expert_input_set({
                "study": "s1",
                "name": "kS",
                "current": 5.0,
            })
        finally:
            srv.WORKSPACE = orig
        body, code = handler._json_calls[0]
        assert code == 200
        assert body["current"] == 5.0


# ---------------------------------------------------------------------------
# 3. FastAPI route tests
# ---------------------------------------------------------------------------


@pytest.fixture
def client(ws) -> TestClient:
    """TestClient wired to the shared ws fixture."""
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


class TestInvestigationSetObservablesRoute:
    def test_200_sets_observables(self, client, ws):
        r = client.post("/api/investigation-set-observables", json={
            "investigation": "dnaa-test",
            "paths": [["path", "a"]],
        })
        assert r.status_code == 200
        spec = _read_inv_spec(ws)
        assert spec["observables"] == [{"path": ["path", "a"]}]

    def test_400_missing_investigation(self, client):
        r = client.post("/api/investigation-set-observables", json={"paths": []})
        assert r.status_code == 400

    def test_in_openapi(self, client):
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/api/investigation-set-observables" in paths


class TestInvestigationSetConclusionsRoute:
    def test_200_sets_conclusions(self, client, ws):
        r = client.post("/api/investigation-set-conclusions", json={
            "investigation": "dnaa-test",
            "markdown": "# Final",
        })
        assert r.status_code == 200
        spec = _read_inv_spec(ws)
        assert spec["conclusions"] == "# Final"

    def test_400_missing_investigation(self, client):
        r = client.post("/api/investigation-set-conclusions", json={"markdown": "x"})
        assert r.status_code == 400

    def test_in_openapi(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/investigation-set-conclusions" in schema["paths"]


class TestInvestigationSetOverviewRoute:
    def test_200_sets_question(self, client, ws):
        r = client.post("/api/investigation-set-overview", json={
            "investigation": "dnaa-test",
            "fields": {"question": "Route question?"},
        })
        assert r.status_code == 200
        spec = _read_inv_spec(ws)
        assert spec["question"] == "Route question?"

    def test_400_invalid_status(self, client):
        r = client.post("/api/investigation-set-overview", json={
            "investigation": "dnaa-test",
            "fields": {"status": "bogus"},
        })
        assert r.status_code == 400

    def test_in_openapi(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/investigation-set-overview" in schema["paths"]


class TestInvestigationSetStatusRoute:
    def test_200_sets_status(self, client, ws):
        r = client.post("/api/investigation-set-status", json={
            "investigation": "dnaa-test",
            "status": "completed",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        spec = _read_inv_yaml(ws)
        assert spec["status"] == "completed"

    def test_400_invalid_status(self, client):
        r = client.post("/api/investigation-set-status", json={
            "investigation": "dnaa-test",
            "status": "bogus",
        })
        assert r.status_code == 400

    def test_404_unknown_investigation(self, client):
        r = client.post("/api/investigation-set-status", json={
            "investigation": "no-such",
            "status": "archived",
        })
        assert r.status_code == 404

    def test_in_openapi(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/investigation-set-status" in schema["paths"]


class TestStudySetObjectiveRoute:
    def test_200_sets_objective(self, client, ws):
        r = client.post("/api/study-set-objective", json={
            "study": "s1",
            "text": "Route objective.",
        })
        assert r.status_code == 200
        spec = _read_study(ws)
        assert spec["objective"] == "Route objective."

    def test_404_unknown_study(self, client):
        r = client.post("/api/study-set-objective", json={
            "study": "no-such",
            "text": "x",
        })
        assert r.status_code == 404

    def test_in_openapi(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/study-set-objective" in schema["paths"]


class TestStudyNarrativeSetRoute:
    def test_200_sets_narrative(self, client, ws):
        r = client.post("/api/study-narrative-set", json={
            "study": "s1",
            "path": "biological_summary",
            "value": "Route narrative.",
        })
        assert r.status_code == 200
        spec = _read_study(ws)
        assert spec["biological_summary"] == "Route narrative."

    def test_400_missing_value_key(self, client):
        # 'value' key absent — lib returns 400
        r = client.post("/api/study-narrative-set", json={
            "study": "s1",
            "path": "biological_summary",
        })
        assert r.status_code == 400

    def test_400_forbidden_root(self, client):
        r = client.post("/api/study-narrative-set", json={
            "study": "s1",
            "path": "baseline.name",
            "value": "x",
        })
        assert r.status_code == 400

    def test_in_openapi(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/study-narrative-set" in schema["paths"]


class TestStudyExpertInputSetRoute:
    def test_200_sets_current(self, client, ws):
        r = client.post("/api/study-expert-input-set", json={
            "study": "s1",
            "name": "kS",
            "current": 7.0,
        })
        assert r.status_code == 200
        assert r.json() == {"study": "s1", "name": "kS", "current": 7.0}
        spec = _read_study(ws)
        target = next(e for e in spec["conditions"]["model_settings"] if e["name"] == "kS")
        assert target["current"] == 7.0

    def test_400_out_of_range(self, client):
        r = client.post("/api/study-expert-input-set", json={
            "study": "s1",
            "name": "kS",
            "current": 99.0,
        })
        assert r.status_code == 400

    def test_400_missing_current_key(self, client):
        r = client.post("/api/study-expert-input-set", json={
            "study": "s1",
            "name": "kS",
        })
        assert r.status_code == 400

    def test_404_setting_not_found(self, client):
        r = client.post("/api/study-expert-input-set", json={
            "study": "s1",
            "name": "no-such",
            "current": 1.0,
        })
        assert r.status_code == 404

    def test_in_openapi(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/study-expert-input-set" in schema["paths"]
