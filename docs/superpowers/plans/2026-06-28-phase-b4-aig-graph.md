# Phase B4 — Render the Typed AIG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's study-only investigation graph with a typed Actionable Investigation Graph — study DAG plus each study's Finding→Evidence→Decision→Conclusion chain nodes, lifecycle badges, and chain violations — that renders identically to today when no chain nodes exist (graceful superset).

**Architecture:** A new read-only backend builder + `GET /api/investigation-graph` endpoint assembles the typed graph from existing pieces (`normalize_dag_edges`, `node_store.load_study_nodes`, `validate_chain`). A new self-contained static module `aig-graph.js` provides a pure `_aigLayout(graph)` transform (node-testable) and a `_renderAigGraph(graph)` SVG/DOM painter that mounts into the existing `#investigation-dag-nodes` / `#investigation-dag-edges` elements. The investigation-detail call site in `walkthrough.js` swaps to fetch the new endpoint and call `_renderAigGraph`, falling back to the old `_renderInvestigationDag` on any failure.

**Tech Stack:** Python 3.11, FastAPI (TestClient), pydantic; vanilla browser JS (no framework, no module bundler — static `<script src>` files sharing `window` globals); `node` + built-in `assert` for the one JS unit test.

## Global Constraints

- **Read-only:** the endpoint and renderer never write workspace state. Chains are authored only via the existing B1 `POST /api/finding|evidence|decision|conclusion` endpoints.
- **Tolerant, never 500:** unknown investigation → 404; a study that fails to load is skipped, not fatal; edges whose endpoints don't resolve are dropped (no dangling lines).
- **Graceful superset:** with empty chains the graph renders the study DAG exactly as today (no regression). The old `_renderInvestigationDag` stays in `walkthrough.js` as the fallback and for any other callers; `window._renderInvestigationDag` remains exported.
- **Node ids are fully-qualified:** chain node ids are `finding/<id>`, `evidence/<id>`, `decision/<id>`, `conclusion/<id>`; study node ids are `study/<slug>`. Reference fields already hold FQ ids (`evidence.findings == ["finding/..."]`, `decision.evidence`, `conclusion.evidence`, `conclusion.decisions`).
- **Edge rels:** `prerequisite` (study→study), `contains` (study→finding), `cites` (evidence→finding), `decides` (decision→evidence), `concludes` (conclusion→evidence), `via` (conclusion→decision).
- **Never call `allocate_core()`** anywhere (it crashes this env via a pbg-emitters f-string). The builder uses `validate_chain`/`load_study_nodes` only — no core construction.
- **Run tests with the workspace venv:** `/Users/eranagmon/code/venv/bin/python -m pytest` (bare `python` lacks deps; bare `pytest` collection of `test_visualization_endpoints.py` fails on a pre-existing unrelated import — scope test runs to the new files).

---

### Task 1: Backend graph builder (`investigation_graph_views.py`)

**Files:**
- Create: `vivarium_dashboard/lib/investigation_graph_views.py`
- Test: `tests/test_investigation_graph_views.py`

**Interfaces:**
- Consumes: `vivarium_dashboard.lib.workspace_paths.WorkspacePaths.load(ws) -> wp` (with `wp.investigations`, `wp.study_dir(slug)`); `vivarium_dashboard.lib.investigations.load_spec(path) -> dict` and `.normalize_dag_edges(spec) -> list[{study,condition,...}]`; `vivarium_dashboard.lib.node_store.load_study_nodes(ws, slug) -> {id: node}`; `investigation_contracts.validate_chain(nodes) -> list[{node_id,invariant,message}]`.
- Produces: `build_investigation_graph(ws_root: Path, inv_slug: str) -> tuple[dict, int]` returning `({investigation, studies:[{id,slug,type,label,status}], study_edges:[{source,target,rel,condition}], chains:{slug:{nodes:[{id,type,label,lifecycle_state}], edges:[{source,target,rel}], violations:[...]}}}, 200)` or `({error}, 404)`. Later tasks (route, frontend) depend on this exact payload shape.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_investigation_graph_views.py
import yaml
from pathlib import Path
from vivarium_dashboard.lib.investigation_graph_views import build_investigation_graph


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    inv = tmp_path / "investigations" / "demo-inv"
    inv.mkdir(parents=True)
    inv.joinpath("investigation.yaml").write_text(yaml.safe_dump(
        {"name": "demo-inv", "studies": ["s1", "s2"]}))
    s1 = tmp_path / "studies" / "s1"; s1.mkdir(parents=True)
    s1.joinpath("study.yaml").write_text(yaml.safe_dump(
        {"schema_version": 4, "name": "s1", "title": "First", "status": "complete"}))
    s2 = tmp_path / "studies" / "s2"; s2.mkdir(parents=True)
    s2.joinpath("study.yaml").write_text(yaml.safe_dump(
        {"schema_version": 4, "name": "s2", "title": "Second", "status": "planned",
         "pipeline_gate": {"prerequisites": [{"study": "s1"}]}}))
    return tmp_path


def _seed_full_chain(ws: Path, slug: str = "s2") -> None:
    d = ws / "studies" / slug
    for sub in ("findings", "evidence", "decisions", "conclusions"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "findings" / "f1.yaml").write_text(yaml.safe_dump(
        {"id": "finding/f1", "type": "finding", "lifecycle_state": "asserted",
         "statement": "X rises with Y", "runs": ["run/1"]}))
    (d / "evidence" / "e1.yaml").write_text(yaml.safe_dump(
        {"id": "evidence/e1", "type": "evidence", "lifecycle_state": "accepted",
         "findings": ["finding/f1"], "hypotheses": ["H1"], "statement": "supports H1"}))
    (d / "decisions" / "d1.yaml").write_text(yaml.safe_dump(
        {"id": "decision/d1", "type": "decision", "lifecycle_state": "recorded",
         "evidence": ["evidence/e1"], "outcome": "accept"}))
    (d / "conclusions" / "c1.yaml").write_text(yaml.safe_dump(
        {"id": "conclusion/c1", "type": "conclusion", "lifecycle_state": "published",
         "evidence": ["evidence/e1"], "decisions": ["decision/d1"], "statement": "H1 holds"}))


def test_studies_and_pipeline_gate_edge(tmp_path):
    body, status = build_investigation_graph(_ws(tmp_path), "demo-inv")
    assert status == 200
    assert {s["id"] for s in body["studies"]} == {"study/s1", "study/s2"}
    assert {"source": "study/s1", "target": "study/s2",
            "rel": "prerequisite", "condition": ""} in body["study_edges"]
    assert set(body["chains"]) == {"s1", "s2"}


def test_full_chain_nodes_edges_and_no_violations(tmp_path):
    ws = _ws(tmp_path); _seed_full_chain(ws)
    body, status = build_investigation_graph(ws, "demo-inv")
    chain = body["chains"]["s2"]
    assert {n["id"] for n in chain["nodes"]} == {
        "finding/f1", "evidence/e1", "decision/d1", "conclusion/c1"}
    rels = {(e["source"], e["target"], e["rel"]) for e in chain["edges"]}
    assert ("study/s2", "finding/f1", "contains") in rels
    assert ("evidence/e1", "finding/f1", "cites") in rels
    assert ("decision/d1", "evidence/e1", "decides") in rels
    assert ("conclusion/c1", "evidence/e1", "concludes") in rels
    assert ("conclusion/c1", "decision/d1", "via") in rels
    assert chain["violations"] == []
    f1 = next(n for n in chain["nodes"] if n["id"] == "finding/f1")
    assert f1["type"] == "finding" and f1["lifecycle_state"] == "asserted"
    assert f1["label"] == "X rises with Y"


def test_unsound_chain_surfaces_violations(tmp_path):
    ws = _ws(tmp_path)
    d = ws / "studies" / "s2"
    (d / "conclusions").mkdir(parents=True)
    (d / "conclusions" / "c1.yaml").write_text(yaml.safe_dump(
        {"id": "conclusion/c1", "type": "conclusion", "lifecycle_state": "published",
         "evidence": ["evidence/missing"], "decisions": [], "statement": "bad"}))
    body, _ = build_investigation_graph(ws, "demo-inv")
    assert any(v["node_id"] == "conclusion/c1"
               for v in body["chains"]["s2"]["violations"])


def test_unknown_investigation_404(tmp_path):
    body, status = build_investigation_graph(_ws(tmp_path), "nope")
    assert status == 404 and "error" in body


def test_invalid_study_skipped_not_fatal(tmp_path):
    ws = _ws(tmp_path)
    (ws / "studies" / "s1" / "study.yaml").write_text("{not: valid: yaml:")
    body, status = build_investigation_graph(ws, "demo-inv")
    assert status == 200
    assert {s["id"] for s in body["studies"]} == {"study/s2"}  # s1 skipped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_graph_views.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.investigation_graph_views'`.

- [ ] **Step 3: Write the builder**

```python
# vivarium_dashboard/lib/investigation_graph_views.py
"""Build the typed Actionable Investigation Graph for one investigation
(RFC-0002 Phase B4): study nodes + pipeline_gate study->study edges, plus each
study's typed evidence-chain nodes/edges and validate_chain violations.
Read-only and tolerant — unknown investigation 404s, bad studies are skipped,
unresolved chain refs are dropped from edges (but still flagged by validate_chain)."""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
from vivarium_dashboard.lib.node_store import load_study_nodes
from investigation_contracts import validate_chain


def _label(node: dict) -> str:
    s = (node.get("statement") or "").strip()
    if not s:
        return node.get("id", "")
    return s if len(s) <= 80 else s[:77] + "..."


def _build_chain(slug: str, nodes: dict[str, dict]) -> dict:
    """Typed chain nodes + edges for one study. Edge targets that don't resolve
    in ``nodes`` are dropped here, but validate_chain still reports them."""
    out_nodes: list[dict] = []
    out_edges: list[dict] = []
    for nid, n in nodes.items():
        t = n.get("type")
        out_nodes.append({"id": nid, "type": t, "label": _label(n),
                          "lifecycle_state": n.get("lifecycle_state", "")})
        if t == "finding":
            out_edges.append({"source": f"study/{slug}", "target": nid, "rel": "contains"})
        elif t == "evidence":
            for f in n.get("findings", []) or []:
                if f in nodes:
                    out_edges.append({"source": nid, "target": f, "rel": "cites"})
        elif t == "decision":
            for e in n.get("evidence", []) or []:
                if e in nodes:
                    out_edges.append({"source": nid, "target": e, "rel": "decides"})
        elif t == "conclusion":
            for e in n.get("evidence", []) or []:
                if e in nodes:
                    out_edges.append({"source": nid, "target": e, "rel": "concludes"})
            for d in n.get("decisions", []) or []:
                if d in nodes:
                    out_edges.append({"source": nid, "target": d, "rel": "via"})
    return {"nodes": out_nodes, "edges": out_edges, "violations": validate_chain(nodes)}


def build_investigation_graph(ws_root: Path, inv_slug: str) -> tuple[dict, int]:
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    spec_path = wp.investigations / inv_slug / "investigation.yaml"
    if not spec_path.is_file():
        return {"error": f"no investigation.yaml for {inv_slug!r}"}, 404
    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {"error": f"unreadable investigation.yaml for {inv_slug!r}"}, 404

    from vivarium_dashboard.lib.investigations import load_spec, normalize_dag_edges

    studies_out: list[dict] = []
    study_edges: list[dict] = []
    chains: dict[str, dict] = {}
    for slug in (spec.get("studies") or []):
        try:
            sp = wp.study_dir(slug) / "study.yaml"
        except FileNotFoundError:
            sp = wp.investigations / slug / "spec.yaml"
        if not sp.is_file():
            continue
        try:
            study_spec = load_spec(sp)
        except Exception:  # noqa: BLE001 — skip invalid/unloadable study, never fatal
            continue
        name = study_spec.get("name", slug)
        studies_out.append({"id": f"study/{name}", "slug": name, "type": "study",
                            "label": study_spec.get("title") or name,
                            "status": study_spec.get("status", "planned")})
        for pre in normalize_dag_edges(study_spec):
            study_edges.append({"source": f"study/{pre['study']}", "target": f"study/{name}",
                               "rel": "prerequisite", "condition": pre.get("condition", "")})
        chains[name] = _build_chain(name, load_study_nodes(ws_root, name))

    return {"investigation": inv_slug, "studies": studies_out,
            "study_edges": study_edges, "chains": chains}, 200
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_graph_views.py -q`
Expected: PASS (5 passed). A `DeprecationWarning` for legacy `parent_studies` may appear if a fixture used it — the fixtures here use `pipeline_gate`, so none expected.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/investigation_graph_views.py tests/test_investigation_graph_views.py
git commit -m "feat(b4): typed AIG graph builder (study DAG + chain nodes/edges/violations)"
```

---

### Task 2: Register `GET /api/investigation-graph`

**Files:**
- Modify: `vivarium_dashboard/api/app.py` (import block near line 102; new route near the investigation-detail route ~line 1617)
- Test: `tests/test_investigation_graph_route.py`

**Interfaces:**
- Consumes: `investigation_graph_views.build_investigation_graph(ws, inv) -> (dict, int)` from Task 1; `get_workspace` dependency; `JSONResponse`.
- Produces: `GET /api/investigation-graph?investigation=<slug>` → 200 with the payload dict, or 404 `{error}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_investigation_graph_route.py
import yaml
import pytest
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root()
    active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    inv = tmp_path / "investigations" / "demo-inv"; inv.mkdir(parents=True)
    inv.joinpath("investigation.yaml").write_text(yaml.safe_dump(
        {"name": "demo-inv", "studies": ["s1"]}))
    s1 = tmp_path / "studies" / "s1"; s1.mkdir(parents=True)
    s1.joinpath("study.yaml").write_text(yaml.safe_dump(
        {"schema_version": 4, "name": "s1", "title": "First", "status": "complete"}))
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def test_graph_route_200(client):
    r = client.get("/api/investigation-graph?investigation=demo-inv")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["investigation"] == "demo-inv"
    assert {s["id"] for s in body["studies"]} == {"study/s1"}
    assert "chains" in body and "study_edges" in body


def test_graph_route_unknown_404(client):
    r = client.get("/api/investigation-graph?investigation=nope")
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_graph_route.py -q`
Expected: FAIL — `test_graph_route_200` gets 404 (route not registered → FastAPI returns 404 for the unknown path).

- [ ] **Step 3: Add the import**

In `vivarium_dashboard/api/app.py`, in the lib-import block (next to line 102 `from vivarium_dashboard.lib import finding_views as _finding_views`), add:

```python
from vivarium_dashboard.lib import investigation_graph_views as _ig_views
```

- [ ] **Step 4: Register the route**

In `vivarium_dashboard/api/app.py`, immediately after the `investigation_detail` route definition (the block that ends `return IsetDetail.model_validate(result)`, ~line 1642), add:

```python
    @app.get(
        "/api/investigation-graph",
        tags=["Data, inputs & references"],
        summary="Typed AIG: study DAG + per-study evidence-chain nodes/edges/violations",
    )
    def investigation_graph(investigation: str = "", ws: Path = Depends(get_workspace)):
        """Typed Actionable Investigation Graph for one investigation (RFC-0002
        Phase B4). Study nodes + pipeline_gate study->study edges, plus each
        study's chain nodes/edges and validate_chain violations. The payload is
        dynamic/nested, so it returns a passthrough JSONResponse (matching other
        dynamic endpoints) rather than a typed response_model. 404 when the
        investigation.yaml is absent."""
        body, status = _ig_views.build_investigation_graph(ws, investigation)
        return JSONResponse(status_code=status, content=body)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_graph_route.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/api/app.py tests/test_investigation_graph_route.py
git commit -m "feat(b4): register GET /api/investigation-graph"
```

---

### Task 3: Frontend module `aig-graph.js` (`_aigLayout` pure transform + `_renderAigGraph` painter)

**Files:**
- Create: `vivarium_dashboard/static/aig-graph.js`
- Test: `tests/js/test_aig_layout.js`

**Interfaces:**
- Consumes: the Task-1 payload shape (`graph.studies`, `graph.study_edges`, `graph.chains{slug:{nodes,edges,violations}}`).
- Produces (both on `window` and `module.exports`): `_aigLayout(graph) -> {nodes:[{id,type,x,y,label,lifecycle_state,status}], edges:[{x1,y1,x2,y2,rel}], violations:[{node_id,invariant,message,study}]}` (pure, DOM-free); `_renderAigGraph(graph)` (paints into `#investigation-dag-nodes` + `#investigation-dag-edges`). Task 4 wires these into the SPA.

- [ ] **Step 1: Write the failing test**

```javascript
// tests/js/test_aig_layout.js  — run with: node tests/js/test_aig_layout.js
const assert = require('assert');
const { _aigLayout } = require('../../vivarium_dashboard/static/aig-graph.js');

const graph = {
  investigation: 'inv',
  studies: [
    { id: 'study/s1', slug: 's1', type: 'study', label: 'S1', status: 'complete' },
    { id: 'study/s2', slug: 's2', type: 'study', label: 'S2', status: 'planned' },
  ],
  study_edges: [{ source: 'study/s1', target: 'study/s2', rel: 'prerequisite' }],
  chains: {
    s1: { nodes: [], edges: [], violations: [] },
    s2: {
      nodes: [
        { id: 'finding/f1', type: 'finding', label: 'F', lifecycle_state: 'asserted' },
        { id: 'evidence/e1', type: 'evidence', label: 'E', lifecycle_state: 'accepted' },
      ],
      edges: [
        { source: 'study/s2', target: 'finding/f1', rel: 'contains' },
        { source: 'evidence/e1', target: 'finding/f1', rel: 'cites' },
      ],
      violations: [{ node_id: 'evidence/e1', invariant: 'evidence->hypothesis', message: 'x' }],
    },
  },
};

const out = _aigLayout(graph);
const s1 = out.nodes.find(n => n.id === 'study/s1');
const s2 = out.nodes.find(n => n.id === 'study/s2');
assert(s1 && s2, 'both studies positioned');
assert(s2.y > s1.y, 's2 (depth 1) below s1 (depth 0)');
const e1 = out.nodes.find(n => n.id === 'evidence/e1');
assert(e1 && typeof e1.x === 'number' && typeof e1.y === 'number', 'evidence positioned');
assert(out.edges.length === 3, 'study edge + 2 chain edges resolved to coords');
out.edges.forEach(e => ['x1', 'y1', 'x2', 'y2'].forEach(
  k => assert(typeof e[k] === 'number', 'edge coord ' + k)));
assert(out.violations.length === 1 && out.violations[0].study === 's2',
  'violation surfaced + tagged with study');

// dangling edge dropped
const dangling = _aigLayout({
  investigation: 'i',
  studies: [{ id: 'study/a', slug: 'a', type: 'study', label: 'A', status: 'planned' }],
  study_edges: [{ source: 'study/ghost', target: 'study/a', rel: 'prerequisite' }],
  chains: { a: { nodes: [], edges: [], violations: [] } },
});
assert(dangling.edges.length === 0, 'edge to absent node dropped');

// graceful: empty chains -> only study nodes
const empty = _aigLayout({
  investigation: 'i',
  studies: [{ id: 'study/a', slug: 'a', type: 'study', label: 'A', status: 'planned' }],
  study_edges: [], chains: { a: { nodes: [], edges: [], violations: [] } },
});
assert(empty.nodes.length === 1 && empty.edges.length === 0, 'graceful empty == study DAG');

console.log('ok');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/js/test_aig_layout.js`
Expected: FAIL — `Cannot find module '../../vivarium_dashboard/static/aig-graph.js'`.

- [ ] **Step 3: Write the module**

```javascript
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/js/test_aig_layout.js`
Expected: prints `ok` (exit 0).

- [ ] **Step 5: Syntax-check the browser path**

Run: `node --check vivarium_dashboard/static/aig-graph.js`
Expected: no output, exit 0 (valid JS).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/static/aig-graph.js tests/js/test_aig_layout.js
git commit -m "feat(b4): aig-graph.js — _aigLayout pure transform + _renderAigGraph painter"
```

---

### Task 4: Wire the renderer into the SPA (script tag + call-site swap)

**Files:**
- Modify: `vivarium_dashboard/templates/index.html.j2` (script tags ~line 1652)
- Modify: `vivarium_dashboard/static/walkthrough.js` (call site, line 5249)
- Test: `tests/test_b4_wiring.py` (static assertions that the wiring is present)

**Interfaces:**
- Consumes: `window._renderAigGraph` (Task 3) loaded before `walkthrough.js` runs the investigation-detail render; `window._renderInvestigationDag` (existing) as fallback; `GET /api/investigation-graph` (Task 2).
- Produces: nothing downstream — this is the terminal integration.

- [ ] **Step 1: Write the failing wiring test**

```python
# tests/test_b4_wiring.py
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_index_loads_aig_graph_before_walkthrough():
    html = (ROOT / "vivarium_dashboard/templates/index.html.j2").read_text()
    assert "assets/aig-graph.js" in html
    assert html.index("assets/aig-graph.js") < html.index("assets/walkthrough.js")


def test_walkthrough_swaps_callsite_with_fallback():
    js = (ROOT / "vivarium_dashboard/static/walkthrough.js").read_text()
    assert "/api/investigation-graph?investigation=" in js
    assert "_renderAigGraph" in js
    # old renderer kept as fallback + still exported
    assert "window._renderInvestigationDag = _renderInvestigationDag;" in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_b4_wiring.py -q`
Expected: FAIL — `aig-graph.js` not in the template; `/api/investigation-graph` not in walkthrough.js.

- [ ] **Step 3: Add the script tag to `index.html.j2`**

In `vivarium_dashboard/templates/index.html.j2`, immediately BEFORE the `walkthrough.js` script tag (line 1652), insert:

```html
<script src="assets/aig-graph.js{% if asset_version %}?v={{ asset_version }}{% endif %}" onerror="/* aig-graph unavailable */"></script>
```

So the order becomes `aig-graph.js` then `walkthrough.js` (the call site below uses `window._renderAigGraph`, defined by the time `walkthrough.js`'s click handler runs; load order also guarantees it for any synchronous use).

- [ ] **Step 4: Swap the call site in `walkthrough.js`**

In `vivarium_dashboard/static/walkthrough.js`, replace the single line at 5249:

```javascript
        _renderInvestigationDag(d.studies || []);
```

with:

```javascript
        // Phase B4: render the typed AIG (study DAG + evidence chains). Falls
        // back to the legacy study-only renderer on any failure (graceful).
        (function () {
          var slug = d.slug || d.name || name;
          if (typeof window._renderAigGraph !== 'function' || !slug) {
            _renderInvestigationDag(d.studies || []);
            return;
          }
          fetch('/api/investigation-graph?investigation=' + encodeURIComponent(slug))
            .then(function (r) { if (!r.ok) throw new Error('graph ' + r.status); return r.json(); })
            .then(function (graph) { window._renderAigGraph(graph); })
            .catch(function () { _renderInvestigationDag(d.studies || []); });
        })();
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_b4_wiring.py -q && node --check vivarium_dashboard/static/walkthrough.js`
Expected: pytest PASS (3 passed); `node --check` exit 0 (the edited walkthrough.js is still valid JS).

- [ ] **Step 6: Manual browser verification (documented — not automated)**

Serve the dashboard against a real workspace and confirm the graceful path, then the typed path:

```bash
# from /Users/eranagmon/code/vdash-phaseB4, with the dashboard installed -e
/Users/eranagmon/code/venv/bin/python -m vivarium_dashboard serve --workspace /Users/eranagmon/code/v2e-readouts --port 8788
```
- Open an investigation (e.g. `parameter-uq`) → the graph renders as today (studies + prerequisite edges, no chain clusters) — graceful, no regression.
- Author a chain on one study via the B1 endpoints, e.g.:
  ```bash
  curl -s localhost:8788/api/finding -H 'content-type: application/json' \
    -d '{"study":"<slug>","statement":"demo finding","runs":["run/1"]}'
  ```
  then re-open the investigation → that study shows a `● demo finding` chain node with a lifecycle badge under the study box. If the chain is unsound, the `⚠ N chain gaps` banner appears above the graph.
- Confirm `GET /api/investigation-graph?investigation=<slug>` returns 200 JSON with `studies`, `study_edges`, `chains`.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/templates/index.html.j2 vivarium_dashboard/static/walkthrough.js tests/test_b4_wiring.py
git commit -m "feat(b4): wire typed AIG renderer into investigation-detail (graceful fallback)"
```

---

## Self-Review

**Spec coverage:**
- Backend `GET /api/investigation-graph` + `build_investigation_graph` (study resolution, `normalize_dag_edges`, `load_study_nodes`, typed chain edges, `validate_chain`) → Tasks 1–2. ✓
- Frontend replace `_renderInvestigationDag` call with `_renderAigGraph`, inline-cluster-per-study, lifecycle badges, violations banner, glyphs, fallback → Tasks 3–4. ✓
- Pure `_aigLayout` extracted + unit-tested → Task 3. ✓
- Graceful superset (empty chains == today) → tested (Task 3 `empty`) + manual (Task 4). ✓
- Tolerance (404, skip bad study, drop dangling edges) → Task 1 tests + `_aigLayout` dangling test. ✓
- Out-of-scope items (author affordances, B2c migration, B2a/b) → not implemented, correct. ✓

**Placeholder scan:** No TBD/TODO; every code step is complete; commands have expected output. ✓

**Type consistency:** Payload keys (`studies`/`study_edges`/`chains`/`nodes`/`edges`/`violations`, node `lifecycle_state`, edge `source`/`target`/`rel`) are identical across Task 1 (producer), Task 3 (`_aigLayout` consumer), and the tests. Edge rels (`prerequisite`/`contains`/`cites`/`decides`/`concludes`/`via`) match between builder and `REL_COLOR`. Study node id format `study/<slug>` is consistent between `study_edges` sources, `_build_chain` `contains` source, and `_aigLayout` cluster anchoring. ✓

**Design refinement vs. spec:** The spec said "reuse the existing `_renderInvestigationDag` SVG machinery"; this plan instead ships a self-contained `aig-graph.js` (cleaner module boundary, lets `_aigLayout` be node-tested, old renderer untouched as fallback). Same behavior and graceful-superset guarantee — a deliberate improvement, noted here for the reviewer.
