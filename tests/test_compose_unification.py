# tests/test_compose_unification.py
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_workbench/templates/study-detail.html").read_text()


def test_panel_compose_exists_and_old_wrappers_gone():
    assert 'data-kind="compose" id="panel-compose"' in HTML
    # NB: `panel-build` is NOT listed here anymore. The study-spine reorg
    # introduces a top-level Assurance `Build` tab (loop provenance) that
    # legitimately owns `id="panel-build"`; it is unrelated to the retired
    # legacy compose `build` sub-member. The genuinely-retired compose CRUD
    # wrappers (baseline/variants/interventions) must still be absent.
    for old in ['id="panel-baseline"', 'id="panel-variants"', 'id="panel-interventions"']:
        assert old not in HTML, f"old wrapper still present: {old}"


def test_single_compose_member_button():
    # Post pillar/member-indirection removal (Fable A #6): the top
    # `.study-pillar` button for Model IS the compose tab — no subnav member
    # button underneath it anymore.
    import re
    compose_btns = re.findall(r'<button class="study-pillar"[^>]*data-kind="compose"[^>]*>', HTML)
    assert len(compose_btns) == 1, f"expected 1 compose pillar button, got {len(compose_btns)}"
    assert "_setStudyTab('compose')" in HTML
    # `_setStudyTab('build')` is NOT forbidden here anymore: the reorg's
    # top-level Assurance `Build` pillar legitimately calls it. What must stay
    # gone is the legacy compose SUBNAV (baseline/variants/interventions).
    for old in ["_setStudyTab('baseline')", "_setStudyTab('variants')", "_setStudyTab('interventions')"]:
        assert old not in HTML, f"old compose tab button call still present: {old}"


def _panel_compose():
    i = HTML.index('id="panel-compose"')
    nxt = HTML.find('class="study-tab-panel"', i + 10)
    return HTML[i: nxt if nxt != -1 else len(HTML)]


def test_inner_hooks_preserved_in_compose():
    p = _panel_compose()
    # The legacy v2 baseline/variants/interventions CRUD was retired; the Model
    # (compose) panel now surfaces the composite + its resolved config + the v3
    # conditions editor.
    assert "data-model-composite" in p and "model-config-mount" in p  # composite + resolved config
    assert "_openCompositeLoom" in p                                  # open in the Composite Explorer
    assert "cond-block" in p                                          # v3 conditions editor
    # Build block guard still present inside the merged panel.
    assert "study.model_change or study.implementation_requirements" in p


def test_other_panels_untouched():
    # Post pillar-unification (Simulate/Visualize merge), the non-compose panels
    # are overview / simulate / visualize / tests / conclusions; the old split
    # simulations/observables/runs/visualizations panels were merged away.
    for k in ["overview", "simulate", "visualize", "tests", "conclusions"]:
        assert f'id="panel-{k}"' in HTML, f"unrelated panel disturbed: panel-{k}"


def test_subnav_and_pillar_indirection_removed():
    # Fable A #6: the second tab row + pillar/member indirection were always
    # vestigial (every pillar had exactly one member) and are now deleted —
    # the top `.study-pillar` buttons drive _setStudyTab directly.
    assert 'id="study-subnav"' not in HTML
    js = (ROOT / "vivarium_workbench/static/study-detail.js").read_text()
    for fn in ("_setStudyPillar", "_showPillarSubnav", "_pillarForKind"):
        assert fn not in js, f"{fn} should have been deleted from study-detail.js"


def test_build_guard_preserves_conditions_and_baseline():
    # Regression: the merged build-block guard must mirror the pre-merge
    # panel-build guard so a non-v3 study with conditions/baseline (but no
    # model_change/impl_reqs) keeps its Model + Conditions sections.
    p = _panel_compose()
    i = p.index("_has_build =")
    guard = p[i:i + 120]
    for field in ["study.model_change", "study.implementation_requirements", "study.conditions", "study.baseline"]:
        assert field in guard, f"build guard dropped {field}: {guard!r}"


def test_analyses_section_present_and_reachable_on_the_study_page():
    # Regression: an earlier "Analyses" authoring control was wired only into
    # the legacy Investigation-detail panel (#investigation-detail inside
    # #page-studies), which no current navigation path opens — dead UI. The
    # control must stay reachable on the Study page. Task E1 relocated it
    # (back) from the Exports (data) tab to the Model (compose) tab — it is
    # study setup ("what to compute"), not an export artifact — near
    # Conditions, so it now lives in #panel-compose. Task E4 later deleted
    # the Exports/data tab entirely (#panel-data no longer exists).
    #
    # PR #844 then removed the box itself as one incidental line inside an
    # unrelated Overview-tab redesign (no standalone rationale given, and
    # _saveStudyAnalyses/the backend endpoint were both left fully intact,
    # not cleaned up) — leaving this test's own name ("present_and_reachable")
    # asserting the opposite of what it says. item 69 (#3) restores it per
    # this test's original intent, reusing the same live
    # /api/visualization-classes registry + honest-degrade convention as the
    # sibling per-investigation fix. Rendered via checklist-select.js's
    # filterable checkbox list (not a native <select multiple> — real
    # visual/UX testing found that unusable: undiscoverable Cmd/Ctrl+click,
    # no selected-state feedback) — this is a mount div, populated client-side.
    assert 'id="study-analyses-list"' in HTML


def test_save_study_analyses_posts_to_the_working_endpoint():
    js = (ROOT / "vivarium_workbench/static/study-detail.js").read_text()
    i = js.index("function _saveStudyAnalyses")
    block = js[i:i + 800]
    assert "/api/study-set-analyses" in block
    assert "studyName()" in block
    assert "window._saveStudyAnalyses = _saveStudyAnalyses" in js


def test_baseline_composite_replace_control_present_and_wired():
    # Regression: the pre-unification "+ Add baseline" form (_submitBaselineAdd)
    # was removed from this template, leaving its JS handler and the
    # /api/study-baseline-add /-remove endpoints orphaned — no UI path could
    # ever set/replace a study's composite ref once created (e.g. to fix the
    # "+ Study" blank scaffold's placeholder ref). This control reuses those
    # existing, working endpoints instead of adding a new one.
    p = _panel_compose()
    assert 'class="baseline-composite-input"' in p
    assert 'class="action-btn baseline-composite-set"' in p
    assert "baseline-composite-status" in p
    js = (ROOT / "vivarium_workbench/static/study-detail.js").read_text()
    i = js.index("baseline-composite-set", js.index("bindAll"))
    block = js[i:i + 1200]
    assert "/api/study-baseline-remove" in block
    assert "/api/study-baseline-add" in block
    # Regression: add MUST come before remove. study_baseline_remove refuses
    # to leave baseline[] empty (400) — a single-entry study (the common
    # case, e.g. a fresh "+ Study" blank scaffold) always has exactly one
    # entry, so remove-then-add always fails on the very first call.
    assert block.index("/api/study-baseline-add") < block.index("/api/study-baseline-remove")
