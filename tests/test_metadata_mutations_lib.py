"""Parity tests for vivarium_workbench.lib.metadata_mutations.

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

from vivarium_workbench.lib import metadata_mutations as mm
from vivarium_workbench.api.app import create_app, get_workspace


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


class TestSetInvestigationAnalyses:
    def test_sets_analyses(self, ws):
        resp, code = mm.set_investigation_analyses(ws, {
            "investigation": "dnaa-test",
            "analyses": [{"name": "doubling_time_distribution", "params": {}}],
        })
        assert code == 200
        assert resp == {"ok": True}
        spec = _read_inv_spec(ws)
        assert spec["analyses"] == [{"name": "doubling_time_distribution", "params": {}}]

    def test_entries_missing_name_are_dropped_not_crashed_on(self, ws):
        resp, code = mm.set_investigation_analyses(ws, {
            "investigation": "dnaa-test",
            "analyses": [{"name": "doubling_time_distribution"}, {"params": {"x": 1}}, "not-a-dict"],
        })
        assert code == 200
        spec = _read_inv_spec(ws)
        # the no-name entry and the non-dict entry are dropped; params defaults to {}
        assert spec["analyses"] == [{"name": "doubling_time_distribution", "params": {}}]

    def test_empty_list_clears_analyses(self, ws):
        mm.set_investigation_analyses(ws, {
            "investigation": "dnaa-test",
            "analyses": [{"name": "doubling_time_distribution"}],
        })
        resp, code = mm.set_investigation_analyses(ws, {
            "investigation": "dnaa-test", "analyses": [],
        })
        assert code == 200
        assert _read_inv_spec(ws)["analyses"] == []

    def test_missing_investigation_400(self, ws):
        resp, code = mm.set_investigation_analyses(ws, {"analyses": []})
        assert code == 400
        assert "investigation required" in resp["error"]

    def test_analyses_not_list_400(self, ws):
        resp, code = mm.set_investigation_analyses(ws, {
            "investigation": "dnaa-test", "analyses": "not-a-list",
        })
        assert code == 400

    def test_unknown_investigation_404(self, ws):
        resp, code = mm.set_investigation_analyses(ws, {
            "investigation": "no-such", "analyses": [],
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
# 2b. Consolidated PATCH orchestrators (patch_study / patch_investigation)
# ---------------------------------------------------------------------------


class TestPatchStudyLib:
    def test_multi_field_applies_all(self, ws):
        res, code = mm.patch_study(ws, "s1", {
            "objective": "New obj",
            "conclusions": "# C",
            "overview": {"question": "Q?"},
            "observables": [["a", "b"]],
            "expert_input": {"name": "kS", "current": 3.0},
            "narrative": {"path": "biological_summary", "value": "N"},
        })
        assert code == 200, res
        assert set(res["applied"]) == {
            "objective", "conclusions", "overview", "observables", "expert_input", "narrative",
        }
        spec = _read_study(ws)
        assert spec["objective"] == "New obj"
        assert spec["conclusions"] == "# C"  # written via markdown, not the old `text` bug
        assert spec["question"] == "Q?"
        assert spec["biological_summary"] == "N"
        target = next(e for e in spec["conditions"]["model_settings"] if e["name"] == "kS")
        assert target["current"] == 3.0

    def test_empty_400(self, ws):
        assert mm.patch_study(ws, "s1", {})[1] == 400

    def test_missing_slug_400(self, ws):
        assert mm.patch_study(ws, "", {"objective": "x"})[1] == 400

    def test_bad_field_is_annotated(self, ws):
        res, code = mm.patch_study(ws, "s1", {"narrative": {"path": "baseline.x", "value": "y"}})
        assert code == 400 and res["field"] == "narrative"

    def test_unknown_study_404(self, ws):
        res, code = mm.patch_study(ws, "no-such", {"objective": "x"})
        assert code == 404 and res["field"] == "objective"


class TestPatchInvestigationLib:
    def test_status_writes_investigation_yaml(self, ws):
        res, code = mm.patch_investigation(ws, "dnaa-test", {"status": "completed", "conclusions": "# IC"})
        assert code == 200, res
        assert _read_inv_yaml(ws)["status"] == "completed"  # investigation.yaml, not the spec
        assert _read_inv_spec(ws)["conclusions"] == "# IC"

    def test_overview_status_is_the_spec_status(self, ws):
        # overview.status is the spec/study.yaml status — a different field from
        # the top-level investigation.yaml status above.
        res, code = mm.patch_investigation(ws, "dnaa-test", {"overview": {"status": "completed"}})
        assert code == 200, res
        assert _read_inv_spec(ws)["status"] == "completed"

    def test_empty_400(self, ws):
        assert mm.patch_investigation(ws, "dnaa-test", {})[1] == 400


# ---------------------------------------------------------------------------
# 3. FastAPI route tests
# ---------------------------------------------------------------------------


@pytest.fixture
def client(ws) -> TestClient:
    """TestClient wired to the shared ws fixture."""
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


class TestStudySetAnalysesRoute:
    def test_200_sets_analyses(self, client, ws):
        r = client.post("/api/study-set-analyses", json={
            "investigation": "dnaa-test",
            "analyses": [{"name": "doubling_time_distribution", "params": {}}],
        })
        assert r.status_code == 200
        spec = _read_inv_spec(ws)
        assert spec["analyses"] == [{"name": "doubling_time_distribution", "params": {}}]

    def test_400_missing_investigation(self, client):
        r = client.post("/api/study-set-analyses", json={"analyses": []})
        assert r.status_code == 400

    def test_in_openapi(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/study-set-analyses" in schema["paths"]


class TestStudyPatchRoute:
    def test_200_multi_field(self, client, ws):
        r = client.patch("/api/study/s1", json={
            "objective": "Route obj",
            "conclusions": "# RC",
            "overview": {"question": "RQ?"},
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert set(body["applied"]) == {"objective", "conclusions", "overview"}
        spec = _read_study(ws)
        assert spec["objective"] == "Route obj"
        assert spec["conclusions"] == "# RC"
        assert spec["question"] == "RQ?"

    def test_200_narrative_and_expert_input(self, client, ws):
        r = client.patch("/api/study/s1", json={
            "narrative": {"path": "biological_summary", "value": "N"},
            "expert_input": {"name": "kS", "current": 4.0},
        })
        assert r.status_code == 200, r.text
        spec = _read_study(ws)
        assert spec["biological_summary"] == "N"
        target = next(e for e in spec["conditions"]["model_settings"] if e["name"] == "kS")
        assert target["current"] == 4.0

    def test_400_empty_body(self, client):
        assert client.patch("/api/study/s1", json={}).status_code == 400

    def test_404_unknown_study(self, client):
        assert client.patch("/api/study/no-such", json={"objective": "x"}).status_code == 404

    def test_in_openapi(self, client):
        schema = client.get("/openapi.json").json()
        assert "patch" in schema["paths"]["/api/study/{slug}"]

    def test_old_setter_routes_gone(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        for p in ("/api/study-set-objective", "/api/study-narrative-set",
                  "/api/study-expert-input-set", "/api/study-set-observables",
                  "/api/study-set-conclusion", "/api/study-set-description"):
            assert p not in paths


class TestInvestigationPatchRoute:
    def test_200_status_writes_investigation_yaml(self, client, ws):
        r = client.patch("/api/investigation/dnaa-test", json={"status": "completed"})
        assert r.status_code == 200, r.text
        assert _read_inv_yaml(ws)["status"] == "completed"

    def test_200_conclusions_overview_observables(self, client, ws):
        r = client.patch("/api/investigation/dnaa-test", json={
            "conclusions": "# IC",
            "overview": {"question": "IQ?"},
            "observables": [["p", "a"]],
        })
        assert r.status_code == 200, r.text
        spec = _read_inv_spec(ws)
        assert spec["conclusions"] == "# IC"
        assert spec["question"] == "IQ?"
        assert spec["observables"] == [{"path": ["p", "a"]}]

    def test_400_invalid_status_is_field_annotated(self, client):
        r = client.patch("/api/investigation/dnaa-test", json={"status": "bogus"})
        assert r.status_code == 400
        assert r.json().get("field") == "status"

    def test_404_unknown(self, client):
        assert client.patch("/api/investigation/no-such", json={"status": "archived"}).status_code == 404

    def test_in_openapi(self, client):
        schema = client.get("/openapi.json").json()
        assert "patch" in schema["paths"]["/api/investigation/{slug}"]

    def test_old_setter_routes_gone(self, client):
        paths = client.get("/openapi.json").json()["paths"]
        for p in ("/api/investigation-set-observables", "/api/investigation-set-conclusions",
                  "/api/investigation-set-overview", "/api/investigation-set-status"):
            assert p not in paths
