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


def test_dockable_graph_and_graph_bar_navigator():
    """Dockable-graph feature: while a study is embedded the graph floats with a
    draggable/resizable DOCKED window + a COLLAPSED slim bar, full graph controls
    in the docked panel, and a ⛶ control that EXITS the study back to the inline
    graph. The graph-bar navigator drives the embedded study."""
    # The graph-bar study navigator (prev / next / jump) was removed — clicking a
    # study node in the graph is the navigation now.
    assert 'id="aig-study-nav"' not in TPL
    # The popup has NO title bar: #investigation-dag-lead + the "Investigation
    # graph" heading are gone, and the dock control is integrated into the toolbar.
    assert 'id="investigation-dag-lead"' not in TPL
    assert '<strong>Investigation graph</strong>' not in TPL
    dvs = TPL.index('id="investigation-dag-fs"')
    dvs_end = TPL.index('<!-- /#investigation-dag-fs -->', dvs)
    fsblock = TPL[dvs:dvs_end]
    assert fsblock.index('id="aig-dock-ctl"') > fsblock.index('class="aig-toolbar"'), \
        "dock control should sit inside the toolbar row"
    for state in ("collapsed", "docked"):
        assert 'data-dock="%s"' % state in TPL
        assert "_aigSetDockState('%s')" % state in TPL
    # ⛶ is now an ACTION (exit the study), not an "expanded" dock state.
    assert 'data-dock="expanded"' not in TPL
    assert "_aigExitToGraph()" in TPL
    # The old separate collapse toggle is gone (unified into the dock control).
    assert 'id="aig-collapse-btn"' not in TPL
    assert "_aigToggleCollapse" not in TPL and "_aigToggleCollapse" not in JS

    # Controls consolidated: one unified Zoom slider + a ⚙ Display popover (the
    # old separate Detail / Width / Zoom groups were folded into the popover).
    assert 'id="aig-unified-zoom"' in TPL and 'id="aig-settings-menu"' in TPL

    # JS: the dock-state + drag + resize + exit functions exist + are exported.
    for fn in ["_renderGraphBarNav", "_aigSetDockState", "_applyDockState",
               "_aigExitToGraph", "_aigWireDockDrag", "_aigWireDockResize",
               "_aigApplyDockRect", "_aigSaveDockRect"]:
        assert "function %s" % fn in JS, fn
    assert "window._aigSetDockState" in JS
    assert "window._aigExitToGraph" in JS

    def _fn_body(name):
        i = JS.index("function %s" % name)
        j = JS.find("\n  function ", i + 1)
        return JS[i:j] if j != -1 else JS[i:]

    # Readable docked zoom: dock does NOT fit-all (which shrank names to tiny) —
    # it sets a readable zoom and lets the panel scroll.
    assert "function _aigDockReadable" in JS
    setb = _fn_body("_aigSetDockState")
    assert "_aigDockReadable()" in setb and "window._aigFit()" not in setb
    assert "aigZoom = 1" in _fn_body("_aigDockReadable")
    # Drag handle is the toolbar now (no title bar); it ignores the controls.
    dragb = _fn_body("_aigWireDockDrag")
    assert ".aig-toolbar" in dragb
    assert "button, input, select" in dragb        # drag ignores interactive controls

    # Opening a study keeps the graph IN PLACE but shrinks its pane
    # (_aigStudyActive) so the study card shows below — no auto-scroll, no float.
    # It still refreshes the navigator and drives the embedded study.
    openb = _fn_body("_wsOpenStudyTab")
    assert "_aigStudyActive = true" in openb
    assert "_renderGraphBarNav()" in openb
    assert "ws-context-sticky" not in openb        # sticky strip superseded
    resetb = _fn_body("_wsResetStudyTabs")
    assert "_aigExitDock()" in resetb
    assert "_aigStudyActive = false" in resetb
    navb = _fn_body("_renderGraphBarNav")
    assert "_openStudyEmbeddedNewTab" in navb
    assert "_lastDagArgs" in navb                  # reuses the graph's data, no re-fetch
    assert "fetch(" not in navb

    # Dock state + dragged rect persist in localStorage.
    setb = _fn_body("_aigSetDockState")
    assert "aig-dock-state" in setb
    assert "_aigApplyDockRect" in setb             # restores the dragged/resized rect
    assert "aig-dock-rect" in _fn_body("_aigSaveDockRect")
    # ⛶ exits the study back to the inline graph (what closing the study restores).
    exitb = _fn_body("_aigExitToGraph")
    assert "_wsResetStudyTabs" in exitb


def test_dag_node_click_selects_then_opens():
    """Three distinct gestures: (1) a first card click SELECTS only (highlight,
    no expand, no open); (2) the "+N more … show full card" affordance expands the
    card IN PLACE (reader only, never opens); (3) a second click on the selected
    card opens it. The floating pop-out was removed in favour of inline expansion."""
    assert "node.onclick = _aigNodeClick" in JS
    # First click selects only — the node handler does NOT expand or open.
    j = JS.index("var _aigNodeClick =")
    node_body = JS[j:j + 400]
    assert "window._aigSelected === s.name" in node_body     # already-selected → open
    assert "_openStudyInsideInvestigation(s.name)" in node_body
    assert "_selectThis()" in node_body                      # first click: select only
    # The "show full card" affordance expands the card IN PLACE (no open), growing
    # it downward from its anchored top and extending the graph's scroll extent.
    p = JS.index("var _toggleExpandInPlace =")
    expand_body = JS[p:p + 400]
    assert "_aigSetNodeExpanded(node" in expand_body
    assert "_aigAccommodateExpansion()" in expand_body
    assert "aig-more-findings" in JS                          # wired to the show affordance
    assert "aig-full-details" in JS                           # inline detail block exists
    # The floating pop-out code path is gone (no modal element / × close / show fn).
    assert "_aigShowCardPopout" not in JS
    assert "aig-card-popout" not in JS
    # Expanding must NOT relayout/reposition — it anchors the top and only grows the
    # shell's scroll extent via dataset.canvasH.
    acc = JS.index("function _aigAccommodateExpansion")
    acc_body = JS[acc:acc + 700]
    assert "dataset.canvasH" in acc_body
    assert "layoutCanvasH" in acc_body
    # The "Open study →" navigation is preserved inside the expanded detail.
    assert "aig-open-study" in JS


def test_floating_graph_hides_prose_when_study_open():
    """While a study is open the investigation prose is hidden so only the graph
    region remains, and that region floats (position:fixed dock) out of flow so
    the study reclaims the full column; the docked window is resizable."""
    assert "function _setInvProseForStudy" in JS
    i = JS.index("function _setInvProseForStudy")
    body = JS[i: JS.index("\n  function ", i + 1)] if "\n  function " in JS[i:] else JS[i:i + 400]
    assert "inv-study-open" in body
    assert "investigation-detail-view" in body
    # CSS: .inv-study-open hides every detail-view child but the graph region,
    # and the graph floats via .aig-dock (position:fixed), with two state sizes.
    css = (ROOT / "vivarium_workbench/static/style.css").read_text(encoding="utf-8")
    assert "#investigation-detail-view.inv-study-open > * { display: none" in css
    assert "#investigation-detail-view.inv-study-open > #investigation-dag-fs { display: block" in css
    assert "#investigation-dag-fs.aig-dock" in css
    assert "position: fixed" in css
    for state in ("aig-dock-collapsed", "aig-dock-docked"):
        assert "#investigation-dag-fs.%s" % state in css
    assert "aig-dock-expanded" not in css          # expanded dock state removed
    # Resize is now custom JS corner grips (CSS `resize` was unreliable here).
    assert "resize: both" not in css
    assert ".aig-dock-grip" in css
    assert ".aig-resizing" in css
    assert 'class="aig-dock-grip" data-grip="se"' in TPL
    assert 'class="aig-dock-grip" data-grip="sw"' in TPL
    # The retired sticky-strip CSS (sticky pane + 200px shell cap) is gone.
    assert "ws-context-sticky" not in css
    assert "height: 200px !important" not in css
    # #investigation-dag-fs is a DIRECT child of the detail view (so hiding the
    # other children leaves graph-only); the prose blocks are its siblings.
    dv = TPL.index('id="investigation-detail-view"')
    dv_end = TPL.index('<!-- /#investigation-dag-fs -->', dv)
    detail = TPL[dv:dv_end]
    assert 'id="investigation-intro"' in detail
    assert 'id="investigation-decision-cockpit"' in detail
    assert 'id="investigation-dag-fs"' in detail


def test_investigation_overview_collapsed_by_default_graph_prominent():
    """Inline investigation view (no study open): the prose is collapsed by
    default behind a "▸ Investigation overview" toggle, and the graph renders
    prominently right after the title + toggle (title · toggle · graph)."""
    # Template: the toggle is integrated into the graph toolbar (not a standalone
    # line); with the prose collapsed by default, the visible order is the toolbar
    # (toggle) directly above the graph. The prose (#investigation-intro) is a
    # separate block, hidden by the collapse CSS.
    assert 'id="inv-overview-toggle"' in TPL
    assert "_toggleInvOverview()" in TPL
    # The toggle now lives in the investigation HEADER bar (above the graph), not
    # in the graph toolbar, and the overview is OPEN by default on entry.
    assert TPL.index('id="inv-overview-toggle"') < TPL.index('id="investigation-dag-fs"'), \
        "overview toggle should be in the header, above the graph"

    # CSS: collapsed hides the prose blocks (intro + decision cockpit).
    css = (ROOT / "vivarium_workbench/static/style.css").read_text(encoding="utf-8")
    assert "#investigation-detail-view.inv-overview-collapsed > #investigation-intro" in css
    assert "#investigation-detail-view.inv-overview-collapsed > #investigation-decision-cockpit" in css

    # JS: default collapsed (unset -> collapsed) + applied on investigation render
    # and on exit-to-graph.
    assert "function _invOverviewCollapsed" in JS
    assert "function _applyInvOverviewState" in JS
    oc = JS[JS.index("function _invOverviewCollapsed"): JS.index("function _applyInvOverviewState")]
    assert "v === null ? false" in oc           # default OPEN when unset (overview shown on entry)
    assert "inv-overview-collapsed" in oc

    def _fn_body(name):
        i = JS.index("function %s" % name)
        j = JS.find("\n  function ", i + 1)
        return JS[i:j] if j != -1 else JS[i:]
    # Landing graph-prominent: the detail render + the exit-to-graph reset both
    # apply the collapsed overview state.
    assert "_applyInvOverviewState()" in _fn_body("_renderInvestigationDetailInto")
    assert "_applyInvOverviewState()" in _fn_body("_wsResetStudyTabs")


def test_needs_attention_is_a_compact_toolbar_chip():
    """The all-clear state is a compact chip in the toolbar, not a big green
    banner; the needs state is a compact clickable chip that reveals the detail."""
    assert 'id="aig-needs-attention-chip"' in TPL
    i = JS.index("function _renderInvNeedsAttention")
    body = JS[i: JS.index("window._renderInvNeedsAttention", i)]
    # All-clear renders a small "✓ All clear" chip — not the old full-width green
    # "✓ Nothing needs attention" banner.
    assert "aig-na-clear" in body and "All clear" in body
    assert "Nothing needs attention" not in body     # old big all-clear banner gone
    # Needs state is a clickable chip that opens the detail list (in a popover —
    # the single surface; the old in-body banner is gone).
    assert "aig-na-flag" in body
    assert "_aigShowAllClearInfo" in body
    assert "needs-attention-banner" not in body      # no duplicate in-body banner
    css = (ROOT / "vivarium_workbench/static/style.css").read_text(encoding="utf-8")
    assert ".aig-na-chip" in css


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
    # Slice exactly the function body (definition → its window.* export) so this
    # stays correct as the function grows (e.g. resolving the display title).
    _start = JS.index("function _showInvestigationWorkspace")
    w = JS[_start: JS.index("window._showInvestigationWorkspace =", _start)]
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


def test_plan_board_renders_from_detail_payload():
    """The investigation overview renders the plan-progress board (cumulative
    gates A→E) from `d.plan_board`, compacted inside its own "Plan progress"
    brief tab (not an always-open board below the brief)."""
    assert "function _planBoardHtml" in JS
    i = JS.index("function _planBoardHtml")
    body = JS[i:JS.index("window._planBoardHtml", i)]
    assert "d.plan_board" in body                       # reads the derived board
    assert "inv-plan-gate" in body                      # one node per gate
    assert "inv-gate-status-" in body                   # colored live status
    assert "inv-plan-board-compact" in body             # compact variant for the tab
    # Honest status set: executed gates show their real verdict, incl. the
    # met-with-caveats (qualified) and tested-and-refuted (refuted) outcomes.
    for st in ("passed", "met", "qualified", "refuted", "progress", "blocked", "pending"):
        assert st in body                               # all states handled
    # Off-spine branch (e.g. a refuted side-question) marked distinctly.
    assert "off_spine" in body and "inv-gate-branch" in body
    # Mounted as a canonical brief tab (compact), not appended as a separate board.
    assert "id:'plan', label:'Plan progress'" in JS
    assert "_planBoardHtml(d, { compact: true })" in JS
