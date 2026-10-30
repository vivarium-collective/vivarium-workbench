"""Investigation rollups (summary == detail) and plan board derive from evidence."""
import json

import yaml

from vivarium_workbench.lib import study_evidence
from vivarium_workbench.lib.composite_runs import (
    complete_metadata, connect, save_metadata,
)
from vivarium_workbench.lib.investigation_status import (
    _study_display_status, build_iset_summary,
)
from vivarium_workbench.lib.report_views import build_iset_detail


def _ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    inv = tmp_path / "investigations" / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "inv", "studies": ["s1"],
        "plan_gates": [{"id": "A", "title": "Gate A", "member_studies": ["s1"]}]}))
    sd = tmp_path / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "s1", "status": "planned",
        "simulation_status": "not_run", "gate_status": "not_started",
        "baseline": [{"name": "baseline", "composite": "my.Composite"}]}))
    conn = connect(sd / "runs.db")
    save_metadata(conn, spec_id="demo", run_id="r1", params={}, label="",
                  started_at=100.0, n_steps=5)
    complete_metadata(conn, run_id="r1", n_steps=5, status="completed")
    conn.close()
    cd = sd / "viz" / "report_card"
    cd.mkdir(parents=True)
    for c in ("a", "b"):
        (cd / f"{c}.verdict.json").write_text(json.dumps({"overall": "within_tol"}))
    rd = tmp_path / ".pbg" / "runs" / "r1"
    rd.mkdir(parents=True)
    (rd / "report.json").write_text(json.dumps({
        "schema": "test_report/v1",
        "cards": {"a": {"overall": "within_tol"}, "b": {"overall": "within_tol"}}}))
    return tmp_path


def _spec(ws):
    return yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())


def test_display_status_evidence_aware_and_none_unchanged(tmp_path):
    ws = _ws(tmp_path)
    ev = study_evidence.evidence_for_study(ws, "s1")
    assert ev.execution == "completed" and ev.gate == "passed"
    stale = _study_display_status(ws, "s1", _spec(ws))
    assert stale == "not_started"
    fresh = _study_display_status(ws, "s1", _spec(ws), evidence=ev)
    assert fresh in {"passed", "evaluated", "complete", "ran"}


def test_summary_and_detail_agree_and_are_fresh(tmp_path):
    ws = _ws(tmp_path)
    summ = next(x for x in build_iset_summary(ws, study_has_runs=lambda s, sp: True)
                if x["name"] == "inv")
    det = build_iset_detail(ws, "inv")
    assert det["effective_status"] == summ["effective_status"] == "complete"


def test_detail_reads_evidence_once(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    calls = []
    real = study_evidence.evidence_for_study
    monkeypatch.setattr(study_evidence, "evidence_for_study",
                        lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    build_iset_detail(ws, "inv")
    assert len(calls) == 1


def test_plan_board_gate_advances(tmp_path):
    ws = _ws(tmp_path)
    det = build_iset_detail(ws, "inv")
    row = det["plan_board"][0]
    assert row["status"] == "passed"
