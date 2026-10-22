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
  var popoutWin = null;      // the ?popout=graph window opened from THIS (main) window
  var initDone = false;      // guards onClose→returnInline against the init-time close
  var popoutArmed = false;   // true once the popout's panel has settled open — only
                             // THEN does a close mean "user wants the graph back inline"
                             // (setup fires programmatic GraphPanel.close()s we must ignore)

  function el(id) { return document.getElementById(id); }
  function graphEl() { return el(GRAPH_ID); }
  function bodyEl() { return el(BODY_ID); }

  // True when THIS page is the popped-out graph window (?popout=graph).
  function inPopout() {
    try { return !!(document.body && document.body.classList.contains('viv-popout')); }
    catch (e) { return false; }
  }

  // Popout window → tell the opener (main window) to restore the inline graph,
  // then close this window. Mirrors the studies panel's returnInline.
  function returnInline() {
    try {
      if (window.opener && !window.opener.closed &&
          typeof window.opener._graphReturnInline === 'function') {
        window.opener._graphReturnInline();
      }
    } catch (e) { /* cross-origin / closed opener */ }
    try { window.close(); } catch (e) { /* ignore */ }
  }

  // --- Main-window side: while the graph is popped out it VACATES the inset; a
  // slim "popped out" bar takes its place, and closing the popout restores it. ---
  function poppedBar() {
    var g = graphEl(); if (!g || !g.parentNode) return null;
    var bar = el('aig-poppedout-bar');
    if (!bar) {
      bar = document.createElement('button');
      bar.id = 'aig-poppedout-bar';
      bar.type = 'button';
      bar.className = 'aig-poppedout-bar';
      bar.textContent = '⧉ Investigation graph is open in a pop-out window — click to bring it back';
      bar.addEventListener('click', bringBackGraph);
      g.parentNode.insertBefore(bar, g);
    }
    return bar;
  }
  function setMainPoppedOut(on) {
    var g = graphEl(); if (!g) return;
    if (on) { poppedBar(); g.classList.add('aig-poppedout-hidden'); }
    else {
      g.classList.remove('aig-poppedout-hidden');
      var bar = el('aig-poppedout-bar');
      if (bar && bar.parentNode) bar.parentNode.removeChild(bar);
    }
  }
  // Bring the graph back inline from the main window (also closes the popout).
  function bringBackGraph() {
    setMainPoppedOut(false);
    try { if (popoutWin && !popoutWin.closed) popoutWin.close(); } catch (e) { /* ignore */ }
    popoutWin = null;
  }

  // Fit on dock ENTER/EXIT (an initial-ish fit) but honor the user's zoom intent:
  // _aigFitOrKeep keeps the current zoom once the user has driven it, else fits.
  function fit() {
    try {
      if (typeof global._aigFitOrKeep === 'function') { global._aigFitOrKeep(); return; }
      if (typeof global._aigFit === 'function') global._aigFit();
    } catch (e) { /* ignore */ }
  }
  // Reflow the viewport at the CURRENT zoom — used on edge-RESIZE (drag the resize
  // grip) so resizing the panel never recomputes (changes) the zoom. This is the
  // headline "resize snaps to fit" fix; it must NOT re-fit.
  function reflow() {
    try {
      if (typeof global._aigReflowKeepZoom === 'function') { global._aigReflowKeepZoom(); return; }
      if (typeof global._aigFit === 'function') global._aigFit();
    } catch (e) { /* ignore */ }
  }
  // Fill the docked panel cleanly — zoom the graph to the panel's constrained
  // dimension (top/bottom band → fill height; left/right column → fill width) so
  // it doesn't sit small in a mostly-empty box. Used on dock ENTER / dock-position
  // SWITCH / expand (a fresh geometry), honoring the user's zoom intent lever.
  function dockFill() {
    try {
      if (typeof global._aigFitDockPanel === 'function') { global._aigFitDockPanel(); return; }
      reflow();
    } catch (e) { /* ignore */ }
  }
  function render() { try { if (typeof global._reflowInvestigationDag === 'function') global._reflowInvestigationDag(); } catch (e) { /* ignore */ } }

  // A dock-position switch RE-AUTOS the orientation from the new dock's aspect:
  // _setGraphOrientationForDock clears any transient manual (⇄/⇅) override and
  // re-renders; the render derives the direction from the live dock side (left/right
  // → TB, top/bottom → LR). So the dock switch itself IS the "reset to auto".
  function orientForDock() {
    if (typeof global._setGraphOrientationForDock === 'function') {
      var d = (ctl && ctl.getDock) ? ctl.getDock() : '';
      var orient = (d === 'left' || d === 'right') ? 'TB' : 'LR';   // advisory; render recomputes
      try { global._setGraphOrientationForDock(orient); return; } catch (e) { /* fall through */ }
    }
    render();
  }

  // Re-auto the orientation for the current dock, then FILL the panel cleanly at a
  // sensible zoom. Used on dock-position SWITCH and on expand: the dock side may
  // flip the flow orientation, and we fill the new band/column so it isn't a small
  // graph in a mostly-empty box.
  function relayout() { orientForDock(); dockFill(); }

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
      // Dock ENTER: auto-orient for the dock side, then fill the panel cleanly
      // (dockFill is zoom-intent aware — it keeps the user's zoom if they set one).
      setTimeout(function () { orientForDock(); dockFill(); }, 60);
    } else {
      showEmptyHint(true);
    }
  }

  function onClose() {
    // In the popped-out window, SETUP fires programmatic GraphPanel.close()s
    // (page-switch / _showInvestigationWorkspace) before the user has touched
    // anything. Those must NOT hide the graph or close the window — keep the panel
    // open until the user actually interacts (popoutArmed, set on first input).
    if (inPopout() && !popoutArmed) {
      setTimeout(function () { if (ctl && !ctl.isOpen()) ctl.open(); }, 0);
      return;   // bounce back open; don't move the graph inline
    }
    graphToInline();
    // A USER close inside the popped-out window returns the graph inline in the
    // opener and closes this window.
    if (initDone && inPopout()) { returnInline(); return; }
    // Back inline == the full-page graph. Clearing the manual override re-renders,
    // and the inline render re-autos the orientation (wide → LR) and runs its own
    // height auto-fill — so no explicit fit here (a fit-both would shrink a wide
    // inline LR graph). Fall back to a fit only if the reset helper is absent.
    setTimeout(function () {
      if (typeof global._resetGraphOrientation === 'function') {
        try { global._resetGraphOrientation(); return; } catch (e) { /* fall through */ }
      }
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
      onOpen: onOpen,
      resizeHandle: 'viv-graph-resize-handle',
      // No header to drag; the hosted graph toolbar is bound as the drag handle
      // in onOpen (once it's relocated into the panel) via ctl.addDragHandle.
      dragHandles: [],
      // Carry the current investigation so the popped window renders ITS graph
      // (a fresh popout has no investigation open, so the hosted #investigation-dag-fs
      // would otherwise be empty — see panel-dock.js initPopout). The inline graph
      // then VACATES the inset (a slim bar takes its place) until the popout closes.
      popout: function () {
        var inv = global._wsInvestigation || global._currentIset || '';
        if (ctl && ctl.isOpen()) ctl.close();   // if docked, return the graph inline first
        popoutWin = global.VivPanelDock.popout('graph', inv ? { investigation: inv } : null);
        setTimeout(function () { setMainPoppedOut(true); }, 90);
      },
      defaultDock: 'right', defaultSize: { side: 480, bottom: 340 },
      onClose: onClose,
      // Resize / re-dock / expand must NOT recompute the zoom — reflow the viewport
      // at the user's current zoom instead (relayout/reflow keep the scale).
      onResize: reflow,
      onDock: function () { setTimeout(relayout, 60); },
      onCollapse: function (c) { if (!c) setTimeout(relayout, 60); },   // re-auto + fill on expand
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
    // Called by the popped-out window (via opener) when it closes: restore the
    // inline graph in the inset. Also exposed so other code can bring it back.
    global._graphReturnInline = function () { setMainPoppedOut(false); popoutWin = null; };
    global._graphBringBack = bringBackGraph;
    // If the user closes the popout window directly (OS chrome), restore the inset.
    global.setInterval(function () {
      if (popoutWin && popoutWin.closed) { setMainPoppedOut(false); popoutWin = null; }
    }, 1500);

    // Default to INLINE on load: the graph lives inline below the investigation
    // overview, and the panel is opt-in (◧). Don't auto-restore a persisted
    // open/docked state — except in a ?popout=graph window, which must open it.
    var isPopout = false;
    try { isPopout = /(?:^|[?&])popout=graph(?:&|$)/.test(global.location.search || ''); } catch (e) { /* ignore */ }
    if (!isPopout && ctl.isOpen()) ctl.close();

    // In the popout, "arm" returnInline on the FIRST real user interaction — setup
    // (page render, _showInvestigationWorkspace) is all programmatic and never fires
    // input events, so this cleanly tells a user-close apart from a setup-close
    // regardless of how long the async graph render takes.
    if (isPopout) {
      var arm = function () {
        popoutArmed = true;
        window.removeEventListener('pointerdown', arm, true);
        window.removeEventListener('keydown', arm, true);
      };
      window.addEventListener('pointerdown', arm, true);
      window.addEventListener('keydown', arm, true);
    }

    initDone = true;   // from here, a close inside a popout returns the graph inline
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
