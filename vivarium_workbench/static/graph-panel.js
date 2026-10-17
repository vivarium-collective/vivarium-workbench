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

  // Re-measure + re-lay-out the graph at its current container width, then fit.
  function reflow() {
    try { if (typeof global._reflowInvestigationDag === 'function') global._reflowInvestigationDag(); } catch (e) { /* ignore */ }
    try { if (typeof global._aigFit === 'function') global._aigFit(); } catch (e) { /* ignore */ }
  }

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
    if (graphIntoPanel()) { showEmptyHint(false); setTimeout(reflow, 60); }
    else showEmptyHint(true);
  }

  function onClose() {
    graphToInline();
    // Back inline == the full-page graph; re-fit it in its wider home.
    setTimeout(function () { try { if (typeof global._aigFit === 'function') global._aigFit(); } catch (e) { /* ignore */ } }, 60);
  }

  function init() {
    var panel = el(PANEL_ID);
    var layout = document.querySelector('.viv-layout');
    var mainEl = document.querySelector('.viv-main');
    if (!panel || !layout || !mainEl || !global.VivPanelDock) return;

    ctl = global.VivPanelDock.make({
      panel: panel, layout: layout, mainEl: mainEl, key: 'graph',
      label: 'Graph',
      launcher: el('viv-graph-toggle'),
      resizeHandle: 'viv-graph-resize-handle',
      dragHandles: ['.viv-graph-head'],
      popout: function () { global.VivPanelDock.popout('graph'); },
      defaultDock: 'right', defaultSize: { side: 480, bottom: 340 },
      onOpen: onOpen, onClose: onClose,
      onResize: reflow,
      onDock: function () { setTimeout(reflow, 60); },
    });

    var tog = el('viv-graph-toggle');
    if (tog) {
      tog.addEventListener('click', function (ev) {
        ev.preventDefault();
        if (ctl.didDrag && ctl.didDrag()) return;   // a drag-to-dock isn't a toggle click
        ctl.toggle();
      });
    }

    // Public API — mirrors window.ProcessCode.* so the header buttons can call it.
    global.GraphPanel = {
      toggle: function () { if (ctl) ctl.toggle(); },
      open: function () { if (ctl) ctl.open(); },
      close: function () { if (ctl) ctl.close(); },
      dockMenu: function (anchor) { if (ctl) ctl.openDockMenu(anchor); },
      // "Full graph": close the panel so the graph returns inline (full-page view).
      toFull: function () { if (ctl) ctl.close(); },
      isOpen: function () { return !!(ctl && ctl.isOpen()); },
    };
    // Used by panel-dock.js initPopout when this page is a ?popout=graph window.
    global._openGraphPanel = function () { if (ctl) ctl.open(); };
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
