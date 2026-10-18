// panel-dock.js — shared "dockable tool panel" behavior for the chat and process-code
// panels. PyCharm-style: a panel docks left / right / bottom, is drag-re-dockable by a
// handle, is edge-resizable, and publishes its footprint as CSS vars on <html> so
// fixed / viewport-height layouts leave room for it. Pure DOM glue over VivChatCore's
// dock math (DOCKS / validDock / dropZone / clampDock) — no chat- or code-specific logic
// lives here; each consumer passes its own labels, keys and open/close callbacks.
//
// Both #viv-ai-panel (chat.js) and #viv-code-rail (process-code.js) are built with this,
// so they gain identical docking, independently: chat can live on the right while code is
// on the bottom, both open at once. Launchers live on the left nav rail; closing a panel
// removes it from the flex flow and lights its rail tab off.
(function (global) {
  'use strict';
  var C = global.VivChatCore;                       // dock math; chat-core.js loads first

  function lsGet(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }

  // One-time reset to the current panel defaults: BOTH the chat and the process-code
  // panel start docked RIGHT and CLOSED. Browsers that used earlier builds remember a
  // left/open state that otherwise sticks (the default only applies when nothing is
  // stored), so clear those four keys ONCE — then a flag lets later manual dock/open
  // choices persist. Runs at load, before either panel reads its stored state
  // (panel-dock.js loads before process-code.js and chat.js).
  try {
    if (localStorage.getItem('viv.panels.reset.v1') == null) {
      ['viv.ai.dock', 'viv.ai.open', 'viv.code.dock', 'viv.code.open'].forEach(function (k) {
        localStorage.removeItem(k);
      });
      localStorage.setItem('viv.panels.reset.v1', '1');
    }
  } catch (e) { /* private mode — the right/closed defaults already apply */ }

  function svg(inner) {
    return '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + inner + '</svg>';
  }
  // Dock glyphs (a framed window with the panel edge highlighted) — the same marks the
  // chat's own dock menu uses, so both dock menus read identically.
  var DOCK_GLYPH = {
    left:   svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>'),
    right:  svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>'),
    bottom: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 14h18"/>'),
    top:    svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18"/>'),
  };
  // Pop-out shares the dock dropdown (one "move this panel" menu) instead of a
  // separate header button.
  var POPOUT_GLYPH = svg('<path d="M14 3h7v7"/><path d="M10 14L21 3"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/>');

  // A single shared dock dropdown (only one open at a time). Uses the chat's .vp-pop /
  // .vp-pop-item chrome so it matches the rest of the panel UI.
  var openMenu = null;
  function closeMenu() {
    if (!openMenu) return;
    openMenu.remove(); openMenu = null;
    document.removeEventListener('mousedown', _onDoc, true);
    document.removeEventListener('keydown', _onKey, true);
  }
  function _onDoc(ev) { if (openMenu && !openMenu.contains(ev.target)) closeMenu(); }
  function _onKey(ev) { if (ev.key === 'Escape') closeMenu(); }

  // opts: {
  //   panel, layout, mainEl,            required DOM nodes (.viv-layout + .viv-main)
  //   key,                              'ai' | 'code' — namespaces storage + CSS vars
  //   label, ghostIcon,                 drag-ghost text + optional leading SVG
  //   launcher,                         the left-rail <a> that toggles this panel
  //   resizeHandle,                     id or element of the edge resize grip
  //   dragHandles,                      [selector|element] that start a drag-to-dock
  //   defaultDock, defaultSize:{side,bottom},
  //   layoutEvent,                      extra window event to fire on footprint change
  //   onOpen, onClose, onDock, onResize callbacks
  // }
  function make(opts) {
    var panel = opts.panel, layout = opts.layout, mainEl = opts.mainEl;
    var key = opts.key;
    var V = function (suffix) { return '--viv-' + key + '-' + suffix; };
    var K = { dock: 'viv.' + key + '.dock', open: 'viv.' + key + '.open', w: 'viv.' + key + '.w', h: 'viv.' + key + '.h', collapsed: 'viv.' + key + '.collapsed' };
    // A panel may restrict which edges it docks to (opts.docks). The investigation
    // graph forbids 'top' so it can never push the investigation header off-screen.
    var ALLOWED = (opts.docks && opts.docks.length) ? opts.docks.filter(C.validDock) : C.DOCKS.slice();
    function isAllowed(z) { return C.validDock(z) && ALLOWED.indexOf(z) >= 0; }
    var def = isAllowed(opts.defaultDock) ? opts.defaultDock : (isAllowed('right') ? 'right' : ALLOWED[0]);
    var dock = isAllowed(lsGet(K.dock, def)) ? lsGet(K.dock, def) : def;
    var justDragged = false;
    var collapsed = lsGet(K.collapsed, '0') === '1';
    var COLLAPSE_AT = 48;   // drag the resize edge below this (px) to collapse

    // Collapse the panel to a thin edge strip (body hidden, a small expand handle
    // shown via CSS .vp-collapsed); expand restores it. State is persisted.
    function setCollapsed(c, persist) {
      collapsed = !!c;
      panel.classList.toggle('vp-collapsed', collapsed);
      if (persist) lsSet(K.collapsed, collapsed ? '1' : '0');
      syncVars();
      if (opts.onCollapse) opts.onCollapse(collapsed);
    }

    function isVertical(d) { return d === 'bottom' || d === 'top'; }   // height-sized docks
    function sizeKey(d) { return isVertical(d) ? K.h : K.w; }
    function applySize() {
      var n = parseInt(lsGet(sizeKey(dock), ''), 10);
      var fallback = isVertical(dock)
        ? ((opts.defaultSize && opts.defaultSize.bottom) || 320)
        : ((opts.defaultSize && opts.defaultSize.side) || 440);
      var size = C.clampDock(dock, n || fallback, innerWidth, innerHeight);
      panel.style.setProperty(isVertical(dock) ? V('h') : V('w'), size + 'px');
    }
    // Placement in the flex row: left → before <main>, right → end of the row, bottom →
    // inside <main>'s column below the content, top → inside <main> above the content.
    // Two right-docked panels stack by order.
    function place(zone) {
      if (!layout || !mainEl) return;
      panel.dataset.dock = zone;
      if (zone === 'left') layout.insertBefore(panel, mainEl);
      else if (zone === 'right') layout.appendChild(panel);
      else if (zone === 'top') {
        // opts.placeTop lets a panel keep something (e.g. the investigation menu
        // bar) ABOVE it when docked top. It returns true if it placed the panel;
        // otherwise fall back to the default (above all of <main>'s content).
        if (!(opts.placeTop && opts.placeTop(panel))) mainEl.insertBefore(panel, mainEl.firstChild);
      }
      else mainEl.appendChild(panel);                                  // bottom
      applySize();
    }
    function isOpen() { return !panel.hidden; }
    // Publish the panel's footprint on <html> so fixed / 100vh layouts leave room for it.
    function syncVars() {
      var on = isOpen();
      var r = panel.getBoundingClientRect();
      var root = document.documentElement.style;
      root.setProperty(V('left'),   on && dock === 'left'   ? Math.round(r.width) + 'px' : '0px');
      root.setProperty(V('right'),  on && dock === 'right'  ? Math.round(innerWidth - r.left) + 'px' : '0px');
      root.setProperty(V('rw'),     on && dock === 'right'  ? Math.round(r.width) + 'px' : '0px');
      root.setProperty(V('bottom'), on && dock === 'bottom' ? Math.round(r.height) + 'px' : '0px');
      root.setProperty(V('top'),    on && dock === 'top'    ? Math.round(r.height) + 'px' : '0px');
      window.dispatchEvent(new CustomEvent('viv:panel-layout', { detail: { key: key } }));
      if (opts.layoutEvent) window.dispatchEvent(new CustomEvent(opts.layoutEvent));
    }
    function setOpen(open) {
      panel.hidden = !open;
      if (open) place(dock);
      panel.classList.toggle('vp-collapsed', open && collapsed);
      document.body.classList.toggle('viv-' + key + '-open', open);
      lsSet(K.open, open ? '1' : '0');
      if (opts.launcher) {
        opts.launcher.classList.toggle('active', open);
        opts.launcher.setAttribute('aria-pressed', open ? 'true' : 'false');
      }
      syncVars();
      if (open && opts.onOpen) opts.onOpen();
      if (!open && opts.onClose) opts.onClose();
    }
    function dockTo(zone, persist) {
      if (!isAllowed(zone)) return;
      dock = zone;
      if (persist) lsSet(K.dock, zone);
      if (isOpen()) { place(zone); syncVars(); }
      if (opts.onDock) opts.onDock(zone);
    }
    // Dropdown of Dock left / right / bottom, anchored under the header's dock button.
    function openDockMenu(anchor) {
      closeMenu();
      var menu = document.createElement('div');
      menu.className = 'vp-pop vp-dock-menu';
      menu.setAttribute('role', 'menu');
      var html = ALLOWED.map(function (z) {
        return '<button type="button" role="menuitemradio" aria-checked="' + (dock === z) + '" ' +
          'class="vp-pop-item' + (dock === z ? ' on' : '') + '" data-zone="' + z + '">' +
          DOCK_GLYPH[z] + '<span>Dock ' + z + '</span></button>';
      }).join('');
      // Pop-out + (when open) a "close / back inline" action, so this single
      // button is the one control for the whole panel: pick a side, pop out, or
      // close it back to the inline view.
      var extras = '';
      if (typeof opts.popout === 'function') {
        extras += '<button type="button" role="menuitem" class="vp-pop-item" data-act="popout">' +
          POPOUT_GLYPH + '<span>Pop out</span></button>';
      }
      if (isOpen()) {
        extras += '<button type="button" role="menuitem" class="vp-pop-item" data-act="close">' +
          '<span aria-hidden="true" style="display:inline-block;width:14px;text-align:center">▣</span>' +
          '<span>Back to inline</span></button>';
      }
      if (extras) html += '<div class="vp-pop-sep" role="separator"></div>' + extras;
      menu.innerHTML = html;
      document.body.appendChild(menu);
      var r = anchor.getBoundingClientRect();
      menu.style.top = Math.round(r.bottom + 4) + 'px';
      menu.style.left = Math.round(Math.min(r.left, innerWidth - menu.offsetWidth - 8)) + 'px';
      menu.addEventListener('click', function (ev) {
        if (ev.target.closest('[data-act="popout"]')) { closeMenu(); opts.popout(); return; }
        if (ev.target.closest('[data-act="close"]')) { closeMenu(); setOpen(false); return; }
        var b = ev.target.closest('[data-zone]'); if (!b) return;
        dockTo(b.getAttribute('data-zone'), true);
        if (!isOpen()) setOpen(true);
        closeMenu();
      });
      openMenu = menu;
      setTimeout(function () {
        document.addEventListener('mousedown', _onDoc, true);
        document.addEventListener('keydown', _onKey, true);
      }, 0);
    }

    // ── drag-to-dock (ghost chip + edge drop zones), like PyCharm tool windows ──
    function initDrag(handle) {
      var THRESH = 6;
      handle.addEventListener('pointerdown', function (ev) {
        if (ev.button !== undefined && ev.button !== 0) return;
        // Don't start a dock drag from an interactive control living in the drag
        // handle — most importantly the zoom/detail/width SLIDERS in the graph
        // toolbar (dragging one must change zoom, not re-dock the panel). Buttons,
        // links, inputs, selects, textareas, labels and editable regions all keep
        // their own interactions.
        if (ev.target.closest && ev.target.closest(
            'button, a[href], input, select, textarea, label, [role="slider"], [contenteditable=""], [contenteditable="true"]')) return;
        var x0 = ev.clientX, y0 = ev.clientY, ghost = null, zones = null, zone = null, active = false;
        function mk() {
          ghost = document.createElement('div');
          ghost.className = 'vp-ghost'; ghost.innerHTML = (opts.ghostIcon || '') + '<span>' + (opts.label || 'Panel') + '</span>';
          zones = document.createElement('div'); zones.className = 'vp-zones';
          zones.innerHTML = ['left', 'right', 'top', 'bottom'].filter(isAllowed).map(function (z) {
            return '<div class="vp-zone vp-zone-' + z + '" data-zone="' + z + '"><span>Dock ' + z + '</span></div>';
          }).join('');
          document.body.appendChild(zones); document.body.appendChild(ghost);
        }
        function move(m) {
          if (!active) { if (Math.hypot(m.clientX - x0, m.clientY - y0) < THRESH) return; active = true; mk(); document.body.classList.add('vp-dragging'); }
          ghost.style.left = (m.clientX + 10) + 'px'; ghost.style.top = (m.clientY + 10) + 'px';
          zone = C.dropZone(m.clientX, m.clientY, innerWidth, innerHeight);
          if (!isAllowed(zone)) zone = null;   // forbidden edge (e.g. top) — no drop
          Array.prototype.forEach.call(zones.children, function (z) { z.classList.toggle('on', z.getAttribute('data-zone') === zone); });
        }
        function end(commit) {
          document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up, true);
          document.removeEventListener('pointercancel', cancel, true); window.removeEventListener('blur', cancel);
          document.removeEventListener('keydown', keyf, true);
          if (!active) return;
          document.body.classList.remove('vp-dragging'); ghost.remove(); zones.remove();
          justDragged = true; setTimeout(function () { justDragged = false; }, 0);   // swallow the click after a drag
          if (commit && zone) { dockTo(zone, true); if (!isOpen()) setOpen(true); }
        }
        // The release position is authoritative (a fast flick may end far from the last move).
        function up(u) { if (active && u && u.clientX !== undefined) { zone = C.dropZone(u.clientX, u.clientY, innerWidth, innerHeight); if (!isAllowed(zone)) zone = null; } end(true); }
        function cancel() { end(false); }
        function keyf(k) { if (k.key === 'Escape') { k.preventDefault(); end(false); } }
        document.addEventListener('pointermove', move); document.addEventListener('pointerup', up, true);
        document.addEventListener('pointercancel', cancel, true); window.addEventListener('blur', cancel);
        document.addEventListener('keydown', keyf, true);
      });
      handle.setAttribute('draggable', 'false');
      handle.addEventListener('dragstart', function (ev) { ev.preventDefault(); });   // no native drag stranding the ghost
    }
    function initResize() {
      var h = typeof opts.resizeHandle === 'string' ? document.getElementById(opts.resizeHandle) : opts.resizeHandle;
      if (!h) return;
      h.addEventListener('mousedown', function (ev) {
        ev.preventDefault(); h.classList.add('dragging'); document.body.classList.add('vp-dragging');
        var r0 = panel.getBoundingClientRect();
        function move(m) {
          var raw = dock === 'left' ? m.clientX - r0.left
                  : dock === 'right' ? r0.right - m.clientX
                  : dock === 'top' ? m.clientY - r0.top
                  : r0.bottom - m.clientY;                              // bottom
          // Drag the resize edge toward the dock edge, past the threshold, to
          // collapse to a thin strip; drag back out to expand.
          if (raw < COLLAPSE_AT) { if (!collapsed) setCollapsed(true); return; }
          if (collapsed) setCollapsed(false);
          panel.style.setProperty(isVertical(dock) ? V('h') : V('w'), C.clampDock(dock, raw, innerWidth, innerHeight) + 'px');
        }
        function up() {
          h.classList.remove('dragging'); document.body.classList.remove('vp-dragging');
          document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
          if (collapsed) { lsSet(K.collapsed, '1'); syncVars(); if (opts.onResize) opts.onResize(); return; }
          lsSet(K.collapsed, '0');
          var r = panel.getBoundingClientRect();
          lsSet(sizeKey(dock), String(Math.round(dock === 'bottom' ? r.height : r.width)));
          syncVars(); if (opts.onResize) opts.onResize();
        }
        document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
      });
    }

    (opts.dragHandles || []).forEach(function (hh) {
      var el = typeof hh === 'string' ? panel.querySelector(hh) : hh;
      if (el) initDrag(el);
    });
    initResize();
    if (window.ResizeObserver) { var ro = new ResizeObserver(syncVars); ro.observe(panel); }
    window.addEventListener('resize', function () { applySize(); syncVars(); });

    setOpen(lsGet(K.open, '0') === '1');            // render the initial open/closed state

    return {
      open: function () { setOpen(true); },
      close: function () { setOpen(false); },
      toggle: function () { setOpen(!isOpen()); },
      isOpen: isOpen,
      dockTo: dockTo,
      openDockMenu: openDockMenu,
      getDock: function () { return dock; },
      didDrag: function () { return justDragged; },
      syncVars: syncVars,
      collapse: function () { setCollapsed(true, true); },
      expand: function () { setCollapsed(false, true); },
      toggleCollapsed: function () { setCollapsed(!collapsed, true); },
      isCollapsed: function () { return collapsed; },
      // Bind drag-to-dock on a handle added AFTER make() (e.g. a toolbar that is
      // relocated into the panel later). Guards against double-binding.
      addDragHandle: function (el) { if (el && !el._vpDragBound) { el._vpDragBound = true; initDrag(el); } },
    };
  }

  // ── Pop-out to a separate window ───────────────────────────────────────────
  // Opens the SAME index route in a new window with ?popout=<kind> (+ the current
  // workspace's session id, so the popped window binds to the same workspace — see
  // the seed script in index.html.j2 <head>, which runs before session.js). Extra
  // params (e.g. a code address) let the popped page reopen the same view.
  function popout(kind, params) {
    var qs = new URLSearchParams();
    qs.set('popout', kind);
    try {
      var id = window.vivSession && window.vivSession.getId && window.vivSession.getId();
      if (id) qs.set('session', id);
    } catch (e) { /* no session module — falls back to default workspace */ }
    if (params) Object.keys(params).forEach(function (k) {
      if (params[k] != null && params[k] !== '') qs.set(k, params[k]);
    });
    var url = window.location.origin + window.location.pathname + '?' + qs.toString();
    window.open(url, 'viv-popout-' + kind,
      'width=560,height=820,menubar=no,toolbar=no,location=no,resizable=yes,scrollbars=yes');
  }

  // In a popped window (?popout=<kind>): mark <body> and open that one panel, which
  // CSS then renders full-window. Polls briefly for the panel's controller, since
  // this runs before chat.js / process-code.js have built their panels.
  function initPopout() {
    var kind;
    try { kind = new URLSearchParams(window.location.search || '').get('popout'); } catch (e) { return; }
    if (!kind || !document.body) return;
    document.body.classList.add('viv-popout', 'viv-popout-' + kind);
    var p = new URLSearchParams(window.location.search);
    var tries = 0;
    (function openWhenReady() {
      if (kind === 'chat' && typeof window._openAiPanel === 'function') { window._openAiPanel(); return; }
      if (kind === 'graph' && typeof window._openGraphPanel === 'function') { window._openGraphPanel(); return; }
      if (kind === 'code' && window.ProcessCode) {
        var addr = p.get('address'), comp = p.get('composite');
        if (addr) window.ProcessCode.open(addr);
        else if (comp) window.ProcessCode.openComposite({ id: comp, module: p.get('module') || '', source_path: p.get('source_path') || '' });
        return;
      }
      if (++tries < 60) setTimeout(openWhenReady, 50);
    })();
  }

  global.VivPanelDock = { make: make, popout: popout, initPopout: initPopout };

  if (typeof window !== 'undefined' && typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initPopout);
    else initPopout();
  }
})(typeof window !== 'undefined' ? window : globalThis);
