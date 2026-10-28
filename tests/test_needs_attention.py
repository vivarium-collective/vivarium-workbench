"""Tests for vivarium_workbench.lib.needs_attention (SP5 — the "decisions needed" scan).

SP5 is a PURE, deterministic aggregator: it gathers + ranks the divergences/gaps
SP1–SP4 already compute (uncovered ACs, verdict divergence, open feedback, param
drift, stale findings, phantom observables) into one per-investigation ranked
list. It makes NO new judgment and writes NOTHING. Signals 1,2,3,5,6 are
build-free; signal 4 (phantom observable) is opt-in behind an injected
``observables_for_ref``.

Fixture style mirrors tests/test_linkage_index.py.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from discovery_engine import study_io
from vivarium_workbench.lib.needs_attention import (
    scan_investigation, _stale_findings, open_epistemic_debts,
)


# ---------------------------------------------------------------------------
# Workspace builders (mirrors test_linkage_index.py)
# ---------------------------------------------------------------------------

def _write(path: Path, spec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    study_io.save_yaml_atomic(path, spec)


def _ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    _write(root / "workspace.yaml", {"name": "ws", "package_path": "pbg_ws"})
    return root


def _inv(root: Path, slug: str, spec: dict) -> None:
    spec = {"name": slug, **spec}
    _write(root / "investigations" / slug / "investigation.yaml", spec)


def _study(root: Path, slug: str, spec: dict, inv: str | None = None) -> None:
    spec = {"name": slug, **spec}
    if inv:
        spec.setdefault("investigation", inv)
    _write(root / "studies" / slug / "study.yaml", spec)


def _snapshot(root: Path) -> dict:
    snap: dict = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_inv_with_unlinked_ac(tmp_path) -> Path:
    """An investigation with one AC keyed to a study and one unlinked (the gap)."""
    root = _ws(tmp_path)
    _inv(root, "inv", {
        "studies": ["s1"],
        "acceptance_criteria": [
            {"study": "s1", "behavior": "covered-behavior"},
            {"behavior": "uncovered-behavior", "status": "failed"},  # gap
        ],
    })
    _study(root, "s1", {"tests": [{"name": "covered-behavior"}]}, inv="inv")
    return root


@pytest.fixture
def tmp_inv_with_open_feedback(tmp_path) -> Path:
    root = _ws(tmp_path)
    _inv(root, "inv", {"studies": ["s1"]})
    _study(root, "s1", {"tests": [{"name": "t"}]}, inv="inv")
    # Feedback annotation under investigations/<inv>/feedback*.yaml; no actions →
    # status == "open".
    _write(root / "investigations" / "inv" / "feedback" / "001.yaml", {
        "meta": {"report_id": "r1"},
        "annotations": {
            "study-s1": [
                {"ts": "2026-06-12T00:00:00", "author": "expert",
                 "text": "Please re-check the translation efficiency."},
            ],
        },
    })
    return root


@pytest.fixture
def tmp_inv_study_diverges(tmp_path) -> Path:
    root = _ws(tmp_path)
    _inv(root, "inv", {"studies": ["s1"]})
    _study(root, "s1", {
        "tests": [{"name": "t"}],
        "pipeline_gate": {
            "gate_evaluator": {"diverges_from_authored": True},
        },
    }, inv="inv")
    return root


@pytest.fixture
def tmp_study_findings(tmp_path) -> dict:
    return {
        "findings": [
            {"id": "F-01", "status": "novel",
             "next_action": "seed a follow-up study"},          # next_action, no seed → STALE
            {"id": "F-02", "status": "contradicts"},             # no next_action → terminal obs, NOT stale
            {"id": "F-03", "status": "novel",
             "next_action": "seed it", "seeded_study": "s2"},    # next_action + seeded → NOT stale
            {"id": "F-04", "status": "seeded",
             "seeded_study": "s9"},                              # no next_action → NOT stale
            {"id": "F-05", "status": "partial",
             "next_action": "test boundary case"},               # next_action, no seed → STALE
        ],
    }


@pytest.fixture
def tmp_inv_run_param_drift(tmp_path) -> Path:
    root = _ws(tmp_path)
    _inv(root, "inv", {"studies": ["s1"]})
    _study(root, "s1", {
        "enforced_params": {"translation_efficiency": 1, "mrna_per_min": 1.5},
        "runs": [
            # params persist as a JSON STRING (the real _mechanical_record shape,
            # from runs_meta.params_json), NOT a dict — the scan must decode it.
            {"name": "r1", "status": "completed",
             "params": '{"translation_efficiency": 20}'},  # mismatch + missing mrna
        ],
    }, inv="inv")
    return root


@pytest.fixture
def tmp_inv_invariant_regression(tmp_path) -> Path:
    root = _ws(tmp_path)
    _inv(root, "inv", {"studies": ["s1"]})
    _study(root, "s1", {
        "tests": [{"name": "t"}],
        "invariant_check": [
            {"study": "s0", "test": "operational_closure",
             "prior": True, "now": False, "status": "invalidated"},   # high
            {"study": "s0", "test": "precariousness",
             "prior": 0.8, "now": 0.4, "status": "weakened"},          # medium
            {"study": "s0", "test": "growth", "prior": 1.0, "now": 1.1,
             "status": "strengthened"},                                # healthy → omit
            {"study": "s0", "test": "containment", "prior": 1.0, "now": 1.0,
             "status": "preserved"},                                   # healthy → omit
        ],
    }, inv="inv")
    return root


@pytest.fixture
def tmp_inv_with_phantom_readout(tmp_path) -> Path:
    root = _ws(tmp_path)
    _inv(root, "inv", {"studies": ["s1"]})
    _study(root, "s1", {
        "conditions": {
            "baseline": {"name": "base", "composite": "comp-1"},
        },
        "readouts": [
            {"name": "phantom", "identifier": "agents.0.listeners.does_not_exist"},
        ],
    }, inv="inv")
    return root


@pytest.fixture
def tmp_inv_mixed(tmp_path) -> Path:
    """Mixed-severity investigation: an uncovered AC (high), open feedback
    (medium), a stale finding (low)."""
    root = _ws(tmp_path)
    _inv(root, "inv", {
        "studies": ["s1"],
        "acceptance_criteria": [
            {"behavior": "uncovered-behavior"},  # high gap
        ],
    })
    _study(root, "s1", {
        "tests": [{"name": "t"}],
        "findings": [{"id": "F-01", "status": "novel",
                      "next_action": "seed a follow-up"}],  # next_action, no seed → low stale
    }, inv="inv")
    _write(root / "investigations" / "inv" / "feedback" / "001.yaml", {
        "annotations": {
            "study-s1": [
                {"ts": "2026-06-12T00:00:00", "author": "expert", "text": "open item"},
            ],
        },
    })
    return root


def _stub_obs(ref):  # composite emits only one leaf; readout references a phantom
    return {"leaves": ["agents.0.listeners.mass.cell_mass"], "catalogs": {}}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_uncovered_ac_surfaces_high(tmp_inv_with_unlinked_ac):
    res = scan_investigation(tmp_inv_with_unlinked_ac, "inv")
    acs = [i for i in res["items"] if i["kind"] == "uncovered_ac"]
    assert acs and acs[0]["severity"] == "high"
    assert acs[0]["ref"] == "uncovered-behavior"
    assert acs[0]["study"] is None


def test_open_feedback_surfaces_medium(tmp_inv_with_open_feedback):
    res = scan_investigation(tmp_inv_with_open_feedback, "inv")
    assert any(i["kind"] == "open_feedback" and i["severity"] == "medium"
               and i["study"] == "s1" for i in res["items"])


def test_verdict_divergence_read_from_persisted_flag(tmp_inv_study_diverges):
    res = scan_investigation(tmp_inv_study_diverges, "inv")
    assert any(i["kind"] == "verdict_divergence" and i["study"] == "s1"
               and i["severity"] == "high" for i in res["items"])


def test_stale_finding_classifier(tmp_study_findings):
    # Stale = next_action declared but never seeded. A finding with NO
    # next_action (F-02, F-04) is a terminal observation, NOT a pending decision.
    stale = {f["id"] for f in _stale_findings(tmp_study_findings)}
    assert stale == {"F-01", "F-05"}


def test_param_drift_surfaces_when_run_violates(tmp_inv_run_param_drift):
    res = scan_investigation(tmp_inv_run_param_drift, "inv")
    drifts = [i for i in res["items"]
              if i["kind"] == "param_drift" and i["severity"] == "high"]
    assert drifts
    assert {d["ref"] for d in drifts} == {"translation_efficiency", "mrna_per_min"}


def test_invariant_regression_surfaces_invalidated_and_weakened(tmp_inv_invariant_regression):
    res = scan_investigation(tmp_inv_invariant_regression, "inv")
    regs = [i for i in res["items"] if i["kind"] == "invariant_regression"]
    # Only invalidated (high) + weakened (medium) surface; preserved/strengthened omitted.
    assert len(regs) == 2
    by_sev = {r["severity"]: r for r in regs}
    assert by_sev["high"]["ref"] == "s0:operational_closure"
    assert by_sev["high"]["study"] == "s1"
    assert by_sev["medium"]["ref"] == "s0:precariousness"


def test_scan_is_pure_no_writes(tmp_inv_with_unlinked_ac):
    before = _snapshot(tmp_inv_with_unlinked_ac)
    scan_investigation(tmp_inv_with_unlinked_ac, "inv")
    assert _snapshot(tmp_inv_with_unlinked_ac) == before


def test_scan_build_free_by_default_omits_phantom(tmp_inv_with_phantom_readout):
    res = scan_investigation(tmp_inv_with_phantom_readout, "inv")
    assert not any(i["kind"] == "phantom_observable" for i in res["items"])


def test_phantom_observable_opt_in(tmp_inv_with_phantom_readout):
    res = scan_investigation(tmp_inv_with_phantom_readout, "inv",
                             observables_for_ref=_stub_obs)
    assert any(i["kind"] == "phantom_observable" and i["severity"] == "high"
               and i["ref"] == "phantom" for i in res["items"])


def test_phantom_build_raise_is_tolerated(tmp_inv_with_phantom_readout):
    def _boom(ref):
        raise RuntimeError("composite build failed")
    res = scan_investigation(tmp_inv_with_phantom_readout, "inv",
                             observables_for_ref=_boom)
    # A build that raises skips that study; the scan still returns.
    assert not any(i["kind"] == "phantom_observable" for i in res["items"])


def test_summary_ranks_by_severity(tmp_inv_mixed):
    res = scan_investigation(tmp_inv_mixed, "inv")
    sev = [i["severity"] for i in res["items"]]
    assert sev == sorted(sev, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s])
    assert res["summary"]["by_severity"]["high"] >= 1
    assert res["summary"]["total"] == len(res["items"])
    assert set(res["summary"]["by_kind"]) <= {
        "uncovered_ac", "verdict_divergence", "open_feedback",
        "param_drift", "stale_finding", "phantom_observable",
        "invariant_regression"}


def test_investigation_slug_echoed(tmp_inv_with_unlinked_ac):
    res = scan_investigation(tmp_inv_with_unlinked_ac, "inv")
    assert res["investigation"] == "inv"


# ---------------------------------------------------------------------------
# Item 15 — open epistemic debts (negative knowledge), per-study collector.
# ---------------------------------------------------------------------------

def _kinds(debts):
    return {d["kind"] for d in debts}


def test_debts_minimal_study_harvests_rigor_gaps():
    # A bare study yields the rigor GAP-derived debts.
    debts = open_epistemic_debts({"name": "s", "findings": [{"statement": "it works"}]})
    kinds = _kinds(debts)
    assert "claim-untested" in kinds            # no replication / no falsifiability
    assert "control-absent" in kinds            # no controls
    assert "alternative-not-excluded" in kinds  # no alternatives declared
    # severity-sorted (high → medium → low)
    rank = {"high": 0, "medium": 1, "low": 2}
    sev = [rank[d["severity"]] for d in debts]
    assert sev == sorted(sev)
    assert {d["severity"] for d in debts} <= {"high", "medium", "low"}
    assert set(kinds) <= {
        "claim-untested", "control-absent", "metric-uncalibrated",
        "alternative-not-excluded", "region-unexplored", "viz-stale"}


def test_debt_control_absent_from_pending_and_empty_observed():
    spec = {
        "controls": [
            {"name": "ctrl-pending", "kind": "negative", "result": "PENDING"},
            {"name": "ctrl-no-observed", "kind": "negative", "result": "PASS"},  # no observed
        ],
    }
    debts = open_epistemic_debts(spec)
    refs = {d["ref"] for d in debts if d["kind"] == "control-absent"}
    assert {"ctrl-pending", "ctrl-no-observed"} <= refs


def test_debt_alternative_not_excluded():
    spec = {"alternative_hypotheses": [
        {"claim": "plain movement", "status": "not-excluded"},
        {"claim": "noise", "status": "untested"},
        {"claim": "ruled out", "status": "excluded"},  # not a debt
    ]}
    alts = [d for d in open_epistemic_debts(spec) if d["kind"] == "alternative-not-excluded"]
    refs = {d["ref"] for d in alts}
    assert "plain movement" in refs and "noise" in refs
    assert "ruled out" not in refs


def test_debt_metric_uncalibrated_from_anchor_marker():
    # An anchor present but with no literature target / observed value is uncalibrated.
    spec = {"findings": [{"id": "F-1", "statement": "x",
                          "calibration_anchor": {"note": "TBD"}}]}
    debts = open_epistemic_debts(spec)
    assert any(d["kind"] == "metric-uncalibrated" and d["ref"] == "F-1" for d in debts)
    # A fully-anchored calibration is NOT a debt.
    ok = {"findings": [{"id": "F-2", "statement": "x",
                        "calibration_anchor": {"literature_target": 28.0, "observed_value": 27.0}}]}
    assert not any(d["kind"] == "metric-uncalibrated" for d in open_epistemic_debts(ok))


def test_debt_viz_stale_from_freshness_marker():
    spec = {"visualizations": [
        {"name": "growth", "chart": "charts/growth.png", "freshness": "stale"},
        {"name": "mass", "chart": "charts/mass.png", "stale": True},
        {"name": "fresh", "chart": "charts/fresh.png", "freshness": "fresh"},  # not a debt
    ]}
    stale = [d for d in open_epistemic_debts(spec) if d["kind"] == "viz-stale"]
    refs = {d["ref"] for d in stale}
    assert {"charts/growth.png", "charts/mass.png"} == refs
    assert all(d["severity"] == "low" for d in stale)


def test_debt_region_unexplored_from_remaining_uncertainties():
    spec = {"discovery_implications": {"remaining_uncertainties": [
        "does it hold under nutrient shift?",
        {"note": "untested at high temperature"},
    ]}}
    regions = [d for d in open_epistemic_debts(spec) if d["kind"] == "region-unexplored"]
    assert len(regions) == 2
    notes = {d["note"] for d in regions}
    assert "does it hold under nutrient shift?" in notes
    assert "untested at high temperature" in notes


def test_debts_well_defended_study_has_few_debts():
    # The well-defended study from test_rigor: no control/alternative/replication debts.
    spec = {
        "name": "study-y",
        "robustness": {"n_replicates": 5, "seeds": [0, 1, 2, 3, 4], "parameter_sweep": True},
        "controls": [
            {"name": "external-membrane", "kind": "negative",
             "observed": "fail-closure", "result": "PASS"},
            {"name": "self-producing", "kind": "positive",
             "observed": "closure-holds", "result": "PASS"},
        ],
        "limitations": "Only the geometric-boundary aspect is modelled.",
        "alternative_hypotheses": [
            {"claim": "plain movement", "discriminated_by": "non-sensing control",
             "status": "excluded"},
        ],
        "findings": [
            {"statement": "agency in service of survival", "tier": "interpretation",
             "mechanism_origin": "emergent", "evidence": {"from_test": "agency-advantage"}},
        ],
        "falsifiability": "Advantage vanishes if the non-sensing control matched it.",
    }
    kinds = _kinds(open_epistemic_debts(spec))
    assert "control-absent" not in kinds
    assert "alternative-not-excluded" not in kinds
    assert "claim-untested" not in kinds  # replication OK + falsifiability + origin OK


def test_debts_tolerant_of_empty():
    assert open_epistemic_debts({}) and isinstance(open_epistemic_debts({}), list)
    assert open_epistemic_debts(None) == open_epistemic_debts({})


# ---------------------------------------------------------------------------
# Golden — read-only scan over the real v2e-invest workspace (skips if absent).
# ---------------------------------------------------------------------------

_V2E_INVEST = Path("/Users/eranagmon/code/v2e-invest")


def _real_investigation_slugs() -> list[str]:
    inv_root = _V2E_INVEST / "investigations"
    if not inv_root.is_dir():
        return []
    return sorted(
        p.name for p in inv_root.iterdir()
        if p.is_dir() and (p / "investigation.yaml").is_file()
    )


@pytest.mark.skipif(not _V2E_INVEST.is_dir(),
                    reason="v2e-invest workspace not present")
def test_golden_scan_real_investigation_read_only():
    slugs = _real_investigation_slugs()
    if not slugs:
        pytest.skip("no real investigations under v2e-invest")

    # Prefer the chromosome-cycle-calibration investigation (the known SP4a
    # live gap — 5 unlinked ACs) when present, else any real slug.
    slug = ("chromosome-cycle-calibration"
            if "chromosome-cycle-calibration" in slugs else slugs[0])

    before = _snapshot(_V2E_INVEST)
    res = scan_investigation(_V2E_INVEST, slug)
    assert _snapshot(_V2E_INVEST) == before, "scan must not write to v2e-invest"

    assert res["investigation"] == slug
    assert "items" in res and "summary" in res
    assert res["summary"]["total"] == len(res["items"])

    if slug == "chromosome-cycle-calibration":
        # The known SP4a live gap: its acceptance criteria have no study link.
        acs = [i for i in res["items"]
               if i["kind"] == "uncovered_ac" and i["severity"] == "high"]
        assert acs, "expected uncovered-AC high items for the SP4a live gap"


# ---------------------------------------------------------------------------
# Wave 3a — new collectors: next_action_type on stale findings (#7),
# confirmatory-not-preregistered (#18), diagnostic_branch_needed (#19)
# ---------------------------------------------------------------------------

from vivarium_workbench.lib.needs_attention import (
    _stale_finding_items,
    _unregistered_confirmatory_items,
    _diagnostic_branch_items,
)


def test_stale_finding_item_carries_next_action_type():
    spec = {"findings": [
        {"id": "F1", "next_action": "Calibrate kS", "next_action_type": "calibrate"},
        {"id": "F2", "next_action": "Look into it"},  # no enum
    ]}
    items = _stale_finding_items("s", spec)
    by_ref = {it["ref"]: it for it in items}
    assert by_ref["F1"]["next_action_type"] == "calibrate"
    assert by_ref["F1"]["action_hint"] == "calibrate"
    assert by_ref["F2"]["next_action_type"] is None
    assert by_ref["F2"]["action_hint"] == "seed a study from this finding"


def test_confirmatory_not_preregistered_item():
    spec = {"study_type": "confirmatory",
            "behavior_tests": [{"name": "t", "pass_if": {"low": 1}}]}
    items = _unregistered_confirmatory_items("c1", spec)
    assert len(items) == 1
    assert items[0]["kind"] == "confirmatory_not_preregistered"
    assert items[0]["severity"] == "medium"


def test_confirmatory_preregistered_before_run_no_item():
    spec = {
        "study_type": "confirmatory",
        "preregistered": {"registered_at": "2026-01-01"},
        "runs": [{"name": "r", "status": "complete", "timestamp": "2026-05-01"}],
    }
    assert _unregistered_confirmatory_items("c1", spec) == []


def test_non_confirmatory_never_flagged():
    assert _unregistered_confirmatory_items("s", {"study_type": "exploratory"}) == []
    assert _unregistered_confirmatory_items("s", {}) == []


def test_diagnostic_branch_needed_on_failure():
    spec = {
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r", "status": "complete", "timestamp": "2026-05-01",
                  "outcomes": {"t1": {"result": "fail"}}}],
    }
    items = _diagnostic_branch_items("s", spec)
    assert len(items) == 1
    assert items[0]["kind"] == "diagnostic_branch_needed"
    assert items[0]["severity"] == "high"
    assert items[0]["action_hint"] == "seed a diagnostic study"


def test_diagnostic_branch_silent_when_already_seeded():
    spec = {
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r", "status": "complete", "timestamp": "2026-05-01",
                  "outcomes": {"t1": {"result": "fail"}}}],
        "conclusion_logic": {"if_primary_tests_fail": {
            "diagnose": [{"hypothesis": "check X", "seeded_study": "s-diag"}]}},
    }
    assert _diagnostic_branch_items("s", spec) == []


def test_diagnostic_branch_silent_on_pass():
    spec = {
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r", "status": "complete", "timestamp": "2026-05-01",
                  "outcomes": {"t1": {"result": "pass"}}}],
    }
    assert _diagnostic_branch_items("s", spec) == []


# ---------------------------------------------------------------------------
# reproducible-rerun-spine Task 5 / G3 — env_stale + nondeterministic signals.
#
# vivarium_workbench stamps a run's ``provenance_status`` in its own
# ``runs_meta`` table (``lib.rerun._flag_env_drift`` / ``verify_reproduction``)
# — pbg-superpowers cannot import vivarium_workbench, so these tests construct
# the run-record fixture state directly on a study.yaml ``runs:`` entry
# (mirroring how a mechanically-recorded run's fields land there), rather than
# driving an actual vwb Reproduce.
# ---------------------------------------------------------------------------

def test_env_stale_surfaces_unless_pinned(tmp_path):
    """Key failing test: a run stamped ``provenance_status: env_stale``
    (reproduced under a different environment than the original run)
    surfaces as a needs_attention item; a study.yaml ``pinned_env:``
    matching that RUN's recorded ``env_id`` suppresses ONLY that run — a
    second run whose drift is to a DIFFERENT (unpinned) env_id must still
    surface. This is the precise per-run semantics matching vwb
    ``rerun.py``'s own ``pinned_env`` gate (accepted drift is scoped to the
    exact env_id, not a blanket study-wide suppression)."""
    root = _ws(tmp_path)
    _inv(root, "inv", {"studies": ["s1"]})
    _study(root, "s1", {
        "tests": [{"name": "t"}],
        "runs": [
            {"name": "r1", "status": "completed", "provenance_status": "env_stale",
             "env_id": "env-B"},
            {"name": "r2", "status": "completed", "provenance_status": "env_stale",
             "env_id": "env-C"},
        ],
    }, inv="inv")

    res = scan_investigation(root, "inv")
    stale = [i for i in res["items"] if i["kind"] == "env_stale"]
    assert {i["ref"] for i in stale} == {"r1", "r2"}
    assert stale[0]["study"] == "s1"

    # Pin env-B — the drift TO env-B is now accepted, but r2 (drifted to a
    # DIFFERENT, unpinned env-C) is an unrelated drift and must still surface.
    spec_file = root / "studies" / "s1" / "study.yaml"
    spec = study_io.load_yaml(spec_file)
    spec["pinned_env"] = "env-B"
    study_io.save_yaml_atomic(spec_file, spec)

    res = scan_investigation(root, "inv")
    stale = [i for i in res["items"] if i["kind"] == "env_stale"]
    assert {i["ref"] for i in stale} == {"r2"}


def test_nondeterministic_surfaces_needs_attention(tmp_path):
    """A run stamped ``provenance_status: nondeterministic`` (confirmed
    result_fingerprint mismatch under identical env_id + seed) surfaces as a
    high-severity needs_attention item; ``pinned_env`` does NOT suppress it
    (only env_stale is an environment-drift signal)."""
    root = _ws(tmp_path)
    _inv(root, "inv", {"studies": ["s1"]})
    _study(root, "s1", {
        "tests": [{"name": "t"}],
        "pinned_env": "env-A",
        "runs": [
            {"name": "r2", "status": "completed", "provenance_status": "nondeterministic"},
        ],
    }, inv="inv")

    res = scan_investigation(root, "inv")
    items = [i for i in res["items"] if i["kind"] == "nondeterministic"]
    assert items and items[0]["severity"] == "high" and items[0]["study"] == "s1"
