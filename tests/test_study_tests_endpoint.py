"""HTTP endpoint test for POST /api/study-tests-run."""
import yaml
from pathlib import Path


def test_post_study_tests_run_returns_summary(tmp_path, dashboard_client):
    # Workspace needs workspace.yaml for the server to mount it. Create a minimal one.
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    (tmp_path / "studies" / "demo" / "tests").mkdir(parents=True)
    (tmp_path / "studies" / "demo" / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "demo", "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
        "references": [], "implementation_tasks": "",
    }))
    (tmp_path / "studies" / "demo" / "tests" / "conftest.py").write_text("")
    (tmp_path / "studies" / "demo" / "tests" / "test_demo.py").write_text(
        "def test_one(): assert 1 + 1 == 2\n"
    )
    client = dashboard_client(workspace=tmp_path)
    resp = client.post("/api/study-tests-run", json={"study": "demo"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["passed"] == 1
    assert body["summary"]["failed"] == 0
    assert body["tests"][0]["outcome"] == "passed"


def test_post_study_tests_run_writes_last_results(tmp_path, dashboard_client):
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    (tmp_path / "studies" / "demo" / "tests").mkdir(parents=True)
    (tmp_path / "studies" / "demo" / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "demo", "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
        "references": [], "implementation_tasks": "",
    }))
    (tmp_path / "studies" / "demo" / "tests" / "test_demo.py").write_text(
        "def test_one(): assert True\n"
    )
    client = dashboard_client(workspace=tmp_path)
    client.post("/api/study-tests-run", json={"study": "demo"})
    spec = yaml.safe_load((tmp_path / "studies" / "demo" / "study.yaml").read_text())
    assert spec["tests"]["last_results"]["passed"] == 1


def test_post_study_tests_run_missing_study_returns_404(tmp_path, dashboard_client):
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    client = dashboard_client(workspace=tmp_path)
    resp = client.post("/api/study-tests-run", json={"study": "nonexistent"})
    assert resp.status_code == 404
