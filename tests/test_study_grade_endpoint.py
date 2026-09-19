"""HTTP endpoint test for POST /api/study-grade."""
import yaml


def test_study_grade_endpoint(tmp_path, dashboard_client):
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    (tmp_path / "studies" / "demo-study").mkdir(parents=True)
    (tmp_path / "studies" / "demo-study" / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "demo-study", "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
        "references": [], "implementation_tasks": "",
    }))
    client = dashboard_client(workspace=tmp_path)
    r = client.post("/api/study-grade", json={"study": "demo-study"})
    assert r.status_code == 200
    assert "graded" in r.json()


def test_study_grade_endpoint_missing_study_returns_404(tmp_path, dashboard_client):
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    client = dashboard_client(workspace=tmp_path)
    r = client.post("/api/study-grade", json={"study": "nonexistent"})
    assert r.status_code == 404
