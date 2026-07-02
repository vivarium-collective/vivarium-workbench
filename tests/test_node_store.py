import yaml
from pathlib import Path
from vivarium_workbench.lib.node_store import load_study_nodes, study_dir


def _seed(ws: Path):
    d = ws / "studies" / "demo"
    (d / "findings").mkdir(parents=True)
    (d / "evidence").mkdir()
    (d / "findings" / "f1.yaml").write_text(yaml.safe_dump(
        {"id": "finding/f1", "type": "finding", "runs": ["run/1"]}))
    (d / "evidence" / "e1.yaml").write_text(yaml.safe_dump(
        {"id": "evidence/e1", "type": "evidence", "findings": ["finding/f1"]}))


def test_loads_nodes_keyed_by_id(tmp_path):
    _seed(tmp_path)
    nodes = load_study_nodes(tmp_path, "demo")
    assert set(nodes) == {"finding/f1", "evidence/e1"}
    assert nodes["finding/f1"]["type"] == "finding"


def test_missing_study_returns_empty(tmp_path):
    assert load_study_nodes(tmp_path, "nope") == {}


def test_tolerates_missing_dirs_and_bad_yaml(tmp_path):
    d = tmp_path / "studies" / "demo" / "findings"; d.mkdir(parents=True)
    (d / "ok.yaml").write_text("id: finding/ok\ntype: finding\n")
    (d / "bad.yaml").write_text("{not: valid: yaml:")
    nodes = load_study_nodes(tmp_path, "demo")
    assert "finding/ok" in nodes  # bad file skipped, no crash
