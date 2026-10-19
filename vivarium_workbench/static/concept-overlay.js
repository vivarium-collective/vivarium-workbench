// concept-overlay.js — draws the derived Concept overlay AS PART OF the
// investigation graph: a labeled boundary around each Concept's member Study
// nodes (§11.5 "expanded Concept: an organizational boundary around persistent
// Study nodes"), tinted by org_state, carrying the Concept's gate + member
// count + divergence. NOT a separate panel — it groups the real graph nodes
// (#investigation-dag-nodes .iset-dag-node[data-study=...]) in their own layout
// coordinate space, so it tracks pan/zoom/relayout.
//
// Membership comes from GET /api/concept-overlay (DataSource.loadConceptOverlay),
// cached per investigation; boundaries are recomputed from live node positions.
(function (global) {
  'use strict';

  var LAYER_ID = 'concept-boundary-layer';
  var _data = null;      // { slug, concepts, verdicts, diverges:{slug:true} }
  var _fetching = null;  // slug currently being fetched

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  var ORG = {
    exploring:    '#6b7280',
    consolidated: '#2563eb',
    reopened:     '#b7791f'
  };
  // concept-level verdict tint (worst-of member verdicts, honest headline)
  var VRANK = { refuted: 4, qualified: 3, inconclusive: 2, supported: 1, unassessed: 0 };
  var VCOLOR = {
    supported: '#1f7a3f', qualified: '#b7791f', inconclusive: '#6b7280',
    refuted: '#b02a37', unassessed: '#9ca3af'
  };

  function _conceptVerdict(members, verdicts) {
    // The honest headline is the most-caveated member verdict (RFC: a claim is
    // only as strong as its sharpest test; nulls/negatives are never smoothed).
    var worst = 'unassessed', wr = -1;
    members.forEach(function (slug) {
      var v = (verdicts[slug] && verdicts[slug].verdict) || 'unassessed';
      if (VRANK[v] > wr) { wr = VRANK[v]; worst = v; }
    });
    return worst;
  }

  function _nodeHost() { return document.getElementById('investigation-dag-nodes'); }

  function _ensureLayer(host) {
    var layer = document.getElementById(LAYER_ID);
    if (!layer) {
      layer = document.createElement('div');
      layer.id = LAYER_ID;
      layer.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;z-index:0;';
      host.insertBefore(layer, host.firstChild); // behind the node cards
    } else if (layer.parentNode !== host) {
      host.insertBefore(layer, host.firstChild);
    }
    return layer;
  }

  function _memberBox(host, members) {
    var minL = Infinity, minT = Infinity, maxR = -Infinity, maxB = -Infinity, n = 0;
    members.forEach(function (slug) {
      var el = host.querySelector('.iset-dag-node[data-study="' + (global.CSS && CSS.escape ? CSS.escape(slug) : slug) + '"]');
      if (!el) return;
      var l = el.offsetLeft, t = el.offsetTop, r = l + el.offsetWidth, b = t + el.offsetHeight;
      if (l < minL) minL = l; if (t < minT) minT = t;
      if (r > maxR) maxR = r; if (b > maxB) maxB = b; n++;
    });
    if (!n) return null;
    return { l: minL, t: minT, w: maxR - minL, h: maxB - minT, n: n };
  }

  function draw() {
    var host = _nodeHost();
    if (!host || !_data || !_data.concepts.length) {
      var dead = document.getElementById(LAYER_ID); if (dead) dead.innerHTML = '';
      return;
    }
    if (!host.querySelector('.iset-dag-node')) return; // graph not laid out yet
    var layer = _ensureLayer(host);
    var PAD = 16, LABEL_H = 22;
    var html = '';
    _data.concepts.forEach(function (c) {
      var members = c.member_studies || [];
      var box = _memberBox(host, members);
      if (!box) return;
      var verdict = _conceptVerdict(members, _data.verdicts);
      var tint = ORG[c.org_state] || '#6b7280';
      var vcol = VCOLOR[verdict] || '#9ca3af';
      var gate = c.from_gate || c.from_act || '';
      var nDiv = members.filter(function (s) { return _data.diverges[s]; }).length;
      var left = box.l - PAD, top = box.t - PAD - LABEL_H;
      var w = box.w + PAD * 2, h = box.h + PAD * 2 + LABEL_H;
      html +=
        '<div style="position:absolute;left:' + left + 'px;top:' + top + 'px;width:' + w + 'px;height:' + h +
        'px;border:1.5px dashed ' + tint + ';border-radius:12px;background:' + tint + '14;box-sizing:border-box;">' +
          '<div style="position:absolute;left:10px;top:-11px;display:flex;align-items:center;gap:6px;' +
          'font-size:11px;font-weight:700;background:var(--bg,#fff);padding:0 6px;border-radius:6px;white-space:nowrap;">' +
            (gate ? '<span style="background:#374151;color:#fff;border-radius:7px;padding:0 6px;">' + _esc(gate) + '</span>' : '') +
            '<span>' + _esc(c.title || c.id) + '</span>' +
            '<span style="background:' + vcol + ';color:#fff;border-radius:7px;padding:0 6px;font-weight:600;">' + _esc(verdict) + '</span>' +
            '<span style="opacity:.6;font-weight:500;">' + box.n + '/' + members.length + '</span>' +
            (nDiv ? '<span style="color:#b02a37;">⚠ ' + nDiv + '</span>' : '') +
          '</div>' +
        '</div>';
    });
    layer.innerHTML = html;
  }

  function _fetch(slug) {
    if (!slug || _fetching === slug || !global.DataSource || !global.DataSource.loadConceptOverlay) return;
    _fetching = slug;
    global.DataSource.loadConceptOverlay(slug).then(function (ov) {
      _fetching = null;
      var dv = {}; ((ov && ov.diverges) || []).forEach(function (s) { dv[s] = true; });
      _data = { slug: slug, concepts: (ov && ov.concepts) || [],
                verdicts: (ov && ov.study_verdicts) || {}, diverges: dv };
      draw();
    }).catch(function () { _fetching = null; });
  }

  // Watch the active investigation + redraw boundaries against live node
  // positions (cheap: a handful of boxes). Robust to relayout/zoom/pan.
  function _tick() {
    var slug = global._currentIsetSlug || global._currentIset || null;
    var fs = document.getElementById('investigation-dag-fs');
    var onGraph = fs && fs.offsetParent !== null;
    if (!onGraph || !slug) {
      var layer = document.getElementById(LAYER_ID); if (layer) layer.innerHTML = '';
      return;
    }
    if (!_data || _data.slug !== slug) { _fetch(slug); return; }
    draw();
  }

  global.ConceptOverlay = { redraw: draw };

  if (typeof document !== 'undefined') {
    var start = function () {
      setInterval(_tick, 600);
      global.addEventListener('resize', draw);
      _tick();
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }
})(typeof window !== 'undefined' ? window : globalThis);
