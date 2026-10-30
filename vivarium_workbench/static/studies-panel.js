// studies-panel.js — a dockable "Studies" panel built on VivPanelDock, the same
// engine the Chat (#viv-ai-panel), Code (#viv-code-rail) and Graph
// (#viv-graph-panel) panels use.
//
// Rather than live permanently in the left rail, the per-investigation study
// browser (#viv-rail-studies-section — its search field + the dependency-ordered
// study tree with select / reorder / browse) is RELOCATED into #viv-studies-body
// and shown through this panel. So the study browser gains docking (left / right
// / bottom / top), edge-resize, drag-to-re-dock and pop-out for free, and can be
// turned on/off from the rail's "Studies" tab — separate from the "Investigations"
// overview page.
//
// Default state: the panel opens automatically when the workspace has any studies
// (so the browser is there when it's useful), until the user expresses an explicit
// intent by toggling it — after which their choice is remembered.
(function (global) {
  'use strict';

  var PANEL_ID = 'viv-studies-panel';
  var BODY_ID = 'viv-studies-body';
  var SECTION_ID = 'viv-rail-studies-section';   // the hosted study-browser section
  var TOGGLE_ID = 'viv-studies-toggle';          // the rail launcher tab
  var LIST_ID = 'viv-rail-investigations';       // where study leaves render
  var K_USERSET = 'viv.studies.userset';         // '1' once the user toggles it

  var ctl = null;
  var popoutWin = null;   // the ?popout=studies window, while one is open
  var initDone = false;   // guards returnInline against init-time close events

  function el(id) { return document.getElementById(id); }

  // The rail "Studies" tab stays highlighted while the panel is open OR popped
  // out into its own window (VivPanelDock only reflects the docked-open state).
  function reflectLauncher() {
    var tog = el(TOGGLE_ID);
    if (!tog) return;
    var active = (ctl && ctl.isOpen()) || !!(popoutWin && !popoutWin.closed);
    tog.classList.toggle('active', active);
    tog.setAttribute('aria-pressed', active ? 'true' : 'false');
  }
  function lsGet(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }

  // Move the study-browser section into the panel body (it starts life in the
  // rail markup). Idempotent. Reveal the study search field, which is hidden by
  // default in the rail (toggled by a magnifier that doesn't exist in the panel).
  function sectionIntoPanel() {
    var s = el(SECTION_ID), b = el(BODY_ID);
    if (!s || !b) return false;
    if (s.parentNode !== b) b.appendChild(s);
    s.hidden = false;
    var head = el('viv-rail-studies-head');
    if (head) head.hidden = false;
    return true;
  }

  function hasStudies() {
    var host = el(LIST_ID);
    // Study leaves are rendered as `a[data-study-name]` (the .viv-rail-sublink
    // rows under each investigation group).
    return !!(host && host.querySelector('[data-study-name], .viv-rail-sublink'));
  }

  function userToggled() { lsSet(K_USERSET, '1'); }

  // True when THIS page is the popped-out Studies window (?popout=studies).
  function inPopout() {
    try { return !!(document.body && document.body.classList.contains('viv-popout')); }
    catch (e) { return false; }
  }
  // Close the popped-out window and bring the panel back to inline docking in the
  // opener (main) window.
  function returnInline() {
    try {
      if (window.opener && !window.opener.closed &&
          typeof window.opener._openStudiesPanel === 'function') {
        window.opener._openStudiesPanel();
      }
    } catch (e) { /* cross-origin / closed opener */ }
    try { window.close(); } catch (e) { /* ignore */ }
  }

  // Open iff the workspace has studies — but only while the user hasn't taken
  // explicit control of the panel. The study list loads asynchronously, so this
  // is re-run as the rail renders (see the MutationObserver in init).
  // On a phone the Studies panel opens as a full-screen sheet (responsive.css),
  // so auto-opening it on load would bury the Investigations page behind it.
  // Leave it closed by default there — the rail's Studies tab still opens it.
  function isMobile() {
    try { return window.matchMedia && window.matchMedia('(max-width:640px)').matches; }
    catch (e) { return false; }
  }

  function applyDefault() {
    // Never auto-open/close in a popped-out window — it IS the panel, and a
    // transient close there would trigger returnInline (closing the window).
    if (!ctl || inPopout() || isMobile() || lsGet(K_USERSET, '0') === '1') return;
    if (hasStudies()) { if (!ctl.isOpen()) ctl.open(); }
    else { if (ctl.isOpen()) ctl.close(); }
    // Keep the rail "Studies" launcher highlight in sync even when the panel was
    // ALREADY open (restored from stored state at init) — in that case ctl.open()
    // isn't called, so onOpen/reflectLauncher never fires and the tab would stay
    // un-highlighted until the user toggled it off/on.
    reflectLauncher();
  }

  function init() {
    var panel = el(PANEL_ID);
    var layout = document.querySelector('.viv-layout');
    var mainEl = document.querySelector('.viv-main');
    if (!panel || !layout || !mainEl || !global.VivPanelDock) return;

    sectionIntoPanel();
    var head = panel.querySelector('.viv-studies-head');

    ctl = global.VivPanelDock.make({
      panel: panel, layout: layout, mainEl: mainEl, key: 'studies',
      label: 'Studies',
      launcher: el(TOGGLE_ID),
      docks: ['left', 'right', 'bottom', 'top'],
      resizeHandle: 'viv-studies-resize-handle',
      dragHandles: head ? [head] : [],
      // Pop out into a window; the docked panel then closes (it lives in the
      // window now), but the rail tab stays highlighted while that window is open.
      popout: function () {
        popoutWin = global.VivPanelDock.popout('studies');
        userToggled();
        if (ctl) ctl.close();
        setTimeout(reflectLauncher, 60);
      },
      defaultDock: 'left', defaultSize: { side: 300, bottom: 280 },
      onOpen: function () { sectionIntoPanel(); reflectLauncher(); },
      // A USER-initiated close inside the popped-out window returns it to inline
      // docking in the opener and closes this window. (Guarded against the
      // init-time setOpen(false) that make() performs from stored state.)
      onClose: function () { reflectLauncher(); if (initDone && inPopout()) returnInline(); },
    });

    // Public API — mirrors window.GraphPanel.* / window.ProcessCode.* so the panel
    // header buttons and the rail tab can call it. The user-facing entry points
    // record an explicit intent so applyDefault() stops overriding them.
    global.StudiesPanel = {
      toggle: function () { userToggled(); if (ctl) ctl.toggle(); },
      open: function () { userToggled(); if (ctl) ctl.open(); },
      close: function () { userToggled(); if (ctl) ctl.close(); },
      dockMenu: function (anchor) { if (ctl) ctl.openDockMenu(anchor); },
      collapse: function () { if (ctl) ctl.collapse(); },
      expand: function () { if (ctl) ctl.expand(); },
      toggleCollapsed: function () { if (ctl) ctl.toggleCollapsed(); },
      isOpen: function () { return !!(ctl && ctl.isOpen()); },
    };
    // Used by panel-dock.js initPopout when this page is a ?popout=studies window.
    global._openStudiesPanel = function () { if (ctl) ctl.open(); };

    // The rail "Studies" tab toggles the panel (VivPanelDock does not bind the
    // launcher itself — each panel owns its click, like process-code.js). Record
    // the explicit intent so the default-open heuristic yields to the user.
    var tog = el(TOGGLE_ID);
    if (tog) tog.addEventListener('click', function (ev) {
      ev.preventDefault();
      if (ctl.didDrag && ctl.didDrag()) return;   // a drag-to-dock isn't a toggle
      userToggled();
      ctl.toggle();
    });

    // Mount the always-visible study stepper as soon as the list renders, and
    // (until the user takes control) keep the default-open heuristic fresh. The
    // study list loads asynchronously, so run on every rail re-render.
    function onListRender() {
      if (typeof global._renderRailStudyStepper === 'function') global._renderRailStudyStepper();
      if (lsGet(K_USERSET, '0') !== '1') applyDefault();
    }

    // A ?popout=studies window always opens the panel (and still mounts the stepper).
    var isPopout = false;
    try { isPopout = /(?:^|[?&])popout=studies(?:&|$)/.test(global.location.search || ''); } catch (e) { /* ignore */ }
    if (isPopout) { ctl.open(); onListRender(); }

    onListRender();
    var host = el(LIST_ID);
    if (host && global.MutationObserver) {
      new global.MutationObserver(onListRender).observe(host, { childList: true, subtree: true });
    }
    // Reflect the launcher highlight for the panel's INITIAL open state — whether
    // it was restored open from stored dock state or default-opened. applyDefault
    // returns early when the user has toggled before (userset='1'), so it can't be
    // relied on to sync the highlight at startup; do it unconditionally here (and
    // once more after the dock state settles asynchronously).
    reflectLauncher();
    setTimeout(reflectLauncher, 300);

    // Deactivate the rail tab when the popped-out window is closed.
    global.setInterval(function () {
      if (popoutWin && popoutWin.closed) { popoutWin = null; reflectLauncher(); }
    }, 1500);

    initDone = true;   // from here, a close in a popout returns the panel inline
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
