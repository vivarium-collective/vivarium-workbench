"""composite_flush writes a severity-aware study gate to run_dir/report.json.

Only hard-severity axis mismatches FAIL; a soft mismatch or drift WARNs;
directional axes never gate (via discovery_engine.study_verdict.severity_gate).
Best-effort: any failure returns None and leaves no partial report.json.
"""
import json
from pathlib import Path

from vivarium_workbench.lib.composite_flush import _write_report_gate


def _verdict(overall, axes):  # axes: list[(id, verdict, severity)]
    return {"schema": "report_card_verdict/v2", "overall": overall,
            "groups": {"g": {"verdict": overall,
                             "axes": [{"id": i, "verdict": v, "severity": s}
                                      for i, v, s in axes]}}}


def _run_dir(tmp_path, cards):  # cards: {name: verdict_doc}
    rd = tmp_path / "run1"
    rd.mkdir()
    for name, doc in cards.items():
        (rd / f"{name}.verdict.json").write_text(json.dumps(doc), encoding="utf-8")
    return rd


def test_hard_mismatch_fails_and_writes_report(tmp_path):
    rd = _run_dir(tmp_path, {"c1": _verdict("mismatch", [("a", "mismatch", "hard")])})
    status = _write_report_gate(rd, "demo", "run1")
    assert status == "fail"
    rep = json.loads((rd / "report.json").read_text())
    assert rep["schema"] == "test_report/v1"
    assert rep["gate"]["status"] == "fail" and rep["gate"]["hard_mismatch"] == 1
    assert rep["gate"]["gated_by"] == [{"card": "c1", "group": "g", "id": "a"}]


def test_soft_mismatch_only_warns(tmp_path):
    rd = _run_dir(tmp_path, {"c1": _verdict("mismatch", [("a", "mismatch", "soft")])})
    assert _write_report_gate(rd, "demo", "run1") == "warn"


def test_all_within_tol_passes(tmp_path):
    rd = _run_dir(tmp_path, {"c1": _verdict("within_tol", [("a", "within_tol", "hard")])})
    assert _write_report_gate(rd, "demo", "run1") == "pass"


def test_best_effort_never_raises(tmp_path):
    rd = _run_dir(tmp_path, {"c1": _verdict("within_tol", [("a", "within_tol", "hard")])})

    def _boom(*a, **k):
        raise ValueError("build blew up")

    assert _write_report_gate(rd, "demo", "run1", build_fn=_boom) is None
    assert not (rd / "report.json").exists()
