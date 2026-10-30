"""StudyEvidence: status derived from runs.db + report cards + per-run report.json.

Ground truth (confirmed against the writers): runs_meta.status in
{running, completed, failed, orphaned, cancelled}; per-card verdict from
studies/<slug>/viz/report_card/<card>.verdict.json ``overall``; live test counts
from <ws>/.pbg/runs/<run_id>/report.json (test_report/v1, ``cards`` map).
"""
import json

import pytest

from vivarium_workbench.lib.composite_runs import (
    complete_metadata, connect, save_metadata,
)
from vivarium_workbench.lib.study_evidence import StudyEvidence, evidence_for_study


def _runs(ws, slug, rows):
    """rows: [(run_id, status, started_at)] written via the real run writer."""
    sd = ws / "studies" / slug
    sd.mkdir(parents=True, exist_ok=True)
    conn = connect(sd / "runs.db")
    for run_id, status, t in rows:
        save_metadata(conn, spec_id="demo", run_id=run_id, params={}, label="",
                      started_at=t, n_steps=5)
        if status != "running":
            complete_metadata(conn, run_id=run_id, n_steps=5, status=status)
    conn.close()


def _card(ws, slug, card, overall):
    d = ws / "studies" / slug / "viz" / "report_card"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{card}.verdict.json").write_text(json.dumps({"overall": overall}))


def _report(ws, run_id, overalls):
    d = ws / ".pbg" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    cards = {f"c{i}": {"overall": o} for i, o in enumerate(overalls)}
    (d / "report.json").write_text(json.dumps(
        {"schema": "test_report/v1", "cards": cards}))


@pytest.fixture
def fixture_ws(tmp_path):
    ws = tmp_path
    (ws / "workspace.yaml").write_text("name: t\n")
    (ws / "studies" / "untouched").mkdir(parents=True)
    (ws / "studies" / "untouched" / "study.yaml").write_text("name: untouched\n")

    _runs(ws, "s3-protrusions-to-enclosure", [("r-ok", "completed", 100.0)])
    _card(ws, "s3-protrusions-to-enclosure", "a", "within_tol")
    _card(ws, "s3-protrusions-to-enclosure", "b", "within_tol")
    _report(ws, "r-ok", ["within_tol", "within_tol"])

    _runs(ws, "mixed-history", [("r-old", "failed", 100.0),
                                ("r-new", "completed", 200.0)])

    _runs(ws, "runs-failed-tests-passed", [("r-f1", "failed", 100.0),
                                           ("r-f2", "failed", 200.0)])
    _card(ws, "runs-failed-tests-passed", "a", "within_tol")
    return ws


def test_evidence_completed_and_gate_passed_for_ran_study(fixture_ws):
    ev = evidence_for_study(fixture_ws, "s3-protrusions-to-enclosure")
    assert isinstance(ev, StudyEvidence)
    assert ev.execution == "completed"
    assert ev.gate == "passed"
    assert ev.evaluation == "evaluated"
    assert (ev.tests_passed, ev.tests_total) == (2, 2)
    assert ev.n_runs == 1


def test_latest_run_wins_over_earlier_failure(fixture_ws):
    ev = evidence_for_study(fixture_ws, "mixed-history")
    assert ev.execution == "completed"
    assert ev.n_runs == 2
    assert (ev.tests_passed, ev.tests_total) == (0, 0)


def test_execution_and_gate_are_independent(fixture_ws):
    ev = evidence_for_study(fixture_ws, "runs-failed-tests-passed")
    assert ev.execution == "failed"
    assert ev.gate == "passed"


def test_no_runs_no_cards_is_not_run(fixture_ws):
    ev = evidence_for_study(fixture_ws, "untouched")
    assert ev.execution == "not_run" and ev.gate == "not_started"
    assert ev.evaluation == "not_evaluated" and ev.n_runs == 0


def test_running_row_means_running(fixture_ws):
    _runs(fixture_ws, "live", [("r1", "completed", 1.0), ("r2", "running", 2.0)])
    assert evidence_for_study(fixture_ws, "live").execution == "running"


def test_orphaned_is_neither_success_nor_running(fixture_ws):
    _runs(fixture_ws, "orph", [("r1", "orphaned", 1.0)])
    assert evidence_for_study(fixture_ws, "orph").execution == "failed"


def test_drift_or_mismatch_card_fails_gate(fixture_ws):
    _card(fixture_ws, "untouched", "a", "within_tol")
    _card(fixture_ws, "untouched", "b", "drift")
    assert evidence_for_study(fixture_ws, "untouched").gate == "failed"


def test_ungraded_cards_do_not_start_gate(fixture_ws):
    _card(fixture_ws, "untouched", "a", "ungraded")
    ev = evidence_for_study(fixture_ws, "untouched")
    assert ev.gate == "not_started" and ev.evaluation == "not_evaluated"


def test_probe_performs_no_writes(fixture_ws):
    # SQLite may create -shm/-wal bookkeeping files when opening a WAL db even
    # read-only; those carry no data, so exclude them from the comparison.
    def snap():
        return {p.relative_to(fixture_ws).as_posix(): (p.stat().st_mtime_ns if p.is_file() else None)
                for p in fixture_ws.rglob("*") if not p.name.endswith(("-shm", "-wal"))}
    before = snap()
    evidence_for_study(fixture_ws, "s3-protrusions-to-enclosure")
    evidence_for_study(fixture_ws, "untouched")
    assert snap() == before


def test_completed_then_failed_stays_completed(fixture_ws):
    _runs(fixture_ws, "redo", [("r1", "completed", 100.0), ("r2", "failed", 200.0)])
    assert evidence_for_study(fixture_ws, "redo").execution == "completed"


def test_unreadable_runs_db_is_logged_not_silent(fixture_ws, caplog):
    sd = fixture_ws / "studies" / "corrupt"
    sd.mkdir(parents=True)
    (sd / "runs.db").write_bytes(b"this is not a sqlite database" * 50)
    with caplog.at_level("WARNING", logger="vivarium_workbench.lib.study_evidence"):
        ev = evidence_for_study(fixture_ws, "corrupt")
    assert ev.execution == "not_run"
    assert any("could not be read" in r.message for r in caplog.records)
