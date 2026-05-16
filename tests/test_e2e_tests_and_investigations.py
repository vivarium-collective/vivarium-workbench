"""End-to-end test: create a 2-study investigation, run study 1's tests,
observe that the next study's gate advances when tests pass.

Slug naming note: the server validates plan + study slugs against the
`^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$` regex (path traversal protection),
so all slugs in this test are lowercase.
"""
import yaml, sqlite3
from pathlib import Path


def test_e2e_create_run_advance(tmp_path, dashboard_client):
    # 1) Scaffold two studies with a passing test + a fake run row.
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    for slug in ("s1", "s2"):
        d = tmp_path / "studies" / slug
        (d / "tests").mkdir(parents=True)
        (d / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 4, "name": slug, "baseline": [],
            "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
            "references": [], "implementation_tasks": "",
        }))
        (d / "tests" / "conftest.py").write_text("")
        (d / "tests" / "test_demo.py").write_text("def test_one(): assert True\n")
        # Pre-populate a runs.db row so the gate's "has run" check can pass.
        conn = sqlite3.connect(d / "runs.db")
        conn.execute(
            "CREATE TABLE runs_meta (run_id TEXT, params TEXT, seed INTEGER, "
            "status TEXT, n_steps INTEGER, variant TEXT, composite TEXT, timestamp TEXT)"
        )
        conn.execute(
            "INSERT INTO runs_meta VALUES ('r1', '{}', NULL, 'completed', 0, NULL, 'b', '2026-05-15T00:00:00')"
        )
        conn.commit()
        conn.close()

    client = dashboard_client(workspace=tmp_path)

    # 2) Create the investigation.
    resp = client.post("/api/plan-create", json={
        "name": "demo",
        "studies": [{"study": "s1", "gate": "tests-pass"}, {"study": "s2"}],
    })
    assert resp.status_code == 201, resp.text

    # 3) Before tests run: s1 has runs but no last_results → in-progress; s2 is blocked.
    plan = client.get("/api/plan/demo").json()
    statuses = [s["derived_status"] for s in plan["studies"]]
    assert statuses == ["in-progress", "blocked"], statuses

    # 4) Run s1's tests.
    resp = client.post("/api/study-tests-run", json={"study": "s1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["summary"]["passed"] == 1

    # 5) Now s1 is complete and s2 should unblock.
    plan = client.get("/api/plan/demo").json()
    statuses = [s["derived_status"] for s in plan["studies"]]
    assert statuses == ["complete", "in-progress"], statuses
