// vivarium_dashboard/static/aig-graph.js
// Phase B4 (superset): render a study's typed evidence chain as a compact block
// that _renderInvestigationDag appends INSIDE the study card (so it is measured
// and stacked with the card and never collides). Pure string builder — node-testable.
(function (global) {
  'use strict';

  var GLYPH = { finding: '●', evidence: '◆', decision: '▣', conclusion: '★' };
  var TYPE_ORDER = { finding: 0, evidence: 1, decision: 2, conclusion: 3 };
  var TYPE_LABEL = { finding: 'finding', evidence: 'evidence', decision: 'decision', conclusion: 'conclusion' };
  var LIFE_COLOR = { proposed: '#94a3b8', asserted: '#64748b', accepted: '#0d9488',
                     rejected: '#e11d48', recorded: '#7c3aed', draft: '#94a3b8', published: '#2563eb' };

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // chain: {nodes:[{id,type,label,lifecycle_state}], edges, violations:[...]} | undefined
  // Returns '' when there is no chain (graceful: the card stays byte-identical to today),
  // else an indented block listing the chain nodes in finding->evidence->decision->
  // conclusion order with lifecycle badges, plus a violations marker.
  function _chainBlockHtml(chain) {
    if (!chain || !chain.nodes || !chain.nodes.length) return '';
    var nodes = chain.nodes.slice().sort(function (a, b) {
      var da = TYPE_ORDER[a.type] !== undefined ? TYPE_ORDER[a.type] : 9;
      var db = TYPE_ORDER[b.type] !== undefined ? TYPE_ORDER[b.type] : 9;
      return da !== db ? da - db : (a.id < b.id ? -1 : 1);
    });
    var rows = nodes.map(function (n) {
      var life = n.lifecycle_state
        ? '<span style="margin-left:5px;font-size:0.9em;padding:0 5px;border-radius:9999px;' +
          'background:' + (LIFE_COLOR[n.lifecycle_state] || '#e2e8f0') + ';color:#fff">' +
          _esc(n.lifecycle_state) + '</span>'
        : '';
      return '<div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' +
        '<span style="color:#475569">' + (GLYPH[n.type] || '•') + '</span> ' +
        '<span style="color:#64748b">' + _esc(TYPE_LABEL[n.type] || n.type) + '</span>' +
        life + '</div>';
    }).join('');
    var nViol = (chain.violations || []).length;
    var viol = nViol
      ? '<div style="margin-top:3px;color:#b45309;font-weight:600">⚠ ' + nViol +
        ' chain gap' + (nViol === 1 ? '' : 's') + '</div>'
      : '';
    return '<div style="margin-top:8px;padding-top:7px;border-top:1px dashed #e5e7eb;' +
      'font-size:0.7em;line-height:1.5">' +
      '<div style="font-weight:600;color:#475569;margin-bottom:2px">Evidence chain</div>' +
      rows + viol + '</div>';
  }

  global._chainBlockHtml = _chainBlockHtml;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { _chainBlockHtml: _chainBlockHtml };
  }
})(typeof window !== 'undefined' ? window : globalThis);
