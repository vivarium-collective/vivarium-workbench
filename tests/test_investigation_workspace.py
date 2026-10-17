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
    # 5-tab / strip-removal: the accumulating #ws-study-tabs strip was removed;
    # the embedded study now opens in the single #ws-study-frame porthole.
    for _id in ['iset-explore', 'iset-workspace', 'ws-back', 'ws-title',
                'ws-context', 'ws-context-bar', 'ws-study-frame']:
        assert 'id="%s"' % _id in TPL, _id
    assert 'id="ws-study-tabs"' not in TPL


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
    # Strip removal: the embedded study view is a SINGLE open study now — the
    # accumulating closeable tabs (_wsRenderStudyTabs + _wsCloseStudyTab) were
    # removed. _wsOpenStudyTab (replace the open study) + _wsResetStudyTabs
    # remain, as the entry points the study navigator + router call.
    for fn in ["_wsOpenStudyTab", "_wsResetStudyTabs"]:
        assert "function %s" % fn in JS, fn
        assert "window.%s" % fn in JS, fn
    for gone in ["_wsCloseStudyTab", "_wsRenderStudyTabs"]:
        assert "function %s" % gone not in JS, gone
    # Opening a study keeps the investigation context EXPANDED above it (PR #587,
    # "keep the investigation context expanded above an open study" — a user
    # request). Slice the function body to the next sibling-function boundary.
    def _fn_body(name):
        i = JS.index("function %s" % name)
        j = JS.find("\n  function ", i + 1)
        return JS[i:j] if j != -1 else JS[i:]
    assert "_setInvestigationContextCollapsed(false)" in _fn_body("_wsOpenStudyTab")


def test_sticky_graph_pane_and_graph_bar_navigator():
    """Persistent-nav-pane feature: opening an embedded study pins the graph
    context (ws-context-sticky) and surfaces the graph-bar study navigator; a
    collapse toggle hides/shows the graph shell (persisted)."""
    # Template: the graph-bar navigator + collapse toggle live in the toolbar.
    assert 'id="aig-study-nav"' in TPL
    assert 'id="aig-study-prev"' in TPL and 'id="aig-study-next"' in TPL
    assert 'id="aig-study-jump-menu"' in TPL
    assert 'id="aig-collapse-btn"' in TPL and "_aigToggleCollapse()" in TPL

    # JS: the new functions exist + are exported.
    for fn in ["_renderGraphBarNav", "_aigToggleCollapse", "_applyAigShellCollapsed"]:
        assert "function %s" % fn in JS, fn
    assert "window._renderGraphBarNav" in JS
    assert "window._aigToggleCollapse" in JS

    def _fn_body(name):
        i = JS.index("function %s" % name)
        j = JS.find("\n  function ", i + 1)
        return JS[i:j] if j != -1 else JS[i:]

    # Opening a study pins the pane + refreshes the navigator; it drives the
    # embedded study via _openStudyEmbeddedNewTab and reads the graph's own data.
    openb = _fn_body("_wsOpenStudyTab")
    assert "ws-context-sticky" in openb
    assert "_renderGraphBarNav()" in openb
    navb = _fn_body("_renderGraphBarNav")
    assert "_openStudyEmbeddedNewTab" in navb
    assert "_lastDagArgs" in navb          # reuses the graph's data, no re-fetch
    assert "fetch(" not in navb

    # Collapse persists in localStorage and hides the graph SHELL only.
    togb = _fn_body("_aigToggleCollapse")
    assert "aig-shell-collapsed" in togb or "aig-shell-collapsed" in _fn_body("_aigShellCollapsed")
    assert "investigation-dag-shell" in _fn_body("_applyAigShellCollapsed")


def test_embedded_study_hides_in_iframe_header_nav():
    """study-detail.js hides the in-header #study-nav when embedded
    (window.parent !== window); the standalone page keeps it."""
    sd = (ROOT / "vivarium_workbench/static/study-detail.js").read_text(encoding="utf-8")
    i = sd.index("function _wireStudyNav")
    body = sd[i: sd.index("\n  function ", i + 1)]
    assert "window.parent !== window" in body
    assert "nav.style.display = 'none'" in body


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
# which has no analyses-editing surface of its own, and (live-verified,
# corrected from an earlier assumption here) neither does the "Begin Study"
# flow reach this panel: it creates the new studies/<auto>/spec.yaml shape via
# /api/study-create-from-composite, so _openInvestigation 404s on it exactly
# like it does for any other flat study (no investigation.yaml). This panel is
# real for the workspace's grouped "investigations" only (real
# investigation.yaml under the workspace.yaml layout: remap) — narrower than
# the primary nav, but not the workspace's only reachable analyses surface
# either: item 69 (#3) separately restored the study-level
# _saveStudyAnalyses/#study-analyses-list pair (see
# test_compose_unification.py::test_analyses_section_present_and_reachable_on_the_study_page
# and test_study_overview_structure.py::test_analyses_box_restored_to_model_tab),
# which is what actually covers flat studies.

def test_analyses_field_is_a_checklist_not_a_textarea():
    # Real UX testing found the original <select multiple> unusable
    # (undiscoverable Cmd/Ctrl+click, no selected-state feedback) — replaced
    # with checklist-select.js's filterable checkbox list, a plain mount div.
    assert '<textarea id="inv-analyses-list"' not in JS
    assert '<select id="inv-analyses-list"' not in JS
    assert 'id="inv-analyses-list"' in JS


def test_load_inv_analyses_populates_from_visualization_classes():
    i = JS.index("function _loadInvAnalyses")
    block = JS[i:JS.index("window._loadInvAnalyses = _loadInvAnalyses")]
    assert "/api/visualization-classes" in block
    assert "kind === 'analysis'" in block
    # honest-degrade: a name already in spec.yaml must survive even if the
    # currently-loaded registry doesn't have it (same convention as the
    # baseline-composite select from phase 1) — never silently drop it.
    assert "flagged: true" in block
    assert "ChecklistSelect.render" in block


def test_save_analyses_reads_checklist_selection():
    i = JS.index("function _saveAnalyses")
    block = JS[i:i + 700]
    assert "ChecklistSelect.selected" in block
    assert ".value.split" not in block   # the old free-text parsing is gone
    assert "o.selected" not in block     # the old <select>-options reading is gone
