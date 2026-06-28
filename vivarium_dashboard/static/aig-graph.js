// vivarium_dashboard/static/aig-graph.js
// Phase B4: render the typed Actionable Investigation Graph (study DAG + each
// study's Finding/Evidence/Decision/Conclusion chain). Self-contained module —
// no walkthrough.js internals. _aigLayout is pure (node-testable); _renderAigGraph
// paints into the existing #investigation-dag-nodes / #investigation-dag-edges.
(function (global) {
  'use strict';

  var ROW_H = 150;   // vertical gap between study depths
  var COL_W = 260;   // horizontal gap between studies at one depth
  var X0 = 40, Y0 = 40;
  var CL_DX = 30;    // chain cluster x-offset from its study
  var CL_DY = 70;    // chain cluster y-offset (below the study)
  var CL_ROW = 34;   // vertical gap between chain nodes
  var TYPE_ORDER = { finding: 0, evidence: 1, decision: 2, conclusion: 3 };
  var GLYPH = { study: '▢', finding: '●', evidence: '◆',
                decision: '▣', conclusion: '★' };
  var REL_COLOR = { prerequisite: '#94a3b8', contains: '#cbd5e1', cites: '#2563eb',
                    decides: '#7c3aed', concludes: '#0d9488', via: '#d97706' };
  var LIFE_COLOR = { proposed: '#94a3b8', asserted: '#64748b', accepted: '#0d9488',
                     rejected: '#e11d48', recorded: '#7c3aed', draft: '#94a3b8',
                     published: '#2563eb' };

  // Pure: graph payload -> positioned nodes + resolved edges + flattened violations.
  function _aigLayout(graph) {
    var studies = (graph && graph.studies) || [];
    var studyEdges = (graph && graph.study_edges) || [];
    var chains = (graph && graph.chains) || {};

    // 1) topological depth of each study from prerequisite edges.
    var depth = {};
    studies.forEach(function (s) { depth[s.id] = 0; });
    var ids = {};
    studies.forEach(function (s) { ids[s.id] = true; });
    for (var pass = 0; pass < studies.length; pass++) {
      studyEdges.forEach(function (e) {
        if (ids[e.source] && ids[e.target]) {
          var cand = depth[e.source] + 1;
          if (cand > depth[e.target]) depth[e.target] = cand;
        }
      });
    }
    // 2) slot studies within each depth (stable order = input order).
    var slotByDepth = {};
    var pos = {};
    var nodes = [];
    studies.forEach(function (s) {
      var d = depth[s.id];
      var slot = slotByDepth[d] || 0; slotByDepth[d] = slot + 1;
      var x = X0 + slot * COL_W, y = Y0 + d * ROW_H;
      pos[s.id] = { x: x, y: y };
      nodes.push({ id: s.id, type: 'study', x: x, y: y,
                   label: s.label || s.slug, lifecycle_state: '', status: s.status || '' });
    });
    // 3) chain cluster per study, stacked by type order then id.
    Object.keys(chains).forEach(function (slug) {
      var sid = 'study/' + slug;
      var anchor = pos[sid] || { x: X0, y: Y0 };
      var cn = (chains[slug].nodes || []).slice().sort(function (a, b) {
        var da = TYPE_ORDER[a.type] || 9, db = TYPE_ORDER[b.type] || 9;
        return da !== db ? da - db : (a.id < b.id ? -1 : 1);
      });
      cn.forEach(function (n, i) {
        var x = anchor.x + CL_DX, y = anchor.y + CL_DY + i * CL_ROW;
        pos[n.id] = { x: x, y: y };
        nodes.push({ id: n.id, type: n.type, x: x, y: y, label: n.label || n.id,
                     lifecycle_state: n.lifecycle_state || '', status: '' });
      });
    });
    // 4) resolve all edges to coordinate pairs; drop any with an unresolved end.
    var edges = [];
    function pushEdge(e) {
      var a = pos[e.source], b = pos[e.target];
      if (!a || !b) return;
      edges.push({ x1: a.x, y1: a.y, x2: b.x, y2: b.y, rel: e.rel });
    }
    studyEdges.forEach(pushEdge);
    Object.keys(chains).forEach(function (slug) {
      (chains[slug].edges || []).forEach(pushEdge);
    });
    // 5) flatten violations, tagging each with its study.
    var violations = [];
    Object.keys(chains).forEach(function (slug) {
      (chains[slug].violations || []).forEach(function (v) {
        violations.push({ node_id: v.node_id, invariant: v.invariant,
                          message: v.message, study: slug });
      });
    });
    return { nodes: nodes, edges: edges, violations: violations };
  }

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // DOM painter — mounts into the existing investigation-dag elements.
  function _renderAigGraph(graph) {
    var nodesHost = document.getElementById('investigation-dag-nodes');
    var edgesSvg = document.getElementById('investigation-dag-edges');
    if (!nodesHost || !edgesSvg) return;
    nodesHost.innerHTML = '';
    edgesSvg.innerHTML = '';
    var studies = (graph && graph.studies) || [];
    if (!studies.length) {
      nodesHost.innerHTML =
        '<p class="empty-state" style="padding:24px">No studies in this investigation.</p>';
      return;
    }
    var layout = _aigLayout(graph);

    if (layout.violations.length) {
      var banner = document.createElement('div');
      banner.style.cssText =
        'margin:0 0 8px;padding:6px 12px;border-radius:6px;background:#fef3c7;' +
        'color:#92400e;font-size:13px;font-weight:600';
      banner.textContent = '⚠ ' + layout.violations.length +
        ' chain gap' + (layout.violations.length === 1 ? '' : 's');
      banner.title = layout.violations.map(function (v) {
        return v.study + ': ' + v.message; }).join('\n');
      nodesHost.appendChild(banner);
    }

    layout.edges.forEach(function (e) {
      var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', e.x1); line.setAttribute('y1', e.y1);
      line.setAttribute('x2', e.x2); line.setAttribute('y2', e.y2);
      line.setAttribute('stroke', REL_COLOR[e.rel] || '#cbd5e1');
      line.setAttribute('stroke-width', e.rel === 'prerequisite' ? '2' : '1.5');
      if (e.rel === 'contains') line.setAttribute('stroke-dasharray', '3,3');
      edgesSvg.appendChild(line);
    });

    layout.nodes.forEach(function (n) {
      var card = document.createElement('div');
      card.style.cssText = 'position:absolute;left:' + n.x + 'px;top:' + n.y +
        'px;font-size:12px;white-space:nowrap';
      var life = n.lifecycle_state
        ? '<span style="margin-left:6px;font-size:10px;padding:1px 6px;border-radius:999px;' +
          'background:' + (LIFE_COLOR[n.lifecycle_state] || '#e2e8f0') +
          ';color:#fff">' + _esc(n.lifecycle_state) + '</span>'
        : '';
      var weight = n.type === 'study' ? '600' : '400';
      card.innerHTML = '<span style="font-weight:' + weight + '">' +
        (GLYPH[n.type] || '•') + ' ' + _esc(n.label) + '</span>' + life;
      nodesHost.appendChild(card);
    });
  }

  global._aigLayout = _aigLayout;
  global._renderAigGraph = _renderAigGraph;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { _aigLayout: _aigLayout, _renderAigGraph: _renderAigGraph };
  }
})(typeof window !== 'undefined' ? window : globalThis);
