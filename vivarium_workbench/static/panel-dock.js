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
  };

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
    var K = { dock: 'viv.' + key + '.dock', open: 'viv.' + key + '.open', w: 'viv.' + key + '.w', h: 'viv.' + key + '.h' };
    var def = opts.defaultDock || 'right';
    var dock = C.validDock(lsGet(K.dock, def)) ? lsGet(K.dock, def) : def;
    var justDragged = false;

    function sizeKey(d) { return d === 'bottom' ? K.h : K.w; }
    function applySize() {
      var n = parseInt(lsGet(sizeKey(dock), ''), 10);
      var fallback = dock === 'bottom'
        ? ((opts.defaultSize && opts.defaultSize.bottom) || 320)
        : ((opts.defaultSize && opts.defaultSize.side) || 440);
      var size = C.clampDock(dock, n || fallback, innerWidth, innerHeight);
      panel.style.setProperty(dock === 'bottom' ? V('h') : V('w'), size + 'px');
    }
    // Placement in the flex row: left → before <main>, right → end of the row, bottom →
    // inside <main>'s column (below the content). Two right-docked panels stack by order.
    function place(zone) {
      if (!layout || !mainEl) return;
      panel.dataset.dock = zone;
      if (zone === 'left') layout.insertBefore(panel, mainEl);
      else if (zone === 'right') layout.appendChild(panel);
      else mainEl.appendChild(panel);
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
      window.dispatchEvent(new CustomEvent('viv:panel-layout', { detail: { key: key } }));
      if (opts.layoutEvent) window.dispatchEvent(new CustomEvent(opts.layoutEvent));
    }
    function setOpen(open) {
      panel.hidden = !open;
      if (open) place(dock);
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
      if (!C.validDock(zone)) return;
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
      menu.innerHTML = C.DOCKS.map(function (z) {
        return '<button type="button" role="menuitemradio" aria-checked="' + (dock === z) + '" ' +
          'class="vp-pop-item' + (dock === z ? ' on' : '') + '" data-zone="' + z + '">' +
          DOCK_GLYPH[z] + '<span>Dock ' + z + '</span></button>';
      }).join('');
      document.body.appendChild(menu);
      var r = anchor.getBoundingClientRect();
      menu.style.top = Math.round(r.bottom + 4) + 'px';
      menu.style.left = Math.round(Math.min(r.left, innerWidth - menu.offsetWidth - 8)) + 'px';
      menu.addEventListener('click', function (ev) {
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
        if (ev.target.closest && ev.target.closest('button')) return;   // header buttons keep their clicks
        var x0 = ev.clientX, y0 = ev.clientY, ghost = null, zones = null, zone = null, active = false;
        function mk() {
          ghost = document.createElement('div');
          ghost.className = 'vp-ghost'; ghost.innerHTML = (opts.ghostIcon || '') + '<span>' + (opts.label || 'Panel') + '</span>';
          zones = document.createElement('div'); zones.className = 'vp-zones';
          zones.innerHTML = ['left', 'right', 'bottom'].map(function (z) {
            return '<div class="vp-zone vp-zone-' + z + '" data-zone="' + z + '"><span>Dock ' + z + '</span></div>';
          }).join('');
          document.body.appendChild(zones); document.body.appendChild(ghost);
        }
        function move(m) {
          if (!active) { if (Math.hypot(m.clientX - x0, m.clientY - y0) < THRESH) return; active = true; mk(); document.body.classList.add('vp-dragging'); }
          ghost.style.left = (m.clientX + 10) + 'px'; ghost.style.top = (m.clientY + 10) + 'px';
          zone = C.dropZone(m.clientX, m.clientY, innerWidth, innerHeight);
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
        function up(u) { if (active && u && u.clientX !== undefined) zone = C.dropZone(u.clientX, u.clientY, innerWidth, innerHeight); end(true); }
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
          var raw = dock === 'left' ? m.clientX - r0.left : dock === 'right' ? r0.right - m.clientX : r0.bottom - m.clientY;
          panel.style.setProperty(dock === 'bottom' ? V('h') : V('w'), C.clampDock(dock, raw, innerWidth, innerHeight) + 'px');
        }
        function up() {
          h.classList.remove('dragging'); document.body.classList.remove('vp-dragging');
          document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
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
