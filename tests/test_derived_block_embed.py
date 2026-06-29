import yaml
from pathlib import Path
from vivarium_dashboard.lib.report_views import build_iset_detail


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    inv = tmp_path / "investigations" / "inv"; inv.mkdir(parents=True)
    inv.joinpath("investigation.yaml").write_text(yaml.safe_dump({"name": "inv", "studies": ["s1"]}))
    s1 = tmp_path / "studies" / "s1"; s1.mkdir(parents=True)
    s1.joinpath("study.yaml").write_text(yaml.safe_dump(
        {"schema_version": 4, "name": "s1", "gate_status": "passed",
         "baseline": [{"name": "baseline", "composite": "my.Composite"}],
         "runs": [{"status": "completed"}], "findings": [{"tier": "interpretation", "statement": "X"}]}))
    return tmp_path


def test_build_iset_detail_attaches_derived_per_study(tmp_path):
    detail = build_iset_detail(_ws(tmp_path), "inv")
    s = next(x for x in detail["studies"] if x["name"] == "s1")
    assert "derived" in s
    assert set(s["derived"]) == {"conclusion_verdicts", "verdict", "insight", "key_metrics"}
    assert s["derived"]["conclusion_verdicts"]["biological_validation"]["result"] == "PASS"


def test_study_detail_route_attaches_derived(tmp_path):
    from fastapi.testclient import TestClient
    from vivarium_dashboard.api.app import create_app, get_workspace

    ws = _ws(tmp_path)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    client = TestClient(app)
    r = client.get("/api/study/s1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "derived" in body
    assert body["derived"]["conclusion_verdicts"]["biological_validation"]["result"] == "PASS"
