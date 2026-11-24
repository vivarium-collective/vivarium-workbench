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

  function renderInto(el, entryName) {
    var fn = global[entryName];
    if (typeof fn === 'function') {
      try { fn(el); } catch (e) { /* a plugin panel's render must not break the page */ }
    }
  }

  function makeLauncher(spec) {
    var rail = document.querySelector('.viv-rail');
    if (!rail || !rail.appendChild) return null;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'viv-rail-item viv-plugin-rail-item';
    btn.dataset.pluginPanel = spec.id;
    btn.dataset.pluginGroup = spec.group || '';
    btn.title = spec.title;
    btn.textContent = spec.title;
    rail.appendChild(btn);
    return btn;
  }

  function makePanel(spec) {
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

    var launcher = makeLauncher(spec);
    if (launcher && launcher.addEventListener) {
      launcher.addEventListener('click', function () { if (ctl && ctl.open) ctl.open(); });
    }

    var ctl = dock.make({
      panel: panel, layout: layout, mainEl: mainEl,
      key: spec.id, label: spec.title, launcher: launcher,
      docks: ['left', 'right', 'bottom', 'top'],
      defaultDock: spec.side || 'left',
      onOpen: function () { renderInto(bodyEl, spec.entry); },
    });
    renderInto(bodyEl, spec.entry);
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
        try { makePanel(spec); } catch (e) { /* one panel must not break the rest */ }
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
