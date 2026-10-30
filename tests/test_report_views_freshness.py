"""Card + graph payloads derive execution/test counts from evidence on read."""
import json

import yaml

from vivarium_workbench.lib import study_evidence
from vivarium_workbench.lib.composite_runs import (
    complete_metadata, connect, save_metadata,
)
from vivarium_workbench.lib.report_views import build_iset_detail
from vivarium_workbench.lib.investigation_graph_views import build_investigation_graph


def _ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    inv = tmp_path / "investigations" / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "inv", "studies": ["s1"]}))
    sd = tmp_path / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "s1", "status": "planned",
        "simulation_status": "not_run",
        "baseline": [{"name": "baseline", "composite": "my.Composite"}]}))
    conn = connect(sd / "runs.db")
    save_metadata(conn, spec_id="demo", run_id="r1", params={}, label="",
                  started_at=100.0, n_steps=5)
    complete_metadata(conn, run_id="r1", n_steps=5, status="completed")
    conn.close()
    cd = sd / "viz" / "report_card"
    cd.mkdir(parents=True)
    for c in ("a", "b", "c"):
        (cd / f"{c}.verdict.json").write_text(json.dumps({"overall": "within_tol"}))
    rd = tmp_path / ".pbg" / "runs" / "r1"
    rd.mkdir(parents=True)
    (rd / "report.json").write_text(json.dumps({
        "schema": "test_report/v1",
        "cards": {"a": {"overall": "within_tol"}, "b": {"overall": "within_tol"},
                  "c": {"overall": "mismatch"}}}))
    return tmp_path


def _count(monkeypatch):
    calls = []
    real = study_evidence.evidence_for_study

    def wrap(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(study_evidence, "evidence_for_study", wrap)
    return calls


def test_card_is_fresh_despite_stale_stored_fields(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    calls = _count(monkeypatch)
    s = next(x for x in build_iset_detail(ws, "inv")["studies"] if x["name"] == "s1")
    assert s["execution_status"] == "completed"
    assert s["simulation_status"] == "completed"
    assert s["gate_status"] == "passed"
    assert (s["behavior_tests"]["passed"], s["behavior_tests"]["total"]) == (2, 3)
    assert len(calls) == 1


def test_graph_node_is_fresh_and_reads_once(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    calls = _count(monkeypatch)
    graph, _ = build_investigation_graph(ws, "inv")
    node = next(n for n in graph["studies"] if n.get("slug") == "s1")
    assert node["execution_status"] == "completed"
    assert len(calls) == 1


def test_evidence_failure_degrades_to_legacy(tmp_path, monkeypatch):
    ws = _ws(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("bad")
    monkeypatch.setattr(study_evidence, "evidence_for_study", boom)
    s = next(x for x in build_iset_detail(ws, "inv")["studies"] if x["name"] == "s1")
    assert s["execution_status"] != "completed"
