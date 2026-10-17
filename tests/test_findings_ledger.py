"""Task C2: findings ledger (compact row + evidence drawer) + the two pure
assertion-formatting Jinja filters (`humanize_assertion`, `kv`).

The old Findings block rendered one tall, always-expanded card per finding.
This reshapes it into a compact row (glyph + claim + evidence chip) that
opens a native <details> drawer holding every existing detail — nothing
dropped, only relocated. It also fixes a real crash: a `study.behavior_tests`
entry whose `pass_if`/`expect` is truthy but `measure` is absent used to blow
up `render_study_detail_html` with `TypeError: Object of type Undefined is
not JSON serializable` (Jinja's bare `tojson` on an unset attribute).

See .superpowers/sdd/fable-increment-a/task-C2-brief.md.
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from vivarium_workbench.lib.study_page import render_study_detail_html
from vivarium_workbench.lib.study_page import humanize_assertion, kv


# ---------------------------------------------------------------------------
# Pure-filter unit tests
# ---------------------------------------------------------------------------

def test_humanize_assertion_dict_readable_phrase_no_brace():
    out = humanize_assertion({"observed": 0.42, "op": "<=", "pass_if": 0.5})
    assert "{" not in out
    assert "0.42" in out and "0.5" in out
    assert "≤" in out


@pytest.mark.parametrize("op,symbol", [("<=", "≤"), (">=", "≥"), ("==", "=")])
def test_humanize_assertion_op_mapping(op, symbol):
    out = humanize_assertion({"observed": 1, "op": op, "pass_if": 2})
    assert symbol in out
    assert op not in out


def test_humanize_assertion_scalar_passthrough():
    assert humanize_assertion(0.42) == "0.42"
    assert humanize_assertion("42 min") == "42 min"
    assert humanize_assertion(None) == ""


def test_humanize_assertion_missing_keys_no_throw():
    # No op, no pass_if/threshold/expected/value at all.
    assert "{" not in humanize_assertion({})
    assert "{" not in humanize_assertion({"observed": 1})
    assert "{" not in humanize_assertion({"op": "<="})
    assert "{" not in humanize_assertion({"measure": {"path": "x"}, "op": ">=", "threshold": 3})


def test_kv_dict_inline_and_escaped():
    out = kv({"a": 1, "b": "<script>"})
    assert "a: 1" in out
    assert "b:" in out
    assert "·" in out
    assert "<script>" not in out  # escaped
    assert "&lt;script&gt;" in out


def test_kv_non_dict_passthrough():
    assert kv(5) == "5"
    assert kv(None) == ""
    assert kv("plain") == "plain"


# ---------------------------------------------------------------------------
# Template: compact ledger + drawer, nothing dropped
# ---------------------------------------------------------------------------

_RICH_FINDING = {
    "id": "F-01", "kind": "empirical", "status": "confirms",
    "statement": "The model reproduces the observed division time.",
    "evidence": {
        "observed": "42 min", "units": "min",
        "from_test": "division-time bt1", "from_run": "run-abc run1",
        "window": "ticks 0-10", "smoking_gun": "some log excerpt",
        "divergence_factor": 1.2,
    },
    "provenance": {"run_ids": ["run-abc"]},
    "expected": {"range": "40-44 min", "threshold": 0.5, "cites": ["ref1"],
                 "summary": "lit says 42"},
    "explanation": "because reasons",
    "expert_reference": {"doc": "paper.pdf", "section": "3.2",
                          "quote": "quoted text", "note": "a note"},
    "next_action": "do the next thing",
}


def _render(findings, behavior_tests=None):
    spec = {"name": "s1", "findings": findings,
            "behavior_tests": behavior_tests or [
                {"name": "division-time", "pass_if": {"op": "at_least", "low": 1}}]}
    with tempfile.TemporaryDirectory() as d:
        return render_study_detail_html(pathlib.Path(d), "s1", spec)


def test_findings_ledger_compact_row_with_drawer():
    html = _render([_RICH_FINDING])
    # A native <details> drawer, not JS-driven.
    assert "<details" in html
    assert "finding-statement" in html or "finding-claim" in html
    assert "The model reproduces the observed division time." in html


def test_findings_ledger_preserves_every_existing_detail():
    html = _render([_RICH_FINDING])
    for needle in [
        "42 min",                      # evidence.observed (+ units)
        "division-time",               # evidence.from_test
        "run-abc",                     # evidence.from_run / provenance.run_ids
        "ticks 0-10",                  # evidence.window
        "some log excerpt",            # evidence.smoking_gun
        "1.2",                         # evidence.divergence_factor
        "40-44 min",                   # expected.range
        "ref1",                        # expected.cites
        "lit says 42",                 # expected.summary
        "because reasons",             # explanation
        "paper.pdf",                   # expert_reference.doc
        "3.2",                         # expert_reference.section
        "quoted text",                 # expert_reference.quote
        "a note",                      # expert_reference.note
        "do the next thing",           # next_action
    ]:
        assert needle in html, f"dropped: {needle!r}"
    # divergence_factor is surfaced as a rendered detail, not the literal
    # field name — assert the actual rendered markup/text, not a tautology.
    assert "finding-divergence" in html
    assert "vs expected" in html
    assert "finding-expert" in html


def test_findings_ledger_evidence_link_still_wired_to_gotostudytab():
    html = _render([_RICH_FINDING])
    # 5-tab layout: the cited test row lives in the Audit tab (Tests folded in).
    assert "_gotoStudyTab('audit'," in html


def test_findings_ledger_pass_if_band_still_present():
    html = _render([_RICH_FINDING])
    assert "pass_if-band" in html


def test_findings_absent_no_ledger_no_empty_box():
    html = _render([])
    assert '<div class="overview-section findings-section">' not in html
    assert "Findings — what this study taught us" not in html


# ---------------------------------------------------------------------------
# Regression: dict-valued observed/pass_if/measure must never 500 or dump a
# raw Python dict repr into the page.
# ---------------------------------------------------------------------------

def test_finding_with_dict_observed_renders_without_500_or_dict_repr():
    finding = dict(_RICH_FINDING)
    finding["evidence"] = dict(_RICH_FINDING["evidence"])
    finding["evidence"]["observed"] = {"observed": 0.42, "op": "<=", "pass_if": 0.5}
    html = _render([finding])  # must not raise
    assert "{'observed'" not in html
    assert "{'op'" not in html
    assert "0.42" in html


def test_behavior_test_pass_if_dict_with_missing_measure_does_not_500():
    # This is the concrete crash site: a behavior_test whose pass_if/expect
    # is truthy but `measure` is entirely absent used to raise TypeError via
    # a bare `{{ b.measure|tojson }}` on Jinja's Undefined sentinel.
    html = _render([_RICH_FINDING], behavior_tests=[
        {"name": "division-time", "pass_if": {"op": "at_least", "low": 1}},
    ])
    assert html  # must not raise


def test_behavior_test_expect_dict_with_missing_measure_does_not_500():
    html = _render([_RICH_FINDING], behavior_tests=[
        {"name": "division-time", "expect": {"op": "at_least", "low": 1}},
    ])
    assert html  # must not raise


# ---------------------------------------------------------------------------
# Fix round 1 (IMPORTANT): humanize_assertion's `measure`-is-a-dict branch
# used to embed kv(measure) -- a pre-escaped markupsafe Markup -- inside a
# plain f-string, which coerces it back to a bare str and drops the "safe"
# marking. Jinja's autoescape then can't tell the text was already escaped
# and escapes it a SECOND time (`&lt;` -> `&amp;lt;`). This only manifests
# when the value round-trips through the actual (autoescaped) Jinja
# template -- calling humanize_assertion() directly in Python already
# "looks" single-escaped either way, so the regression test must render.
# ---------------------------------------------------------------------------

def test_humanize_assertion_measure_dict_single_escaped_through_template():
    finding = dict(_RICH_FINDING)
    finding["evidence"] = dict(_RICH_FINDING["evidence"])
    finding["evidence"]["observed"] = {
        "measure": {"path": "<b>x</b> & y"}, "op": ">=", "threshold": 3,
    }
    html = _render([finding])
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in html
    assert "&amp;lt;" not in html
    assert "&amp;amp;" not in html


def test_humanize_assertion_measure_dict_escapes_metachars_once_directly():
    out = humanize_assertion({"measure": {"path": "<b>x</b> & y"}, "op": ">=", "threshold": 3})
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in out
    assert "&amp;lt;" not in out
    assert "&amp;amp;" not in out
