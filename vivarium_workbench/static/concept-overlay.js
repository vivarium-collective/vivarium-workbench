// concept-overlay.js — the §11 Concept view, as a resolution of the investigation
// graph itself (RFC-0004 §2, formalization §11). A "Concepts | Studies" toggle in
// the graph toolbar switches between the full Study DAG and a COLLAPSED graph of
// ~N Concept nodes wired by AGGREGATED edges (the collapsed-edge rule), re-feeding
// the existing renderer (window._renderInvestigationDag). In Studies mode a labeled
// Concept boundary is drawn around each gate's member nodes. Membership + verdicts
// come from GET /api/concept-overlay (DataSource.loadConceptOverlay).
(function (global) {
  'use strict';

  var LAYER_ID = 'concept-boundary-layer';
  var BTN_ID = 'concept-mode-toggle';
  var MODE_KEY = 'viv.concept.mode';
  var _overlay = null;     // cached { slug, concepts, verdicts, diverges }
  var _lastSlug = null;
  var _busy = false;

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function _mode() { try { return localStorage.getItem(MODE_KEY) || 'studies'; } catch (e) { return 'studies'; } }
  function _setMode(m) { try { localStorage.setItem(MODE_KEY, m); } catch (e) { /* ignore */ } }

  var VRANK = { refuted: 4, qualified: 3, inconclusive: 2, supported: 1, unassessed: 0 };
  var VGATE = { supported: 'passed', qualified: 'needs_calibration', inconclusive: 'in_progress',
                refuted: 'failed', unassessed: 'planned' };
  var VCOLOR = { supported: '#1f7a3f', qualified: '#b7791f', inconclusive: '#6b7280',
                 refuted: '#b02a37', unassessed: '#9ca3af' };
  var ORG = { exploring: '#6b7280', consolidated: '#2563eb', reopened: '#b7791f' };

  function _conceptVerdict(members, verdicts) {
    var worst = 'unassessed', wr = -1;
    (members || []).forEach(function (slug) {
      var v = (verdicts[slug] && verdicts[slug].verdict) || 'unassessed';
      if (VRANK[v] > wr) { wr = VRANK[v]; worst = v; }
    });
    return worst;
  }

  // ---- Collapsed Concept graph: synthesize Concept nodes + aggregated edges ----

  function _study2concept(concepts) {
    var m = {};
    (concepts || []).forEach(function (c) {
      (c.member_studies || []).forEach(function (s) { if (!(s in m)) m[s] = c.id; });
    });
    return m;
  }

  function _conceptNodes(overlay) {
    return (overlay.concepts || []).map(function (c) {
      var members = c.member_studies || [];
      var verdict = (c.assessment && c.assessment.verdict) || _conceptVerdict(members, overlay.verdicts);
      var gate = c.from_gate || c.from_act || '';
      return {
        name: c.id,
        title: (gate ? gate + ' · ' : '') + (c.title || c.id),
        claim: c.synthesis || ('Concept verdict: ' + verdict + ' · ' + members.length + ' studies'),
        gate_status: VGATE[verdict] || 'planned',
        verdict: verdict,
        _concept: true,
      };
    });
  }

  function _conceptEdges(studyEdges, s2c) {
    var seen = {}, edges = [];
    (studyEdges || []).forEach(function (e) {
      var src = String(e.source || '').replace(/^study\//, '');
      var tgt = String(e.target || '').replace(/^study\//, '');
      var cs = s2c[src], ct = s2c[tgt];
      if (cs && ct && cs !== ct) {
        var key = cs + '->' + ct;
        if (!seen[key]) { seen[key] = 1; edges.push({ source: cs, target: ct, relation: e.relation || 'leads-to' }); }
      }
    });
    return edges;
  }

  // ---- Studies-mode boundaries (grouping the real nodes) ----------------------

  function _nodeHost() { return document.getElementById('investigation-dag-nodes'); }

  function _clearBoundaries() { var l = document.getElementById(LAYER_ID); if (l) l.innerHTML = ''; }

  function _drawBoundaries() {
    var host = _nodeHost();
    if (!host || !_overlay || !host.querySelector('.iset-dag-node')) return;
    var layer = document.getElementById(LAYER_ID);
    if (!layer) {
      layer = document.createElement('div');
      layer.id = LAYER_ID;
      layer.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;z-index:0;';
      host.insertBefore(layer, host.firstChild);
    }
    var PAD = 16, LABEL_H = 20, html = '';
    (_overlay.concepts || []).forEach(function (c) {
      var members = c.member_studies || [];
      var minL = Infinity, minT = Infinity, maxR = -Infinity, maxB = -Infinity, n = 0;
      members.forEach(function (slug) {
        var el = host.querySelector('.iset-dag-node[data-study="' + (global.CSS && CSS.escape ? CSS.escape(slug) : slug) + '"]');
        if (!el) return;
        minL = Math.min(minL, el.offsetLeft); minT = Math.min(minT, el.offsetTop);
        maxR = Math.max(maxR, el.offsetLeft + el.offsetWidth); maxB = Math.max(maxB, el.offsetTop + el.offsetHeight); n++;
      });
      if (!n) return;
      var verdict = (c.assessment && c.assessment.verdict) || _conceptVerdict(members, _overlay.verdicts);
      var tint = ORG[c.org_state] || '#6b7280', gate = c.from_gate || c.from_act || '';
      html += '<div style="position:absolute;left:' + (minL - PAD) + 'px;top:' + (minT - PAD - LABEL_H) +
        'px;width:' + (maxR - minL + PAD * 2) + 'px;height:' + (maxB - minT + PAD * 2 + LABEL_H) +
        'px;border:1.5px dashed ' + tint + ';border-radius:12px;background:' + tint + '12;box-sizing:border-box;">' +
        '<div style="position:absolute;left:10px;top:-10px;font-size:11px;font-weight:700;background:var(--bg,#fff);padding:0 6px;border-radius:6px;white-space:nowrap;">' +
        (gate ? '<span style="background:#374151;color:#fff;border-radius:7px;padding:0 5px;">' + _esc(gate) + '</span> ' : '') +
        _esc(c.title || c.id) + ' <span style="color:' + (VCOLOR[verdict] || '#999') + ';">' + _esc(verdict) + '</span></div></div>';
    });
    layer.innerHTML = html;
  }

  // ---- Mode rendering ---------------------------------------------------------

  function _applyMode(slug) {
    if (!global._renderInvestigationDag || !global.DataSource) return;
    var DS = global.DataSource;
    if (_mode() === 'concepts' && _overlay && (_overlay.concepts || []).length) {
      _clearBoundaries();
      DS.loadInvestigationGraph(slug).then(function (g) {
        var s2c = _study2concept(_overlay.concepts);
        var nodes = _conceptNodes(_overlay);
        var edges = _conceptEdges((g && g.study_edges) || [], s2c);
        global._renderInvestigationDag(nodes, {}, edges);
      }).catch(function () { /* ignore */ });
    } else {
      // Studies mode: restore the full study graph, then draw boundaries over it.
      Promise.all([DS.loadInvestigation(slug), DS.loadInvestigationGraph(slug)]).then(function (r) {
        var inv = r[0] || {}, g = r[1] || {};
        var studies = inv.studies || (inv.investigation && inv.investigation.studies) || [];
        global._renderInvestigationDag(studies, (g && g.chains) || {}, (g && g.study_edges) || []);
        setTimeout(_drawBoundaries, 50);
      }).catch(function () { setTimeout(_drawBoundaries, 50); });
    }
    _syncButton();
  }

  // ---- Toolbar toggle ---------------------------------------------------------

  function _ensureButton() {
    var tb = document.querySelector('#investigation-dag-fs .aig-toolbar');
    if (!tb || document.getElementById(BTN_ID)) return;
    var wrap = document.createElement('div');
    wrap.id = BTN_ID;
    wrap.style.cssText = 'display:inline-flex;gap:4px;align-items:center;margin-left:8px;';
    wrap.innerHTML =
      '<button type="button" data-cm="concepts" class="btn-mini">Concepts</button>' +
      '<button type="button" data-cm="studies" class="btn-mini">Studies</button>';
    wrap.querySelectorAll('button').forEach(function (b) {
      b.addEventListener('click', function () {
        _setMode(b.getAttribute('data-cm'));
        var slug = global._currentIsetSlug || global._currentIset;
        if (slug) _applyMode(slug);
      });
    });
    tb.appendChild(wrap);
    _syncButton();
  }

  function _syncButton() {
    var wrap = document.getElementById(BTN_ID);
    if (!wrap) return;
    var m = _mode();
    wrap.querySelectorAll('button').forEach(function (b) {
      var on = b.getAttribute('data-cm') === m;
      b.style.cssText = 'font-weight:' + (on ? '700' : '400') + ';' +
        (on ? 'background:#2563eb;color:#fff;' : '') + 'border-radius:6px;padding:2px 8px;';
    });
  }

  // ---- Watcher ----------------------------------------------------------------

  function _tick() {
    var slug = global._currentIsetSlug || global._currentIset || null;
    var fs = document.getElementById('investigation-dag-fs');
    if (!fs || fs.offsetParent === null || !slug) { _clearBoundaries(); return; }
    _ensureButton();
    if (slug !== _lastSlug && !_busy) {
      _busy = true; _lastSlug = slug;
      global.DataSource.loadConceptOverlay(slug).then(function (ov) {
        _busy = false;
        var dv = {}; ((ov && ov.diverges) || []).forEach(function (s) { dv[s] = true; });
        _overlay = { slug: slug, concepts: (ov && ov.concepts) || [], verdicts: (ov && ov.study_verdicts) || {}, diverges: dv };
        _applyMode(slug);
      }).catch(function () { _busy = false; });
    } else if (_mode() === 'studies') {
      _drawBoundaries();   // keep boundaries aligned as the study graph relayouts
    }
  }

  global.ConceptOverlay = { apply: function () { var s = global._currentIsetSlug || global._currentIset; if (s) _applyMode(s); } };

  if (typeof document !== 'undefined') {
    var start = function () { setInterval(_tick, 700); global.addEventListener('resize', function () { if (_mode() === 'studies') _drawBoundaries(); }); _tick(); };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }
})(typeof window !== 'undefined' ? window : globalThis);
