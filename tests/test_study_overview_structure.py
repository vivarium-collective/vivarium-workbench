from pathlib import Path

TPL = Path("vivarium_workbench/templates/study-detail.html").read_text(encoding="utf-8")


def test_group_headers_present():
    assert "Summary" in TPL
    assert "Question &amp; approach" in TPL or "Question & approach" in TPL
    assert "Findings" in TPL
    assert "Plan &amp; provenance" in TPL or "Plan & provenance" in TPL


def test_study_card_cut():
    assert 'data-narrative-path="study_card.' not in TPL


def test_status_select_exactly_once():
    assert TPL.count('id="status-select"') == 1


def test_question_text_exactly_once():
    assert TPL.count('id="question-text"') == 1


def test_hypothesis_text_exactly_once():
    assert TPL.count('id="hypothesis-text"') == 1


def test_objective_text_exactly_once():
    assert TPL.count('id="objective-text"') == 1


def test_epistemic_debts_panel_present():
    assert 'id="epistemic-debts-panel"' in TPL


def test_feedback_tracked_panel_present():
    assert 'id="feedback-tracked-panel"' in TPL


def test_report_conclusion_present():
    assert 'data-narrative-path="report.conclusion"' in TPL


def test_biological_summary_present():
    assert 'data-narrative-path="biological_summary"' in TPL


def test_set_study_tab_tests_present():
    assert "_setStudyTab('tests')" in TPL


def test_set_study_tab_conclusions_present():
    assert "_setStudyTab('conclusions')" in TPL
