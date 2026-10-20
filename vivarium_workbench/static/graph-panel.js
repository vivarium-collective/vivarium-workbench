// graph-panel.js — a dockable investigation-graph panel built on VivPanelDock,
// the same engine the Chat (#viv-ai-panel) and Code (#viv-code-rail) panels use.
//
// Rather than re-implement the graph, this panel HOSTS the existing investigation
// graph: #investigation-dag-fs (its toolbar + shell, rendered by walkthrough.js's
// _renderInvestigationDag) is RELOCATED into #viv-graph-body while the panel is
// open, and moved back to its inline home for the full-page view. So the panel
// gains docking (left / right / bottom / top), edge-resize, drag-to-re-dock and
// pop-out for free, and the graph itself is unchanged.
(function (global) {
  'use strict';

  var PANEL_ID = 'viv-graph-panel';
  var BODY_ID = 'viv-graph-body';
  var GRAPH_ID = 'investigation-dag-fs';   // the toolbar + shell wrapper

  var ctl = null;            // the VivPanelDock controller
  var placeholder = null;    // marks the graph's inline home while it lives in the panel

  function el(id) { return document.getElementById(id); }
  function graphEl() { return el(GRAPH_ID); }
  function bodyEl() { return el(BODY_ID); }

  // Fit on dock ENTER/EXIT (an initial-ish fit) but honor the user's zoom intent:
  // _aigFitOrKeep keeps the current zoom once the user has driven it, else fits.
  function fit() {
    try {
      if (typeof global._aigFitOrKeep === 'function') { global._aigFitOrKeep(); return; }
      if (typeof global._aigFit === 'function') global._aigFit();
    } catch (e) { /* ignore */ }
  }
  // Reflow the viewport at the CURRENT zoom — used on panel RESIZE / RE-DOCK /
  // expand so relocating or resizing the panel never recomputes (changes) the
  // zoom. Falls back to a plain re-render when the helper isn't present.
  function reflow() {
    try {
      if (typeof global._aigReflowKeepZoom === 'function') { global._aigReflowKeepZoom(); return; }
      if (typeof global._aigFit === 'function') global._aigFit();
    } catch (e) { /* ignore */ }
  }
  function render() { try { if (typeof global._reflowInvestigationDag === 'function') global._reflowInvestigationDag(); } catch (e) { /* ignore */ } }

  // Orientation follows the dock side: a LEFT/RIGHT (tall, narrow) panel reads best
  // with a VERTICAL flow (top→bottom), a TOP/BOTTOM (wide, short) panel with a
  // HORIZONTAL flow (left→right). _setGraphOrientation re-renders the graph.
  function orientForDock() {
    if (!ctl || !ctl.getDock) { render(); return; }
    var d = ctl.getDock();
    // Match the flow to the panel's aspect: a LEFT/RIGHT (tall, narrow) panel reads
    // best top→bottom (TB); a TOP/BOTTOM (wide, short) panel left→right (LR).
    var orient = (d === 'left' || d === 'right') ? 'TB' : 'LR';
    // Use the DOCK setter: it sets the DEFAULT direction (doesn't persist) and
    // steps aside once the user has picked a layout via the ⇄/⇅ toggle, so the
    // auto-switch is the default on dock change, not a lock.
    if (typeof global._setGraphOrientationForDock === 'function') {
      try { global._setGraphOrientationForDock(orient); return; } catch (e) { /* fall through */ }
    }
    render();
  }

  // Re-orient for the current dock and re-render, keeping the user's zoom. Used on
  // RE-DOCK and on expand: the dock side may flip the flow orientation (a re-render),
  // but the zoom scale is preserved — the viewport reflows, it does not re-fit.
  function relayout() { orientForDock(); reflow(); }

  function showEmptyHint(show) {
    var b = bodyEl(); if (!b) return;
    var hint = b.querySelector('.viv-graph-empty');
    if (show && !hint) {
      hint = document.createElement('div');
      hint.className = 'viv-graph-empty';
      hint.textContent = 'Open an investigation to see its graph here.';
      b.appendChild(hint);
    } else if (!show && hint) {
      hint.remove();
    }
  }

  // Move the graph into the panel body, leaving a hidden placeholder at its inline
  // home so it can be put back exactly where it was.
  function graphIntoPanel() {
    var g = graphEl(), b = bodyEl();
    if (!g || !b) return false;
    if (g.parentNode === b) return true;      // already hosted here
    if (!placeholder) {
      placeholder = document.createElement('div');
      placeholder.className = 'viv-graph-home';
      placeholder.style.display = 'none';
    }
    if (g.parentNode) g.parentNode.insertBefore(placeholder, g);
    b.appendChild(g);
    return true;
  }

  // Return the graph to its inline home (before the placeholder) for full-page view.
  function graphToInline() {
    var g = graphEl();
    if (g && placeholder && placeholder.parentNode) {
      placeholder.parentNode.insertBefore(g, placeholder);
      placeholder.parentNode.removeChild(placeholder);
    }
    placeholder = null;
  }

  function onOpen() {
    if (graphIntoPanel()) {
      showEmptyHint(false);
      // The graph's own toolbar is now the panel's single bar + drag-to-dock
      // handle (there is no separate header). Bind it once it's in the panel.
      var g = graphEl();
      var tb = g && g.querySelector('.aig-toolbar');
      if (tb && ctl && ctl.addDragHandle) ctl.addDragHandle(tb);
      // Dock ENTER: orient for the dock side, then fit the initial view (fit() is
      // zoom-intent aware — it keeps the user's zoom if they've set one).
      setTimeout(function () { orientForDock(); fit(); }, 60);
    } else {
      showEmptyHint(true);
    }
  }

  function onClose() {
    graphToInline();
    // Back inline == the full-page graph; restore auto orientation + re-fit it.
    setTimeout(function () {
      try { if (typeof global._resetGraphOrientation === 'function') global._resetGraphOrientation(); } catch (e) { /* ignore */ }
      fit();
    }, 60);
  }

  function init() {
    var panel = el(PANEL_ID);
    var layout = document.querySelector('.viv-layout');
    var mainEl = document.querySelector('.viv-main');
    if (!panel || !layout || !mainEl || !global.VivPanelDock) return;

    // No left-rail launcher: the panel is opened from the "◧ side panel" button on
    // the inline investigation-graph toolbar (which calls GraphPanel.open), and
    // closed from the panel header (× / ⤢ Full graph), which returns the graph
    // inline. So there is no rail tab for it.
    ctl = global.VivPanelDock.make({
      panel: panel, layout: layout, mainEl: mainEl, key: 'graph',
      label: 'Graph',
      launcher: null,
      // All four edges — the dock menu and the drag drop-zones both offer
      // left / right / bottom / top (unified). Top docks as a band (default height
      // from defaultSize.bottom); placeTop keeps it BELOW the investigation menu.
      docks: ['left', 'right', 'bottom', 'top'],
      // Top dock: insert the panel just BELOW the investigation menu bar (inside
      // #iset-workspace), so the "← All investigations / overview / report…" bar
      // always stays at the very top. Returns false (→ default top placement) when
      // no investigation workspace is on screen.
      placeTop: function (panel) {
        var ws = document.getElementById('iset-workspace');
        var header = ws && ws.querySelector('.inv-header-bar');
        if (header && header.parentNode) {
          header.parentNode.insertBefore(panel, header.nextSibling);
          return true;
        }
        return false;
      },
      resizeHandle: 'viv-graph-resize-handle',
      // No header to drag; the hosted graph toolbar is bound as the drag handle
      // in onOpen (once it's relocated into the panel) via ctl.addDragHandle.
      dragHandles: [],
      popout: function () { global.VivPanelDock.popout('graph'); },
      defaultDock: 'right', defaultSize: { side: 480, bottom: 340 },
      onOpen: onOpen, onClose: onClose,
      // Resize / re-dock / expand must NOT recompute the zoom — reflow the viewport
      // at the user's current zoom instead (relayout/reflow keep the scale).
      onResize: reflow,
      onDock: function () { setTimeout(relayout, 60); },
      onCollapse: function (c) { if (!c) setTimeout(relayout, 60); },   // reflow on expand
    });

    // Public API — mirrors window.ProcessCode.* so the toolbar buttons can call it.
    global.GraphPanel = {
      toggle: function () { if (ctl) ctl.toggle(); },
      open: function () { if (ctl) ctl.open(); },
      close: function () { if (ctl) ctl.close(); },
      dockMenu: function (anchor) { if (ctl) ctl.openDockMenu(anchor); },
      // Collapse the panel to a thin edge strip / expand it back.
      collapse: function () { if (ctl) ctl.collapse(); },
      expand: function () { if (ctl) ctl.expand(); },
      toggleCollapsed: function () { if (ctl) ctl.toggleCollapsed(); },
      isOpen: function () { return !!(ctl && ctl.isOpen()); },
    };
    // Used by panel-dock.js initPopout when this page is a ?popout=graph window.
    global._openGraphPanel = function () { if (ctl) ctl.open(); };

    // Default to INLINE on load: the graph lives inline below the investigation
    // overview, and the panel is opt-in (◧). Don't auto-restore a persisted
    // open/docked state — except in a ?popout=graph window, which must open it.
    var isPopout = false;
    try { isPopout = /(?:^|[?&])popout=graph(?:&|$)/.test(global.location.search || ''); } catch (e) { /* ignore */ }
    if (!isPopout && ctl.isOpen()) ctl.close();
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
