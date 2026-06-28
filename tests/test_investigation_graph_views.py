import yaml
from pathlib import Path
from vivarium_dashboard.lib.investigation_graph_views import build_investigation_graph


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    inv = tmp_path / "investigations" / "demo-inv"
    inv.mkdir(parents=True)
    inv.joinpath("investigation.yaml").write_text(yaml.safe_dump(
        {"name": "demo-inv", "studies": ["s1", "s2"]}))
    s1 = tmp_path / "studies" / "s1"; s1.mkdir(parents=True)
    s1.joinpath("study.yaml").write_text(yaml.safe_dump(
        {"schema_version": 4, "name": "s1", "title": "First", "status": "complete"}))
    s2 = tmp_path / "studies" / "s2"; s2.mkdir(parents=True)
    s2.joinpath("study.yaml").write_text(yaml.safe_dump(
        {"schema_version": 4, "name": "s2", "title": "Second", "status": "planned",
         "pipeline_gate": {"prerequisites": [{"study": "s1"}]}}))
    return tmp_path


def _seed_full_chain(ws: Path, slug: str = "s2") -> None:
    d = ws / "studies" / slug
    for sub in ("findings", "evidence", "decisions", "conclusions"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "findings" / "f1.yaml").write_text(yaml.safe_dump(
        {"id": "finding/f1", "type": "finding", "lifecycle_state": "asserted",
         "statement": "X rises with Y", "runs": ["run/1"]}))
    (d / "evidence" / "e1.yaml").write_text(yaml.safe_dump(
        {"id": "evidence/e1", "type": "evidence", "lifecycle_state": "accepted",
         "findings": ["finding/f1"], "hypotheses": ["H1"], "statement": "supports H1"}))
    (d / "decisions" / "d1.yaml").write_text(yaml.safe_dump(
        {"id": "decision/d1", "type": "decision", "lifecycle_state": "recorded",
         "evidence": ["evidence/e1"], "outcome": "accept"}))
    (d / "conclusions" / "c1.yaml").write_text(yaml.safe_dump(
        {"id": "conclusion/c1", "type": "conclusion", "lifecycle_state": "published",
         "evidence": ["evidence/e1"], "decisions": ["decision/d1"], "statement": "H1 holds"}))


def test_studies_and_pipeline_gate_edge(tmp_path):
    body, status = build_investigation_graph(_ws(tmp_path), "demo-inv")
    assert status == 200
    assert {s["id"] for s in body["studies"]} == {"study/s1", "study/s2"}
    assert {"source": "study/s1", "target": "study/s2",
            "rel": "prerequisite", "condition": ""} in body["study_edges"]
    assert set(body["chains"]) == {"s1", "s2"}


def test_full_chain_nodes_edges_and_no_violations(tmp_path):
    ws = _ws(tmp_path); _seed_full_chain(ws)
    body, status = build_investigation_graph(ws, "demo-inv")
    chain = body["chains"]["s2"]
    assert {n["id"] for n in chain["nodes"]} == {
        "finding/f1", "evidence/e1", "decision/d1", "conclusion/c1"}
    rels = {(e["source"], e["target"], e["rel"]) for e in chain["edges"]}
    assert ("study/s2", "finding/f1", "contains") in rels
    assert ("evidence/e1", "finding/f1", "cites") in rels
    assert ("decision/d1", "evidence/e1", "decides") in rels
    assert ("conclusion/c1", "evidence/e1", "concludes") in rels
    assert ("conclusion/c1", "decision/d1", "via") in rels
    assert chain["violations"] == []
    f1 = next(n for n in chain["nodes"] if n["id"] == "finding/f1")
    assert f1["type"] == "finding" and f1["lifecycle_state"] == "asserted"
    assert f1["label"] == "X rises with Y"


def test_unsound_chain_surfaces_violations(tmp_path):
    ws = _ws(tmp_path)
    d = ws / "studies" / "s2"
    (d / "conclusions").mkdir(parents=True)
    (d / "conclusions" / "c1.yaml").write_text(yaml.safe_dump(
        {"id": "conclusion/c1", "type": "conclusion", "lifecycle_state": "published",
         "evidence": ["evidence/missing"], "decisions": [], "statement": "bad"}))
    body, _ = build_investigation_graph(ws, "demo-inv")
    assert any(v["node_id"] == "conclusion/c1"
               for v in body["chains"]["s2"]["violations"])
    assert all(e["target"] != "evidence/missing" for e in body["chains"]["s2"]["edges"])


def test_unknown_investigation_404(tmp_path):
    body, status = build_investigation_graph(_ws(tmp_path), "nope")
    assert status == 404 and "error" in body


def test_invalid_study_skipped_not_fatal(tmp_path):
    ws = _ws(tmp_path)
    (ws / "studies" / "s1" / "study.yaml").write_text("{not: valid: yaml:")
    body, status = build_investigation_graph(ws, "demo-inv")
    assert status == 200
    assert {s["id"] for s in body["studies"]} == {"study/s2"}  # s1 skipped
