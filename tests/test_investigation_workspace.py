from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "vivarium_workbench/static/walkthrough.js").read_text(encoding="utf-8")
TPL = (ROOT / "vivarium_workbench/templates/index.html.j2").read_text(encoding="utf-8")


def test_studies_grouped_by_investigation():
    # _renderStudyBrowseCards groups by investigation (not one flat "All studies").
    i = JS.index("function _renderStudyBrowseCards")
    block = JS[i:i + 2500]
    assert "data-study-group" in block          # one group per investigation
    assert "__ungrouped__" in block             # bucket for studies with no iset
    assert "All studies" not in block           # the old single flat group is gone


def test_explore_tab_row_has_counts():
    assert 'id="iset-tab-inv-count"' in TPL
    assert 'id="iset-tab-study-count"' in TPL
    assert "Explore" in TPL                      # the surface label


def test_workspace_regions_exist():
    for _id in ['iset-explore', 'iset-workspace', 'ws-back', 'ws-title',
                'ws-context', 'ws-context-bar', 'ws-study-tabs', 'ws-study-frame']:
        assert 'id="%s"' % _id in TPL, _id


def test_explore_workspace_toggle_functions():
    assert "function _showExplore" in JS
    assert "function _showWorkspace" in JS
    assert "window._showExplore" in JS
    assert "window._showWorkspace" in JS


def test_showexplore_restores_investigations_list():
    # _openInvestigationDetail hides #investigations-list (the card grid shared
    # by the Investigations and Studies tabs of Explore) via a legacy
    # display:none. The "All investigations" back button routes through
    # _showExplore, so it must restore that display — otherwise the back
    # button lands on a blank Explore surface (cards still in the DOM, just
    # hidden). No re-render is required, only the display restore.
    e = JS[JS.index("function _showExplore"): JS.index("function _showExplore") + 900]
    assert "investigations-list" in e
    assert "style.display = ''" in e


def test_context_collapse_function():
    assert "function _setInvestigationContextCollapsed" in JS
    assert "ws-context-bar" in JS
    # the slim bar's onclick re-expands
    assert "_setInvestigationContextCollapsed(false)" in TPL


def test_study_tabs_manager():
    for fn in ["_wsOpenStudyTab", "_wsCloseStudyTab", "_wsRenderStudyTabs", "_wsResetStudyTabs"]:
        assert "function %s" % fn in JS, fn
        assert "window.%s" % fn in JS, fn
    # Opening a study keeps the investigation context EXPANDED above it (PR #587,
    # "keep the investigation context expanded above an open study" — a user
    # request), and closing the last tab returns to graph-only — both call
    # _setInvestigationContextCollapsed(false). Slice each function body to the
    # next sibling-function boundary rather than a fixed char window, so unrelated
    # edits inside the function can't shift the assertion out of range.
    def _fn_body(name):
        i = JS.index("function %s" % name)
        j = JS.find("\n  function ", i + 1)
        return JS[i:j] if j != -1 else JS[i:]
    assert "_setInvestigationContextCollapsed(false)" in _fn_body("_wsOpenStudyTab")
    assert "_setInvestigationContextCollapsed(false)" in _fn_body("_wsCloseStudyTab")


# ── Task 5: consistency router + investigation-workspace render ──────────────

def test_router_uses_workspace_not_legacy():
    assert "function _showInvestigationWorkspace" in JS
    # Extract exactly the function body (from its definition to its window.*
    # export), so this stays correct as the router grows and never spills into
    # the next function (which would break the window.location negative below).
    _start = JS.index("function _openStudyEmbeddedNewTab")
    r = JS[_start: JS.index("window._openStudyEmbeddedNewTab =", _start)]
    assert "_showInvestigationWorkspace" in r        # loads the study's own investigation
    assert "_wsOpenStudyTab" in r                    # opens/focuses the tab
    assert "_selectStudyInRail" in r                 # reflects selection in the rail
    assert "window.location = _studyHref" not in r   # no dead-end full-window nav
    assert "window.location" not in r                # no full-window navigation at all
    assert "_openInvestigation(" not in r            # never the legacy icon-view path


def test_showworkspace_renders_graph_not_legacy_icon_view():
    w = JS[JS.index("function _showInvestigationWorkspace"): JS.index("function _showInvestigationWorkspace") + 1200]
    assert "ws-context" in w
    assert "_showWorkspace" in w
    assert "_wsResetStudyTabs" in w
    assert "_openInvestigation(" not in w            # never the legacy icon-view path


def test_investigation_open_entry_points_route_to_workspace():
    # The card onclick and the rail entry point open the workspace, not the
    # legacy focus-mode render.
    assert 'onclick="_showInvestigationWorkspace(' in JS
    rail = JS[JS.index("function _vivOpenInvestigationFromRail"): JS.index("function _vivOpenInvestigationFromRail") + 500]
    assert "_showInvestigationWorkspace" in rail
    # No leftover page switch: _showWorkspace() (below) self-activates the
    # host page now, so the old _switchPage('studies') call must be gone.
    assert "_switchPage('studies')" not in rail


def test_showworkspace_activates_investigations_page():
    # #iset-workspace/#ws-context live inside #page-investigations, but pages
    # are shown/hidden via the .page/.active CSS toggle, not the
    # display:none/'' toggle _showWorkspace uses for the Explore/workspace
    # surfaces. _showWorkspace must activate #page-investigations itself
    # (mirroring _railOpenInvestigationDetail's manual page/menu activation)
    # so callers landing here from another page don't render the workspace
    # onto a hidden page.
    w = JS[JS.index("function _showWorkspace"): JS.index("function _showWorkspace") + 900]
    assert "page-investigations" in w
    assert "classList.add('active')" in w


# ── item 69 phase 2 — analyses-to-run: free-text textarea -> multi-select ────
# Lives in the legacy _openInvestigation/#investigation-detail tabbed panel
# (see test_showworkspace_renders_graph_not_legacy_icon_view above) — the
# primary card/rail entry points route to _showInvestigationWorkspace instead,
# which has no analyses-editing surface of its own. This panel stays reachable
# via the "Begin Study" flow (walkthrough.js _submitBeginStudy-style handler
# calls _openInvestigation(newName) right after creating an investigation) and
# via every existing "re-fetch + re-render" call after a group/composite/
# analyses mutation — narrower than the primary nav, but real, not dead code
# like the study-level _saveStudyAnalyses/#study-analyses-list pair (see
# test_compose_unification.py::test_analyses_section_present_and_reachable_on_the_study_page).

def test_analyses_field_is_a_multiselect_not_a_textarea():
    assert '<textarea id="inv-analyses-list"' not in JS
    assert '<select id="inv-analyses-list" multiple' in JS


def test_load_inv_analyses_populates_from_visualization_classes():
    i = JS.index("function _loadInvAnalyses")
    block = JS[i:JS.index("window._loadInvAnalyses = _loadInvAnalyses")]
    assert "/api/visualization-classes" in block
    assert "kind === 'analysis'" in block
    # honest-degrade: a name already in spec.yaml must survive even if the
    # currently-loaded registry doesn't have it (same convention as the
    # baseline-composite select from phase 1) — never silently drop it.
    assert "not in registry" in block


def test_save_analyses_reads_selected_options_not_value():
    i = JS.index("function _saveAnalyses")
    block = JS[i:i + 700]
    assert "o.selected" in block
    assert ".value.split" not in block   # the old free-text parsing is gone
