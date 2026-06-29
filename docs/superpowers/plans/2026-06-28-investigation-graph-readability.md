# Investigation Graph Readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the per-investigation graph readable and interrogable — collapse the derived chain to one row per claim (claim text + stage dots + status), add a side detail drawer for claims and studies, and condense the intro so the graph is above the fold.

**Architecture:** Enrich the existing `/api/investigation-graph` chain-node payload (full `statement`/`outcome`/`source` — no new route). A pure `_groupClaims(chain)` groups chain nodes into claims (connected components over `cites`/`decides`/`concludes`/`via` edges); `_chainBlockHtml` renders one clickable row per claim. A right-side `#investigation-detail-drawer` (opened by `_openInvestigationDrawer`) shows a claim's full content + provenance, or a study summary, beside the graph. The long intro description collapses by default.

**Tech Stack:** Python 3.11 / pytest; vanilla browser JS (`aig-graph.js`, `walkthrough.js`) + `node`/`assert`; Jinja template.

## Global Constraints

- **No new API endpoints.** Only enrich the existing `/api/investigation-graph` chain-node payload; existing keys (`nodes`/`edges`/`violations`/`derived`) stay unchanged and backward-compatible.
- **No regression / graceful:** an empty or absent chain still yields `''` from `_chainBlockHtml` (chain-less study cards unchanged). The legacy `_renderInvestigationDag` study presentation (status badges, legend, follow-ups) is untouched.
- **Claim grouping** = connected components over edges with `rel ∈ {cites, decides, concludes, via}` only (the `contains` study→finding edge is excluded so claims don't merge). Each isolated node is its own claim.
- **Status precedence:** `published` (conclusion lifecycle `published`) → `refuted` (decision `reject` or evidence `rejected`) → `accepted` (decision `accept`) → `partial` (decision `defer`) → `pending`.
- **Stage glyphs:** `●` finding, `◆` evidence, `▣` decision, `★` conclusion; filled (`#475569`) if the claim has that stage, else grey (`#d1d5db`).
- Escape all dynamic strings (`_esc`). NEVER call `allocate_core()`.
- **Run tests with the venv:** `/Users/eranagmon/code/venv/bin/python -m pytest`; JS via `node`.

---

### Task 1: Enrich the chain-node payload (`_build_chain`)

**Files:**
- Modify: `vivarium_dashboard/lib/investigation_graph_views.py` (the `out_nodes.append(...)` in `_build_chain`, ~line 33)
- Test: `tests/test_investigation_graph_views.py` (extend)

**Interfaces:**
- Produces: each chain-node dict now also has `statement: str` (full), `outcome: str|None` (decisions), `source: str` (provenance justification). Consumed by `aig-graph.js` Task 2/3.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_investigation_graph_views.py`:

```python
def test_chain_nodes_enriched_with_statement_outcome_source(tmp_path):
    ws = _ws(tmp_path)
    _seed_full_chain(ws)  # authored finding/evidence/decision/conclusion on s2
    body, _ = build_investigation_graph(ws, "demo-inv")
    nodes = {n["id"]: n for n in body["chains"]["s2"]["nodes"]}
    f = nodes["finding/f1"]
    assert f["statement"] == "X rises with Y"          # full statement, not just label
    assert "source" in f                                # provenance justification (may be "")
    d = nodes["decision/d1"]
    assert d.get("outcome") == "accept"                 # decision carries its outcome
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_graph_views.py::test_chain_nodes_enriched_with_statement_outcome_source -q`
Expected: FAIL — `KeyError: 'statement'`.

- [ ] **Step 3: Enrich the node dict**

In `vivarium_dashboard/lib/investigation_graph_views.py`, in `_build_chain`, replace:

```python
        out_nodes.append({"id": nid, "type": t, "label": _label(n),
                          "lifecycle_state": n.get("lifecycle_state", "")})
```

with:

```python
        out_nodes.append({"id": nid, "type": t, "label": _label(n),
                          "lifecycle_state": n.get("lifecycle_state", ""),
                          "statement": str(n.get("statement", "")),
                          "outcome": n.get("outcome"),
                          "source": (n.get("provenance") or {}).get("justification", "")})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_graph_views.py -q`
Expected: PASS (all prior tests + the new one).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/investigation_graph_views.py tests/test_investigation_graph_views.py
git commit -m "feat(readability): enrich chain-node payload with statement/outcome/source"
```

---

### Task 2: Claim grouping + chain reframe (`aig-graph.js`)

**Files:**
- Modify: `vivarium_dashboard/static/aig-graph.js`
- Test: `tests/js/test_chain_block.js` (rewrite the chain-content assertions; keep graceful-empty)

**Interfaces:**
- Consumes: the enriched chain payload (Task 1) — node `statement`/`outcome`/`source` + `edges`.
- Produces (on `window` + `module.exports`): `_groupClaims(chain) -> [{parts:{finding,evidence,decision,conclusion}, stages:{...bool}, claimText, status, source, nodeIds}]` (pure); `_chainBlockHtml(chain)` now renders one `.aig-claim-row[data-claim-index]` per claim. Consumed by walkthrough.js Task 3.

- [ ] **Step 1: Write the failing test (rewrite the content assertions)**

Replace the body of `tests/js/test_chain_block.js` with:

```javascript
// tests/js/test_chain_block.js — run with: node tests/js/test_chain_block.js
const assert = require('assert');
const { _chainBlockHtml, _groupClaims } = require('../../vivarium_dashboard/static/aig-graph.js');

// graceful: no chain -> '' (card identical to today)
assert.strictEqual(_chainBlockHtml(undefined), '', 'undefined chain -> empty');
assert.strictEqual(_chainBlockHtml({ nodes: [], edges: [], violations: [] }), '', 'empty chain -> empty');
assert.deepStrictEqual(_groupClaims({ nodes: [], edges: [] }), [], 'empty -> no claims');

function fullClaim(cv, statement, opts) {
  opts = opts || {};
  const f = 'finding/d-' + cv, e = 'evidence/d-' + cv, d = 'decision/d-' + cv, c = 'conclusion/d-' + cv;
  const nodes = [
    { id: f, type: 'finding', lifecycle_state: 'asserted', statement: statement, source: 'derived from study.yaml conclusion_verdicts[' + cv + ']' },
    { id: e, type: 'evidence', lifecycle_state: opts.evState || 'accepted', statement: 'the basis' },
  ];
  const edges = [
    { source: 'study/s', target: f, rel: 'contains' },
    { source: e, target: f, rel: 'cites' },
  ];
  if (opts.decision) {
    nodes.push({ id: d, type: 'decision', lifecycle_state: 'recorded', outcome: opts.decision });
    edges.push({ source: d, target: e, rel: 'decides' });
  }
  if (opts.conclusion) {
    nodes.push({ id: c, type: 'conclusion', lifecycle_state: 'published', statement: statement });
    edges.push({ source: c, target: e, rel: 'concludes' });
    edges.push({ source: c, target: d, rel: 'via' });
  }
  return { nodes, edges };
}

// one published claim -> one claim, all stages, status published, claim text present
const pub = fullClaim('cv0', 'basal elongation dominates', { evState: 'accepted', decision: 'accept', conclusion: true });
pub.derived = true; pub.violations = [];
const g = _groupClaims(pub);
assert(g.length === 1, 'one component');
assert(g[0].claimText === 'basal elongation dominates', 'claim text from finding statement');
assert(g[0].status === 'published', 'status published');
assert(g[0].stages.finding && g[0].stages.evidence && g[0].stages.decision && g[0].stages.conclusion, 'all stages');
assert(g[0].source.indexOf('conclusion_verdicts[cv0]') !== -1, 'source carried');

const htmlPub = _chainBlockHtml(pub);
assert(htmlPub.indexOf('basal elongation dominates') !== -1, 'renders the claim text');
assert(htmlPub.indexOf('published') !== -1, 'renders status word');
assert(htmlPub.indexOf('· derived') !== -1, 'derived hint');
assert((htmlPub.match(/aig-claim-row/g) || []).length === 1, 'one clickable claim row');
assert(htmlPub.indexOf('data-claim-index="0"') !== -1, 'row carries index');

// pending: finding+evidence(proposed), no decision/conclusion
const pend = fullClaim('cv0', 'needs more samples', { evState: 'proposed' });
pend.violations = [];
const gp = _groupClaims(pend);
assert(gp.length === 1 && gp[0].status === 'pending', 'pending status');
assert(gp[0].stages.finding && gp[0].stages.evidence && !gp[0].stages.decision, 'two stages');

// refuted
const ref = fullClaim('cv0', 'claim X', { evState: 'rejected', decision: 'reject' });
ref.violations = [];
assert(_groupClaims(ref)[0].status === 'refuted', 'refuted status');

// two claims -> two components, two rows
const a = fullClaim('cv0', 'claim A', { decision: 'accept', conclusion: true });
const b = fullClaim('cv1', 'claim B', { decision: 'accept', conclusion: true });
const two = { nodes: a.nodes.concat(b.nodes), edges: a.edges.concat(b.edges), derived: true, violations: [] };
assert(_groupClaims(two).length === 2, 'two claims');
assert(_chainBlockHtml(two).indexOf('(2 claims)') !== -1, 'count shown');
assert((_chainBlockHtml(two).match(/aig-claim-row/g) || []).length === 2, 'two rows');

// singleton findings.entries finding (no intra edges)
const single = { nodes: [{ id: 'finding/d-fe0', type: 'finding', lifecycle_state: 'asserted', statement: 'a gap' }],
                 edges: [{ source: 'study/s', target: 'finding/d-fe0', rel: 'contains' }], violations: [] };
const gs = _groupClaims(single);
assert(gs.length === 1 && gs[0].claimText === 'a gap' && gs[0].status === 'pending', 'singleton finding claim');

console.log('ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/js/test_chain_block.js`
Expected: FAIL — `_groupClaims is not a function` (not yet exported).

- [ ] **Step 3: Rewrite `_chainBlockHtml` + add `_groupClaims`**

In `vivarium_dashboard/static/aig-graph.js`, replace the whole `_chainBlockHtml` function (from `function _chainBlockHtml(chain) {` through its closing `}` before `global._chainBlockHtml`) with:

```javascript
  var STATUS_COLOR = { published: '#2563eb', accepted: '#0d9488', refuted: '#e11d48',
                       partial: '#d97706', pending: '#94a3b8' };
  var STAGE_SEQ = ['finding', 'evidence', 'decision', 'conclusion'];
  var _INTRA = { cites: 1, decides: 1, concludes: 1, via: 1 };

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
```

Then update the export block at the bottom:

```javascript
  global._chainBlockHtml = _chainBlockHtml;
  global._groupClaims = _groupClaims;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { _chainBlockHtml: _chainBlockHtml, _groupClaims: _groupClaims };
  }
```

(The `n.type in parts && parts[n.type] === null` guard keeps the FIRST node of each type and ignores any unexpected types.)

- [ ] **Step 4: Run test + syntax check**

Run: `node tests/js/test_chain_block.js && node --check vivarium_dashboard/static/aig-graph.js`
Expected: prints `ok`; `node --check` exits 0.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/aig-graph.js tests/js/test_chain_block.js
git commit -m "feat(readability): group chain into claims; one row per claim with stage dots"
```

---

### Task 3: Detail drawer (`walkthrough.js` + template)

**Files:**
- Modify: `vivarium_dashboard/templates/index.html.j2` (add the drawer element inside `#investigation-detail-view`, after `#investigation-dag-shell` ~line 928)
- Modify: `vivarium_dashboard/static/walkthrough.js` (card onclick ~5630; after `nodesHost.appendChild(node)` ~5668; add `_openInvestigationDrawer` near `_openStudyInsideInvestigation` ~5960)
- Test: `tests/test_readability_wiring.py` (static assertions)

**Interfaces:**
- Consumes: `window._groupClaims` (Task 2); the per-study `chainsBySlug`; `d.studies` study objects.
- Produces: `window._openInvestigationDrawer(kind, data)`.

- [ ] **Step 1: Write the failing wiring test**

```python
# tests/test_readability_wiring.py
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_drawer_element_present():
    html = (ROOT / "vivarium_dashboard/templates/index.html.j2").read_text()
    assert 'id="investigation-detail-drawer"' in html
    assert 'id="investigation-detail-drawer-body"' in html


def test_drawer_wired_in_walkthrough():
    js = (ROOT / "vivarium_dashboard/static/walkthrough.js").read_text()
    assert "function _openInvestigationDrawer(" in js
    assert "_openInvestigationDrawer('study'" in js or '_openInvestigationDrawer("study"' in js
    assert "aig-claim-row" in js            # claim-row click wiring
    assert "stopPropagation" in js          # row clicks don't trigger the card
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_readability_wiring.py -q`
Expected: FAIL — drawer element + functions absent.

- [ ] **Step 3: Add the drawer element to the template**

In `vivarium_dashboard/templates/index.html.j2`, immediately AFTER the `#investigation-dag-shell` closing `</div>` (the graph shell ends ~line 932, before `#investigation-study-embed-panel`), insert:

```html
    <div id="investigation-detail-drawer" style="display:none; position:fixed; top:96px; right:16px; width:360px; max-height:78vh; overflow:auto; background:#fff; border:1px solid #e2e8f0; border-radius:12px; box-shadow:0 8px 30px rgba(0,0,0,0.12); padding:14px 16px; z-index:50; font-size:0.92em">
      <button onclick="document.getElementById('investigation-detail-drawer').style.display='none'" style="float:right;border:none;background:none;cursor:pointer;font-size:1.15em;color:#94a3b8" title="Close">✕</button>
      <div id="investigation-detail-drawer-body"></div>
    </div>
```

- [ ] **Step 4: Add `_openInvestigationDrawer` to walkthrough.js**

In `vivarium_dashboard/static/walkthrough.js`, immediately BEFORE `function _openStudyInsideInvestigation(name) {` (~line 5960), insert:

```javascript
  function _drawerStudyHtml(s) {
    var q = (s.question || '').replace(/\s+/g, ' ').trim();
    return '<div style="font-weight:700;color:#0f172a">' + _esc(s.title || s.name) + '</div>' +
      '<div style="font-size:0.78em;color:#64748b;margin:2px 0 8px">' + _esc(s.effective_status || s.status || '') + '</div>' +
      (q ? '<div style="margin:6px 0"><span style="font-weight:600;color:#475569">Asks: </span>' + _esc(q) + '</div>' : '') +
      '<button class="drawer-open-study" data-study="' + _esc(s.name) + '" style="margin-top:10px;cursor:pointer">Open full study →</button>';
  }

  function _drawerBlock(label, node, extra) {
    if (!node) return '';
    return '<div style="margin:9px 0;padding:8px 10px;border:1px solid #e5e7eb;border-radius:8px">' +
      '<div style="font-size:0.72em;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#64748b">' +
      label + (node.lifecycle_state ? ' · ' + _esc(node.lifecycle_state) : '') + (extra || '') + '</div>' +
      '<div style="margin-top:3px;color:#1e293b">' + _esc(node.statement || node.label || '') + '</div></div>';
  }

  function _drawerClaimHtml(claim, study) {
    var P = claim.parts || {};
    var dec = P.decision ? _drawerBlock('▣ Decision', P.decision, P.decision.outcome ? ' · ' + _esc(P.decision.outcome) : '') : '';
    var prov = claim.source
      ? 'Derived from ' + _esc(study ? study.name : '') + ' · ' + _esc(claim.source)
      : 'Authored' + (study ? ' in ' + _esc(study.name) : '');
    return '<div style="font-weight:700;color:#0f172a;line-height:1.3">' + _esc(claim.claimText) + '</div>' +
      '<div style="font-size:0.8em;color:#64748b;margin:2px 0 8px">' + _esc(claim.status) + '</div>' +
      _drawerBlock('● Finding', P.finding) +
      _drawerBlock('◆ Evidence', P.evidence) +
      dec +
      _drawerBlock('★ Conclusion', P.conclusion) +
      '<div style="margin-top:10px;font-size:0.74em;color:#94a3b8">' + prov + '</div>' +
      (study ? '<button class="drawer-open-study" data-study="' + _esc(study.name) + '" style="margin-top:10px;cursor:pointer">Open full study →</button>' : '');
  }

  function _openInvestigationDrawer(kind, data) {
    var drawer = document.getElementById('investigation-detail-drawer');
    var body = document.getElementById('investigation-detail-drawer-body');
    if (!drawer || !body) return;
    if (kind === 'claim') body.innerHTML = _drawerClaimHtml(data.claim, data.study);
    else if (kind === 'study') body.innerHTML = _drawerStudyHtml(data);
    else return;
    drawer.style.display = 'block';
    var btn = body.querySelector('.drawer-open-study');
    if (btn) btn.addEventListener('click', function () {
      drawer.style.display = 'none';
      _openStudyInsideInvestigation(btn.getAttribute('data-study'));
    });
  }
  window._openInvestigationDrawer = _openInvestigationDrawer;

```

- [ ] **Step 5: Wire the card + claim-row clicks in `_renderInvestigationDag`**

In `vivarium_dashboard/static/walkthrough.js`, replace the card onclick line (~5630):

```javascript
      node.onclick = function() { _openStudyInsideInvestigation(s.name); };
```

with:

```javascript
      node.onclick = function() {
        if (window._openInvestigationDrawer) window._openInvestigationDrawer('study', s);
        else _openStudyInsideInvestigation(s.name);
      };
```

Then, immediately AFTER `nodesHost.appendChild(node);` (~line 5668), insert:

```javascript
      if (chainsBySlug && window._groupClaims && window._openInvestigationDrawer) {
        (function (study, chain) {
          var claims = window._groupClaims(chain);
          node.querySelectorAll('.aig-claim-row').forEach(function (row) {
            row.addEventListener('click', function (ev) {
              ev.stopPropagation();
              var idx = parseInt(row.getAttribute('data-claim-index'), 10);
              if (claims[idx]) window._openInvestigationDrawer('claim', { claim: claims[idx], study: study });
            });
          });
        })(s, chainsBySlug[s.name]);
      }
```

- [ ] **Step 6: Run tests + syntax check**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_readability_wiring.py -q && node --check vivarium_dashboard/static/walkthrough.js`
Expected: pytest PASS (2 passed); `node --check` exit 0.

- [ ] **Step 7: Manual browser verification (documented)**

Serve against `v2e-readouts` (`/Users/eranagmon/code/venv/bin/python -m vivarium_dashboard.cli serve --workspace /Users/eranagmon/code/v2e-readouts --port 8802`), open `?investigation=parameter-uq`:
- Each card's chain shows one row per claim (stage dots + claim text + status), not the 4N list.
- Click a claim row → the right drawer shows Finding/Evidence/Decision/Conclusion content + the "Derived from … conclusion_verdicts[i]" line. Clicking another row updates it; ✕ closes it.
- Click a study card (not on a row) → the drawer shows the study summary; "Open full study →" opens the full study view.

- [ ] **Step 8: Commit**

```bash
git add vivarium_dashboard/templates/index.html.j2 vivarium_dashboard/static/walkthrough.js tests/test_readability_wiring.py
git commit -m "feat(readability): side detail drawer for claims and studies"
```

---

### Task 4: Condense the intro

**Files:**
- Modify: `vivarium_dashboard/templates/index.html.j2` (`#investigation-detail-description`, ~line 892)
- Test: extend `tests/test_readability_wiring.py`

**Interfaces:**
- Consumes: nothing new — the existing JS still fills `#investigation-detail-description`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_readability_wiring.py`:

```python
def test_intro_description_collapsed_by_default():
    html = (ROOT / "vivarium_dashboard/templates/index.html.j2").read_text()
    # the long description is wrapped in a collapsed <details> with a summary
    assert 'id="investigation-intro-details"' in html
    # the description container id is preserved (JS still targets it)
    assert 'id="investigation-detail-description"' in html
    i = html.index('id="investigation-intro-details"')
    j = html.index('id="investigation-detail-description"')
    assert i < j  # description lives inside the details wrapper
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_readability_wiring.py::test_intro_description_collapsed_by_default -q`
Expected: FAIL — no `investigation-intro-details`.

- [ ] **Step 3: Wrap the description in a collapsed `<details>`**

In `vivarium_dashboard/templates/index.html.j2`, replace:

```html
      <div id="investigation-detail-description" class="inv-lead"></div>
```

with:

```html
      <details id="investigation-intro-details" class="inv-lead-details">
        <summary style="cursor:pointer;color:#475569;font-size:0.92em;font-weight:600">About this investigation</summary>
        <div id="investigation-detail-description" class="inv-lead" style="margin-top:6px"></div>
      </details>
```

(The JS that sets `#investigation-detail-description` content is unchanged — it still finds the id inside the `<details>`. Collapsed by default → the graph rises above the fold.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_readability_wiring.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/templates/index.html.j2 tests/test_readability_wiring.py
git commit -m "feat(readability): collapse the investigation intro description by default"
```

---

## Self-Review

**Spec coverage:**
- Backend enrichment (statement/outcome/source) → Task 1. ✓
- Claim grouping (connected components over cites/decides/concludes/via) + one-row-per-claim with stage dots + status + clickable → Task 2. ✓
- Detail drawer (claim content + provenance source; study summary; supersedes scroll-down iframe via "Open full study") → Task 3. ✓
- Condensed intro → Task 4. ✓
- No new endpoint / graceful empty / status precedence / stage glyphs → Global Constraints, enforced in Tasks 1-2. ✓

**Placeholder scan:** No TBD/TODO; every code step is complete; commands have expected output. The `_groupClaims` parts-assignment has a noted simplification (`if (n.type in parts && parts[n.type] === null)`) — the implementer should use that clean form. ✓

**Type consistency:** Task 1 adds `statement`/`outcome`/`source` to each node; Task 2's `_groupClaims` reads exactly those (`parts.finding.statement`, `parts.decision.outcome`, `first.source`) and `chain.edges[].rel`; Task 3's drawer reads `claim.parts`, `claim.claimText`, `claim.status`, `claim.source` (produced by Task 2) and `s.name`/`s.title`/`s.question` (existing study fields). `.aig-claim-row` + `data-claim-index` are produced in Task 2 and consumed by Task 3's wiring. `_openInvestigationDrawer(kind, data)` signature matches its two call sites. ✓
