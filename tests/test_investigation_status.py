"""Unit tests for lib.investigation_status (extracted from server.py)."""

import yaml

from vivarium_workbench.lib import investigation_status as inv


def test_compute_investigation_status_rules():
    c = inv.compute_investigation_status
    assert c([]) == "planning"
    assert c(["planning"]) == "planning"
    assert c(["failed"]) == "failed"            # rule 1 wins
    assert c(["complete", "failed"]) == "failed"
    assert c(["complete", "ran"]) == "complete"  # all done
    assert c(["evaluated", "decided"]) == "complete"
    assert c(["running"]) == "running"
    assert c(["complete", "planning"]) == "in_progress"  # some done, not all
    # injected runs flip an all-planning investigation to in_progress (rule 4)
    assert c(["planning"], has_runs=[True]) == "in_progress"


def test_gate_refuted_study_is_not_a_failure(tmp_path):
    """A study that RAN and was evaluated but whose pre-registered gate was not
    met (gate_status == failed, i.e. the hypothesis was refuted) is a valid
    negative result, not an execution failure. Its rolled-up display status must
    be a done state ("evaluated"), never "failed" — so one refuted study can't
    flip the whole investigation to "Failed". The refutation is still carried by
    the study's verdict, separately (two-axis execution × verdict)."""
    refuted = {
        "name": "og-x", "status": "ran", "simulation_status": "ran",
        "evaluation_status": "evaluated", "gate_status": "failed",
        "verdict": "refuted",
    }
    assert inv._study_display_status(tmp_path, "og-x", refuted) == "evaluated"
    # A genuine execution failure (no gate verdict, the sim failed) still reads
    # "failed" and still fails the investigation roll-up.
    broke = {"name": "og-y", "simulation_status": "failed"}
    assert inv._study_display_status(tmp_path, "og-y", broke) == "failed"
    # Roll-up: a refuted study alongside passed ones is complete, not failed.
    assert inv.compute_investigation_status(["passed", "evaluated"]) == "complete"


def _make_ws(tmp_path):
    """A minimal workspace: one investigation referencing one study."""
    (tmp_path / "investigations" / "inv-a").mkdir(parents=True)
    (tmp_path / "investigations" / "inv-a" / "investigation.yaml").write_text(
        yaml.safe_dump({
            "name": "inv-a", "title": "Inv A", "status": "active",
            "description": "d", "question": "q", "hypothesis": "h",
            "studies": ["study-1"],
        })
    )
    (tmp_path / "studies" / "study-1").mkdir(parents=True)
    (tmp_path / "studies" / "study-1" / "study.yaml").write_text(
        yaml.safe_dump({"status": "complete"})
    )
    return tmp_path


def test_build_iset_summary_shape(tmp_path):
    ws = _make_ws(tmp_path)
    out = inv.build_iset_summary(ws, study_has_runs=lambda s, spec: False)
    assert len(out) == 1
    e = out[0]
    assert e["name"] == "inv-a"
    assert e["title"] == "Inv A"
    assert e["n_studies"] == 1
    assert e["studies"] == ["study-1"]
    assert e["effective_status"] == "complete"   # one study, status complete
    assert e["lifecycle"] == "wip"               # tmp dir is not a git repo
    assert e["current"] is False                 # no matching git branch


def test_build_iset_summary_injects_runs(tmp_path):
    """A planning study with injected has_runs=True rolls up to in_progress."""
    ws = tmp_path
    (ws / "investigations" / "inv-b").mkdir(parents=True)
    (ws / "investigations" / "inv-b" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "inv-b", "studies": ["s-plan"]})
    )
    (ws / "studies" / "s-plan").mkdir(parents=True)
    (ws / "studies" / "s-plan" / "study.yaml").write_text(yaml.safe_dump({"status": "planning"}))

    no_runs = inv.build_iset_summary(ws, study_has_runs=lambda s, spec: False)
    has_runs = inv.build_iset_summary(ws, study_has_runs=lambda s, spec: True)
    assert no_runs[0]["effective_status"] == "planning"
    assert has_runs[0]["effective_status"] == "in_progress"


def test_build_iset_summary_federated_string_executive(tmp_path, monkeypatch):
    """A federated investigation whose ``executive`` is a plain string (as
    spatio-flux 1.5.5 ships) must not blow up build_iset_summary. Regression for
    the HTTP 500 on /api/investigation-summaries reported from RENCI."""
    from vivarium_workbench.lib import federation as _fed

    def _fake_federated(ws_root):
        return [{
            "name": "paper-figures",
            "id": "spatio_flux::paper-figures",
            "origin_repo": "spatio-flux",
            "member_studies": ["spatio_flux::fig-1"],
            "spec": {
                "title": "Paper figures",
                "status": "planning",
                # The failure mode: executive is a string, not a mapping.
                "executive": "One study per figure of the Process Bigraph paper.",
            },
        }]

    monkeypatch.setattr(_fed, "federated_investigation_sets", _fake_federated)

    out = inv.build_iset_summary(tmp_path, study_has_runs=lambda s, spec: False)
    fed = next(e for e in out if e["name"] == "paper-figures")
    assert fed["read_only"] is True
    assert fed["verdict_status"] == "planning"  # falls back to spec.status


def test_build_iset_summary_parse_error(tmp_path):
    """A malformed investigation.yaml yields the minimal {name, error} entry."""
    (tmp_path / "investigations" / "inv-bad").mkdir(parents=True)
    (tmp_path / "investigations" / "inv-bad" / "investigation.yaml").write_text("a: [unterminated")
    out = inv.build_iset_summary(tmp_path, study_has_runs=lambda s, spec: False)
    assert out[0]["name"] == "inv-bad"
    assert "error" in out[0]


def test_iter_iset_dirs_empty(tmp_path):
    assert list(inv.iter_iset_dirs(tmp_path)) == []


def test_study_run_slugs_empty(tmp_path):
    assert inv.study_run_slugs(tmp_path) == set()


# ── authoritative terminal status wins over the member aggregate (sbml-cycle finding) ──
def test_closed_investigation_reads_complete_not_in_progress():
    import vivarium_workbench.lib.investigation_status as inv
    # a closed investigation whose members are mixed (some done, parked Planned follow-ups) would
    # aggregate to "in_progress" — but the authoritative closed status must win.
    mixed = ["complete", "planned", "planned"]
    assert inv.compute_investigation_status(mixed) == "in_progress"            # member aggregate (unchanged)
    assert inv.compute_investigation_status(mixed, authored_status="closed") == "complete"
    assert inv.compute_investigation_status(mixed, authored_status="complete") == "complete"
    assert inv.compute_investigation_status(mixed, closed=True) == "complete"


def test_authored_failed_wins_and_absent_status_is_a_noop():
    import vivarium_workbench.lib.investigation_status as inv
    assert inv.compute_investigation_status(["running"], authored_status="failed") == "failed"
    # absent/planning authored status does NOT override the aggregate (regression guard)
    assert inv.compute_investigation_status(["complete", "planned"], authored_status="planning") == "in_progress"
    assert inv.compute_investigation_status(["running"], authored_status=None) == "running"


# --- parked ontology (deferred future directions distinct from pending) ---

def test_parked_study_reads_parked_not_pending(tmp_path):
    """A study flagged parked:true reads 'parked' (deferred), not a pending lifecycle value."""
    # planned + parked -> parked (the 3 future-direction studies sbml normalizes)
    parked = {"name": "recover-enzyme-moiety-aware", "status": "planned", "parked": True,
              "kind": "refinement"}
    assert inv._study_display_status(tmp_path, "p-x", parked) == "parked"
    # NOT parked -> the ordinary planned lifecycle, unchanged
    not_parked = {"name": "s", "status": "planned", "kind": "refinement"}
    assert inv._study_display_status(tmp_path, "p-y", not_parked) == "planned"


def test_parked_is_overridden_by_a_terminal_state(tmp_path):
    """terminal -> parked -> lifecycle: a parked study that reached a done/failed axis
    keeps the terminal state (parked never masks a real conclusion)."""
    done = {"name": "s", "status": "planned", "parked": True, "evaluation_status": "evaluated"}
    assert inv._study_display_status(tmp_path, "p-z", done) == "evaluated"
    # a terminal invalid stays invalid (the display helper returns the raw terminal; the
    # failed-rollup happens in compute_investigation_status) — parked must not mask it.
    failed = {"name": "s", "status": "invalid", "parked": True}
    assert inv._study_display_status(tmp_path, "p-w", failed) == "invalid"


def test_synthesis_with_claim_reads_complete_not_pending(tmp_path):
    """A kind:synthesis study carries a claim but no runs by design -> 'complete', not the
    'Completed · pending' weak render."""
    synth = {"name": "recoverability-frontier", "status": "planned", "kind": "synthesis",
             "claim": "The recoverable frontier is bounded by data richness."}
    assert inv._study_display_status(tmp_path, "syn", synth) == "complete"
    # a synthesis with no claim stays weak (nothing to conclude yet)
    empty = {"name": "s", "status": "planned", "kind": "synthesis"}
    assert inv._study_display_status(tmp_path, "syn2", empty) == "planned"


def test_compute_investigation_status_excludes_parked_from_completeness():
    c = inv.compute_investigation_status
    # 6 complete members + 3 parked -> complete (parked don't block completeness)
    statuses = ["complete"] * 6 + ["planned"] * 3
    parked = [False] * 6 + [True] * 3
    assert c(statuses, parked=parked) == "complete"
    # without the parked exclusion the trailing planned members would read in_progress
    assert c(statuses) == "in_progress"
    # all-parked (or empty non-parked) -> planning, never complete
    assert c(["planned", "planned"], parked=[True, True]) == "planning"
