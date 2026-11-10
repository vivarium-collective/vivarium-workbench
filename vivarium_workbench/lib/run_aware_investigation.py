"""Run-aware investigation-level markings (gate, closure, verdict_status, decisions).

Authored values always win; when a marking is not (meaningfully) authored it is
derived from the discovery engine and flagged ``*_source == "derived"``:

* study gate status   <- ``study_verdict.roll_up_verdict`` over graded behavior tests
* closure (Open/Closed) <- ``discovery_engine.closure(run_cycle(...))``
* investigation verdict_status <- roll-up of the members' run-aware verdicts
* open decisions      <- the cycle frontier's ``open_decision`` items

Everything here is pure over already-loaded specs (one ``run_cycle`` per
investigation, no file/db reads) and degrades to the authored value when the
engine is absent or raises.
"""
from __future__ import annotations

from typing import Any, Optional

_CLOSED_STATUSES = frozenset({"closed", "archived"})
# verdict_status values that mean "nothing concluded yet" -- a derived roll-up may
# replace these; any other authored value is a deliberate claim and wins.
_WEAK_VERDICT_STATUS = frozenset({
    "", "in-progress", "in_progress", "not-yet-run", "planning", "planned", "draft",
})


# Fields on an acceptance entry (behavior_test / expected_behavior /
# acceptance_criteria) that declare it is backed by a pytest node/file rather
# than a run-data predicate (mirrors study_verdict._COMMITTED_TEST_FIELDS).
_PYTEST_REF_FIELDS = ("pytest", "pytest_node", "test_path")

# roll_up_verdict results that mean "no graded behavior-test verdict" — the only
# states a pytest gate is allowed to fill in. A real behavior-test
# passed/failed/needs_calibration verdict always wins.
_NO_BEHAVIOR_VERDICT = frozenset({None, "not_started", "blocked"})

# Authored gate_status → the roll_up_verdict vocabulary (mirrors
# study_verdict._GATE_STATUS_MAP) for recomputing diverges_from_authored.
_GATE_STATUS_MAP = {
    "passed": "passed", "failed": "failed", "failed_evaluation": "failed",
    "blocked": "blocked", "needs_calibration": "needs_calibration",
}


def last_test_run_counts(spec: dict) -> Optional[tuple[int, int, int]]:
    """``(passed, failed, skipped)`` from a study's recorded pytest run, or None.

    The per-study pytest runner (``study_tests._write_last_results``) records the
    aggregate under top-level ``last_test_run`` when ``tests:`` is absent or a
    behavior-test LIST, and under ``tests.last_results`` when ``tests:`` is a
    config mapping. Reads both. Returns None when neither is present or the
    counts are unparseable.
    """
    spec = spec or {}
    meta = spec.get("last_test_run")
    if not isinstance(meta, dict):
        t = spec.get("tests")
        meta = t.get("last_results") if isinstance(t, dict) else None
    if not isinstance(meta, dict):
        return None
    try:
        return (int(meta.get("passed") or 0),
                int(meta.get("failed") or 0),
                int(meta.get("skipped") or 0))
    except (TypeError, ValueError):
        return None


def criteria_reference_pytest(spec: dict) -> bool:
    """True when a study's acceptance surface is its pytest suite (#1476).

    Either an acceptance entry explicitly names a pytest node/path (or declares
    ``kind``/``source: pytest``), OR the study has recorded a pytest run at all —
    having run ``POST /api/study-tests-run`` over ``studies/<slug>/tests/`` is
    itself the declaration that pytest, not YAML behavior tests, is the study's
    acceptance surface.
    """
    spec = spec or {}
    for key in ("behavior_tests", "expected_behavior", "acceptance_criteria", "tests"):
        entries = spec.get(key)
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            if any(e.get(f) for f in _PYTEST_REF_FIELDS):
                return True
            if str(e.get("kind") or e.get("source") or "").strip().lower() == "pytest":
                return True
    return last_test_run_counts(spec) is not None


def pytest_gate_result(spec: dict) -> Optional[str]:
    """``'passed'`` / ``'failed'`` from a study's pytest ``last_test_run``, else None.

    Only meaningful when pytest is the study's acceptance surface
    (:func:`criteria_reference_pytest`). ``failed > 0`` → ``'failed'``; else
    ``passed > 0`` → ``'passed'``. A run that collected nothing
    (``passed == failed == 0``, e.g. "no tests directory") yields None, so it
    never fabricates a pass.
    """
    if not criteria_reference_pytest(spec):
        return None
    counts = last_test_run_counts(spec)
    if counts is None:
        return None
    passed, failed, _ = counts
    if failed > 0:
        return "failed"
    if passed > 0:
        return "passed"
    return None


def fold_pytest_into_verdict(spec: dict, cgv: Optional[dict]) -> dict:
    """Fold a study's pytest result into a computed gate verdict (#1476).

    When the behavior-test roll-up gave no graded verdict (``not_started`` /
    ``blocked``) but the study's pytest suite recorded pass/fail, surface that as
    the computed gate ``result`` and recompute ``diverges_from_authored``. A real
    behavior-test verdict (``passed`` / ``failed`` / ``needs_calibration``) always
    wins. Pure/render-only: returns a new dict, never mutates ``spec``.
    """
    cgv = dict(cgv or {})
    if cgv.get("result") not in _NO_BEHAVIOR_VERDICT:
        return cgv
    pyt = pytest_gate_result(spec)
    if pyt not in ("passed", "failed"):
        return cgv
    cgv["result"] = pyt
    cgv["evaluated_by"] = "pytest"
    cgv["blocked_by"] = [] if pyt == "passed" else list(cgv.get("blocked_by") or [])
    authored_mapped = _GATE_STATUS_MAP.get(str(spec.get("gate_status") or "").strip().lower())
    cgv["diverges_from_authored"] = authored_mapped is not None and authored_mapped != pyt
    return cgv


def resolve_gate_status(spec: dict) -> tuple[Optional[str], Optional[str]]:
    """``(gate_status, source)``. Authored ``gate_status`` wins (``"authored"``);
    else the engine roll-up of graded behavior tests -- ``passed`` when every
    test passed, ``failed`` when one failed (``"derived"``); else, when the
    study's acceptance surface is its pytest suite and behavior tests gave no
    verdict, the pytest ``last_test_run`` result (#1476, ``"derived"``); else
    ``(None, None)``."""
    authored = spec.get("gate_status")
    if authored:
        return authored, "authored"
    result = None
    try:
        from discovery_engine.study_verdict import roll_up_verdict  # noqa: PLC0415
        result = (roll_up_verdict(spec) or {}).get("result")
    except Exception:  # noqa: BLE001 -- engine optional
        result = None
    if result in ("passed", "failed"):
        return result, "derived"
    # #1476: a pytest-backed study has no graded behavior-test verdict; fold its
    # recorded pass/fail counts in so a green suite isn't stuck 'blocked'. Only
    # when behavior tests gave no verdict, so a real behavior-test fail wins.
    if result in _NO_BEHAVIOR_VERDICT:
        pyt = pytest_gate_result(spec)
        if pyt in ("passed", "failed"):
            return pyt, "derived"
    return None, None


def _members(inv_spec: dict, studies_by_slug: dict) -> list[str]:
    try:
        from vivarium_workbench.lib.investigation_members import investigation_member_slugs
        slugs = [s for s in investigation_member_slugs(inv_spec) if s in studies_by_slug]
    except Exception:  # noqa: BLE001
        slugs = []
    return slugs or list(studies_by_slug)


def engine_view(inv_spec: dict, studies_by_slug: dict) -> Optional[dict]:
    """One discovery cycle -> ``{closure, open_decision_targets}`` (or None)."""
    try:
        from discovery_engine import discover  # noqa: PLC0415
        from discovery_engine import discovery_engine as de  # noqa: PLC0415
        slugs = _members(inv_spec, studies_by_slug)
        spec = dict(inv_spec)
        spec["studies"] = slugs
        studies = {s: studies_by_slug[s] for s in slugs}
        cyc = discover.run_cycle(spec, studies)
        clo = de.closure(cyc, spec)
        front = de.frontier(cyc, spec)
        return {
            "closure": clo,
            "open_decision_targets": {
                i.get("target") for i in front if i.get("kind") == "open_decision"},
        }
    except Exception:  # noqa: BLE001 -- engine absent / cycle failed: authored only
        return None


def _rollup_verdict_status(studies_by_slug: dict, slugs: list[str]) -> Optional[str]:
    from vivarium_workbench.lib.study_ontology import resolve_verdict  # noqa: PLC0415
    verdicts = [resolve_verdict(studies_by_slug[s])[0] for s in slugs]
    if not verdicts:
        return None
    if "refuted" in verdicts:
        return "failing"
    if any(v is None for v in verdicts):
        return None                      # not every member assessed yet
    if "inconclusive" in verdicts:
        return "inconclusive"
    return "passing" if all(v == "supported" for v in verdicts) else "passing-with-caveats"


def _decision_target(d: Any) -> Optional[str]:
    if isinstance(d, dict):
        return d.get("question") or "open_decision"
    if isinstance(d, str) and d.strip():
        return d
    return None


def derive_markings(inv_spec: dict, studies_by_slug: dict) -> dict:
    """Run-aware closure / verdict_status / decision count for one investigation.

    Returns ``{closure, verdict_status, verdict_status_source, decisions_open_count,
    decisions_open_source, decisions_engine_resolved}``.
    """
    inv_spec = inv_spec or {}
    ex = inv_spec.get("executive") if isinstance(inv_spec.get("executive"), dict) else {}
    slugs = _members(inv_spec, studies_by_slug)
    view = engine_view(inv_spec, studies_by_slug)
    rollup = _rollup_verdict_status(studies_by_slug, slugs)

    # --- closure (Open / Closed) ---------------------------------------------
    status = str(inv_spec.get("status") or "").strip().lower()
    authored_closure = str(ex.get("closure") or "").strip().lower()
    clo = (view or {}).get("closure") or {}
    base = {
        "unaddressed_criteria": clo.get("unaddressed_criteria") or [],
        "open_challenges": clo.get("open_challenges", 0),
        "open_items": clo.get("open_items", 0),
    }
    if status in _CLOSED_STATUSES or authored_closure == "closed":
        closure = {"closed": True, "source": "authored", **base}
    elif authored_closure == "open":
        closure = {"closed": False, "source": "authored", **base}
    elif view is not None and rollup is not None:
        closure = {"closed": bool(clo.get("closed")), "source": "derived", **base}
    elif view is not None and clo.get("unaddressed_criteria"):
        closure = {"closed": False, "source": "derived", **base}
    else:
        closure = {"closed": False, "source": None, **base}

    # --- verdict_status ------------------------------------------------------
    authored_vs = str(ex.get("verdict_status") or "").strip()
    if authored_vs and authored_vs.lower() not in _WEAK_VERDICT_STATUS:
        vs, vs_src = authored_vs, "authored"
    elif rollup is not None:
        vs, vs_src = rollup, "derived"
    else:
        vs = authored_vs or inv_spec.get("status") or "planning"
        vs_src = "authored"

    # --- decisions badge -----------------------------------------------------
    needed = ex.get("decisions_needed") if isinstance(ex.get("decisions_needed"), list) else []
    resolved_idx: list[int] = []
    if view is not None:
        open_targets = view["open_decision_targets"]
        for i, d in enumerate(needed):
            t = _decision_target(d)
            if t is not None and t not in open_targets:
                resolved_idx.append(i)
    return {
        "closure": closure,
        "verdict_status": vs,
        "verdict_status_source": vs_src,
        "decisions_open_count": len(needed) - len(resolved_idx),
        "decisions_open_source": "derived" if view is not None else "authored",
        "decisions_engine_resolved": resolved_idx,
    }
