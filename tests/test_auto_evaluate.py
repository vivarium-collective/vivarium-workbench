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


# ---------------------------------------------------------------------------
# #1472 — grade a test only against its target run (requires_simulation / given)
# ---------------------------------------------------------------------------

# Two arms, each with a test that only makes sense against its own arm's run.
_MULTI_ARM_SPEC = {
    "name": "multi-arm",
    "baseline": [{"name": "base"}],
    "variants": [{"name": "severity-0.2"}, {"name": "severity-0.8"}],
    "behavior_tests": [
        {"name": "low_keeps_map", "requires_simulation": "severity-0.2",
         "measure": {"kind": "generation_average", "path": "map"},
         "pass_if": {"op": "range", "low": 0, "high": 2}},
        {"name": "high_drops_map", "requires_simulation": "severity-0.8",
         "measure": {"kind": "generation_average", "path": "map"},
         "pass_if": {"op": "gte", "value": 8}},
        {"name": "baseline_sane",
         "measure": {"kind": "generation_average", "path": "map"},
         "pass_if": {"op": "range", "low": 0, "high": 100}},
    ],
    "runs": [
        {"name": "run-base", "status": "completed"},                      # baseline (no variant)
        {"name": "run-low", "status": "completed", "variant": "severity-0.2"},
        {"name": "run-high", "status": "completed", "variant": "severity-0.8"},
    ],
}


def _run(spec, name):
    return next(r for r in spec["runs"] if r["name"] == name)


def test_tests_for_run_matches_only_target_arm():
    s = _MULTI_ARM_SPEC
    def names(run_name):
        return {t["name"] for t in ae._tests_for_run(s, _run(s, run_name))}
    # The severity-0.2 run grades only its own test (+ the untargeted baseline test
    # is NOT graded on a variant arm).
    assert names("run-low") == {"low_keeps_map"}
    assert names("run-high") == {"high_drops_map"}
    # The baseline run grades the untargeted test (target None → baseline arm) and
    # NOT the variant-specific tests.
    assert names("run-base") == {"baseline_sane"}


def test_tests_for_run_matches_by_run_name_and_given():
    spec = {
        "variants": [{"name": "arm-x"}],
        "behavior_tests": [
            {"name": "by_name", "requires_simulation": "run-x"},
            {"name": "by_given", "given": {"run": "variant", "variant": "arm-x"}},
        ],
        "runs": [{"name": "run-x", "variant": "arm-x"}],
    }
    assert {t["name"] for t in ae._tests_for_run(spec, spec["runs"][0])} == {"by_name", "by_given"}


def test_evaluator_runner_filters_tests_per_run(tmp_path, monkeypatch):
    """The default runner hands evaluate_study ONLY the tests that target the run
    — so a cross-arm test is left unevaluated (absent) on the other arm's run."""
    import ruamel.yaml
    d = tmp_path / "studies" / "ma"
    d.mkdir(parents=True)
    ry = ruamel.yaml.YAML()
    spec = dict(_MULTI_ARM_SPEC)
    # Mark runs evaluation_only so the default runner grades with reader=None and
    # never needs a real emitter store / RunReader.
    spec["runs"] = [dict(r, evaluation_only=True) for r in spec["runs"]]
    with open(d / "study.yaml", "w", encoding="utf-8") as fh:
        ry.dump(spec, fh)

    seen: dict = {}

    def spy_evaluate_study(received_spec, reader, ws_root=None, run_opener=None):
        tests = received_spec.get("tests") or received_spec.get("behavior_tests") or []
        names = [t["name"] for t in tests]
        seen.setdefault("calls", []).append(names)
        return {n: {"result": "PASS", "evaluated_by": "code"} for n in names}

    import discovery_engine.study_evaluator as se
    monkeypatch.setattr(se, "evaluate_study", spy_evaluate_study)

    ae.evaluate_on_run_completion(d, "run-low")
    ae.evaluate_on_run_completion(d, "run-high")

    assert seen["calls"] == [["low_keeps_map"], ["high_drops_map"]]

    spec_after = study_io.load_yaml_mapping(d / "study.yaml")
    by = {r["name"]: r for r in spec_after["runs"]}
    # Each run's outcomes carry ONLY its own test; the cross-arm test is unevaluated.
    assert set(by["run-low"].get("outcomes") or {}) == {"low_keeps_map"}
    assert set(by["run-high"].get("outcomes") or {}) == {"high_drops_map"}
