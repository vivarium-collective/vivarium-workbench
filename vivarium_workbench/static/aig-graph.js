// vivarium_workbench/static/aig-graph.js
// Phase B4 (superset): render a study's typed evidence chain as a compact block
// that _renderInvestigationDag appends INSIDE the study card (so it is measured
// and stacked with the card and never collides). Pure string builder — node-testable.
(function (global) {
  'use strict';

  var GLYPH = { finding: '●', evidence: '◆', decision: '▣', conclusion: '★' };
  var STATUS_COLOR = { published: '#2563eb', accepted: '#0d9488', refuted: '#e11d48',
                       partial: '#d97706', pending: '#94a3b8' };
  var STAGE_SEQ = ['finding', 'evidence', 'decision', 'conclusion'];
  var _INTRA = { cites: 1, decides: 1, concludes: 1, via: 1 };

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function _claimStatus(parts) {
    if (parts.conclusion && parts.conclusion.lifecycle_state === 'published') return 'published';
    if ((parts.decision && parts.decision.outcome === 'reject') ||
        (parts.evidence && parts.evidence.lifecycle_state === 'rejected')) return 'refuted';
    if (parts.decision && parts.decision.outcome === 'accept') return 'accepted';
    if (parts.decision && parts.decision.outcome === 'defer') return 'partial';
    return 'pending';
  }

  // Group chain nodes into claims = connected components over cites/decides/concludes/via.
  function _groupClaims(chain) {
    var nodes = (chain && chain.nodes) || [];
    if (!nodes.length) return [];
    var byId = {}, parent = {};
    nodes.forEach(function (n) { byId[n.id] = n; parent[n.id] = n.id; });
    function find(x) { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; }
    ((chain && chain.edges) || []).forEach(function (e) {
      if (_INTRA[e.rel] && byId[e.source] && byId[e.target]) parent[find(e.source)] = find(e.target);
    });
    var groups = {};
    nodes.forEach(function (n) { var r = find(n.id); (groups[r] = groups[r] || []).push(n); });
    var claims = Object.keys(groups).map(function (r) {
      var comp = groups[r];
      var parts = { finding: null, evidence: null, decision: null, conclusion: null };
      comp.forEach(function (n) { if (n.type in parts && parts[n.type] === null) parts[n.type] = n; });
      var stages = { finding: !!parts.finding, evidence: !!parts.evidence,
                     decision: !!parts.decision, conclusion: !!parts.conclusion };
      var first = parts.finding || parts.conclusion || parts.evidence || comp[0];
      var claimText = (parts.finding && parts.finding.statement) ||
                      (parts.conclusion && parts.conclusion.statement) ||
                      (parts.evidence && parts.evidence.statement) ||
                      (comp[0].label || comp[0].statement || comp[0].id);
      var source = (first && first.source) ||
                   (comp[0] && comp[0].source) || '';
      return { parts: parts, stages: stages, claimText: claimText, status: _claimStatus(parts),
               source: source, nodeIds: comp.map(function (n) { return n.id; }),
               _sk: (parts.finding || comp[0]).id };
    });
    claims.sort(function (a, b) { return a._sk < b._sk ? -1 : (a._sk > b._sk ? 1 : 0); });
    claims.forEach(function (c) { delete c._sk; });
    return claims;
  }

  // Human labels for the four chain stages — used by the glyph tooltips, the
  // per-card legend, and the claim drawer, so the symbols are never unexplained.
  var STAGE_LABEL = { finding: 'Finding', evidence: 'Evidence', decision: 'Decision', conclusion: 'Conclusion' };

  function _chainBlockHtml(chain) {
    var claims = _groupClaims(chain);
    if (!claims.length) return '';
    var rows = claims.map(function (c, i) {
      var dots = STAGE_SEQ.map(function (t) {
        var on = c.stages[t];
        return '<span title="' + STAGE_LABEL[t] + (on ? ' — recorded' : ' — missing') +
          '" style="color:' + (on ? '#475569' : '#d1d5db') + '">' + GLYPH[t] + '</span>';
      }).join('');
      var badge = '<span title="Claim status: ' + _esc(c.status) + '" style="margin-left:6px;font-size:0.92em;padding:0 6px;border-radius:9999px;' +
        'background:' + (STATUS_COLOR[c.status] || '#e2e8f0') + ';color:#fff">' + _esc(c.status) + '</span>';
      // The whole row is clickable → opens the claim drawer (full text + each
      // stage labelled). title= gives the full claim text on hover too.
      return '<div class="aig-claim-row" data-claim-index="' + i + '" title="' + _esc(c.claimText) + '\u000a(click for the full evidence chain)" ' +
        'style="display:flex;gap:6px;align-items:flex-start;margin:3px 0;cursor:pointer">' +
        '<span class="aig-claim-dots" aria-hidden="true" style="flex:none;letter-spacing:1px">' + dots + '</span>' +
        '<span class="aig-claim-text" style="flex:1;color:#334155;display:-webkit-box;-webkit-box-orient:vertical;' +
        '-webkit-line-clamp:2;line-clamp:2;overflow:hidden">' + _esc(c.claimText) + '</span>' +
        badge + '</div>';
    }).join('');
    var n = claims.length;
    var header = 'Evidence chain' +
      (chain.derived ? '<span style="font-weight:400;color:#94a3b8"> · derived</span>' : '') +
      (n > 1 ? '<span style="font-weight:400;color:#94a3b8"> (' + n + ' claims)</span>' : '');
    // Legend: the four stages a claim can record, in order, so the glyphs read.
    var legend = '<div style="font-weight:400;color:#94a3b8;margin-bottom:4px;letter-spacing:.2px">' +
      STAGE_SEQ.map(function (t) {
        return '<span style="color:#475569">' + GLYPH[t] + '</span> ' + STAGE_LABEL[t].toLowerCase();
      }).join(' ') + ' · filled = recorded · click a claim for detail</div>';
    var nViol = (chain.violations || []).length;
    var viol = nViol ? '<div style="margin-top:3px;color:#b45309;font-weight:600">⚠ ' + nViol +
      ' chain gap' + (nViol === 1 ? '' : 's') + '</div>' : '';
    return '<div style="margin-top:8px;padding-top:7px;border-top:1px dashed #e5e7eb;font-size:0.7em;line-height:1.4">' +
      '<div style="font-weight:600;color:#475569;margin-bottom:2px">' + header + '</div>' +
      legend + rows + viol + '</div>';
  }

  // One labeled row of the Methods · Simulations block (label muted → value).
  function _mRow(label, valueHtml) {
    return '<div class="aig-methods-row" style="display:flex;gap:6px;align-items:flex-start;margin:2px 0">' +
      '<span class="aig-methods-label" style="flex:none;color:#64748b">' + _esc(label) + '</span>' +
      '<span class="aig-methods-val" style="flex:1;color:#334155">' + valueHtml + '</span></div>';
  }

  // "Methods · Simulations" study-card block (band-2 semantic zoom). Pure string
  // builder — node-testable. Reads the backend projection fields on the study
  // node (baseline_composite / baseline_source, methods, fidelity, runs,
  // behavior_tests). Each row is omitted when its data is absent; the whole
  // block renders nothing when there is nothing to show (no empty block).
  function _methodsBlockHtml(study) {
    study = study || {};
    var rows = [];

    // 1. Model: baseline composite name + a fidelity chip.
    var model = study.baseline_source || '';
    if (!model && study.baseline_composite) {
      var segs = String(study.baseline_composite).split('.');
      model = segs[segs.length - 1];
    }
    var fid = study.fidelity || '';
    var chip = '';
    if (fid === 'real') {
      chip = ' <span class="aig-fidelity-chip aig-fidelity-real" ' +
        'title="Wraps a genuine simulator">real tool</span>';
    } else if (fid === 'reproduction') {
      chip = ' <span class="aig-fidelity-chip aig-fidelity-repro" ' +
        'title="Coarse-grained reproduction — not a wrapped engine">reproduction</span>';
    }
    if (model || chip) {
      rows.push(_mRow('Model',
        '<span class="aig-methods-model">' + _esc(model) + '</span>' + chip));
    }

    // 2. Methods: the modeling-method tags, joined by " · ".
    var methods = (study.methods || []).filter(function (m) {
      return m != null && String(m).length;
    });
    if (methods.length) {
      rows.push(_mRow('Methods', _esc(methods.join(' · '))));
    }

    // 3. Sims: <count> run(s) · <status> · <n_steps> steps (omit missing pieces).
    var runs = study.runs || {};
    var sim = [];
    var cnt = runs.count;
    if (cnt != null && cnt !== 0 && cnt !== '') {
      sim.push(_esc(cnt) + ' run' + (Number(cnt) === 1 ? '' : 's'));
    }
    if (runs.status) sim.push(_esc(runs.status));
    if (runs.n_steps != null && runs.n_steps !== '') sim.push(_esc(runs.n_steps) + ' steps');
    if (sim.length) {
      rows.push(_mRow('Sims', sim.join(' · ')));
    }

    // 4. Tests: stage-style glyphs + "<passed>/<total> passed", colored by
    // STATUS_COLOR (green all-pass, amber some-pass, red none-pass).
    var bt = study.behavior_tests || {};
    var total = Number(bt.total) || 0;
    if (total > 0) {
      var passed = Number(bt.passed) || 0;
      var color = passed >= total ? STATUS_COLOR.accepted
        : (passed > 0 ? STATUS_COLOR.partial : STATUS_COLOR.refuted);
      var glyphs = (bt.tests || []).slice(0, 12).map(function (t) {
        var on = !!(t && t.passed);
        return '<span title="' + _esc((t && t.name) || '') + (on ? ' — passed' : ' — not passed') +
          '" style="color:' + (on ? STATUS_COLOR.accepted : STATUS_COLOR.refuted) + '">' +
          (on ? '●' : '○') + '</span>';
      }).join('');
      rows.push(_mRow('Tests',
        '<span style="letter-spacing:1px">' + glyphs + '</span>' +
        '<span style="color:' + color + ';font-weight:600"> ' + passed + '/' + total + ' passed</span>'));
    }

    if (!rows.length) return '';
    return '<div class="aig-methods-block" style="margin-top:8px;padding-top:7px;' +
      'border-top:1px dashed #e5e7eb;font-size:0.7em;line-height:1.4">' +
      '<div class="aig-methods-head" style="font-weight:600;color:#475569;' +
      'margin-bottom:2px">Methods · Simulations</div>' +
      rows.join('') + '</div>';
  }

  function _layoutOptsForBand(band) {
    var b = Math.max(0, Math.min(2, band | 0));
    // asksFull + plan turn ON only at the nearest band (full magnification):
    // the card then shows the FULL question text and a "Plan" layer (declared
    // contract, proposed mechanisms / models & methods, success criteria).
    var BANDS = [
      { band: 0, cls: 'aig-zoom-far',  cardW: 150, xGap: 40, asks: false, finds: false, chain: false, followups: false, asksFull: false, plan: false },
      { band: 1, cls: 'aig-zoom-mid',  cardW: 210, xGap: 64, asks: true,  finds: true,  chain: false, followups: false, asksFull: false, plan: false },
      { band: 2, cls: 'aig-zoom-near', cardW: 360, xGap: 72, asks: true,  finds: true,  chain: true,  followups: true,  methods: true, asksFull: true, plan: true },
    ];
    return BANDS[b];
  }

  // Auto orientation heuristic for the investigation DAG (_renderInvestigationDag
  // in walkthrough.js). Pure + unit-testable: given a map of {depth: countAtThatDepth}
  // (depths 0..N, 0 = roots), pick the orientation that fills the available space
  // best for the graph's SHAPE rather than always defaulting to one direction.
  //   - WIDE/SHALLOW graphs (many roots / few depth levels, e.g. a mostly-unlinked
  //     investigation with 12 studies all at depth 0) go "TB" (top-to-bottom): rows
  //     stack downward so a wide single row doesn't get squished into one column.
  //   - DEEP/NARROW graphs (long dependency chains, few studies per depth) go "LR"
  //     (left-to-right): a long chain reads naturally as a left->right sequence.
  // A manual per-investigation toggle (persisted in localStorage) overrides this
  // default entirely — see _getStoredGraphOrientation/_setGraphOrientation in
  // walkthrough.js.
  function chooseGraphOrientation(depthCounts) {
    var counts = depthCounts || {};
    var levels = Object.keys(counts);
    var maxDepth = levels.length;               // number of distinct depth levels present
    var maxBreadth = 0;
    levels.forEach(function (d) {
      var n = counts[d] || 0;
      if (n > maxBreadth) maxBreadth = n;
    });
    var nRoots = counts[0] || counts['0'] || 0;  // depth-0 (root) study count
    // Wide/shallow -> TB: few levels, or some level wider than the graph is deep.
    if (maxDepth <= 2 || maxBreadth > maxDepth) return 'TB';
    // Deep/narrow -> LR: more levels than the widest level.
    if (maxDepth > maxBreadth) return 'LR';
    // Tie (equally deep and wide, both > 2): more roots reads as a wider overall
    // shape, so lean TB; otherwise a single long chain reads better as LR.
    return nRoots > 1 ? 'TB' : 'LR';
  }

  global._chainBlockHtml = _chainBlockHtml;
  global._methodsBlockHtml = _methodsBlockHtml;
  global._groupClaims = _groupClaims;
  global._layoutOptsForBand = _layoutOptsForBand;
  global.chooseGraphOrientation = chooseGraphOrientation;
  global.STATUS_COLOR = STATUS_COLOR;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      _chainBlockHtml: _chainBlockHtml,
      _methodsBlockHtml: _methodsBlockHtml,
      _groupClaims: _groupClaims,
      _layoutOptsForBand: _layoutOptsForBand,
      chooseGraphOrientation: chooseGraphOrientation,
      STATUS_COLOR: STATUS_COLOR,
    };
  }
})(typeof window !== 'undefined' ? window : globalThis);
