// registry-panel.js — a dockable "Registry" panel built on VivPanelDock, the
// same engine the Studies (#viv-studies-panel), Chat, Code and Graph panels use.
//
// It is the registry BROWSER: a searchable, grouped list of everything in the
// workspace registry — composites, processes, analyses, visualizations, emitters,
// tests and types — fetched from /api/registry + /api/composites via DataSource.
// Clicking a row focuses that one item as a full semantic-zoom card in the main
// "Process" view (#page-modules), via window._showRegistryCard(address, kind) —
// mirroring how the Studies panel drives the investigation detail view.
//
// Navigation for a large registry: top-level categories COLLAPSE (persisted),
// items are sub-grouped by their source PACKAGE, each row shows its provenance
// (module path + source: workspace / framework / env), and the scroll position
// is preserved across selection and re-render.
//
// Unlike the Studies panel (which RELOCATES an existing rail section into its
// body), this panel BUILDS its own list into #viv-registry-body, because the
// registry list only otherwise exists inside the main Process/Registry page.
(function (global) {
  'use strict';

  var PANEL_ID = 'viv-registry-panel';
  var BODY_ID = 'viv-registry-body';
  var TOGGLE_ID = 'viv-registry-toggle';
  var K_USERSET = 'viv.registry.userset';     // '1' once the user toggles the panel
  var K_QUERY = 'viv.registry.query';         // remembered search text
  var K_COLLAPSED = 'viv.registry.collapsed'; // JSON {catKey:true} collapsed categories

  var ctl = null;
  var popoutWin = null;
  var initDone = false;
  var loaded = false;           // registry data fetched at least once
  var items = [];               // flat [{address, name, kind, source, module, pkg, description}]

  function el(id) { return document.getElementById(id); }
  function lsGet(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  // ── collapsed-category persistence ─────────────────────────────────────────
  function collapsedMap() {
    try { return JSON.parse(lsGet(K_COLLAPSED, '{}')) || {}; } catch (e) { return {}; }
  }
  function isCollapsed(cat) { return !!collapsedMap()[cat]; }
  function setCollapsed(cat, on) {
    var m = collapsedMap();
    if (on) m[cat] = true; else delete m[cat];
    lsSet(K_COLLAPSED, JSON.stringify(m));
  }

  function reflectLauncher() {
    var tog = el(TOGGLE_ID);
    if (!tog) return;
    var active = (ctl && ctl.isOpen()) || !!(popoutWin && !popoutWin.closed);
    tog.classList.toggle('active', active);
    tog.setAttribute('aria-pressed', active ? 'true' : 'false');
  }

  function userToggled() { lsSet(K_USERSET, '1'); }

  function inPopout() {
    try { return !!(document.body && document.body.classList.contains('viv-popout')); }
    catch (e) { return false; }
  }
  function isMobile() {
    try { return global.matchMedia && global.matchMedia('(max-width:640px)').matches; }
    catch (e) { return false; }
  }

  // ── registry data → the flat item list ────────────────────────────────────
  // Display order + human labels. Keys match the `kind` the registry reports
  // (composites are merged in under 'composite').
  var GROUPS = [
    ['composite', 'Composites'],
    ['process', 'Processes'],
    ['analysis', 'Analyses'],
    ['visualization', 'Visualizations'],
    ['emitter', 'Emitters'],
    ['report_card', 'Tests'],
    ['type', 'Types'],
  ];
  // Re-bucket a reported kind the way the Registry page does: analysis /
  // visualization classes mechanically report kind 'step', so re-route by the
  // module-path convention; a bare 'step' otherwise shows under Processes.
  function _cat(e) {
    var k = (e.kind || '').toLowerCase();
    var addr = (e.address || '').toLowerCase();
    if (k === 'composite') return 'composite';
    if (addr.indexOf('.analyses.') >= 0 || addr.indexOf('.analysis.') >= 0) return 'analysis';
    if (addr.indexOf('.visualizations.') >= 0 || addr.indexOf('.visualization.') >= 0) return 'visualization';
    if (k === 'visualization') return 'visualization';
    if (k === 'analysis') return 'analysis';
    if (k === 'emitter') return 'emitter';
    if (k === 'report_card') return 'report_card';
    if (k === 'type') return 'type';
    return 'process';   // process, step, other
  }
  // The module path (address minus the trailing class name for processes; the
  // whole module for composites) and the top-level package it comes from.
  function _moduleOf(addr, kind) {
    var parts = String(addr || '').split('.');
    if (parts.length <= 1) return addr || '';
    // composites: the id IS the module path to the composite (no class suffix);
    // processes/steps: the last segment is the ClassName — drop it for the module.
    return (kind === 'composite') ? parts.slice(0, -1).join('.') : parts.slice(0, -1).join('.');
  }
  function _pkgOf(addr) { var p = String(addr || '').split('.'); return p[0] || '(top level)'; }

  // Normalize the `source` field into a short provenance chip.
  function _srcChip(source) {
    var s = String(source || '').toLowerCase();
    if (s.indexOf('workspace') >= 0) return { cls: 'ws', label: 'workspace' };
    if (s.indexOf('framework') >= 0) return { cls: 'fw', label: 'framework' };
    if (s.indexOf('environment') >= 0 || s === 'env') return { cls: 'env', label: 'env' };
    return s ? { cls: 'other', label: s } : null;
  }

  function loadData() {
    var DS = global.DataSource;
    if (!DS || typeof DS.loadRegistry !== 'function') return Promise.resolve([]);
    return Promise.all([
      DS.loadRegistry(false).catch(function () { return {}; }),
      (typeof DS.loadComposites === 'function' ? DS.loadComposites() : Promise.resolve({})).catch(function () { return {}; }),
    ]).then(function (res) {
      var reg = res[0] || {}, comp = res[1] || {};
      var out = [];
      (reg.processes || []).forEach(function (p) {
        if (!p || !p.address) return;
        out.push({ address: p.address, name: p.name || p.address.split('.').pop(), kind: _cat(p),
          source: p.source || '', module: _moduleOf(p.address, _cat(p)), pkg: _pkgOf(p.address),
          description: p.description || '' });
      });
      (comp.composites || []).forEach(function (c) {
        var addr = c.id || c.address || c.module;
        if (!addr) return;
        out.push({ address: addr, name: c.name || String(addr).split('.').pop(), kind: 'composite',
          source: c.read_only ? 'framework' : 'in_workspace', module: _moduleOf(addr, 'composite'),
          pkg: _pkgOf(addr), description: c.description || '' });
      });
      var seen = {};
      out = out.filter(function (it) {
        if (seen[it.address] && it.kind !== 'composite') return false;
        seen[it.address] = true; return true;
      });
      out.sort(function (a, b) { return a.name.localeCompare(b.name); });
      return out;
    });
  }

  // Rebuild ONLY the list div (the search header stays), preserving scroll.
  function renderList(query) {
    var list = el('viv-reg-list');
    if (!list) return;
    var prevScroll = list.scrollTop;
    var q = String(query == null ? lsGet(K_QUERY, '') : query).trim().toLowerCase();
    var filtered = !q ? items : items.filter(function (it) {
      return it.name.toLowerCase().indexOf(q) >= 0 ||
        it.address.toLowerCase().indexOf(q) >= 0 ||
        it.description.toLowerCase().indexOf(q) >= 0;
    });
    var byKind = {};
    filtered.forEach(function (it) { (byKind[it.kind] = byKind[it.kind] || []).push(it); });

    var html = '';
    if (!filtered.length) {
      html = '<div class="viv-reg-empty muted">' + (loaded ? (q ? 'No matches.' : 'Registry is empty.') : 'Loading…') + '</div>';
    } else {
      // When searching, auto-expand every category so matches are visible.
      var searching = !!q;
      GROUPS.forEach(function (g) {
        var key = g[0], label = g[1], arr = byKind[key] || [];
        if (!arr.length) return;
        var collapsed = !searching && isCollapsed(key);
        html += '<div class="viv-reg-cat' + (collapsed ? ' collapsed' : '') + '" data-cat="' + key + '">' +
          '<button type="button" class="viv-reg-cat-head" data-cat="' + key + '" aria-expanded="' + (!collapsed) + '">' +
          '<span class="viv-reg-chev" aria-hidden="true">▸</span>' +
          '<span class="viv-reg-cat-label">' + esc(label) + '</span>' +
          '<span class="viv-reg-count">' + arr.length + '</span></button>' +
          '<div class="viv-reg-cat-body"' + (collapsed ? ' hidden' : '') + '>';
        if (!collapsed) {
          // sub-group by source package (helps a long list sort itself out).
          var byPkg = {};
          arr.forEach(function (it) { (byPkg[it.pkg] = byPkg[it.pkg] || []).push(it); });
          var pkgs = Object.keys(byPkg).sort();
          var multi = pkgs.length > 1;
          pkgs.forEach(function (pkg) {
            if (multi) html += '<div class="viv-reg-sub" title="source package">' + esc(pkg) + ' <span class="viv-reg-subcount">' + byPkg[pkg].length + '</span></div>';
            byPkg[pkg].forEach(function (it) {
              var sel = (global._registrySelected === it.address) ? ' active' : '';
              var ws = (it.source && it.source.indexOf('workspace') >= 0) ? '<span class="viv-reg-ws" title="defined in this workspace">●</span>' : '';
              var chip = _srcChip(it.source);
              var prov = '<span class="viv-reg-mod" title="' + esc(it.module) + '">' + esc(it.module) + '</span>' +
                (chip ? '<span class="viv-reg-chip viv-reg-chip-' + chip.cls + '">' + esc(chip.label) + '</span>' : '');
              html += '<div class="viv-reg-row' + sel + '" role="button" tabindex="0"' +
                ' data-address="' + esc(it.address) + '" data-kind="' + esc(it.kind) + '"' +
                ' title="' + esc(it.address) + (it.description ? ' — ' + esc(it.description) : '') + '">' +
                '<div class="viv-reg-row-main">' + ws + '<span class="viv-reg-name">' + esc(it.name) + '</span></div>' +
                '<div class="viv-reg-prov">' + prov + '</div>' +
                '</div>';
            });
          });
        }
        html += '</div></div>';
      });
    }
    list.innerHTML = html;
    try { list.scrollTop = prevScroll; } catch (e) { /* ignore */ }
  }

  function render() {
    var body = el(BODY_ID);
    if (!body) return;
    if (!el('viv-reg-head')) {
      body.innerHTML = '' +
        '<div class="viv-reg-head" id="viv-reg-head">' +
        '  <input type="search" class="viv-search viv-reg-search" id="viv-reg-search" placeholder="Search registry…" value="' + esc(lsGet(K_QUERY, '')) + '" aria-label="Search the registry">' +
        '  <button class="icon-btn viv-reg-refresh" type="button" title="Refresh registry" aria-label="Refresh registry">↻</button>' +
        '</div>' +
        '<div class="viv-reg-list" id="viv-reg-list"></div>';
      var search = el('viv-reg-search');
      if (search) search.addEventListener('input', function () { lsSet(K_QUERY, this.value); renderList(this.value); });
      var refresh = body.querySelector('.viv-reg-refresh');
      if (refresh) refresh.addEventListener('click', function () { refreshData(); });

      var list = el('viv-reg-list');
      if (list) {
        // Delegated: a category header toggles collapse; a row selects + focuses.
        function onActivate(target) {
          var head = target.closest ? target.closest('.viv-reg-cat-head') : null;
          if (head) {
            var cat = head.getAttribute('data-cat');
            setCollapsed(cat, !isCollapsed(cat));
            renderList();                 // scroll preserved inside renderList
            return;
          }
          var row = target.closest ? target.closest('.viv-reg-row') : null;
          if (!row) return;
          var prev = list.querySelector('.viv-reg-row.active'); if (prev) prev.classList.remove('active');
          row.classList.add('active');    // no list rebuild → scroll spot kept
          var addr = row.getAttribute('data-address'), kind = row.getAttribute('data-kind');
          global._registrySelected = addr;
          if (typeof global._showRegistryCard === 'function') global._showRegistryCard(addr, kind);
        }
        list.addEventListener('click', function (ev) { ev.preventDefault(); onActivate(ev.target); });
        list.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar') { ev.preventDefault(); onActivate(ev.target); }
        });
      }
    }
    renderList();
  }

  function refreshData() {
    loadData().then(function (list) {
      items = list; loaded = true;
      render();
      if (!inPopout()) applyDefault();
      reflectLauncher();
    });
  }

  function hasItems() { return loaded && items.length > 0; }

  // Auto-opens on the "Process" page (#modules) only — mirroring how the Studies
  // panel auto-opens on the Investigations page — not on every page.
  function onProcessPage() {
    try { return (global.location.hash || '').replace(/^#/, '') === 'modules'; }
    catch (e) { return false; }
  }

  function applyDefault() {
    if (!ctl || inPopout() || isMobile() || lsGet(K_USERSET, '0') === '1') { reflectLauncher(); return; }
    if (hasItems() && onProcessPage()) { if (!ctl.isOpen()) ctl.open(); }
    reflectLauncher();
  }

  function returnInline() {
    try {
      if (global.opener && !global.opener.closed && typeof global.opener._openRegistryPanel === 'function') {
        global.opener._openRegistryPanel();
      }
    } catch (e) { /* ignore */ }
    try { global.close(); } catch (e) { /* ignore */ }
  }

  function init() {
    var panel = el(PANEL_ID);
    var layout = document.querySelector('.viv-layout');
    var mainEl = document.querySelector('.viv-main');
    if (!panel || !layout || !mainEl || !global.VivPanelDock) return;

    var head = panel.querySelector('.viv-registry-head');
    ctl = global.VivPanelDock.make({
      panel: panel, layout: layout, mainEl: mainEl, key: 'registry',
      label: 'Registry',
      launcher: el(TOGGLE_ID),
      docks: ['left', 'right', 'bottom', 'top'],
      resizeHandle: 'viv-registry-resize-handle',
      dragHandles: head ? [head] : [],
      popout: function () {
        popoutWin = global.VivPanelDock.popout('registry');
        userToggled();
        if (ctl) ctl.close();
        setTimeout(reflectLauncher, 60);
      },
      defaultDock: 'left', defaultSize: { side: 300, bottom: 280 },
      onOpen: function () { reflectLauncher(); if (!loaded) refreshData(); },
      onClose: function () { reflectLauncher(); if (initDone && inPopout()) returnInline(); },
    });

    global.RegistryPanel = {
      toggle: function () { userToggled(); if (ctl) ctl.toggle(); },
      open: function () { userToggled(); if (ctl) ctl.open(); },
      close: function () { userToggled(); if (ctl) ctl.close(); },
      dockMenu: function (anchor) { if (ctl) ctl.openDockMenu(anchor); },
      collapse: function () { if (ctl) ctl.collapse(); },
      expand: function () { if (ctl) ctl.expand(); },
      toggleCollapsed: function () { if (ctl) ctl.toggleCollapsed(); },
      isOpen: function () { return !!(ctl && ctl.isOpen()); },
      refresh: function () { refreshData(); },
    };
    global._openRegistryPanel = function () { if (ctl) ctl.open(); };

    var tog = el(TOGGLE_ID);
    if (tog) tog.addEventListener('click', function (ev) {
      ev.preventDefault();
      if (ctl.didDrag && ctl.didDrag()) return;
      userToggled();
      ctl.toggle();
    });

    var isPopout = false;
    try { isPopout = /(?:^|[?&])popout=registry(?:&|$)/.test(global.location.search || ''); } catch (e) { /* ignore */ }

    refreshData();
    if (isPopout) ctl.open();

    global.addEventListener('hashchange', function () { if (lsGet(K_USERSET, '0') !== '1') applyDefault(); });

    reflectLauncher();
    setTimeout(reflectLauncher, 300);
    global.setInterval(function () {
      if (popoutWin && popoutWin.closed) { popoutWin = null; reflectLauncher(); }
    }, 1500);

    initDone = true;
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
