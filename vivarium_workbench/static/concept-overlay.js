// concept-overlay.js — renders the derived Concept overlay (the formalized
// investigation-graph grouping layer) as a card strip above the investigation
// graph. Purely additive: fetches GET /api/concept-overlay via
// DataSource.loadConceptOverlay and mounts #concept-overlay-strip into
// #iset-workspace, above #investigation-dag-fs. No edits to the DAG renderer —
// a lightweight watcher re-renders when the active investigation changes.
(function (global) {
  'use strict';

  var MOUNT_ID = 'concept-overlay-strip';
  var _lastSlug = null;
  var _busy = false;

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // Canonical verdict -> {label, bg, fg}. Theme-neutral, legible in light+dark.
  var VERDICT = {
    supported:    { bg: '#1f7a3f', fg: '#fff', dot: '✓' },
    qualified:    { bg: '#b7791f', fg: '#fff', dot: '≈' },
    inconclusive: { bg: '#6b7280', fg: '#fff', dot: '–' },
    refuted:      { bg: '#b02a37', fg: '#fff', dot: '✗' },
    unassessed:   { bg: '#9ca3af', fg: '#111', dot: '·' }
  };
  var ORG = {
    exploring:    { label: 'exploring',    bg: '#6b7280' },
    consolidated: { label: 'consolidated', bg: '#2563eb' },
    reopened:     { label: 'reopened',     bg: '#b7791f' }
  };

  function _chip(text, bg, fg) {
    return '<span style="display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;' +
      'font-weight:600;background:' + bg + ';color:' + (fg || '#fff') + ';">' + _esc(text) + '</span>';
  }

  function _verdictChip(v) {
    var m = VERDICT[v] || VERDICT.unassessed;
    return _chip((m.dot + ' ' + (v || 'unassessed')), m.bg, m.fg);
  }

  function _memberRow(slug, detail, diverges) {
    var v = (detail && detail.verdict) || 'unassessed';
    var row = '<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">' +
      '<span style="font-family:ui-monospace,monospace;font-size:12px;opacity:.9;">' + _esc(slug) + '</span>' +
      _verdictChip(v);
    if (diverges) {
      var a = detail && detail.authored, c = detail && detail.coded;
      row += '<span title="computed verdict diverges from the authored one" ' +
        'style="font-size:11px;color:#b02a37;font-weight:600;">⚠ diverges' +
        (a && c ? ' (authored ' + _esc(a) + ' vs coded ' + _esc(c) + ')' : '') + '</span>';
    }
    return row + '</div>';
  }

  function _conceptCard(c, verdicts, divergeSet) {
    var org = ORG[c.org_state] || null;
    var gate = c.from_gate || c.from_act || '';
    var head = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">' +
      '<strong style="font-size:13px;">' + _esc(c.title || c.id) + '</strong>' +
      (gate ? _chip('gate ' + gate, '#374151', '#fff') : '') +
      (org ? _chip(org.label, org.bg, '#fff') : '') +
      '<span style="margin-left:auto;font-size:11px;opacity:.65;">' +
      ((c.member_studies || []).length) + ' studies</span></div>';
    var members = (c.member_studies || []).map(function (slug) {
      return _memberRow(slug, verdicts[slug], divergeSet[slug]);
    }).join('');
    return '<div style="flex:0 0 240px;border:1px solid var(--border,#d1d5db);border-radius:10px;' +
      'padding:10px 12px;background:var(--card,rgba(127,127,127,.06));">' + head + members + '</div>';
  }

  function _mount() {
    var ws = document.getElementById('investigation-detail-view');
    if (!ws) return null;
    var strip = document.getElementById(MOUNT_ID);
    if (!strip) {
      strip = document.createElement('div');
      strip.id = MOUNT_ID;
      // Place the strip just above the detail tab bar (Verdict / Background / …),
    // i.e. below the investigation overview, visible without scrolling.
      var tabs = ws.querySelector('.investigation-detail-tabs');
      if (tabs && tabs.parentNode) tabs.parentNode.insertBefore(strip, tabs);
      else ws.insertBefore(strip, ws.firstChild);
    }
    return strip;
  }

  function _renderInto(strip, overlay) {
    var concepts = (overlay && overlay.concepts) || [];
    var verdicts = (overlay && overlay.study_verdicts) || {};
    var divergeSet = {};
    ((overlay && overlay.diverges) || []).forEach(function (s) { divergeSet[s] = true; });
    if (!concepts.length) { strip.innerHTML = ''; strip.style.display = 'none'; return; }
    var nDiv = (overlay.diverges || []).length;
    strip.style.display = '';
    strip.innerHTML =
      '<div style="margin:10px 0 6px;display:flex;align-items:center;gap:10px;">' +
      '<strong style="font-size:12px;letter-spacing:.04em;text-transform:uppercase;opacity:.75;">Concept overlay</strong>' +
      '<span style="font-size:11px;opacity:.6;">derived · ' + concepts.length + ' concepts' +
      (nDiv ? ' · <span style="color:#b02a37;font-weight:600;">' + nDiv + ' divergent</span>' : '') + '</span>' +
      '</div>' +
      '<div style="display:flex;gap:10px;overflow-x:auto;padding-bottom:6px;">' +
      concepts.map(function (c) { return _conceptCard(c, verdicts, divergeSet); }).join('') +
      '</div>';
  }

  function render(slug) {
    if (!slug || !global.DataSource || !global.DataSource.loadConceptOverlay) return;
    var strip = _mount();
    if (!strip) return;
    _busy = true;
    global.DataSource.loadConceptOverlay(slug).then(function (overlay) {
      _busy = false;
      if ((global._currentIsetSlug || global._currentIset) !== slug) return; // changed mid-fetch
      _renderInto(strip, overlay);
    }).catch(function () { _busy = false; /* degrade silently */ });
  }

  // Watch the active investigation; (re)render the overlay when it changes and the
  // investigation workspace is on screen. Cheap poll — a dev-grade reactive seam.
  function _watch() {
    var slug = global._currentIsetSlug || global._currentIset || null;
    var ws = document.getElementById('investigation-detail-view');
    var onInvView = ws && ws.offsetParent !== null;
    if (!onInvView) {
      var strip = document.getElementById(MOUNT_ID);
      if (strip) strip.style.display = 'none';
      _lastSlug = null;
      return;
    }
    if (slug && slug !== _lastSlug && !_busy) {
      _lastSlug = slug;
      render(slug);
    }
  }

  global.ConceptOverlay = { render: render };

  if (typeof document !== 'undefined') {
    var start = function () { setInterval(_watch, 700); _watch(); };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }
})(typeof window !== 'undefined' ? window : globalThis);
