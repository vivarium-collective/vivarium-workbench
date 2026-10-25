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

  // ── Shared dock registry + per-side order ──────────────────────────────────
  // Panels are independent instances; to reorder/swap two panels docked on the
  // same side (iTerm-style) a drag must see its siblings and a shared order. Each
  // make() registers its instance; order persists in localStorage per side.
  var REGISTRY = [];
  function loadOrder() { try { return JSON.parse(localStorage.getItem('viv.dock.order') || '{}') || {}; } catch (e) { return {}; } }
  function saveOrder(o) { try { localStorage.setItem('viv.dock.order', JSON.stringify(o)); } catch (e) { /* private mode */ } }
  // Per-side layout is a list of LANES. On a left/right side a lane is a COLUMN and
  // lanes sit side by side; on a top/bottom band a lane is a ROW and lanes stack.
  // Each lane is an ordered list of panel keys stacked along the side's main axis.
  // Legacy flat ['a','b'] (one column) normalizes to [['a','b']] — one lane renders
  // exactly like the pre-lanes single-column layout.
  function sideLanes(side) {
    var a = loadOrder()[side];
    if (!Array.isArray(a) || !a.length) return [];
    if (!Array.isArray(a[0])) return [a.slice()];                 // legacy flat → one lane
    return a.map(function (l) { return (l || []).slice(); }).filter(function (l) { return l.length; });
  }
  function setSideLanes(side, lanes) {
    var clean = (lanes || []).map(function (l) { return (l || []).slice(); }).filter(function (l) { return l.length; });
    var o = loadOrder(); if (clean.length) o[side] = clean; else delete o[side]; saveOrder(o);
  }
  function sideKeys(side) { return sideLanes(side).reduce(function (acc, l) { return acc.concat(l); }, []); }   // flattened (back-compat)
  function setSideKeys(side, keys) { setSideLanes(side, keys && keys.length ? [keys] : []); }                    // single-lane setter
  function laneOf(side, key) { var ls = sideLanes(side); for (var i = 0; i < ls.length; i++) if (ls[i].indexOf(key) >= 0) return i; return -1; }
  function removeFromSide(side, key) {
    var ls = sideLanes(side), changed = false;
    ls = ls.map(function (l) { var n = l.filter(function (k) { return k !== key; }); if (n.length !== l.length) changed = true; return n; });
    if (changed) setSideLanes(side, ls);
  }
  function ensureInOrder(side, key) {
    if (laneOf(side, key) >= 0) return;
    var ls = sideLanes(side); if (!ls.length) ls.push([]);
    ls[ls.length - 1].push(key); setSideLanes(side, ls);          // append to the last lane
  }
  function dropFromAllSides(key, exceptSide) {
    ['left', 'right', 'top', 'bottom'].forEach(function (s) { if (s !== exceptSide) removeFromSide(s, key); });
  }
  function registryFor(key) { for (var i = 0; i < REGISTRY.length; i++) if (REGISTRY[i].key === key) return REGISTRY[i]; return null; }
  // Rects of the OTHER open docked panels (for the drop-target hit test).
  function otherPanelRects(exceptKey) {
    return REGISTRY.filter(function (e) { return e.key !== exceptKey && e.isOpen(); })
      .map(function (e) { var r = e.panel.getBoundingClientRect(); return { key: e.key, side: e.getDock(), left: r.left, top: r.top, right: r.right, bottom: r.bottom }; });
  }

  // ── Per-side stack containers (iTerm-style same-side splitting) ──────────────
  // A dock side with ≥1 open panel holds a .vp-stack flex container. On a vertical
  // side (left / right) the stack is a COLUMN — its panels split top↔bottom; on a
  // horizontal band (top / bottom) it is a ROW — its panels split left↔right. The
  // container carries the side's OUTER size (column width / band height); each
  // panel's flex-grow carries its share of the split, and a .vp-stack-sizer bar
  // between two panels drags that ratio. One panel on a side → the container is
  // simply a sized wrapper (same footprint as before stacking existed).
  var STACKS = {};                 // side -> container element
  var _layout = null, _main = null;
  function layoutEl() { return (_layout && _layout.isConnected) ? _layout : (_layout = document.querySelector('.viv-layout')); }
  function mainElGet() { return (_main && _main.isConnected) ? _main : (_main = document.querySelector('.viv-main')); }
  function isVertSide(side) { return side === 'bottom' || side === 'top'; }   // height-sized band (row stack)
  function stackSizeKey(side) { return 'viv.stack.' + side + '.size'; }
  function stackGrowKey(side) { return 'viv.stack.' + side + '.grows'; }
  function stackGrows(side) { try { return JSON.parse(lsGet(stackGrowKey(side), '{}')) || {}; } catch (e) { return {}; } }
  // Per-lane cross-axis grow share (keyed by lane index; reset on structural change).
  function stackLaneGrowKey(side) { return 'viv.stack.' + side + '.lanegrows'; }
  function stackLaneGrows(side) { try { return JSON.parse(lsGet(stackLaneGrowKey(side), '{}')) || {}; } catch (e) { return {}; } }
  function defaultSideSize(side) { return isVertSide(side) ? 320 : 440; }

  function ensureStack(side) {
    var s = STACKS[side];
    if (!s || !s.isConnected) {
      if (!s) { s = document.createElement('div'); s.className = 'vp-stack vp-stack-' + side; s.dataset.side = side; STACKS[side] = s; }
    }
    var L = layoutEl(), M = mainElGet();
    if (!L || !M) return s;
    // Place the container in the correct layout slot (idempotent).
    if (side === 'left') { if (s.parentNode !== L || s.nextSibling !== M) L.insertBefore(s, M); }
    else if (side === 'right') { if (s.parentNode !== L || L.lastElementChild !== s) L.appendChild(s); }
    else if (side === 'top') { if (s.parentNode !== M || M.firstChild !== s) M.insertBefore(s, M.firstChild); }
    else { if (s.parentNode !== M || M.lastElementChild !== s) M.appendChild(s); }   // bottom
    var n = parseInt(lsGet(stackSizeKey(side), ''), 10);
    var sz = C.clampDock(side, (n > 0 ? n : defaultSideSize(side)), innerWidth, innerHeight);
    s.style.setProperty(isVertSide(side) ? 'height' : 'width', sz + 'px');
    return s;
  }

  // Re-place every OPEN panel docked on `side` into that side's stack as LANES, in
  // persisted order, interleaving fresh within-lane sizers and between-lane sizers.
  // A side with no open panels hides its container so it reserves no space.
  function replaceSide(side) {
    var openByKey = {};
    REGISTRY.forEach(function (e) { if (e.isOpen() && e.getDock() === side) openByKey[e.key] = e; });
    // Persisted lanes, filtered to open panels; any freshly-docked open panel not yet
    // in a lane joins the last lane (or a first lane if none).
    var lanes = sideLanes(side).map(function (l) { return l.filter(function (k) { return openByKey[k]; }); })
                               .filter(function (l) { return l.length; });
    var placed = {}; lanes.forEach(function (l) { l.forEach(function (k) { placed[k] = 1; }); });
    Object.keys(openByKey).forEach(function (k) { if (!placed[k]) { if (!lanes.length) lanes.push([]); lanes[lanes.length - 1].push(k); } });

    var s = ensureStack(side);
    if (!s) return;
    // Tear down the prior lane/sizer wrappers WITHOUT detaching panel nodes: reparent
    // every panel back onto `s` first (so clearing wrappers never drops one), then drop
    // the empty wrappers. Hidden same-side panels stay as direct (hidden) children of s.
    Array.prototype.slice.call(s.querySelectorAll('.vp-dockable')).forEach(function (p) { s.appendChild(p); });
    Array.prototype.slice.call(s.querySelectorAll(':scope > .vp-stack-lane, :scope > .vp-lane-sizer')).forEach(function (el) { el.remove(); });
    var grows = stackGrows(side), laneGrows = stackLaneGrows(side);
    lanes.forEach(function (lane, li) {
      if (li > 0) {
        var lz = document.createElement('div');
        lz.className = 'vp-lane-sizer'; lz.dataset.side = side;
        s.appendChild(lz); initSizer(lz, side, true);     // cross-axis: resizes lanes
      }
      var laneEl = document.createElement('div');
      laneEl.className = 'vp-stack-lane'; laneEl.dataset.side = side;
      laneEl.style.setProperty('--vp-lane-grow', String(laneGrows[li] > 0 ? laneGrows[li] : 1));
      lane.forEach(function (k, i) {
        if (i > 0) {
          var z = document.createElement('div');
          z.className = 'vp-stack-sizer'; z.dataset.side = side;
          laneEl.appendChild(z); initSizer(z, side, false);   // main-axis: resizes panels in the lane
        }
        var e = openByKey[k];
        e.panel.dataset.dock = side;
        e.panel.style.flex = '';   // let the CSS (flex:var(--vp-grow) 1 0 !important) drive it
        e.panel.style.setProperty('--vp-grow', String(grows[k] > 0 ? grows[k] : 1));
        laneEl.appendChild(e.panel);                    // moves it here from wherever it was
        if (e.onPlaced) e.onPlaced(side);
      });
      s.appendChild(laneEl);
    });
    s.hidden = !lanes.length;
  }
  function replaceAllSides() { ['left', 'right', 'top', 'bottom'].forEach(replaceSide); }

  // Persist the live flex-grow of each open panel on `side` (after a split drag).
  function persistGrows(side) {
    var o = stackGrows(side);
    REGISTRY.forEach(function (e) {
      if (e.isOpen() && e.getDock() === side) o[e.key] = parseFloat(e.panel.style.getPropertyValue('--vp-grow')) || 1;
    });
    try { localStorage.setItem(stackGrowKey(side), JSON.stringify(o)); } catch (ex) { /* private mode */ }
  }

  // Persist the live cross-axis grow of each lane on `side` (keyed by lane index;
  // index-based, so a structural change (added/removed lane) resets the ratios).
  function persistLaneGrows(side) {
    var s = STACKS[side]; if (!s) return;
    var o = {};
    Array.prototype.slice.call(s.querySelectorAll(':scope > .vp-stack-lane')).forEach(function (laneEl, i) {
      o[i] = parseFloat(laneEl.style.getPropertyValue('--vp-lane-grow')) || 1;
    });
    try { localStorage.setItem(stackLaneGrowKey(side), JSON.stringify(o)); } catch (ex) { /* private mode */ }
  }

  // Drag a sizer to transfer flex-grow between the two siblings it separates, keeping
  // their combined share constant. A within-lane sizer (cross=false) moves --vp-grow
  // between two PANELS along the lane's main axis; a between-lane sizer (cross=true)
  // moves --vp-lane-grow between two LANES along the side's cross axis.
  function initSizer(z, side, cross) {
    // Panel sizer drags the side's main axis (vertical for left/right); a lane sizer
    // drags the cross axis (horizontal for left/right).
    var dragVertical = cross ? isVertSide(side) : !isVertSide(side);
    var growVar = cross ? '--vp-lane-grow' : '--vp-grow';
    z.addEventListener('mousedown', function (ev) {
      if (ev.button !== undefined && ev.button !== 0) return;
      var prev = z.previousElementSibling, next = z.nextElementSibling;
      if (!prev || !next) return;
      ev.preventDefault();
      document.body.classList.add('vp-dragging'); z.classList.add('dragging');
      var rp = prev.getBoundingClientRect(), rn = next.getBoundingClientRect();
      var sizeP = dragVertical ? rp.height : rp.width;
      var total = sizeP + (dragVertical ? rn.height : rn.width);
      var gP = parseFloat(prev.style.getPropertyValue(growVar)) || 1, gN = parseFloat(next.style.getPropertyValue(growVar)) || 1, gSum = gP + gN;
      var start = dragVertical ? ev.clientY : ev.clientX;
      function move(m) {
        var d = (dragVertical ? m.clientY : m.clientX) - start;
        var frac = Math.max(0.1, Math.min(0.9, (sizeP + d) / total));
        prev.style.setProperty(growVar, String(gSum * frac));
        next.style.setProperty(growVar, String(gSum * (1 - frac)));
      }
      function up() {
        document.body.classList.remove('vp-dragging'); z.classList.remove('dragging');
        document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
        if (cross) persistLaneGrows(side); else persistGrows(side);
        REGISTRY.forEach(function (e) { if (e.isOpen() && e.getDock() === side && e.syncVars) e.syncVars(); });
      }
      document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
    });
  }

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
    // Place this panel on `zone` and (re)lay out every side's stack. The per-side
    // .vp-stack container (see module scope) owns the outer size + ordering now, so
    // placing one panel re-flows all sides (a move vacates the old side's stack).
    function place(zone) {
      ensureInOrder(zone, key);
      replaceAllSides();
    }
    function isOpen() { return !panel.hidden; }
    // Publish the panel's footprint on <html> so fixed / 100vh layouts leave room for it.
    // The reserved space for a side is the whole stack (all same-side panels), so read
    // the side's .vp-stack rect when present, falling back to this panel's own rect.
    function syncVars() {
      var on = isOpen();
      var st = STACKS[dock];
      var r = (on && st && st.isConnected && !st.hidden) ? st.getBoundingClientRect() : panel.getBoundingClientRect();
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
      // Re-flow every side on open AND close: closing must collapse this side's
      // stack (hide it so it reserves no space) and let <main> reclaim the room —
      // otherwise the closed panel's column stays as an empty gap.
      if (open) place(dock); else replaceAllSides();
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
      dropFromAllSides(key, zone);           // moving sides → drop stale order entries
      if (isOpen()) { place(zone); syncVars(); }
      if (opts.onDock) opts.onDock(zone);
    }
    // Insert this panel INTO relKey's lane, before/after relKey (stack in the same
    // column / row — reorder / co-dock from a drag). Other lanes are preserved.
    function insertRelativeTo(side, relKey, after) {
      if (!isAllowed(side)) return;
      dock = side; lsSet(K.dock, side);
      dropFromAllSides(key, side);
      removeFromSide(side, key);
      var ls = sideLanes(side);
      var li = laneOf(side, relKey);
      if (li < 0) { if (!ls.length) ls.push([]); ls[ls.length - 1].push(key); }
      else { var at = ls[li].indexOf(relKey); ls[li].splice(after ? at + 1 : at, 0, key); }
      setSideLanes(side, ls);
      if (!isOpen()) setOpen(true); else { replaceAllSides(); syncVars(); }
      if (opts.onDock) opts.onDock(side);
    }
    // Place this panel in a NEW lane (column on left/right, row on top/bottom) before/
    // after relKey's lane — "dock beside" an already-docked panel.
    function splitLane(side, relKey, after) {
      if (!isAllowed(side)) return;
      dock = side; lsSet(K.dock, side);
      dropFromAllSides(key, side);
      removeFromSide(side, key);
      var ls = sideLanes(side);
      var li = laneOf(side, relKey); if (li < 0) li = ls.length - 1;
      ls.splice(after ? li + 1 : li, 0, [key]);
      setSideLanes(side, ls);
      if (!isOpen()) setOpen(true); else { replaceAllSides(); syncVars(); }
      if (opts.onDock) opts.onDock(side);
    }
    // Swap this panel's position (side + order slot + size) with another panel's.
    function swapWith(otherKey) {
      var other = registryFor(otherKey); if (!other || other.key === key) return;
      var aSide = dock, bSide = other.getDock();
      if (!isAllowed(bSide) || !other.allows(aSide)) return;    // respect each panel's dock limits
      var aSize = lsGet(stackSizeKey(aSide), ''), bSize = other.sizeStr(bSide);
      // Exchange order slots IN PLACE — relabel the two keys within the lane
      // structure so a multi-lane (column/row) layout is preserved on swap.
      if (aSide === bSide) {
        setSideLanes(aSide, sideLanes(aSide).map(function (l) {
          return l.map(function (k) { return k === key ? otherKey : (k === otherKey ? key : k); });
        }));
      } else {
        setSideLanes(aSide, sideLanes(aSide).map(function (l) { return l.map(function (k) { return k === key ? otherKey : k; }); }));
        setSideLanes(bSide, sideLanes(bSide).map(function (l) { return l.map(function (k) { return k === otherKey ? key : k; }); }));
      }
      // Exchange sides + sizes.
      dock = bSide; lsSet(K.dock, bSide); if (bSize !== '') lsSet(stackSizeKey(bSide), bSize);
      other.setSideAndSize(aSide, aSize);
      replaceAllSides(); syncVars();
      if (opts.onDock) opts.onDock(bSide);
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
        ev.preventDefault();   // don't begin a native text selection from the drag handle
        var x0 = ev.clientX, y0 = ev.clientY, ghost = null, zones = null, insEl = null, lblEl = null;
        var target = null, panels = null, active = false;
        function mk() {
          ghost = document.createElement('div');
          ghost.className = 'vp-ghost'; ghost.innerHTML = (opts.ghostIcon || '') + '<span>' + (opts.label || 'Panel') + '</span>';
          zones = document.createElement('div'); zones.className = 'vp-zones';
          zones.innerHTML = ['left', 'right', 'top', 'bottom'].filter(isAllowed).map(function (z) {
            return '<div class="vp-zone vp-zone-' + z + '" data-zone="' + z + '"><span>Dock ' + z + '</span></div>';
          }).join('');
          insEl = document.createElement('div'); insEl.className = 'vp-insert-line'; insEl.hidden = true;
          lblEl = document.createElement('div'); lblEl.className = 'vp-drop-label'; lblEl.hidden = true;   // says what the drop does
          document.body.appendChild(zones); document.body.appendChild(insEl); document.body.appendChild(lblEl); document.body.appendChild(ghost);
        }
        function rectFor(k) { for (var i = 0; i < panels.length; i++) if (panels[i].key === k) return panels[i]; return null; }
        function render() {
          // Edge zones highlight only for an 'edge' target.
          Array.prototype.forEach.call(zones.children, function (z) {
            z.classList.toggle('on', !!target && target.kind === 'edge' && z.getAttribute('data-zone') === target.side);
          });
          var showInd = !!(target && (target.kind === 'insert' || target.kind === 'split'));
          insEl.hidden = !showInd; lblEl.hidden = !showInd;
          if (showInd) {
            var r = rectFor(target.relKey); if (r) {
              var vert = (target.side === 'left' || target.side === 'right');
              // 'insert' = stack in the lane → a line along the MAIN-axis edge;
              // 'split' = a new lane beside the panel → a line along the CROSS-axis edge.
              var horizLine = (target.kind === 'insert') ? vert : !vert;   // full-width bar vs full-height bar
              var lx, ly;
              if (horizLine) { var yy = target.after ? r.bottom : r.top;
                insEl.style.cssText = 'left:' + r.left + 'px;top:' + (yy - 2) + 'px;width:' + (r.right - r.left) + 'px;height:4px;';
                lx = (r.left + r.right) / 2; ly = yy; }
              else { var xx = target.after ? r.right : r.left;
                insEl.style.cssText = 'left:' + (xx - 2) + 'px;top:' + r.top + 'px;width:4px;height:' + (r.bottom - r.top) + 'px;';
                lx = xx; ly = (r.top + r.bottom) / 2; }
              // Label says exactly what the drop will do.
              var txt = (target.kind === 'split')
                ? (vert ? 'New column' : 'New row')
                : (vert ? (target.after ? 'Stack below' : 'Stack above')
                        : (target.after ? 'Stack right' : 'Stack left'));
              lblEl.textContent = txt;
              lblEl.style.cssText = 'left:' + Math.round(lx) + 'px;top:' + Math.round(ly) + 'px;';
            }
          }
        }
        function compute(mx, my) {
          target = C.dropTarget(mx, my, innerWidth, innerHeight, panels);
          // Honor this panel's dock limits (e.g. graph forbids 'top').
          if (target && target.kind === 'edge' && !isAllowed(target.side)) target = null;
          if (target && (target.kind === 'insert' || target.kind === 'split') && !isAllowed(target.side)) target = null;
        }
        function move(m) {
          if (!active) { if (Math.hypot(m.clientX - x0, m.clientY - y0) < THRESH) return; active = true; panels = otherPanelRects(key); mk(); document.body.classList.add('vp-dragging'); }
          ghost.style.left = (m.clientX + 10) + 'px'; ghost.style.top = (m.clientY + 10) + 'px';
          compute(m.clientX, m.clientY); render();
        }
        function end(commit) {
          document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up, true);
          document.removeEventListener('pointercancel', cancel, true); window.removeEventListener('blur', cancel);
          document.removeEventListener('keydown', keyf, true);
          if (!active) return;
          document.body.classList.remove('vp-dragging'); ghost.remove(); zones.remove(); insEl.remove(); lblEl.remove();
          justDragged = true; setTimeout(function () { justDragged = false; }, 0);   // swallow the click after a drag
          if (commit && target) {
            if (target.kind === 'edge') { dockTo(target.side, true); if (!isOpen()) setOpen(true); }
            else if (target.kind === 'insert') { insertRelativeTo(target.side, target.relKey, target.after); }
            else if (target.kind === 'split') { splitLane(target.side, target.relKey, target.after); }
          }
        }
        // The release position is authoritative (a fast flick may end far from the last move).
        function up(u) { if (active && u && u.clientX !== undefined) { compute(u.clientX, u.clientY); } end(true); }
        function cancel() { end(false); }
        function keyf(k) { if (k.key === 'Escape') { k.preventDefault(); end(false); } }
        document.addEventListener('pointermove', move); document.addEventListener('pointerup', up, true);
        document.addEventListener('pointercancel', cancel, true); window.addEventListener('blur', cancel);
        document.addEventListener('keydown', keyf, true);
      });
      handle.setAttribute('draggable', 'false');
      handle.addEventListener('dragstart', function (ev) { ev.preventDefault(); });   // no native drag stranding the ghost
    }
    // The edge resize handle drags the whole SIDE (its .vp-stack) bigger/smaller —
    // outer column width (left/right) or band height (top/bottom), shared by every
    // panel stacked there. Collapse-to-a-strip only applies when this panel is the
    // lone occupant of the side (collapsing one of a split is ambiguous).
    function soloOnSide() {
      return REGISTRY.filter(function (e) { return e.isOpen() && e.getDock() === dock; }).length <= 1;
    }
    function initResize() {
      var h = typeof opts.resizeHandle === 'string' ? document.getElementById(opts.resizeHandle) : opts.resizeHandle;
      if (!h) return;
      h.addEventListener('mousedown', function (ev) {
        ev.preventDefault(); h.classList.add('dragging'); document.body.classList.add('vp-dragging');
        var st = STACKS[dock] || panel;
        var r0 = st.getBoundingClientRect();
        var canCollapse = soloOnSide();
        function move(m) {
          var raw = dock === 'left' ? m.clientX - r0.left
                  : dock === 'right' ? r0.right - m.clientX
                  : dock === 'top' ? m.clientY - r0.top
                  : r0.bottom - m.clientY;                              // bottom
          // Drag the resize edge toward the dock edge, past the threshold, to
          // collapse to a thin strip; drag back out to expand (solo panels only).
          if (canCollapse && raw < COLLAPSE_AT) { if (!collapsed) setCollapsed(true); return; }
          if (collapsed) setCollapsed(false);
          var px = C.clampDock(dock, raw, innerWidth, innerHeight);
          if (STACKS[dock]) STACKS[dock].style.setProperty(isVertical(dock) ? 'height' : 'width', px + 'px');
        }
        function up() {
          h.classList.remove('dragging'); document.body.classList.remove('vp-dragging');
          document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up);
          if (collapsed) { lsSet(K.collapsed, '1'); syncVars(); if (opts.onResize) opts.onResize(); return; }
          lsSet(K.collapsed, '0');
          var r = (STACKS[dock] || panel).getBoundingClientRect();
          lsSet(stackSizeKey(dock), String(Math.round(isVertical(dock) ? r.height : r.width)));
          syncVars(); if (opts.onResize) opts.onResize();
        }
        document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
      });
    }

    // Set this panel's side + the side's persisted outer size in one step (swap).
    function setSideAndSize(side, sizeStr) {
      dock = side; lsSet(K.dock, side);
      if (sizeStr !== undefined && sizeStr !== '') lsSet(stackSizeKey(side), sizeStr);
    }
    // Register so sibling panels' drags can see + reorder/swap this one.
    REGISTRY.push({
      key: key, panel: panel,
      getDock: function () { return dock; },
      isOpen: isOpen, allows: isAllowed, syncVars: syncVars,
      sizeStr: function (side) { return lsGet(stackSizeKey(side), ''); },
      setSideAndSize: setSideAndSize,
    });
    panel.classList.add('vp-dockable');
    panel.dataset.vpKey = key;

    // Slim iTerm-style title bar: the whole bar is the drag handle (move → dock /
    // insert / swap) and carries the × close (+ ↗ pop-out when available). It
    // replaces the old dock-menu button and the floating × corner controls.
    if (opts.titleBar !== false && panel) {
      var titlebar = document.createElement('div');
      titlebar.className = 'vp-titlebar';
      var tPop = (typeof opts.popout === 'function')
        ? '<button type="button" class="vp-tb-btn vp-tb-pop" title="Pop out" aria-label="Pop out">' + POPOUT_GLYPH + '</button>' : '';
      titlebar.innerHTML =
        '<span class="vp-tb-label">' + (opts.label || 'Panel') + '</span>' +
        '<span class="vp-tb-spacer"></span>' + tPop +
        '<button type="button" class="vp-tb-btn vp-tb-close" title="Close" aria-label="Close">' +
          svg('<path d="M6 6l12 12M18 6L6 18"/>') + '</button>';
      panel.insertBefore(titlebar, panel.firstChild);
      panel.classList.add('vp-has-titlebar');
      var _pop = titlebar.querySelector('.vp-tb-pop');
      if (_pop) _pop.addEventListener('click', function (e) { e.stopPropagation(); if (!justDragged) opts.popout(); });
      titlebar.querySelector('.vp-tb-close').addEventListener('click', function (e) { e.stopPropagation(); setOpen(false); });
      initDrag(titlebar);
    }

    (opts.dragHandles || []).forEach(function (hh) {
      var el = typeof hh === 'string' ? panel.querySelector(hh) : hh;
      if (el) initDrag(el);
    });
    initResize();
    if (window.ResizeObserver) { var ro = new ResizeObserver(syncVars); ro.observe(panel); }
    window.addEventListener('resize', function () { if (isOpen()) ensureStack(dock); syncVars(); });

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
    var w = window.open(url, 'viv-popout-' + kind,
      'width=440,height=840,menubar=no,toolbar=no,location=no,resizable=yes,scrollbars=yes');
    // Some browsers ignore the width feature (open at a remembered / large size);
    // nudge it to a sensible narrow default — the user can still resize freely.
    try { if (w && w.resizeTo) w.resizeTo(440, Math.min(840, (window.screen && screen.availHeight) || 840)); }
    catch (e) { /* ignore */ }
    return w;
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
      if (kind === 'graph' && typeof window._openGraphPanel === 'function') {
        // Render the investigation's graph first (a fresh popout has none open),
        // then host the RENDERED graph in the panel. Opening the panel before the
        // DAG renders hosts an empty node, and the async detail render then rebuilds
        // #investigation-dag-fs inline — leaving the popout blank. So wait for the
        // DAG nodes to appear, then relocate it into the panel.
        var ginv = p.get('investigation');
        if (ginv && typeof window._openInvestigationDetail === 'function') {
          try { window._openInvestigationDetail(ginv); } catch (e) { /* ignore */ }
          var gt = 0;
          (function openGraphWhenRendered() {
            var fs = document.getElementById('investigation-dag-fs');
            var ready = fs && fs.querySelector('.aig-node, [data-study-name], [data-aig-node]');
            if (ready || gt++ > 100) { window._openGraphPanel(); return; }
            setTimeout(openGraphWhenRendered, 60);
          })();
        } else {
          window._openGraphPanel();
        }
        return;
      }
      if (kind === 'studies' && typeof window._openStudiesPanel === 'function') { window._openStudiesPanel(); return; }
      if (kind === 'registry' && typeof window._openRegistryPanel === 'function') { window._openRegistryPanel(); return; }
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
