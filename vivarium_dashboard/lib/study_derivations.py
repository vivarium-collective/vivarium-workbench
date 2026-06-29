"""Canonical study derivations — the SINGLE source of the rules that the report,
the study page, and the investigation graph all consume. Pure (no I/O). Moved
out of single_study_report.py so the rules are defined once instead of being
hand-kept-identical in Python + two JS files."""
from __future__ import annotations

GATE_TO_VERDICT = {
    "passed": "passing", "failed": "failing-bio",
    "needs_calibration": "calibrating", "blocked": "blocked",
    "not_started": "not-started",
}
GATE_RESULT_NORM = {
    "pass": "PASS", "passed": "PASS", "ok": "PASS",
    "fail": "FAIL", "failed": "FAIL",
    "partial": "PARTIAL", "mixed": "PARTIAL", "needs_calibration": "PARTIAL",
}
RUN_ERRORED = {"error", "errored", "failed", "crashed", "fail"}
RUN_COMPLETED = {"completed", "complete", "success", "succeeded", "ok", "done", "finished"}


def norm_gate_result(val) -> str:
    return GATE_RESULT_NORM.get(str(val or "").strip().lower(), "PENDING")


def verdict(spec: dict) -> str:
    ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator") or {}
    return GATE_TO_VERDICT.get(ge.get("result") or spec.get("gate_status"), "")


def latest_outcomes(spec: dict) -> dict:
    for r in reversed(spec.get("runs") or []):
        if isinstance(r, dict) and r.get("outcomes"):
            return r["outcomes"]
    return {}


def key_metrics(spec: dict) -> list[dict]:
    """Behavior-test outcomes as metric chips (PASS/FAIL + the observed value)."""
    metrics = []
    for name, o in latest_outcomes(spec).items():
        if not isinstance(o, dict):
            continue
        res = str(o.get("result", "")).upper()
        observed = o.get("observed")
        metrics.append({
            "label": name,
            "value": observed if observed is not None else res,
            "status": "pass" if res == "PASS" else ("fail" if res == "FAIL" else "warn"),
        })
    return metrics


def insight(spec: dict) -> str:
    """Headline insight: the first finding's statement/summary."""
    for f in (spec.get("findings") or []):
        if isinstance(f, dict):
            s = f.get("statement") or f.get("summary")
            if s:
                return s
    return ""


def conclusion_verdicts(spec: dict) -> dict:
    """Three verdict-track results computed from canonical fields (read-only).
    biological_validation ← gate_evaluator.result / gate_status;
    regression_compatibility ← run statuses; explanatory_gain ← finding tiers.
    The authored `basis` free-text is carried through per track."""
    authored = spec.get("conclusion_verdicts") or {}
    ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator") or {}
    bio = norm_gate_result(ge.get("result") or spec.get("gate_status"))

    runs = [r for r in (spec.get("runs") or []) if isinstance(r, dict)]
    if not runs:
        reg = "PENDING"
    else:
        statuses = [str(r.get("status", "")).strip().lower() for r in runs]
        if any(s in RUN_ERRORED for s in statuses):
            reg = "FAIL"
        elif all(s in RUN_COMPLETED for s in statuses):
            reg = "PASS"
        else:
            reg = "PARTIAL"

    findings = [f for f in (spec.get("findings") or []) if isinstance(f, dict)]
    if not findings:
        exp = "GAP"
    elif any((f.get("tier") == "interpretation") or f.get("mechanism_origin") for f in findings):
        exp = "PASS"
    else:
        exp = "PARTIAL"

    def _basis(track):
        t = authored.get(track)
        return (t.get("basis", "") if isinstance(t, dict) else "")

    return {
        "biological_validation":    {"result": bio, "basis": _basis("biological_validation")},
        "regression_compatibility": {"result": reg, "basis": _basis("regression_compatibility")},
        "explanatory_gain":         {"result": exp, "basis": _basis("explanatory_gain")},
    }


def derived_block(spec: dict) -> dict:
    """The full derived study-content block embedded into payloads/surfaces."""
    return {
        "conclusion_verdicts": conclusion_verdicts(spec),
        "verdict": verdict(spec),
        "insight": insight(spec),
        "key_metrics": key_metrics(spec),
    }
