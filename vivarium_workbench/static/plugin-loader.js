// plugin-loader.js — the one generic frontend seam for workbench plugins.
//
// Fetches /api/plugins/manifest (enabled plugins only), injects each plugin's CSS and
// JS from its namespaced /plugins/<name>/ mount, and registers each declared panel as
// a dockable VivPanelDock panel with a left-rail launcher. A panel's body is rendered
// by the global the plugin's bundle defines (spec.entry), looked up lazily so script
// load order does not matter. No plugins → no-op. See docs/plugins.md.
(function (global) {
  'use strict';

  function injectCss(href) {
    var l = document.createElement('link');
    l.rel = 'stylesheet';
    l.href = href;
    (document.head || document.documentElement).appendChild(l);
    return l;
  }

  function injectJs(src) {
    return new Promise(function (resolve) {
      var s = document.createElement('script');
      s.src = src;
      s.async = false;
      s.onload = s.onerror = function () { resolve(); };
      (document.head || document.documentElement).appendChild(s);
    });
  }

  // The context a plugin's panel entry is called with — the one clean surface a
  // plugin renders through, so it never hardcodes its own namespace:
  //   el       the panel body element to render into
  //   name     the plugin name
  //   base     static asset base  ("/plugins/<name>/")
  //   apiBase  API base           ("/api/plugins/<name>")
  //   api(path, opts) -> Promise<json>  fetch under apiBase
  //   host     stable host hooks, so a panel never reaches for window globals:
  //     host.dock              the shared VivPanelDock engine
  //     host.refreshViews(slug) ask the workbench to refresh its views after the
  //                            plugin changed workspace data (no-op if unavailable)
  function panelContext(name, el) {
    var apiBase = '/api/plugins/' + name;
    return {
      el: el, name: name, base: '/plugins/' + name + '/', apiBase: apiBase,
      api: function (path, opts) {
        return fetch(apiBase + path, opts).then(function (r) {
          if (!r.ok) throw new Error('plugin api ' + path + ' -> ' + r.status);
          return r.json();
        });
      },
      host: {
        dock: global.VivPanelDock,
        refreshViews: function (slug) {
          if (typeof global._vivRefreshDiscoveryViews === 'function') {
            try { global._vivRefreshDiscoveryViews(slug); } catch (e) { /* host refresh is best-effort */ }
          }
        },
      },
    };
  }

  function renderInto(ctx, entryName) {
    var fn = global[entryName];
    if (typeof fn === 'function') {
      try { fn(ctx); } catch (e) { /* a plugin panel's render must not break the page */ }
    }
  }

  // Lucide-style rail icons (viewBox 0 0 24 24), matching core's rail links.
  var ICONS = {
    graph: '<circle cx="5" cy="6" r="2.4"/><circle cx="19" cy="6" r="2.4"/><circle cx="12" cy="18" r="2.4"/><path d="M7 7.2l3.6 8.6M17 7.2l-3.6 8.6"/>',
    doc: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    board: '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/>',
    database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
  };

  function iconSvg(name) {
    var inner = ICONS[name] || '<circle cx="12" cy="12" r="9"/>';
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
      + ' stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" class="viv-rail-link-svg">'
      + inner + '</svg>';
  }

  // One dockable-panel group (its own rail well) per plugin panel group, styled like
  // core's Studies/Chat/Code group so plugin launchers read as native rail items.
  function railGroup(group) {
    var rail = document.querySelector('.viv-rail') || document.getElementById('viv-rail');
    if (!rail || !rail.appendChild) return null;
    var id = 'viv-plugin-dockgroup-' + String(group || 'plugins').toLowerCase().replace(/[^a-z0-9]+/g, '-');
    var nav = document.getElementById(id);
    if (!nav) {
      nav = document.createElement('nav');
      nav.id = id;
      nav.className = 'viv-rail-dock-group viv-plugin-dock-group';
      nav.setAttribute('role', 'group');
      if (group) nav.setAttribute('aria-label', group);
      rail.appendChild(nav);
    }
    return nav;
  }

  function makeLauncher(spec) {
    var group = railGroup(spec.group);
    if (!group) return null;
    var a = document.createElement('a');
    a.href = '#';
    a.className = 'viv-rail-link';
    a.setAttribute('role', 'button');
    a.setAttribute('aria-pressed', 'false');
    a.title = spec.title;
    a.dataset.pluginPanel = spec.id;
    var icon = document.createElement('span');
    icon.className = 'viv-rail-link-icon';
    icon.innerHTML = iconSvg(spec.icon);        // icon markup is from our own ICONS map
    var label = document.createElement('span');
    label.className = 'viv-rail-link-label';
    label.textContent = spec.title || '';       // plugin-supplied text → textContent
    a.appendChild(icon);
    a.appendChild(label);
    group.appendChild(a);
    return a;
  }

  function makePanel(plugin, spec) {
    var dock = global.VivPanelDock;
    if (!dock || typeof dock.make !== 'function') return null;
    var layout = document.querySelector('.viv-layout');
    var mainEl = document.querySelector('.viv-main');

    var panel = document.createElement('div');
    panel.className = 'vp-panel viv-plugin-panel';
    panel.id = 'plugin-panel-' + spec.id;
    var bodyEl = document.createElement('div');
    bodyEl.className = 'viv-plugin-panel-body';
    panel.appendChild(bodyEl);
    if (layout && layout.appendChild) layout.appendChild(panel);

    var ctx = panelContext(plugin.name, bodyEl);
    var launcher = makeLauncher(spec);
    if (launcher && launcher.addEventListener) {
      launcher.addEventListener('click', function (e) {
        if (e && e.preventDefault) e.preventDefault();
        if (ctl && ctl.toggle) ctl.toggle();      // make() keeps the launcher's active state
      });
    }

    var ctl = dock.make({
      panel: panel, layout: layout, mainEl: mainEl,
      key: spec.id, label: spec.title, launcher: launcher,
      docks: ['left', 'right', 'bottom', 'top'],
      defaultDock: spec.side || 'left',
      onOpen: function () { renderInto(ctx, spec.entry); },
    });
    renderInto(ctx, spec.entry);
    return ctl;
  }

  // Load one plugin's assets + panels. Each panel is isolated so one bad panel does
  // not stop the plugin's others; the caller isolates one plugin from the next.
  function loadOne(p) {
    (p.css || []).forEach(function (href) { try { injectCss(href); } catch (e) { /* skip */ } });
    // sequential so a plugin's window[entry] globals exist before its panels render
    return (p.js || []).reduce(function (c, src) {
      return c.then(function () { return injectJs(src); });
    }, Promise.resolve()).then(function () {
      (p.panels || []).forEach(function (spec) {
        try { makePanel(p, spec); } catch (e) { /* one panel must not break the rest */ }
      });
    });
  }

  function loadPlugins() {
    if (typeof fetch !== 'function') return Promise.resolve();
    return fetch('/api/plugins/manifest')
      .then(function (res) { return (res && res.ok) ? res.json() : []; })
      .then(function (manifest) {
        if (!Array.isArray(manifest)) return;
        // Sequential + per-plugin isolation: one plugin failing never aborts the others.
        return manifest.reduce(function (chain, p) {
          return chain
            .then(function () { return loadOne(p); })
            .catch(function () { /* isolate this plugin's failure from the next */ });
        }, Promise.resolve());
      })
      .catch(function () { /* manifest unreachable (e.g. static bundle) → no-op */ });
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { loadPlugins: loadPlugins };
  }
  if (typeof document !== 'undefined' && typeof window !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', loadPlugins);
    } else {
      loadPlugins();
    }
  }
})(typeof window !== 'undefined' ? window : globalThis);
