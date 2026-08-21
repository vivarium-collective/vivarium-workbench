"""The study Overview tab is a Claim → Test → Result spine (study-ui redesign).

Replaces the old Question&approach / Findings / Conclusion / epistemic-debts
layout. Asserts the three sections are present, the noisy blocks are gone, and
the feedback panel + tab wrapper survive.
"""
from pathlib import Path

TEMPLATE = (Path(__file__).parent.parent / "vivarium_workbench" / "templates" / "study-detail.html").read_text()


def _overview():
    start = TEMPLATE.index('id="panel-overview"')
    end = TEMPLATE.index('id="panel-compose"')
    return TEMPLATE[start:end]


def test_overview_is_claim_test_result():
    ov = _overview()
    assert '>Claim</h2>' in ov
    assert '>Test</h2>' in ov
    assert '>Result</h2>' in ov
    # driven by the study's direct fields
    assert 'study.claim' in ov
    assert 'study.experiment' in ov
    assert 'study.result' in ov


def test_overview_drops_the_old_noise():
    ov = _overview()
    assert 'Question &amp; approach' not in ov
    assert 'Open epistemic debts' not in ov
    assert 'id="question-text"' not in ov
    assert 'id="hypothesis-text"' not in ov
    assert 'id="objective-text"' not in ov
    assert '<strong>Summary.</strong>' not in ov


def test_feedback_panel_and_wrapper_survive():
    ov = _overview()
    assert 'id="feedback-tracked-panel"' in ov
    assert 'class="study-overview"' in ov


def test_analyses_box_restored_to_model_tab():
    # This box (then id="analyses-section", a free-text textarea) was cut in
    # PR #844 as one incidental line inside an unrelated Overview redesign —
    # no rationale given, and _saveStudyAnalyses/its backend endpoint were
    # both left fully intact. item 69 (#3) restores it as a proper
    # <select multiple> (id="study-analyses-list", new id — see
    # test_compose_unification.py::test_analyses_section_present_and_reachable_on_the_study_page
    # for the markup-shape assertions), reusing the same save handler.
    assert 'id="study-analyses-section"' in TEMPLATE
    assert 'Save analyses' in TEMPLATE
