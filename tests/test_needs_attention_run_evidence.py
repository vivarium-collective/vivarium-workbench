"""Needs-attention honesty for never-run studies (issue #1618, bug 3).

The scan used to report "Hard gates passing" / "No behavior-test regressions"
for a study that had never run — treating absence-of-failure as a pass. These
run-derived checks now use the count of studies that actually ran as their
denominator, so an unrun study reads "none to check" (examined 0) rather than a
silent pass, and the scan reports how many member studies were evaluated.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib.needs_attention import _checks, scan_investigation


def _by_kind(checks: list[dict]) -> dict[str, dict]:
    return {c["kind"]: c for c in checks}


def test_checks_run_derived_use_evaluated_denominator():
    # 3 members, only 2 have run; one hard-gate failure flagged.
    summary = {"by_kind": {"hard_gate": 1}}
    checks = _by_kind(_checks(summary, member_count=3, ac_total=0, ac_covered=0,
                              include_phantom=False, evaluated_count=2))
    # Run-derived: denominator is the 2 evaluated studies, not all 3.
    assert checks["hard_gate"]["examined"] == 2
    assert checks["hard_gate"]["passing"] == 1
    assert checks["test_regression"]["examined"] == 2
    # Non-run-derived readiness check still spans every member.
    assert checks["unresolved_composite"]["examined"] == 3


def test_checks_never_run_reads_none_to_check():
    # No study has run → run-derived checks have nothing to examine (examined 0),
    # so the UI renders "none to check" rather than a green pass.
    checks = _by_kind(_checks({"by_kind": {}}, member_count=2, ac_total=0,
                              ac_covered=0, include_phantom=False, evaluated_count=0))
    assert checks["hard_gate"]["examined"] == 0
    assert checks["hard_gate"]["passing"] == 0
    assert checks["test_regression"]["examined"] == 0


def test_checks_evaluated_count_defaults_to_member_count():
    # A caller that omits evaluated_count keeps the old whole-membership behavior.
    checks = _by_kind(_checks({"by_kind": {}}, member_count=4, ac_total=0,
                              ac_covered=0, include_phantom=False))
    assert checks["hard_gate"]["examined"] == 4


def _ws_one_study(tmp_path: Path, *, with_run: bool) -> Path:
    ws = tmp_path / "ws"
    (ws / "investigations" / "inv").mkdir(parents=True)
    (ws / "studies" / "s1").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (ws / "investigations" / "inv" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "inv", "studies": ["s1"]}))
    study: dict = {"name": "s1", "investigation": "inv", "tests": [{"name": "b1"}]}
    if with_run:
        study["runs"] = [{"name": "r1", "status": "completed"}]
    (ws / "studies" / "s1" / "study.yaml").write_text(yaml.safe_dump(study))
    return ws


def test_scan_reports_zero_evaluated_for_never_run_study(tmp_path):
    ws = _ws_one_study(tmp_path, with_run=False)
    res = scan_investigation(ws, "inv")
    assert res["summary"]["evaluated_count"] == 0
    checks = _by_kind(res["checks"])
    # The never-run study is not counted as a passing hard gate.
    assert checks["hard_gate"]["examined"] == 0
    assert checks["test_regression"]["examined"] == 0


def test_scan_counts_evaluated_study_with_a_completed_run(tmp_path):
    ws = _ws_one_study(tmp_path, with_run=True)
    res = scan_investigation(ws, "inv")
    assert res["summary"]["evaluated_count"] == 1
    checks = _by_kind(res["checks"])
    assert checks["hard_gate"]["examined"] == 1
    assert checks["hard_gate"]["passing"] == 1
