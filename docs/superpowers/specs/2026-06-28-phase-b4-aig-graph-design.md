# Phase B4 — Render the Typed AIG (replace the investigation graph)

**Date:** 2026-06-28
**Status:** Implemented (with a post-review design revision — see below)
**Implements:** RFC-0002 Phase B — "render nodes + transitions in the dashboard"
**Repos touched:** `vivarium-dashboard` (backend endpoint + frontend graph renderer)

> **Design revision (post final-review, 2026-06-28).** The frontend was implemented as a self-contained renderer that *replaced* the legacy study-DAG (`_renderAigGraph` + `_aigLayout`). The whole-branch review caught that this regressed every chain-less investigation (all of them today) — it dropped the legacy status/verdict badges, the legend, follow-up popovers, and click-through, violating the "renders identically to today" guarantee. The user chose the **true-superset** fix: keep the legacy `_renderInvestigationDag` rendering exactly as-is and **inject each study's typed evidence chain into its study card** via a pure `_chainBlockHtml(chain)` (in `static/aig-graph.js`). Because the chain block is part of the card's measured `innerHTML`, it stacks correctly and never collides; an absent/empty chain yields `''`, so a chain-less card is byte-identical to today. The backend endpoint/builder below are unchanged; only the frontend section (Component ②) was superseded by this card-injection approach. The `GET /api/investigation-graph` payload contract is the same.

## Goal

Replace the dashboard's existing per-investigation graph (a study-only DAG) with a renderer for the **typed Actionable Investigation Graph**: studies as typed nodes carrying the existing `pipeline_gate` edges, **plus** the typed evidence-chain nodes (Finding → Evidence → Decision → Conclusion) authored under each study (B1), drawn as an **inline cluster per study**, with lifecycle-state badges and `validate_chain` violations surfaced. **Graceful superset:** with no chain nodes authored yet (today's reality — all real studies hold prose), it renders like today's graph; it progressively *becomes* the AIG as B1 endpoints / a future B2c migration populate nodes. No regression.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| Next increment | **B4 — render the chain**, replacing the existing investigation graph (not a study-detail tab). |
| Approach (data is empty today) | **Graceful superset** — study DAG + typed chain nodes where authored; evolves into the AIG. |
| Chain layout | **Inline cluster per study** — each study node's chain nodes drawn in a cluster beside/below it, within the one graph. |

## Current state (grounded)

- The existing graph is `walkthrough.js:_renderInvestigationDag(studies)` (def ~5510-5760): a bespoke SVG node-link of **study nodes**, BFS depth-laid-out, edges from `_dagEdges(s)` (study `parent_studies`/`pipeline_gate`), styled by `_dagRelStyle(rel)`, with a status legend + "add followups" popovers. Called at `walkthrough.js:5249` with `d.studies` (where `d` = the investigation detail from `GET /api/investigation/<slug>`). Exported at 5772.
- Backend has `lib/investigations.normalize_dag_edges(spec) -> list[dict]` (reads `pipeline_gate.prerequisites`) — the study→study edge source, reusable in Python.
- B1 (merged) provides `lib/node_store.load_study_nodes(ws, slug) -> {id: node}` and `investigation_contracts.validate_chain(nodes) -> [violation]`. Chain node types: `finding/evidence/decision/conclusion` with reference fields (`evidence.findings`, `decision.evidence`, `conclusion.evidence`/`conclusion.decisions`) and `lifecycle_state`.
- **No real study has typed chain nodes yet** — they exist only in tests. The graph must render correctly (== today) when chains are empty.

## Architecture

```
GET /api/investigation-graph?investigation=<slug>          (NEW, backend)
  lib/investigation_graph_views.build_investigation_graph
    for each study in the investigation:
      normalize_dag_edges(study_spec)        -> study->study edges
      node_store.load_study_nodes(ws, slug)  -> chain nodes
      derive typed chain edges from refs     -> cites/decides/concludes (+ study contains finding)
      validate_chain(nodes)                  -> violations
  -> {investigation, studies[], study_edges[], chains{slug:{nodes,edges,violations}}}
        │
        ▼
walkthrough.js  (frontend)
  line 5249: replace `_renderInvestigationDag(d.studies)` with a fetch of
  /api/investigation-graph + `_renderAigGraph(graph)` — studies depth-laid-out
  (reusing the existing layout/SVG machinery) + per-study chain clusters +
  typed/colored edges + lifecycle badges + a violations banner.
```

### Component ① — backend: the typed-AIG read endpoint

**`lib/investigation_graph_views.py`** — `build_investigation_graph(ws_root, inv_slug) -> tuple[dict, int]`:
1. Resolve the investigation's studies. Reuse the existing investigation-detail study resolution (the same set `GET /api/investigation/<slug>` renders): load `investigations/<slug>/investigation.yaml`'s `studies` list, OR scan `studies/*/study.yaml` for `investigation == inv_slug` — match the existing helper used by the investigation-detail route. 404 if the investigation is unknown.
2. For each study spec (loaded with the existing `_project_v4_redesign_to_legacy_view` projection so v4 studies resolve): `study_edges += normalize_dag_edges(spec)` (each → `{source: "study/<prereq>", target: "study/<slug>", rel}`); `nodes = node_store.load_study_nodes(ws_root, slug)`.
3. Build the **per-study chain** from `nodes`:
   - chain nodes: every `finding/evidence/decision/conclusion` (id, type, label = `statement` truncated or the id, `lifecycle_state`).
   - chain edges (within the study cluster): `study/<slug> → finding/<id>` rel `contains`; `evidence/<id> → finding/<id>` rel `cites` (per `evidence.findings`); `decision/<id> → evidence/<id>` rel `decides` (per `decision.evidence`); `conclusion/<id> → evidence/<id>` rel `concludes` + `conclusion/<id> → decision/<id>` rel `via` (per `conclusion.evidence`/`conclusion.decisions`). Skip edges whose target isn't in `nodes` (tolerant).
   - `violations = validate_chain(nodes)`.
4. Return `{investigation: inv_slug, studies: [{id:"study/<slug>", slug, label, status, type:"study"}], study_edges: [...], chains: {slug: {nodes:[...], edges:[...], violations:[...]}}}`, 200. Tolerant: a study that fails to load is skipped (logged), never 500.

**Route** `GET /api/investigation-graph?investigation=<slug>` (mirror an existing GET route: `Depends(get_workspace)`, `body,status = _ig_views.build_investigation_graph(ws, inv)`, typed/JSONResponse). A pydantic `InvestigationGraph` pass-through model (or plain dict) — match the dashboard's payload convention.

### Component ② — frontend: replace the graph renderer

In `walkthrough.js`:
- At the call site (~5249), replace `_renderInvestigationDag(d.studies || [])` with: `fetch('/api/investigation-graph?investigation=' + encodeURIComponent(d.slug || d.name)).then(r=>r.json()).then(_renderAigGraph)` — with a fallback to `_renderInvestigationDag(d.studies)` if the fetch fails (graceful — keeps the old graph if the new endpoint errors).
- **`_renderAigGraph(graph)`** — the new renderer. REUSE the existing `_renderInvestigationDag` SVG machinery (the BFS depth layout, the SVG edge-drawing, the status legend, the shell) — extend it rather than rewrite:
  - Lay out the **study** nodes by depth from `graph.study_edges` (the existing layout) and draw study→study edges with the existing `_dagRelStyle`.
  - For each study, render its `chains[slug].nodes` as a **cluster** positioned below/beside the study node; draw the cluster's `edges` with new per-rel styles (`contains`/`cites`/`decides`/`concludes`/`via`).
  - Node glyphs by type: study = box (as today); finding = ●, evidence = ◆, decision = ▣, conclusion = ★. Lifecycle badge/color per node (`proposed`/`accepted`/`rejected`/`recorded`/`draft`/`published`).
  - Extend the legend with the new node glyphs + edge rels.
  - Surface `chains[*].violations` as a small banner above the graph ("⚠ N chain gaps").
  - Empty chains → the cluster is absent → the graph == today's study DAG (graceful).
- Keep `window._renderInvestigationDag` exported (other callers / fallback). Add `window._renderAigGraph`.
- **Extract the payload→render-model transform** (graph payload → positioned nodes + edges) as a **pure function** `_aigLayout(graph) -> {nodes:[{id,type,x,y,label,lifecycle}], edges:[{x1,y1,x2,y2,rel}]}` so it is unit-testable without the DOM; `_renderAigGraph` calls it then paints SVG.

## Data flow

Open an investigation → SPA loads `/api/investigation/<slug>` (as today) → instead of rendering `d.studies` directly, fetch `/api/investigation-graph?investigation=<slug>` → `_renderAigGraph` paints studies + per-study chain clusters + typed edges + lifecycle badges. Authoring a finding/evidence/decision/conclusion via the B1 endpoints (or an agent) → re-open/refresh → the new nodes appear in the study's cluster.

## Error handling

- Endpoint: 404 unknown investigation; per-study load failure skipped (never 500); tolerant of empty/missing chain dirs (B1's `load_study_nodes` already is).
- Frontend: if `/api/investigation-graph` fails, fall back to the existing `_renderInvestigationDag(d.studies)` (no worse than today).
- Edges referencing a non-resolved node are skipped (no dangling SVG lines).

## Testing

**Backend (`lib/investigation_graph_views`)** — the deterministic core:
- An investigation with 2 studies + a `pipeline_gate` edge → `study_edges` has the edge; `chains` keyed by slug.
- A study with a full authored chain (finding→evidence(accepted)→decision(accept)→conclusion(published)) → `chains[slug].nodes` has all 4 typed + the study; `edges` include `cites`/`decides`/`concludes`/`via` + `contains`; `violations == []`.
- A study with an unsound chain (conclusion citing non-accepted evidence) → `violations` non-empty.
- Unknown investigation → 404; a study that fails to load is skipped, not fatal.
- Route returns 200 with the payload; unknown investigation → 404.

**Frontend (`_aigLayout` pure transform)** — extracted for testability:
- Given a graph payload, returns positioned study nodes (one per study, depth-ordered) + chain nodes clustered under their study + edges with the right `rel`. (DOM-free assertions on the node/edge model.)
- Empty chains → only study nodes + study edges (== today's graph shape).
- (The SVG painting itself is verified manually against a real investigation — see Rollout.)

## Rollout / manual verification

- Land on `feat/phase-b4-aig-graph` (worktree `/Users/eranagmon/code/vdash-phaseB4`, off `origin/main` with B1 merged).
- Manual check: serve the dashboard against `v2e-readouts`; open an investigation (e.g. `parameter-uq`) → the graph renders as today (no chain nodes). Then author a chain on one study via the B1 endpoints (`POST /api/finding|evidence|decision|conclusion`) → re-open → the study's cluster shows the typed chain + lifecycle badges + (if broken) a violations banner.
- Keep `walkthrough.js` additive: the old `_renderInvestigationDag` stays (fallback + other callers); only the call site at ~5249 and the new renderer/transform are added.

## Out of scope (later increments)

- Author affordances in the graph (buttons to create findings/evidence/etc.) — the chain is authored via the B1 API / agents; B4 is read/render only.
- B2c: migrating legacy prose `findings.entries[]`/`conclusion_verdicts[]` into typed nodes (agentic).
- B2a/B2b: typed Study/Investigation validation + the v2/v3/v4 schema-debt collapse.
- Interactive graph editing, drag, zoom beyond what `_renderInvestigationDag` already does.
