// concept-overlay.js — the §11 Concept view as a resolution of the investigation
// graph (RFC-0004 §2). A "Concepts | Studies" toolbar toggle switches between the
// full Study DAG (with Concept boundaries drawn around each gate's members) and a
// COLLAPSED graph of ~N Concept nodes wired by AGGREGATED edges (collapsed-edge
// rule), re-feeding the existing renderer (window._renderInvestigationDag).
//
// Stability: the native investigation view re-renders the Study DAG on its own
// (relayout, tab change, interaction). To avoid thrashing, each mode is
// SELF-HEALING — the watcher only re-renders when the graph is actually in the
// wrong state (checked cheaply by node count/ids), so a steady view never
// re-renders and there is no flicker. The study + concept render inputs are
// cached once per investigation.
(function (global) {
  'use strict';

  var LAYER_ID = 'concept-boundary-layer';
  var BTN_ID = 'concept-mode-toggle';
  var MODE_KEY = 'viv.concept.mode';

  var _overlay = null;   // { slug, concepts, verdicts, diverges }
  var _studies = null;   // cached study nodes for Studies mode
  var _chains = null;
  var _edges = null;     // cached study_edges (drives both modes)
  var _lastSlug = null;
  var _busy = false;

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function _mode() { try { return localStorage.getItem(MODE_KEY) || 'studies'; } catch (e) { return 'studies'; } }
  function _setMode(m) { try { localStorage.setItem(MODE_KEY, m); } catch (e) { /* ignore */ } }
  function _cssId(s) { return (global.CSS && CSS.escape) ? CSS.escape(s) : s; }

  var VRANK = { refuted: 4, qualified: 3, inconclusive: 2, supported: 1, unassessed: 0 };
  var VGATE = { supported: 'passed', qualified: 'needs_calibration', inconclusive: 'in_progress', refuted: 'failed', unassessed: 'planned' };
  var VCOLOR = { supported: '#1f7a3f', qualified: '#b7791f', inconclusive: '#6b7280', refuted: '#b02a37', unassessed: '#9ca3af' };
  var ORG = { exploring: '#6b7280', consolidated: '#2563eb', reopened: '#b7791f' };

  function _conceptVerdict(members, verdicts) {
    var worst = 'unassessed', wr = -1;
    (members || []).forEach(function (slug) {
      var v = (verdicts[slug] && verdicts[slug].verdict) || 'unassessed';
      if (VRANK[v] > wr) { wr = VRANK[v]; worst = v; }
    });
    return worst;
  }
  function _study2concept(concepts) {
    var m = {};
    (concepts || []).forEach(function (c) { (c.member_studies || []).forEach(function (s) { if (!(s in m)) m[s] = c.id; }); });
    return m;
  }
  function _conceptNodes(overlay) {
    return (overlay.concepts || []).map(function (c) {
      var members = c.member_studies || [];
      var verdict = (c.assessment && c.assessment.verdict) || _conceptVerdict(members, overlay.verdicts);
      var gate = c.from_gate || c.from_act || '';
      return {
        name: c.id, title: (gate ? gate + ' · ' : '') + (c.title || c.id),
        claim: c.synthesis || ('Concept verdict: ' + verdict + ' · ' + members.length + ' studies'),
        gate_status: VGATE[verdict] || 'planned', verdict: verdict, _concept: true,
      };
    });
  }
  function _conceptEdges(studyEdges, s2c) {
    var seen = {}, edges = [];
    (studyEdges || []).forEach(function (e) {
      var src = String(e.source || '').replace(/^study\//, ''), tgt = String(e.target || '').replace(/^study\//, '');
      var cs = s2c[src], ct = s2c[tgt];
      if (cs && ct && cs !== ct) { var k = cs + '->' + ct; if (!seen[k]) { seen[k] = 1; edges.push({ source: cs, target: ct, relation: e.relation || 'leads-to' }); } }
    });
    return edges;
  }

  // ---- boundaries (studies mode) ----------------------------------------------
  function _clearBoundaries() { var l = document.getElementById(LAYER_ID); if (l) l.innerHTML = ''; }
  function _drawBoundaries() {
    var host = document.getElementById('investigation-dag-nodes');
    if (!host || !_overlay || !host.querySelector('.iset-dag-node')) return;
    var layer = document.getElementById(LAYER_ID);
    if (!layer) { layer = document.createElement('div'); layer.id = LAYER_ID; layer.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;z-index:0;'; host.insertBefore(layer, host.firstChild); }
    var PAD = 16, LABEL_H = 20, html = '';
    (_overlay.concepts || []).forEach(function (c) {
      var members = c.member_studies || [], minL = Infinity, minT = Infinity, maxR = -Infinity, maxB = -Infinity, n = 0;
      members.forEach(function (slug) {
        var el = host.querySelector('.iset-dag-node[data-study="' + _cssId(slug) + '"]');
        if (!el) return;
        minL = Math.min(minL, el.offsetLeft); minT = Math.min(minT, el.offsetTop);
        maxR = Math.max(maxR, el.offsetLeft + el.offsetWidth); maxB = Math.max(maxB, el.offsetTop + el.offsetHeight); n++;
      });
      if (!n) return;
      var verdict = (c.assessment && c.assessment.verdict) || _conceptVerdict(members, _overlay.verdicts);
      var tint = ORG[c.org_state] || '#6b7280', gate = c.from_gate || c.from_act || '';
      html += '<div style="position:absolute;left:' + (minL - PAD) + 'px;top:' + (minT - PAD - LABEL_H) + 'px;width:' + (maxR - minL + PAD * 2) + 'px;height:' + (maxB - minT + PAD * 2 + LABEL_H) + 'px;border:1.5px dashed ' + tint + ';border-radius:12px;background:' + tint + '12;box-sizing:border-box;">' +
        '<div style="position:absolute;left:10px;top:-10px;font-size:11px;font-weight:700;background:var(--bg,#fff);padding:0 6px;border-radius:6px;white-space:nowrap;">' +
        (gate ? '<span style="background:#374151;color:#fff;border-radius:7px;padding:0 5px;">' + _esc(gate) + '</span> ' : '') +
        _esc(c.title || c.id) + ' <span style="color:' + (VCOLOR[verdict] || '#999') + ';">' + _esc(verdict) + '</span></div></div>';
    });
    layer.innerHTML = html;
  }

  // ---- render + self-heal -----------------------------------------------------
  function _graphIsConcepts() {
    if (!_overlay || !(_overlay.concepts || []).length) return false;
    var host = document.getElementById('investigation-dag-nodes');
    if (!host) return false;
    if (host.querySelectorAll('.iset-dag-node').length !== _overlay.concepts.length) return false;
    return !!host.querySelector('.iset-dag-node[data-study="' + _cssId(_overlay.concepts[0].id) + '"]');
  }
  function _renderConcepts() {
    if (!global._renderInvestigationDag || !_overlay) return;
    global._renderInvestigationDag(_conceptNodes(_overlay), {}, _conceptEdges(_edges || [], _study2concept(_overlay.concepts)));
    _clearBoundaries();
  }
  function _renderStudies() {
    if (!global._renderInvestigationDag || !_studies) return;
    global._renderInvestigationDag(_studies, _chains || {}, _edges || []);
    setTimeout(_drawBoundaries, 40);
  }
  function _applyMode() {
    if (_mode() === 'concepts') _renderConcepts(); else _renderStudies();
    _syncButton();
  }

  // ---- toolbar toggle ---------------------------------------------------------
  function _ensureButton() {
    var tb = document.querySelector('#investigation-dag-fs .aig-toolbar');
    if (!tb || document.getElementById(BTN_ID)) return;
    var wrap = document.createElement('div');
    wrap.id = BTN_ID;
    wrap.style.cssText = 'display:inline-flex;gap:4px;align-items:center;margin-left:8px;';
    wrap.innerHTML = '<button type="button" data-cm="concepts" class="btn-mini">Concepts</button><button type="button" data-cm="studies" class="btn-mini">Studies</button>';
    wrap.querySelectorAll('button').forEach(function (b) {
      b.addEventListener('click', function () { _setMode(b.getAttribute('data-cm')); _applyMode(); });
    });
    tb.appendChild(wrap);
    _syncButton();
  }
  function _syncButton() {
    var wrap = document.getElementById(BTN_ID); if (!wrap) return;
    var m = _mode();
    wrap.querySelectorAll('button').forEach(function (b) {
      var on = b.getAttribute('data-cm') === m;
      b.style.cssText = 'font-weight:' + (on ? '700' : '400') + ';' + (on ? 'background:#2563eb;color:#fff;' : '') + 'border-radius:6px;padding:2px 8px;';
    });
  }

  // ---- watcher ----------------------------------------------------------------
  function _tick() {
    var slug = global._currentIsetSlug || global._currentIset || null;
    var fs = document.getElementById('investigation-dag-fs');
    if (!fs || fs.offsetParent === null || !slug) { _clearBoundaries(); return; }
    _ensureButton();

    if (slug !== _lastSlug && !_busy) {
      _busy = true; _lastSlug = slug; _overlay = _studies = _edges = _chains = null;
      Promise.all([
        global.DataSource.loadConceptOverlay(slug),
        global.DataSource.loadInvestigation(slug),
        global.DataSource.loadInvestigationGraph(slug),
      ]).then(function (r) {
        _busy = false;
        var ov = r[0] || {}, inv = r[1] || {}, g = r[2] || {};
        var dv = {}; (ov.diverges || []).forEach(function (s) { dv[s] = true; });
        _overlay = { slug: slug, concepts: ov.concepts || [], verdicts: ov.study_verdicts || {}, diverges: dv };
        _studies = inv.studies || (inv.investigation && inv.investigation.studies) || [];
        _chains = g.chains || {};
        _edges = g.study_edges || [];
        _applyMode();
      }).catch(function () { _busy = false; });
      return;
    }

    // same investigation: enforce the current mode, but ONLY re-render when the
    // graph is actually in the wrong state (no steady-state churn → no flicker).
    if (_busy) return;
    if (_mode() === 'concepts') {
      if (!_graphIsConcepts()) _renderConcepts();
    } else {
      if (_graphIsConcepts()) _renderStudies();   // native hasn't reclaimed it yet
      else _drawBoundaries();                       // keep boundaries aligned
    }
  }

  global.ConceptOverlay = { apply: _applyMode };

  if (typeof document !== 'undefined') {
    var start = function () { setInterval(_tick, 800); global.addEventListener('resize', function () { if (_mode() === 'studies') _drawBoundaries(); }); _tick(); };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }
})(typeof window !== 'undefined' ? window : globalThis);
