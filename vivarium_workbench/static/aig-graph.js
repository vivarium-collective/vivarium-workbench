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

  function _chainBlockHtml(chain) {
    var claims = _groupClaims(chain);
    if (!claims.length) return '';
    var rows = claims.map(function (c, i) {
      var dots = STAGE_SEQ.map(function (t) {
        return '<span style="color:' + (c.stages[t] ? '#475569' : '#d1d5db') + '">' + GLYPH[t] + '</span>';
      }).join('');
      var badge = '<span style="margin-left:6px;font-size:0.92em;padding:0 6px;border-radius:9999px;' +
        'background:' + (STATUS_COLOR[c.status] || '#e2e8f0') + ';color:#fff">' + _esc(c.status) + '</span>';
      return '<div class="aig-claim-row" data-claim-index="' + i + '" ' +
        'style="display:flex;gap:6px;align-items:flex-start;margin:3px 0;cursor:pointer">' +
        '<span style="flex:none;letter-spacing:1px">' + dots + '</span>' +
        '<span style="flex:1;color:#334155;display:-webkit-box;-webkit-box-orient:vertical;' +
        '-webkit-line-clamp:2;line-clamp:2;overflow:hidden">' + _esc(c.claimText) + '</span>' +
        badge + '</div>';
    }).join('');
    var n = claims.length;
    var header = 'Evidence chain' +
      (chain.derived ? '<span style="font-weight:400;color:#94a3b8"> · derived</span>' : '') +
      (n > 1 ? '<span style="font-weight:400;color:#94a3b8"> (' + n + ' claims)</span>' : '');
    var nViol = (chain.violations || []).length;
    var viol = nViol ? '<div style="margin-top:3px;color:#b45309;font-weight:600">⚠ ' + nViol +
      ' chain gap' + (nViol === 1 ? '' : 's') + '</div>' : '';
    return '<div style="margin-top:8px;padding-top:7px;border-top:1px dashed #e5e7eb;font-size:0.7em;line-height:1.4">' +
      '<div style="font-weight:600;color:#475569;margin-bottom:3px">' + header + '</div>' +
      rows + viol + '</div>';
  }

  global._chainBlockHtml = _chainBlockHtml;
  global._groupClaims = _groupClaims;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { _chainBlockHtml: _chainBlockHtml, _groupClaims: _groupClaims };
  }
})(typeof window !== 'undefined' ? window : globalThis);
