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


def derive_execution_status(spec: dict, node_status: str = "") -> str:
    """Execution axis (what happened), independent of the scientific verdict."""
    ex = str(spec.get("execution_status") or "").strip().lower()
    if ex in EXEC_VALUES:
        return ex
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
    if life in ("ran", "complete", "completed", "decided"):
        return "completed"
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
