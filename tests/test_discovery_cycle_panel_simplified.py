"""The Discovery cycle panel is restructured into one prioritized to-do.

Before: renderGraph stacked six parallel sections (delta + a verbose banner +
Generation requests wall + Gates & frontier + phase nodes). That was three
overlapping "what to do" lists plus raw trace, hard to read or act on.

After: a one-line status, then TWO action buckets — "Needs you" (human
decisions/reviews) and "The engine can do these" (auto/runnable, with an inline
Advance) — then a collapsed "Details" disclosure holding the trace, scores,
what-changed and figure toggle. Generation requests are grouped by kind so the
repetitive ones collapse (10 identical "write the claim line" -> one row).

Static assertions (no browser) — they guard the structure in the source.
"""
from pathlib import Path

JS = (Path(__file__).parent.parent / "vivarium_workbench" / "static" / "discovery-cycle.js").read_text()


def test_two_buckets_replace_the_three_parallel_lists():
    assert "function needsYouHtml(" in JS and 'dcyc-bucket-you' in JS
    assert "function engineHtml(" in JS and 'dcyc-bucket-engine' in JS
    assert "function partitionFrontier(" in JS and "function bucketOf(" in JS
    # the old verbose banner + the separate gates/frontier section are gone
    assert "function bannerHtml(" not in JS, "verbose banner replaced by statusLineHtml"
    assert "function frontierHtml(" not in JS, "gates/frontier folded into the buckets"
    assert "function statusLineHtml(" in JS


def test_generation_requests_are_grouped_not_a_flat_wall():
    assert "function groupReqs(" in JS and "function genGroupHtml(" in JS
    # grouping keys on kind and shows a count + expandable targets
    assert "data-gengroup" in JS and "dcyc-gtargets" in JS


def test_details_disclosure_holds_the_raw_view():
    assert "function detailsHtml(" in JS
    assert "dcyc-details-h" in JS and "S.detailsOpen" in JS
    # the raw requests list, what-changed, figure toggle and trace live in Details
    assert "requestsHtml()" in JS and "phaseTraceHtml()" in JS and "deltaHtml()" in JS


def test_renderGraph_composes_the_new_structure():
    # the one assembly line is status + buckets + details, not the old 6-part stack
    assert "applyReportHtml() + statusLineHtml() + you + engine + nothing + detailsHtml(" in JS


def test_verbs_say_what_they_do():
    # Run -> Analyze (read-only re-derive); Advance / Apply kept; stray Switch button removed
    assert ">Analyze<" in JS
    assert "id=\"dcyc-switch\"" not in JS, "standalone Switch button removed (now a link on the name)"
    assert "dcyc-switch-link" in JS
