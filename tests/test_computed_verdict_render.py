"""Data-path tests for spine stage #2 dashboard render (Task 4).

Verifies that:
- _study_detail_spec includes computed_gate_verdict with result/blocked_by/evaluated_by
- computed_gate_verdict.result matches the server._condition_satisfied "tests-passed"
  predicate (fail==0 and pass>0)
- investigation roll_up_acceptance returns the right verdict_status + criteria + unmet
- GET /api/investigation/<name> carries computed_acceptance
"""
from __future__ import annotations

import yaml
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Study data-path: _study_detail_spec carries computed_gate_verdict
# ---------------------------------------------------------------------------

_V3_BASE = {
    "schema_version": 3,
    "baseline": [{"name": "core", "composite": "pkg.composites.core"}],
    "variants": [],
}


def _v3_study(name: str, tests: list, runs: list) -> dict:
    """Minimal v3 study spec that load_spec accepts + has behavior_tests+runs."""
    return dict(_V3_BASE, name=name,
                objective="test",
                status="in_progress",
                behavior_tests=tests,
                runs=runs)


@pytest.fixture
def ws_with_passing_study(tmp_path, monkeypatch):
    """Workspace with a v3 study where all tests pass."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    sd = ws / "studies" / "my-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = _v3_study(
        "my-study",
        tests=[{"name": "t1"}, {"name": "t2"}],
        runs=[{
            "name": "r1", "status": "completed",
            "outcomes": {"t1": {"result": "PASS"}, "t2": {"result": "PASS"}},
        }],
    )
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_study_detail_spec_carries_computed_gate_verdict(ws_with_passing_study):
    """_study_detail_spec must attach computed_gate_verdict to the spec."""
    from vivarium_dashboard.server import _study_detail_spec
    spec = _study_detail_spec("my-study")
    assert spec is not None
    assert "computed_gate_verdict" in spec, (
        "spec must carry computed_gate_verdict when study has test outcomes"
    )


def test_computed_gate_verdict_has_required_fields(ws_with_passing_study):
    from vivarium_dashboard.server import _study_detail_spec
    spec = _study_detail_spec("my-study")
    cgv = spec["computed_gate_verdict"]
    assert "result" in cgv
    assert "blocked_by" in cgv
    assert "evaluated_by" in cgv
    assert cgv["evaluated_by"] == "code"


def test_computed_gate_verdict_passed_matches_server_condition_satisfied(
        ws_with_passing_study):
    """The computed verdict 'passed' MUST equal server._condition_satisfied
    tests-passed branch: counts['fail']==0 and counts['pass']>0."""
    from vivarium_dashboard.server import _study_detail_spec
    from pbg_superpowers import study_status

    spec = _study_detail_spec("my-study")
    # Verify the server predicate agrees
    counts = study_status.count_test_outcomes(spec, spec.get("runs"))
    server_passed = counts["fail"] == 0 and counts["pass"] > 0
    assert server_passed is True, "server predicate must hold for this spec"
    assert spec["computed_gate_verdict"]["result"] == "passed"


def test_computed_gate_verdict_failed_when_tests_fail(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    sd = ws / "studies" / "fail-study"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    spec = _v3_study(
        "fail-study",
        tests=[{"name": "t1"}],
        runs=[{"name": "r1", "status": "completed",
               "outcomes": {"t1": {"result": "FAIL"}}}],
    )
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    monkeypatch.setattr(srv, "WORKSPACE", ws)

    from vivarium_dashboard.server import _study_detail_spec
    result_spec = _study_detail_spec("fail-study")
    assert result_spec["computed_gate_verdict"]["result"] == "failed"
    assert "t1" in result_spec["computed_gate_verdict"]["blocked_by"]


def test_computed_gate_verdict_does_not_modify_gate_status(ws_with_passing_study):
    """computed_gate_verdict must not change authored gate_status."""
    sd = ws_with_passing_study / "studies" / "my-study" / "study.yaml"
    spec_data = yaml.safe_load(sd.read_text())
    spec_data["gate_status"] = "passed"
    sd.write_text(yaml.safe_dump(spec_data))

    from vivarium_dashboard.server import _study_detail_spec
    result_spec = _study_detail_spec("my-study")
    # authored gate_status must be untouched
    assert result_spec.get("gate_status") == "passed"
    # computed verdict is attached separately
    assert "computed_gate_verdict" in result_spec


# ---------------------------------------------------------------------------
# Investigation data-path: roll_up_acceptance
# ---------------------------------------------------------------------------

def test_roll_up_acceptance_all_pass_returns_passing():
    """Pure data-path: all criteria passing → verdict_status=='passing'."""
    from pbg_superpowers.investigation_status import roll_up_acceptance

    inv_spec = {
        "name": "my-inv",
        "acceptance_criteria": [
            {"study": "s1", "behavior": "beh-a"},
            {"study": "s2", "behavior": "beh-b"},
        ],
    }
    studies = {
        "s1": {"name": "s1", "behavior_tests": [{"name": "beh-a"}],
               "runs": [{"name": "r1", "status": "completed",
                         "outcomes": {"beh-a": {"result": "PASS"}}}]},
        "s2": {"name": "s2", "behavior_tests": [{"name": "beh-b"}],
               "runs": [{"name": "r1", "status": "completed",
                         "outcomes": {"beh-b": {"result": "PASS"}}}]},
    }
    result = roll_up_acceptance(inv_spec, studies)
    assert result["verdict_status"] == "passing"
    assert result["unmet"] == []
    assert len(result["criteria"]) == 2


def test_roll_up_acceptance_fail_returns_failing():
    from pbg_superpowers.investigation_status import roll_up_acceptance

    inv_spec = {
        "name": "my-inv",
        "acceptance_criteria": [{"study": "s1", "behavior": "beh-a"}],
    }
    studies = {
        "s1": {"name": "s1", "behavior_tests": [{"name": "beh-a"}],
               "runs": [{"name": "r1", "status": "completed",
                         "outcomes": {"beh-a": {"result": "FAIL"}}}]},
    }
    result = roll_up_acceptance(inv_spec, studies)
    assert result["verdict_status"] == "failing"
    assert len(result["unmet"]) == 1


def test_roll_up_acceptance_criteria_fields():
    from pbg_superpowers.investigation_status import roll_up_acceptance

    inv_spec = {
        "name": "my-inv",
        "acceptance_criteria": [{"study": "s1", "behavior": "beh-a"}],
    }
    studies = {
        "s1": {"name": "s1", "behavior_tests": [{"name": "beh-a"}],
               "runs": [{"name": "r1", "status": "completed",
                         "outcomes": {"beh-a": {"result": "PASS"}}}]},
    }
    result = roll_up_acceptance(inv_spec, studies)
    c = result["criteria"][0]
    assert c["study"] == "s1"
    assert c["behavior"] == "beh-a"
    assert c["result"] == "passing"


# ---------------------------------------------------------------------------
# /api/investigation/<name> carries computed_acceptance
# ---------------------------------------------------------------------------

def test_iset_detail_carries_computed_acceptance(tmp_path, dashboard_client):
    """End-to-end: GET /api/investigation/<name> must include computed_acceptance."""
    ws = tmp_path / "ws"
    # Use nested investigation/studies layout for study_dir resolution
    inv_dir = ws / "investigations" / "my-inv"
    study_dir = inv_dir / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")

    # Study spec (nested layout: investigations/my-inv/studies/s1/study.yaml)
    study_spec = _v3_study(
        "s1",
        tests=[{"name": "beh-a"}],
        runs=[{"name": "r1", "status": "completed",
               "outcomes": {"beh-a": {"result": "PASS"}}}],
    )
    (study_dir / "study.yaml").write_text(yaml.safe_dump(study_spec))

    # Investigation yaml
    (inv_dir / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "my-inv",
        "studies": ["s1"],
        "acceptance_criteria": [{"study": "s1", "behavior": "beh-a"}],
    }))

    client = dashboard_client(ws)
    resp = client.get("/api/investigation/my-inv")
    assert resp.status_code == 200
    data = resp.json()
    assert "computed_acceptance" in data, (
        "iset detail must carry computed_acceptance"
    )
    ca = data["computed_acceptance"]
    assert ca is not None
    assert ca["verdict_status"] == "passing"
    assert ca["unmet"] == []
