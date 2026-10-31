"""Two-axis investigation-graph ontology derivation — the single server-side
source for a study's execution_status × verdict and its edge-relation typing.

Canonical study fields (viva-template study.schema.json):
  execution_status ∈ {proposed, planned, running, completed, failed}
  verdict          ∈ {supported, qualified, inconclusive, refuted}
  relation         ∈ {builds-on, tests, motivates, alternative-to}

All optional — derived here from the legacy status / simulation_status /
gate_status / report.verdict fields when absent. Authored values always win.
Consumed by both report_views.build_iset_detail (the study cards) and
investigation_graph_views.build_investigation_graph (the typed graph + edges),
so the two can never disagree.
"""
from __future__ import annotations

EXEC_VALUES = frozenset({"proposed", "planned", "running", "completed", "failed"})

# Legacy verdict values fold into the four canonical ones (ontology decision:
# an honest negative is a refuted verdict; mixed is inconclusive).
VERDICT_ALIASES = {
    "supported": "supported", "qualified": "qualified",
    "inconclusive": "inconclusive", "refuted": "refuted",
    "honest-negative": "refuted", "honest_negative": "refuted",
    "mixed": "inconclusive",
}

# Legacy edge relations fold into the four canonical ones.
REL_ALIASES = {
    "builds-on": "builds-on", "builds on": "builds-on",
    "leads-to": "builds-on", "leads to": "builds-on",
    "model-input": "builds-on", "model input": "builds-on",
    "evidence": "builds-on", "calibrates-threshold": "builds-on", "regulatory": "builds-on",
    "tests": "tests", "refutes": "tests", "refutes-alternative": "tests",
    "motivates": "motivates", "precedes": "motivates",
    "alternative-to": "alternative-to", "alternative": "alternative-to",
}


def canon_relation(rel):
    """Map any legacy/alias edge relation to one of the four canonical types."""
    if not rel:
        return None
    return REL_ALIASES.get(str(rel).strip().lower(), "builds-on")


# Authored axis values that carry no information ("nothing happened yet"). A
# human-set axis outside this set always wins over run evidence; one inside it
# defers to what runs.db actually shows.
WEAK_AXIS_VALUES = frozenset({
    "", "none", "null", "not_run", "not_evaluated", "pending", "planned", "planning",
})
_RUN_COMPLETE = frozenset({"complete", "completed", "ran", "done"})


def run_derived_axes(spec: dict, runs) -> dict:
    """``{"simulation_status", "evaluation_status"}`` derived from run rows.

    Delegates to ``study_status.derive_status`` (the single run->status rule)
    when importable; otherwise applies the same rule inline so a missing
    optional package never silently reverts cards to "Planned".
    """
    runs = [r for r in (runs or []) if isinstance(r, dict)]
    if not runs:
        return {"simulation_status": "not_run", "evaluation_status": "not_evaluated"}
    for mod in ("discovery_engine.study_status", "viva_superpowers.study_status"):
        try:
            import importlib
            d = importlib.import_module(mod).derive_status(spec, runs)
            return {k: str((d.get(k) or {}).get("value") or "") for k in
                    ("simulation_status", "evaluation_status")}
        except Exception:  # noqa: BLE001
            continue
    sts = {str(r.get("status") or "").strip().lower() for r in runs}
    sim = "ran" if sts & _RUN_COMPLETE else ("running" if "running" in sts else "not_run")
    has_v = any(isinstance(spec.get(k), (list, dict)) and spec.get(k)
                for k in ("conclusion_verdicts", "findings"))
    return {"simulation_status": sim,
            "evaluation_status": "evaluated" if sim == "ran" and has_v else "not_evaluated"}


def with_run_derived_axes(spec: dict, runs) -> dict:
    """Shallow copy of ``spec`` whose simulation/evaluation axes are filled from
    runs when the authored value is absent or weak. Authored values win."""
    derived = run_derived_axes(spec, runs)
    out = dict(spec)
    for axis, val in derived.items():
        if str(spec.get(axis) or "").strip().lower() in WEAK_AXIS_VALUES and \
                val not in ("not_run", "not_evaluated", ""):
            out[axis] = val
    return out


def derive_execution_status(spec: dict, node_status: str = "", runs=None) -> str:
    """Execution axis (what happened), independent of the scientific verdict.

    ``runs`` (optional run rows from runs.db / study.yaml) makes the result
    run-aware: a weak/absent authored simulation/evaluation axis defers to the
    run-derived one, so a study with a completed run reads ``completed`` without
    an authored axis. Authored values the human set still win.
    """
    ex = str(spec.get("execution_status") or "").strip().lower()
    if ex in EXEC_VALUES:
        return ex
    run_ran = False
    if runs:
        merged = with_run_derived_axes(spec, runs)
        run_ran = (merged.get("simulation_status") != spec.get("simulation_status")
                   and merged.get("simulation_status") == "ran")
        spec = merged
    sim = str(spec.get("simulation_status") or "").strip().lower()
    evalst = str(spec.get("evaluation_status") or "").strip().lower()
    gate = str(spec.get("gate_status") or "").strip().lower()
    life = str(spec.get("status") or "").strip().lower()
    if sim == "failed" or life in ("invalid", "failed"):
        return "failed"
    if gate in ("passed", "failed", "needs_calibration", "stale"):
        return "completed"  # a gate reached a verdict ⇒ the study ran
    if sim == "ran" and evalst in ("evaluated", "failed_evaluation"):
        return "completed"
    if str(node_status).strip().lower() == "evaluated":
        return "completed"
    if run_ran:
        return "completed"  # a completed run is evidence the study executed
    if life in ("ran", "complete", "completed", "decided", "evaluated"):
        return "completed"  # a legacy `status: evaluated` study has executed
    if sim == "running" or life in ("running", "analyzing", "in_progress"):
        return "running"
    if life == "planned":
        return "proposed" if spec.get("proposed") is True else "planned"
    conf = str(spec.get("confidence") or "").strip().lower()
    if conf == "accepted":
        return "completed"
    if conf == "investigating":
        return "running"
    if conf == "planned":
        return "planned"
    if spec.get("proposed") is True:
        return "proposed"
    return "planned"


def derive_verdict(spec: dict):
    """Scientific verdict axis (what the evidence supports). Authored wins (with
    legacy values folded); else parse the free-text report.verdict /
    study_card.status. Returns None when nothing supports a verdict yet."""
    v = str(spec.get("verdict") or "").strip().lower()
    if v in VERDICT_ALIASES:
        return VERDICT_ALIASES[v]
    rep = spec.get("report") if isinstance(spec.get("report"), dict) else {}
    card = spec.get("study_card") if isinstance(spec.get("study_card"), dict) else {}
    text = str(rep.get("verdict") or card.get("status") or "").strip().lower()
    if not text:
        return None
    if "refut" in text or "contradict" in text or text.startswith("fail") or " fail" in text:
        return "refuted"
    if "inconclusive" in text or "mixed" in text or "unresolved" in text:
        return "inconclusive"
    if "caveat" in text or "qualified" in text or "warn" in text:
        return "qualified"
    if "pass" in text or "support" in text or "reproduc" in text:
        return "supported"
    return None
