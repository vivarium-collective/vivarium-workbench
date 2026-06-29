# Investigation Graph Readability — Design

**Date:** 2026-06-28
**Status:** Design — approved, pending spec review
**Builds on:** B4 (graph rendering), B2 (derived chains). Branch `feat/investigation-graph-readability`, stacked on `feat/phase-b2-chain-derivation`.
**Repo:** `vivarium-dashboard`

## Goal

Make the per-investigation graph readable and interrogable. Today each study card lists the derived chain as 4 rows × N claims of bare `type + lifecycle` ("finding asserted / evidence accepted / decision recorded / conclusion published", repeated) — verbose, content-free (you can't tell what a finding *is*), and the only way to dig in is to click the card, which opens a study iframe *below* the graph. Behind a long, scroll-past intro.

This redesign: (A) collapse the chain to **one row per claim** with the claim text + a 4-stage progress glyph; (B) a **side detail drawer** that shows a claim's full content + provenance (and a study's summary) *beside* the graph; (C) **condense the intro** so the graph is immediately visible. No new API endpoint — only the existing `/api/investigation-graph` payload is enriched.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| Chain display | **One row per claim + stage dots** — claim text + a `●◆▣★` glyph filled to the reached stage + a status word. Clickable. |
| Interrogation + study-detail placement | **A right-side detail drawer**, one mechanism for both: click a claim row → its full content + provenance; click a study card → study summary + "open full study". Supersedes the scroll-down iframe. |
| Intro | **Condense** — the long description collapses (collapsed-by-default) so the graph rises up. |
| API surface | **No new endpoint** — enrich the existing `/api/investigation-graph` chain-node payload. |

## Current state (grounded)

- `lib/investigation_graph_views.py::_build_chain` emits chain nodes as `{id, type, label, lifecycle_state}` (label = statement truncated to 80) plus `edges` (`contains`/`cites`/`decides`/`concludes`/`via`) and `violations`. The full node dicts in scope carry `statement`, `outcome` (decisions), and `provenance.justification` — currently dropped from the payload.
- `static/aig-graph.js::_chainBlockHtml(chain)` renders each node as a row of `glyph + TYPE_LABEL + lifecycle badge` — i.e. the bare type, not the claim. Injected into each study card by `_renderInvestigationDag` (B4 superset).
- `templates/index.html.j2` investigation-detail view: header → `#investigation-intro` (long `#investigation-detail-description` + `<details>` how-to-read/glossary/biology-story + needs-attention) → `#investigation-dag-lead` → `#investigation-dag-shell` (the graph) → `#investigation-study-embed-panel` (an iframe shown *below* on card click).
- Study cards (`_renderInvestigationDag`) carry `onclick=_openStudyInsideInvestigation(s.name)` (opens the iframe). The investigation payload `d.studies[]` already has `name/title/question/status/findings`.

## Architecture

```
GET /api/investigation-graph   (existing; payload ENRICHED — no new route)
  _build_chain → chain nodes now carry: statement (full), outcome?, source
        │
        ▼
static/aig-graph.js
  _groupClaims(chain)   PURE: connected components over cites/decides/concludes/via
                        → [{claimText, stages, status, nodeIds, source, parts}]
  _chainBlockHtml(chain) → one clickable row per claim (stage dots + claim + status)
        │ click claim row
        ▼
static/walkthrough.js
  _renderInvestigationDag: card click → study drawer; chain-row click → claim drawer
  _openInvestigationDrawer(kind, payload) → fills the right-side #investigation-detail-drawer
  condensed intro (description collapsed-by-default)
templates/index.html.j2
  + #investigation-detail-drawer (right-side panel); intro markup condensed
```

### Component ① — backend payload enrichment (`_build_chain`)

Add three fields to each chain-node dict in `_build_chain` (the source values are already on `n`):
- `statement`: `str(n.get("statement", ""))` — the FULL claim/basis (untruncated; `label` stays as the truncated form for compact rendering).
- `outcome`: `n.get("outcome")` — present for decisions (`accept`/`reject`/`defer`); omitted/None otherwise.
- `source`: `(n.get("provenance") or {}).get("justification", "")` — e.g. `"derived from study.yaml conclusion_verdicts[1]"`. This is the "what it's derived from" line.

No other backend change. The chain dict keys (`nodes`/`edges`/`violations`/`derived`) are unchanged.

### Component ② — claim grouping + chain rows (`aig-graph.js`)

**`_groupClaims(chain) -> [claim]`** (PURE, node-testable): group the chain nodes into "claims" = connected components over the intra-chain edges (`rel ∈ {cites, decides, concludes, via}`, treated as undirected; the `contains` study→finding edge is excluded so it doesn't merge all claims). Each isolated node (e.g. a `findings.entries` Finding) is its own singleton component. For each component, return:
- `parts`: `{finding, evidence, decision, conclusion}` → the node object of that type in the component (or null).
- `stages`: `{finding:bool, evidence:bool, decision:bool, conclusion:bool}` — type present in the component.
- `claimText`: `parts.finding?.statement` || `parts.conclusion?.statement` || `parts.evidence?.statement` || first node's label.
- `status`: precedence — `published` (conclusion present, lifecycle `published`) → `refuted` (decision outcome `reject`, or evidence `rejected`) → `accepted` (decision `accept`, no conclusion) → `partial` (decision `defer`) → `pending` (evidence `proposed` / finding only). One word.
- `source`: any part's `source` (shared for derived; per-node for authored).
- `nodeIds`: ids in the component.
Deterministic order: components sorted by their finding/first node id.

**`_chainBlockHtml(chain)`** rewrite: header `Evidence chain · derived (N claims)` (or omit `· derived` when `!chain.derived`, omit the count when N≤1). One row per claim:
- a 4-glyph stage indicator: `●◆▣★`, each filled (colored) if `stages[type]` else hollow/grey (`○` style). Order finding→evidence→decision→conclusion.
- the `claimText` (clamped to 2 lines).
- a small status badge (`status` word, colored: published=blue, accepted=teal, refuted=rose, partial/pending=grey).
- the row carries `data-claim-index` (its index in `_groupClaims`) and a class `aig-claim-row` so the click handler (walkthrough.js) can open the drawer. Empty/absent chain → `''` (unchanged).

The stage/status/glyph constants live in `aig-graph.js`. All dynamic strings escaped via the existing `_esc`.

### Component ③ — detail drawer (`walkthrough.js` + template)

- **Template:** add `#investigation-detail-drawer` — a right-side panel inside the investigation-detail view (fixed/sticky right column or an overlay drawer), hidden by default, with a close button. Remove reliance on `#investigation-study-embed-panel` for the quick-look (keep the full-study iframe reachable via an "Open full study →" link, or repurpose the panel).
- **`_openInvestigationDrawer(kind, data)`** in walkthrough.js fills + shows the drawer:
  - `kind="claim"`: render the claim's parts top-to-bottom — Finding (claim), Evidence (basis), Decision (verdict = outcome), Conclusion — each as a labelled block with its `statement` and lifecycle badge; then a provenance footer: `Derived from <study> · <source>` (or `Authored` when not derived). Data comes from the `_groupClaims` entry already in memory.
  - `kind="study"`: render the study summary from `d.studies` (title, question, status, its claims list) + an "Open full study →" link/button that triggers the existing full-study view.
- **Wiring in `_renderInvestigationDag`:** the study card keeps a click → `_openInvestigationDrawer("study", study)`; chain rows get a click handler (with `event.stopPropagation()`) → `_openInvestigationDrawer("claim", claims[idx])`. The grouped claims for a study are computed once (via `window._groupClaims`) and held so the row click can resolve its claim.

### Component ④ — condensed intro (`walkthrough.js` / template)

- The long `#investigation-detail-description` collapses: show a compact one/two-line teaser with a "more ▾" toggle (or wrap the full text in a collapsed `<details>` with a short summary). The how-to-read/glossary/biology-story `<details>` already collapse — leave them. Needs-attention stays but below the (now-visible) graph or compact. Net effect: the graph appears without scrolling past a wall of text.

## Data flow

Open an investigation → `/api/investigation-graph` (enriched) → `_renderInvestigationDag` draws study cards; for each, `_groupClaims(chain)` → `_chainBlockHtml` renders one row per claim with stage dots. Click a claim row → `_openInvestigationDrawer("claim", …)` shows its finding/evidence/decision/conclusion content + provenance in the side drawer. Click a study card → study summary in the drawer. The condensed intro keeps the graph above the fold.

## Error handling

- Backend enrichment is additive and tolerant (missing `statement`/`outcome`/`provenance` → `""`/None). Payload shape stays backward-compatible (existing keys unchanged), so the B4 fallback path still works.
- `_groupClaims` tolerates missing edges/parts (singleton components; null parts). Empty chain → `[]` → `_chainBlockHtml` returns `''` (no regression to today's chain-less cards).
- The drawer degrades: clicking with no resolvable data is a no-op; an "Open full study" link falls back to the existing study view.

## Testing

- **`_groupClaims` (pure, node test):** a full derived chain (finding+evidence+decision+conclusion connected by cites/decides/concludes/via) → ONE claim with all four stages true, status `published`, claimText = the finding statement; a pending chain (finding+evidence proposed) → one claim, stages finding/evidence true, status `pending`; a refuted chain → status `refuted`; two verdicts → two separate claims; a standalone `findings.entries` finding → one singleton claim; empty → `[]`.
- **`_chainBlockHtml` (node test, extends existing):** renders one row per claim (not 4N); contains the claim text and the status word; the `· derived` marker + count when derived; clickable rows carry `data-claim-index`; empty/absent chain → `''`.
- **Backend (`test_investigation_graph_views.py`, extend):** chain nodes now include `statement` (full), `source`; a decision node includes `outcome`; existing keys unchanged.
- **Wiring (static assertions / manual):** `#investigation-detail-drawer` present; `_openInvestigationDrawer` defined; claim rows wired with stopPropagation; intro description collapsed-by-default. Live manual check on `v2e-readouts` parameter-uq.

## Implementation note (plan ordering)

To stay incremental, the plan sequences: (1) backend enrichment, (2) `_groupClaims` + `_chainBlockHtml` reframe (delivers the high-level claim rows + visible claim text — addresses repetition and most of "see what they are"), (3) the detail drawer (deep interrogation + study-detail placement), (4) intro condense. Each is independently testable; (2) already ships visible value if (3)/(4) slip.

## Out of scope

- Drawing the typed chain edges as arrows in the graph (the earlier "interactive graph" idea) — the drawer covers interrogation for now.
- The v4 3-track-dict `conclusion_verdicts` source (B2b follow-up).
- Editing/authoring from the drawer (read-only).
- Restyling the study cards' study-level presentation (status badges/legend) — unchanged.
