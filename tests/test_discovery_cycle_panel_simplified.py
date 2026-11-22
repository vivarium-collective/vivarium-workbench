"""The Discovery cycle panel is a streamlined, instrument-like surface.

Earlier it stacked parallel lists; then two action buckets ("Needs you" / "The
engine can do these") + a Details drawer. It is now ONE screen, three zones:

- **State** — a compact cycle *strip* (phases as a progress track; click a stage
  to see its trace cards inline) + the picked investigation + open/closed status.
- **Next** — the two buckets merged into ONE ranked list, each row carrying a
  line-icon who-badge (user vs processor); generation requests grouped by kind.
- **Steer** — a free-text goal box that hands the goal to the AI assistant
  (``/viva:discover``). Details collapses to what-changed · scores · gates, and
  the tracked non-blocking items are a separate collapsed disclosure.

Static assertions (no browser) — they guard the structure in the source.
"""
from pathlib import Path

JS = (Path(__file__).parent.parent / "vivarium_workbench" / "static" / "discovery-cycle.js").read_text()


def test_one_next_list_replaces_the_two_buckets():
    # the two coloured bucket boxes are gone, merged into a single "Next" list
    assert "function nextHtml(" in JS and "dcyc-next" in JS
    assert "function needsYouHtml(" not in JS and "function engineHtml(" not in JS
    assert "dcyc-bucket-you" not in JS and "dcyc-bucket-engine" not in JS
    # partition + bucketOf still classify items into you / engine / tracked
    assert "function partitionFrontier(" in JS and "function bucketOf(" in JS


def test_rows_carry_a_who_badge_not_emoji():
    # each row gets a line-icon who-badge (user outline / processor outline)
    assert "function whoBadge(" in JS and "WHO_SVG" in JS and "dcyc-who" in JS
    # fitem + group rows take a `who` arg
    assert 'fitemHtml(it, "you")' in JS and 'fitemHtml(it, "engine")' in JS
    assert 'genGroupHtml(gp, "review", "you")' in JS and 'genGroupHtml(gp, "auto", "engine")' in JS
    # no emoji badges
    for emoji in ("🧑", "⚙️", "🎯"):
        assert emoji not in JS, f"emoji {emoji!r} should be a line-icon"


def test_cycle_strip_shows_where_we_are_and_holds_the_trace():
    # the strip is derived from the step trace and is the home of the per-stage trace
    assert "function renderStrip(" in JS and "dcyc-strip" in JS and "dcyc-stage" in JS
    assert "function stagePopHtml(" in JS, "clicking a stage shows its trace cards"
    # the trace is NO LONGER duplicated in Details
    assert "function phaseTraceHtml(" not in JS
    assert "phaseTraceHtml()" not in JS


def test_steer_zone_hands_a_goal_to_the_assistant():
    assert "function steerHtml(" in JS and "dcyc-steer" in JS
    assert "function steerEngine(" in JS and "/viva:discover" in JS
    assert "_aiSeedPrompt" in JS


def test_about_link_opens_the_interactive_cycle_map():
    assert "dcyc-about-link" in JS
    assert "vivarium-collective.github.io/discovery-engine/cycle-map.html" in JS


def test_generation_requests_are_grouped_not_a_flat_wall():
    assert "function groupReqs(" in JS and "function genGroupHtml(" in JS
    assert "data-gengroup" in JS and "dcyc-gtargets" in JS


def test_details_holds_only_what_changed_scores_gates():
    assert "function detailsHtml(" in JS
    assert "dcyc-details-h" in JS and "S.detailsOpen" in JS
    assert "requestsHtml()" in JS and "deltaHtml()" in JS
    # the trace moved to the strip, so Details must not re-render it
    assert "phaseTraceHtml()" not in JS


def test_renderGraph_composes_the_new_structure():
    # the assembly line is next + steer + tracked + details (+ the strip, rendered
    # separately), not the old status + two-buckets stack
    assert "nextHtml(P, genReqs(\"review\"), genReqs(\"auto\")) + steerHtml()" in JS
    assert "statusLineHtml(P.tracked) + detailsHtml()" in JS
    assert "renderStrip();" in JS


def test_target_navigation_resolves_by_shape():
    # a clean slug opens that study; "investigation" opens the graph; free-text
    # (resolve_decision / prose) opens the Decisions tab. claim:<slug> -> the study.
    assert "function targetNav(" in JS and "function claimToStudy(" in JS
    assert "function isSlug(" in JS
    assert "data-genstudy" in JS and "data-gentab" in JS and "function genTargetLink(" in JS
    assert "openStudyLink(b.getAttribute(\"data-genstudy\")" in JS
    assert "data-geninv" in JS and "switchToInvestigation()" in JS
    assert "data-gendecide" in JS and "function openDecision(" in JS
    assert "_aigOpenDecisionsTab" in JS
    assert "dcyc-grouprow-nav" in JS
    # critique targets a study (its Conclusions tab), not a decision
    assert 'critique: "conclusions"' in JS


def test_whoBadge_ignores_a_stray_map_index_arg():
    # whoBadge(it, index) from .map must not render "undefined"
    assert 'who !== "you" && who !== "engine"' in JS


def test_each_group_explains_what_it_does():
    assert "KIND_DESC" in JS and "dcyc-gdesc" in JS


def test_tracked_items_are_a_collapsed_non_blocking_disclosure():
    assert "dcyc-status-h" in JS and "S.statusOpen" in JS
    assert "function statusLineHtml(tracked)" in JS
    assert "Tracked · non-blocking" in JS


def test_noisy_op_consequence_chips_removed():
    assert "dcyc-fchip op" not in JS
    assert '" · human" : " · runnable"' not in JS
    assert "blocks closure" not in JS


def test_runnable_builds_go_to_the_engine_not_needs_you():
    assert '(it.buildable || it.buildable_gap) && !it.human && !it.decision && !it.resolvable) return "engine"' in JS


def test_verbs_say_what_they_do():
    assert ">Analyze<" in JS
    assert "id=\"dcyc-switch\"" not in JS, "standalone Switch button removed (now a link on the name)"
    assert "dcyc-switch-link" in JS


def test_generation_request_rows_show_the_filler_skill():
    assert "r.skill" in JS and "skill a filler triggers" in JS


def test_kind_labels_cover_the_engine_generation_and_frontier_vocabulary():
    for k in ("propose_study", "acquire_model", "visualize", "hypothesize", "critique", "coverage_gap"):
        assert k + ":" in JS, f"KIND_LABELS missing {k}"


def test_show_in_main_uses_the_canonical_workspace_entry():
    # "show in main" must enter via _showInvestigationWorkspace (which switches the
    # main view into the investigation workspace), NOT the retired
    # _openInvestigationDetail directly — that left the investigations-list chrome
    # up and painted the detail/graph into a hidden container (mixed/blank view).
    sw = JS.split("function switchToInvestigation()", 1)
    assert len(sw) == 2, "switchToInvestigation not found"
    body = sw[1].split("function ", 1)[0]
    assert "window._showInvestigationWorkspace" in body
    # _openInvestigationDetail may remain only as a fallback, after the workspace entry
    assert body.index("window._showInvestigationWorkspace") < body.index("window._openInvestigationDetail")
