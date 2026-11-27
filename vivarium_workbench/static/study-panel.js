// study-panel.js — a dockable Study-detail panel built on VivPanelDock.
//
// Double-clicking a graph node (or a study card / ref link) opens the
// /studies/<name> route in THIS panel's iframe, as a free-floating, redockable
// window (docks left/right/bottom/top, resizes, pops out) that is INDEPENDENT of
// the investigation graph above it — you can move it, redock it, or collapse it
// without disturbing the graph. One reusable panel: each open retargets its iframe.
//
// Entry point: window.StudyPanel.open(name, tab). _openStudyInsideInvestigation
// (walkthrough.js) routes graph-node opens here.
(function (global) {
  'use strict';
  var PANEL_ID = 'viv-study-panel';
  var ctl = null;
  var current = '';

  function el(id) { return document.getElementById(id); }

  function href(name, tab) {
    var h = global._studyHref ? global._studyHref(name) : ('/studies/' + encodeURIComponent(name));
    if (tab) h += (h.indexOf('#') >= 0 ? '' : '#') + tab;
    return h;
  }

  function popoutURL() { return current ? href(current) : ''; }
  function detach(url) {
    if (!url) return;
    if (global._openDetachedWindow) global._openDetachedWindow(url, 1200, 800);
    else global.open(url, '_blank');
  }

  // When this Study window is docked at the BOTTOM (directly below the inline
  // investigation graph), its top resize handle is the single divider between the
  // graph and the study. Dragging it UP grows the study AND collapses the graph to
  // its slim bar once the study fills most of the viewport; dragging it back DOWN
  // reveals the graph again. Hysteresis (66% / 52%) avoids flicker at the boundary.
  var _ctl = null;
  function _coordinateGraph() {
    var fs = document.getElementById('investigation-dag-fs');
    var panel = el(PANEL_ID);
    if (!fs || !panel || !_ctl || !_ctl.isOpen() || _ctl.getDock() !== 'bottom') return;
    if (fs.classList.contains('aig-dock') || fs.classList.contains('aig-poppedout-hidden')) return;
    var vh = global.innerHeight || 900;
    var frac = panel.getBoundingClientRect().height / vh;
    var collapsed = fs.classList.contains('aig-collapsed');
    if (!collapsed && frac > 0.66) {
      if (typeof global._aigCollapseInline === 'function') global._aigCollapseInline();
    } else if (collapsed && frac < 0.52) {
      if (typeof global._aigExpandInline === 'function') global._aigExpandInline();
    }
  }

  function setup() {
    var panel = el(PANEL_ID);
    var layout = document.querySelector('.viv-layout');
    var mainEl = document.querySelector('.viv-main');
    if (!panel || !layout || !mainEl || !global.VivPanelDock) return;
    var head = panel.querySelector('.viv-study-head');

    ctl = global.VivPanelDock.make({
      panel: panel, layout: layout, mainEl: mainEl, key: 'study',
      label: 'Study', launcher: null,
      docks: ['left', 'right', 'bottom', 'top'],
      dragHandles: head ? [head] : [],
      resizeHandle: 'viv-study-resize-handle',
      defaultDock: 'bottom', defaultSize: { side: 520, bottom: 440 },
      // The dock menu's "pop out" opens the study in a detached browser window.
      popout: function () { detach(popoutURL()); },
      // Bottom-docked: dragging this window's top divider up/down also collapses /
      // reveals the investigation graph above it (one unified divider).
      onResize: function () { _coordinateGraph(); },
      onDock: function () { _coordinateGraph(); },
      // Closing the Study window clears the graph's "open" marker.
      onClose: function () {
        current = '';
        try {
          if (global._wsStudyTabs) global._wsStudyTabs.active = null;
          if (typeof global._wsHighlightActiveDagNode === 'function') global._wsHighlightActiveDagNode(null);
        } catch (e) { /* no graph present — ignore */ }
      },
    });
    _ctl = ctl;

    global.StudyPanel = {
      // Open a study in the panel, retargeting the iframe. When no explicit tab is
      // given, PRESERVE the currently-open study tab (Overview / Visualizations /
      // Model / Simulations / Audit / Conclusions) so clicking a different study in
      // the Studies browser shows the SAME view. The study detail reports its active
      // tab to the parent via postMessage (tracked in window._wsStudyTabs.tab); we
      // re-apply it on the new study's load through the same-origin _setStudyTab.
      open: function (name, tab) {
        if (!name || !ctl) return;
        var frame = el('viv-study-frame'), nameEl = el('viv-study-name');
        // Preserve the currently-open tab across study switches. Read it straight
        // from the live iframe's active pillar (most reliable), falling back to the
        // last tab the study detail reported via postMessage.
        if (!tab) {
          try {
            var cw = frame && frame.contentWindow;
            var act = cw && cw.document && cw.document.querySelector('.study-pillar.active');
            if (act && act.dataset && act.dataset.kind) tab = act.dataset.kind;
          } catch (e) { /* cross-frame / not ready */ }
          if (!tab) { try { tab = (global._wsStudyTabs && global._wsStudyTabs.tab) || ''; } catch (e) { /* ignore */ } }
        }
        var sameStudy = (current === name);
        current = name;
        if (nameEl) nameEl.textContent = name;
        // Mark this study's node as "open" in the investigation graph — restores the
        // open-marker the old inline-open flow (_wsOpenStudyTab) set. Keep
        // _wsStudyTabs.active in sync so the highlight survives graph re-renders
        // (walkthrough.js re-applies it from .active on every re-render).
        try {
          global._wsStudyTabs = global._wsStudyTabs || {};
          global._wsStudyTabs.active = name;
          if (typeof global._wsHighlightActiveDagNode === 'function') global._wsHighlightActiveDagNode(name);
        } catch (e) { /* no investigation graph present (e.g. snapshot) — ignore */ }
        // The study page's own init reads ?tab=<kind> (study-detail.js _runStudyInit),
        // so carry the tab as a query param — it opens on that tab with no load race.
        var base = href(name);
        var url = tab ? base + (base.indexOf('?') >= 0 ? '&' : '?') + 'tab=' + encodeURIComponent(tab) : base;
        if (frame && !sameStudy) {
          frame.src = url;
        } else if (frame && tab) {
          // Same study already loaded — just switch its tab, no reload.
          try {
            var w = frame.contentWindow;
            if (w && typeof w._setStudyTab === 'function') w._setStudyTab(tab);
          } catch (e) { /* ignore */ }
        }
        if (!ctl.isOpen()) ctl.open();
        ctl.expand();
      },
      openTab: function () { detach(popoutURL()); },
      dockMenu: function (anchor) { if (ctl) ctl.openDockMenu(anchor); },
      close: function () { if (ctl) ctl.close(); },
      toggle: function () { if (ctl) ctl.toggle(); },
      expand: function () { if (ctl) ctl.expand(); },
      isOpen: function () { return !!(ctl && ctl.isOpen()); },
      current: function () { return current; },
    };
    // Used by panel-dock.js initPopout if this page is ever a ?popout=study window.
    global._openStudyPanel = function () { if (ctl) ctl.open(); };

    // Drag-to-restore on the collapsed strip (click already restores via onclick).
    (function bindCollapsedDrag() {
      var btn = panel.querySelector('.viv-study-expand');
      if (!btn || btn._dragWired) return;
      btn._dragWired = true;
      btn.addEventListener('pointerdown', function (ev) {
        if (!ctl || !panel.classList.contains('vp-collapsed')) return;
        var sx = ev.clientX, sy = ev.clientY, moved = false;
        function mv(e) {
          if (!moved && (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy)) > 5) {
            moved = true; ctl.expand();
          }
        }
        function up() {
          document.removeEventListener('pointermove', mv);
          document.removeEventListener('pointerup', up);
        }
        document.addEventListener('pointermove', mv);
        document.addEventListener('pointerup', up);
      });
    })();

    // Start closed (empty): the panel opens on the first study-open, not on load.
    if (ctl.isOpen()) ctl.close();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup);
  else setup();
})(window);
