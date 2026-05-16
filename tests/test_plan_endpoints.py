"""HTTP endpoint tests for the investigation-plan family."""
import yaml
from pathlib import Path


def _scaffold_plan(ws: Path, slug: str, *, studies: list[dict], **kwargs) -> None:
    p = ws / "investigations" / slug / "investigation.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": 1, "name": slug, "studies": studies, **kwargs}
    p.write_text(yaml.safe_dump(data, sort_keys=False))


def _scaffold_study(ws: Path, slug: str) -> None:
    d = ws / "studies" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": slug, "baseline": [],
        "tests": {"auto_discover": True, "data_source": "latest_run", "pytest_args": [], "last_results": None},
        "references": [], "implementation_tasks": "",
    }))


def _scaffold_workspace(ws: Path) -> None:
    (ws / "workspace.yaml").write_text("name: test-ws\n")


def test_get_plans_list(tmp_path, dashboard_client):
    _scaffold_workspace(tmp_path)
    _scaffold_plan(tmp_path, "a", studies=[{"study": "s1"}])
    _scaffold_plan(tmp_path, "b", studies=[{"study": "s1"}, {"study": "s2"}])
    client = dashboard_client(workspace=tmp_path)
    resp = client.get("/api/plans")
    assert resp.status_code == 200, resp.text
    plans = resp.json()
    slugs = sorted(p["slug"] for p in plans)
    assert slugs == ["a", "b"]


def test_get_plan_detail_returns_derived_statuses(tmp_path, dashboard_client):
    _scaffold_workspace(tmp_path)
    _scaffold_plan(tmp_path, "demo", studies=[
        {"study": "s1", "gate": "tests-pass"},
        {"study": "s2"},
    ])
    _scaffold_study(tmp_path, "s1")
    _scaffold_study(tmp_path, "s2")
    client = dashboard_client(workspace=tmp_path)
    resp = client.get("/api/plan/demo")
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    statuses = [s["derived_status"] for s in plan["studies"]]
    # s1 has no run + no last_results → planned
    # s2 is blocked because s1's gate is not satisfied
    assert statuses == ["planned", "blocked"]


def test_get_plan_detail_404(tmp_path, dashboard_client):
    _scaffold_workspace(tmp_path)
    client = dashboard_client(workspace=tmp_path)
    resp = client.get("/api/plan/nonexistent")
    assert resp.status_code == 404


def test_post_plan_create(tmp_path, dashboard_client):
    _scaffold_workspace(tmp_path)
    client = dashboard_client(workspace=tmp_path)
    resp = client.post("/api/plan-create", json={
        "name": "dnaA-replication",
        "objective": "build DnaA cycle",
        "studies": [{"study": "s1"}, {"study": "s2", "gate": "tests-pass"}],
    })
    assert resp.status_code == 201, resp.text
    p = tmp_path / "investigations" / "dnaA-replication" / "investigation.yaml"
    assert p.exists()
    data = yaml.safe_load(p.read_text())
    assert data["objective"] == "build DnaA cycle"
    assert data["studies"][1]["gate"] == "tests-pass"


def test_post_plan_create_rejects_duplicate(tmp_path, dashboard_client):
    _scaffold_workspace(tmp_path)
    _scaffold_plan(tmp_path, "demo", studies=[{"study": "s1"}])
    client = dashboard_client(workspace=tmp_path)
    resp = client.post("/api/plan-create", json={"name": "demo", "studies": []})
    assert resp.status_code == 409


def test_delete_plan(tmp_path, dashboard_client):
    _scaffold_workspace(tmp_path)
    _scaffold_plan(tmp_path, "demo", studies=[{"study": "s1"}])
    client = dashboard_client(workspace=tmp_path)
    resp = client.delete("/api/plan", json={"slug": "demo"})
    assert resp.status_code == 200
    assert not (tmp_path / "investigations" / "demo").exists()


def test_post_plan_set_meta(tmp_path, dashboard_client):
    _scaffold_workspace(tmp_path)
    _scaffold_plan(tmp_path, "demo", studies=[{"study": "s1"}])
    client = dashboard_client(workspace=tmp_path)
    resp = client.post("/api/plan-set-meta", json={
        "slug": "demo", "objective": "new obj", "hypothesis": "h", "status": "in-progress",
    })
    assert resp.status_code == 200, resp.text
    data = yaml.safe_load((tmp_path / "investigations" / "demo" / "investigation.yaml").read_text())
    assert data["objective"] == "new obj"
    assert data["hypothesis"] == "h"
    assert data["status"] == "in-progress"
