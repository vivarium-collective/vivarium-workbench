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
      getDock: function () { return dock; },
      didDrag: function () { return justDragged; },
      syncVars: syncVars,
    };
  }

  global.VivPanelDock = { make: make };
})(typeof window !== 'undefined' ? window : globalThis);
