"""Rigor-split rendering (viva-superpowers #285 companion).

Covers the new display surfaces for the gate-class distinctions:
  * ``gate_rigor.verdict_count_split`` — pins vs acceptance vs expected-fail
    buckets (delegates to viva_superpowers.study_verdict when #285 is
    installed; local mirror otherwise).
  * ``behavior_test_card`` — per-row gate_class / expected-fail badges and the
    split ledger in the card summary; an expected-fail control never renders
    as a green pass.
  * ``single_study_report`` — the gate-ledger section, the units_and_time
    table, and the provenance/environment block (degrading gracefully when
    git / packages are absent).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import behavior_test_card as btc
from vivarium_workbench.lib import gate_rigor
from vivarium_workbench.lib.single_study_report import (
    _provenance_info,
    _render_gate_ledger,
    _render_provenance,
    _render_units_and_time,
    render_single_study_report,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _mixed_spec() -> dict:
    """A study with one of each gate class: a passing regression pin, a
    passing + a failing acceptance criterion, and a behaved expected-fail
    control (negative control that failed as designed)."""
    return {
        "name": "s1",
        "behavior_tests": [
            {"name": "pin_a", "gate_class": "regression_pin",
             "measure": {"path": "x"}, "pass_if": {"op": "at_least", "low": 1}},
            {"name": "acc_b", "gate_class": "acceptance_criterion",
             "measure": {"path": "y"}, "pass_if": {"op": "at_least", "low": 1}},
            {"name": "acc_c", "gate_class": "acceptance_criterion",
             "measure": {"path": "z"}, "pass_if": {"op": "at_least", "low": 1}},
            {"name": "neg_d", "control": "negative",
             "measure": {"path": "w"}, "pass_if": {"op": "at_least", "low": 1}},
        ],
        "runs": [{
            "name": "r1", "canonical": True, "status": "completed",
            "outcomes": {
                "pin_a": {"result": "PASS"},
                "acc_b": {"result": "PASS"},
                "acc_c": {"result": "FAIL"},
                "neg_d": {"result": "FAIL"},
            },
        }],
    }


@pytest.fixture
def _ws(tmp_path):
    """Workspace with the minimum directory layout the renderer expects."""
    ws = tmp_path / "ws"
    (ws / "investigations").mkdir(parents=True)
    (ws / "studies").mkdir(parents=True)
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: ws\nplugin_version: 0.6.1\npackage_path: pkg\n"
    )
    return ws


def _write_study(ws: Path, slug: str, **fields) -> Path:
    p = ws / "studies" / slug / "study.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": 4, "name": slug, **fields}
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


# ---------------------------------------------------------------------------
# gate_rigor — classification + count split
# ---------------------------------------------------------------------------


def test_expected_fail_markers():
    assert gate_rigor.is_expected_fail({"expected_result": "fail"})
    assert gate_rigor.is_expected_fail({"classification": "diagnostic"})
    assert gate_rigor.is_expected_fail({"control": "negative"})
    assert not gate_rigor.is_expected_fail({"gate_class": "regression_pin"})
    assert not gate_rigor.is_expected_fail({})


def test_expected_fail_checked_before_gate_class():
    # A negative control that ALSO declares gate_class is still a control —
    # it must never be counted as an acceptance pass.
    t = {"control": "negative", "gate_class": "acceptance_criterion"}
    assert gate_rigor.is_expected_fail(t)
    assert gate_rigor.test_gate_class(t) is None


def test_verdict_count_split_mixed():
    split = gate_rigor.verdict_count_split(_mixed_spec())
    assert split["regression_pins"] == {"total": 1, "pass": 1, "fail": 0}
    assert split["acceptance_criteria"] == {"total": 2, "pass": 1, "fail": 1}
    assert split["expected_fail"] == {"total": 1, "behaved": 1}
    assert split["unclassified"] == 0
    # #285: committed_rerunnable is a COUNT of tests carrying an executable
    # check / machine predicate (pass_if here), not a bool — all 4 tests
    # in this fixture declare pass_if.
    assert split["committed_rerunnable"] == 4
    assert "pins: 1/1" in split["label"]
    assert "acceptance: 1/2" in split["label"]
    assert "expected-fail behaved: 1/1" in split["label"]


def test_verdict_count_split_narrated_and_unclassified():
    spec = {
        "name": "s1",
        "behavior_tests": [
            {"name": "prose_only", "en": "just a narrative expectation"},
            {"name": "graded_no_class",
             "measure": {"path": "x"}, "pass_if": {"op": "at_least", "low": 1}},
        ],
        "runs": [],
    }
    split = gate_rigor.verdict_count_split(spec)
    assert split["narrated"] == 1
    assert split["unclassified"] == 1
    # #285: committed_rerunnable counts tests with an executable check —
    # only "graded_no_class" (pass_if) qualifies; "prose_only" doesn't.
    assert split["committed_rerunnable"] == 1
    assert split["regression_pins"]["total"] == 0


def test_verdict_count_split_tolerates_garbage():
    # #285: the label always renders the pins/acceptance ledger (even at
    # 0/0) rather than falling back to a "no classified gates" sentinel.
    assert gate_rigor.verdict_count_split({})["label"] == "pins: 0/0; acceptance: 0/0"
    assert gate_rigor.verdict_count_split(None)["regression_pins"]["total"] == 0


# ---------------------------------------------------------------------------
# behavior_test_card — badges + split-aware rollup
# ---------------------------------------------------------------------------


def test_card_verdict_carries_gate_class_and_count_split():
    v = btc.build_behavior_tests_verdict(_mixed_spec())
    by_name = {r["name"]: r for r in v["tests"]}
    assert by_name["pin_a"]["gate_class"] == "regression_pin"
    assert by_name["acc_b"]["gate_class"] == "acceptance_criterion"
    assert by_name["neg_d"]["expected_fail"] is True
    assert v["count_split"]["expected_fail"]["behaved"] == 1


def test_card_behaved_control_is_not_a_failure():
    # Only the behaved negative control + a passing pin: the card must roll up
    # within_tol, not mismatch — the control failed AS DESIGNED.
    spec = _mixed_spec()
    spec["behavior_tests"] = [t for t in spec["behavior_tests"]
                              if t["name"] in ("pin_a", "neg_d")]
    v = btc.build_behavior_tests_verdict(spec)
    assert v["overall"] == "within_tol"
    assert v["n_fail"] == 0


def test_card_html_renders_gate_badges_and_expected_fail():
    spec = _mixed_spec()
    v = btc.build_behavior_tests_verdict(spec)
    html = btc.render_behavior_tests_html(v, spec)
    assert ">pin<" in html and ">acceptance<" in html
    assert "EXPECTED FAIL" in html
    # The behaved control's amber pill — never the green PASS palette.
    assert "#fef3c7" in html
    # Split ledger surfaces in the summary strip (#285 label uses "behaved:").
    assert "expected-fail behaved: 1/1" in html


def test_card_html_unexpected_pass_never_green():
    spec = _mixed_spec()
    spec["runs"][0]["outcomes"]["neg_d"] = {"result": "PASS"}
    v = btc.build_behavior_tests_verdict(spec)
    html = btc.render_behavior_tests_html(v, spec)
    assert "UNEXPECTED PASS" in html
    # A non-behaving control is an effective failure → the card can't be clean.
    assert v["overall"] == "mismatch"


# ---------------------------------------------------------------------------
# single_study_report — ledger / units_and_time / provenance sections
# ---------------------------------------------------------------------------


def test_gate_ledger_renders_split():
    html = _render_gate_ledger(_mixed_spec())
    assert 'id="gate-ledger"' in html
    assert "pins 1/1" in html
    assert "acceptance 1/2" in html
    assert "expected-fail behaved 1/1" in html


def test_gate_ledger_empty_without_tests():
    assert _render_gate_ledger({"name": "s1"}) == ""


def test_units_and_time_renders_list_form():
    spec = {"units_and_time": [
        {"quantity": "biomass", "unit": "fg", "note": "dry mass"},
        {"quantity": "step", "unit": "s", "scale": 2.0},
    ]}
    html = _render_units_and_time(spec)
    assert 'id="units-and-time"' in html
    assert "biomass" in html and "fg" in html and "dry mass" in html
    assert "2.0" in html


def test_units_and_time_renders_mapping_form():
    html = _render_units_and_time(
        {"units_and_time": {"time_unit": "s", "step_seconds": 1.0}})
    assert "time_unit" in html and "step_seconds" in html


def test_units_and_time_absent_omits_section():
    assert _render_units_and_time({}) == ""
    assert _render_units_and_time({"units_and_time": None}) == ""


def test_provenance_degrades_without_git_or_pkgs(tmp_path, monkeypatch):
    # A non-repo dir + a git binary that "isn't there" → no crash, no commit.
    import subprocess

    def _no_git(*a, **k):
        raise FileNotFoundError("git not installed")
    monkeypatch.setattr(subprocess, "run", _no_git)
    info = _provenance_info(tmp_path)
    assert "workspace_commit" not in info
    html = _render_provenance(info)
    assert 'id="provenance"' in html
    assert "unavailable" in html


def test_provenance_renders_versions_when_importable():
    html = _render_provenance(_provenance_info(None))
    # The test venv has vivarium-workbench installed, so at least one version
    # chip should render; and no absolute paths are baked into the block.
    assert 'id="provenance"' in html
    assert str(Path.home()) not in html


# ---------------------------------------------------------------------------
# End-to-end: the rendered report carries the new sections
# ---------------------------------------------------------------------------


def test_full_report_renders_ledger_units_and_provenance(_ws):
    spec = _mixed_spec()
    _write_study(
        _ws, "s1",
        behavior_tests=spec["behavior_tests"],
        runs=spec["runs"],
        units_and_time={"time_unit": "s", "step_seconds": 1.0},
        report={"title": "S1", "verdict": "passing"},
    )
    out = render_single_study_report(_ws, "s1")
    html = out.read_text(encoding="utf-8")
    assert 'id="gate-ledger"' in html and "pins 1/1" in html
    assert 'id="units-and-time"' in html and "time_unit" in html
    assert 'id="provenance"' in html
    assert "EXPECTED FAIL" not in html  # card badges live on the card, not here


def test_load_study_detail_spec_attaches_count_split(_ws):
    from vivarium_workbench.lib.study_spec import load_study_detail_spec
    spec = _mixed_spec()
    _write_study(
        _ws, "s1",
        baseline=[{"name": "baseline", "composite": "pkg.composites.demo.c1"}],
        behavior_tests=spec["behavior_tests"], runs=spec["runs"],
    )
    loaded = load_study_detail_spec(_ws, "s1")
    split = loaded.get("verdict_count_split")
    assert isinstance(split, dict)
    assert split["regression_pins"]["total"] == 1
    assert split["expected_fail"]["behaved"] == 1
