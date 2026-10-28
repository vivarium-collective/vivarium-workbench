"""Unit tests for vivarium_workbench.lib.auto_evaluate — auto-record behavior-test
outcomes into runs[].outcomes on canonical-run completion.

Uses an injected fake test_runner so the orchestration is exercised with no
live dashboard and no real RunReader / emitter store.
"""
from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib import auto_evaluate as ae
from discovery_engine import study_io


def _write_study(tmp_path: Path, text: str) -> Path:
    d = tmp_path / "studies" / "s1"
    d.mkdir(parents=True)
    (d / "study.yaml").write_text(text, encoding="utf-8")
    return d


def _fake_runner(outcomes: dict):
    """Return a test_runner that ignores its args and yields fixed outcomes."""
    def runner(study_dir, spec, run, ws_root):
        return outcomes
    return runner


# ---------------------------------------------------------------------------
# Result normalization (pure)
# ---------------------------------------------------------------------------

def test_normalize_result_synonyms_and_case():
    assert ae._normalize_result({"result": "passed"}) == "PASS"
    assert ae._normalize_result({"result": "Fail"}) == "FAIL"
    assert ae._normalize_result({"result": "partial"}) == "PARTIAL"
    assert ae._normalize_result({"result": "skipped"}) == "SKIP"
    assert ae._normalize_result("OK") == "PASS"


def test_normalize_no_result_is_skip():
    # the evaluator agent / needs_rerun buckets carry no "result"
    assert ae._normalize_result({"evaluated_by": "agent", "reason": "x"}) == "SKIP"
    assert ae._normalize_result({"evaluated_by": "needs_rerun"}) == "SKIP"
    assert ae._normalize_result(None) == "SKIP"


# ---------------------------------------------------------------------------
# Core write behavior
# ---------------------------------------------------------------------------

_BASE_YAML = """\
# study research log: top comment must survive
name: s1
behavior_tests:
- name: t_pass
  measure: {kind: generation_average, path: mass}
  pass_if: {op: range, low: 0, high: 10}
- name: t_fail
  measure: {kind: generation_average, path: mass}
  pass_if: {op: range, low: 0, high: 1}
- name: t_skip
  measure: {kind: observable-comparison}

runs:
- name: run-001          # the just-completed canonical run
  status: completed
  timestamp: '2026-06-22T00:00:00Z'
# end-of-file note
"""


def test_writes_outcomes_keyed_by_test_name_uppercased(tmp_path):
    d = _write_study(tmp_path, _BASE_YAML)
    runner = _fake_runner({
        "t_pass": {"result": "pass", "measured_value": 5, "evaluated_by": "code"},
        "t_fail": {"result": "fail", "detail": "out of band"},
        "t_skip": {"evaluated_by": "agent", "reason": "non-run-data kind"},
    })
    summary = ae.evaluate_on_run_completion(d, "run-001", test_runner=runner)

    assert summary["status"] == "ok"
    assert summary["is_canonical"] is True
    assert summary["evaluated"] == 3
    assert summary["written"] == 3
    assert summary["changed"] is True

    spec = study_io.load_yaml_mapping(d / "study.yaml")
    outcomes = spec["runs"][0]["outcomes"]
    assert outcomes["t_pass"]["result"] == "PASS"
    assert outcomes["t_fail"]["result"] == "FAIL"
    assert outcomes["t_skip"]["result"] == "SKIP"        # agent bucket → SKIP
    # provenance + carried fields
    assert outcomes["t_pass"]["measured_value"] == 5
    assert outcomes["t_pass"]["evaluated_by"] == "code"
    assert outcomes["t_pass"]["auto_evaluated"] is True
    # the agent reason is recorded as a note for context
    assert outcomes["t_skip"]["note"] == "non-run-data kind"


def test_rerun_is_a_noop(tmp_path):
    d = _write_study(tmp_path, _BASE_YAML)
    runner = _fake_runner({
        "t_pass": {"result": "PASS", "measured_value": 5},
        "t_fail": {"result": "FAIL"},
        "t_skip": {"evaluated_by": "agent", "reason": "x"},
    })
    s1 = ae.evaluate_on_run_completion(d, "run-001", test_runner=runner)
    assert s1["changed"] is True
    first = (d / "study.yaml").read_text()

    s2 = ae.evaluate_on_run_completion(d, "run-001", test_runner=runner)
    assert s2["changed"] is False
    assert (d / "study.yaml").read_text() == first


def test_comments_and_prose_preserved(tmp_path):
    d = _write_study(tmp_path, _BASE_YAML)
    runner = _fake_runner({"t_pass": {"result": "PASS"}})
    ae.evaluate_on_run_completion(d, "run-001", test_runner=runner)
    after = (d / "study.yaml").read_text()
    assert "# study research log: top comment must survive" in after
    assert "# the just-completed canonical run" in after
    assert "# end-of-file note" in after


def test_authored_outcomes_are_not_clobbered(tmp_path):
    """A human verdict (result + note, no auto_evaluated stamp) is preserved;
    its prose survives and the code result does NOT overwrite it."""
    yaml_text = """\
name: s1
behavior_tests:
- name: t_human
  measure: {kind: generation_average, path: mass}
  pass_if: {op: range, low: 0, high: 10}
runs:
- name: run-001
  status: completed
  outcomes:
    t_human:
      result: FAIL                 # expert verdict
      note: 'gate unmet — kept honest'
"""
    d = _write_study(tmp_path, yaml_text)
    runner = _fake_runner({"t_human": {"result": "PASS", "measured_value": 5}})
    summary = ae.evaluate_on_run_completion(d, "run-001", test_runner=runner)

    assert summary["skipped_authored"] == 1
    assert summary["written"] == 0
    assert summary["changed"] is False

    spec = study_io.load_yaml_mapping(d / "study.yaml")
    out = spec["runs"][0]["outcomes"]["t_human"]
    assert out["result"] == "FAIL"                     # authored verdict intact
    assert out["note"] == "gate unmet — kept honest"   # prose intact


def test_overwrite_authored_when_requested(tmp_path):
    yaml_text = """\
name: s1
behavior_tests:
- name: t_human
  measure: {kind: generation_average, path: mass}
  pass_if: {op: range, low: 0, high: 10}
runs:
- name: run-001
  status: completed
  outcomes:
    t_human:
      result: FAIL
      note: 'expert prose to keep'
"""
    d = _write_study(tmp_path, yaml_text)
    runner = _fake_runner({"t_human": {"result": "PASS", "measured_value": 5}})
    summary = ae.evaluate_on_run_completion(
        d, "run-001", test_runner=runner, overwrite_authored=True
    )
    assert summary["written"] == 1
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    out = spec["runs"][0]["outcomes"]["t_human"]
    assert out["result"] == "PASS"                 # overwritten
    assert out["note"] == "expert prose to keep"   # prose still preserved


def test_auto_written_outcomes_refresh_on_rerun(tmp_path):
    """An entry we previously auto-wrote IS refreshed (not treated as authored)
    when the run data changes."""
    d = _write_study(tmp_path, _BASE_YAML)
    ae.evaluate_on_run_completion(
        d, "run-001", test_runner=_fake_runner({"t_pass": {"result": "PASS"}})
    )
    summary = ae.evaluate_on_run_completion(
        d, "run-001", test_runner=_fake_runner({"t_pass": {"result": "FAIL"}})
    )
    assert summary["skipped_authored"] == 0
    assert summary["changed"] is True
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    assert spec["runs"][0]["outcomes"]["t_pass"]["result"] == "FAIL"


# ---------------------------------------------------------------------------
# Canonical-run resolution + status branches
# ---------------------------------------------------------------------------

def test_targets_named_run_and_reports_non_canonical(tmp_path):
    yaml_text = """\
name: s1
behavior_tests:
- {name: t1, measure: {kind: generation_average, path: m}, pass_if: {op: range, low: 0, high: 1}}
runs:
- name: old-run
  status: completed
  canonical: true
- name: late-run
  status: completed
"""
    d = _write_study(tmp_path, yaml_text)
    runner = _fake_runner({"t1": {"result": "PASS"}})
    # late-run is NOT canonical (old-run is explicitly pinned) but its own
    # outcomes are still recorded.
    summary = ae.evaluate_on_run_completion(d, "late-run", test_runner=runner)
    assert summary["is_canonical"] is False
    assert summary["written"] == 1
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    by = {r["name"]: r for r in spec["runs"]}
    assert by["late-run"]["outcomes"]["t1"]["result"] == "PASS"
    assert "outcomes" not in by["old-run"]   # untouched


def test_run_not_found(tmp_path):
    d = _write_study(tmp_path, _BASE_YAML)
    summary = ae.evaluate_on_run_completion(
        d, "no-such-run", test_runner=_fake_runner({"t_pass": {"result": "PASS"}})
    )
    assert summary["status"] == "run_not_found"
    assert summary["changed"] is False


def test_no_tests_status(tmp_path):
    d = _write_study(tmp_path, _BASE_YAML)
    summary = ae.evaluate_on_run_completion(d, "run-001", test_runner=_fake_runner({}))
    assert summary["status"] == "no_tests"
    assert summary["changed"] is False


def test_store_unresolved_status_from_default_runner(tmp_path):
    # No injected runner → default evaluator runner; the run has no resolvable
    # store, so it surfaces a clean status rather than crashing.
    d = _write_study(tmp_path, _BASE_YAML)
    summary = ae.evaluate_on_run_completion(d, "run-001")
    assert summary["status"] in ("store_unresolved", "evaluator_unavailable")  \
        or summary["status"].startswith("evaluator_unavailable")
    assert summary["changed"] is False
