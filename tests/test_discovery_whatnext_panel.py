"""The "Discovery — what to do next" panel (argument-graph.js) summarizes a
degenerate value-ranking instead of rendering it as a flat, uninterpretable table.

When an investigation has no epistemic edges, the engine's planner gives every
opportunity the same score (downstream impact is 0 for all), so the old render —
a "value-ranked" table of identical scores plus a redundant "Decide" table listing
the same studies again — was noise. The panel now detects that degeneracy and shows
an honest one-line summary + the suggestions (the real signal) + the ready studies
behind one disclosure. The ranked view is preserved when scores actually vary.

Static assertions (no browser) guard the structure in the source.
"""
from pathlib import Path

JS = (Path(__file__).parent.parent / "vivarium_workbench" / "static" / "argument-graph.js").read_text()


def test_degenerate_ranking_is_detected_and_summarized():
    assert "_hasVariance" in JS and "var degenerate" in JS
    assert "No priority ranking yet" in JS


def test_ready_studies_merge_into_one_disclosure():
    # the redundant "Most valuable" + "Decide" tables collapse into a single
    # <details> ("show N ready studies") in the degenerate case
    assert "disc-det" in JS
    assert "ready " in JS and "<details" in JS


def test_suggestions_are_promoted_as_the_real_signal():
    assert "_pushSuggestions(" in JS
    assert "Suggested next studies" in JS


def test_ranked_view_is_preserved_when_scores_vary():
    # the non-degenerate branch still renders the ranked opportunities + Decide tables
    assert "Most valuable to investigate" in JS
    assert "Decide — the next action" in JS
