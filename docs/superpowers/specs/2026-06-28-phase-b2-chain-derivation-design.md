# Phase B2 — Chain Derivation (promote study fields → typed chain nodes)

**Date:** 2026-06-28
**Status:** Design — approved, pending spec review
**Implements:** RFC-0002 Phase B — "promote existing YAML to typed nodes" (the B2 core)
**Builds on:** B1 (typed chain node types + `validate_chain`), B4 (graph endpoint + `aig-graph.js`, PR #402)
**Repo:** `vivarium-dashboard` (branch `feat/phase-b2-chain-derivation`, stacked on `feat/phase-b4-aig-graph`)

## Goal

Make the B4 investigation graph show each study's **real** evidence chain, derived deterministically from the result fields already in `study.yaml` (`conclusion_verdicts[]`, `findings.entries[]`, `gate_status`) — with **no new API endpoint** and **no workspace mutation**. Today every real study's chain is empty (typed nodes only existed in tests); this lifts the existing semi-structured results into the typed Finding → Evidence → Decision → Conclusion model as a read-time view.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| B2 sub-project to do first | **Promote existing fields → typed nodes** (deterministic lift). Typed Study/Investigation nodes (B2a) and schema-debt collapse (B2b) are separate later cycles. |
| Mapping fidelity | **Full micro-chain (verdict-driven)** — each `conclusion_verdicts[]` entry → a complete F→E→D→C chain whose lifecycle states are driven by `verdict` + `gate_status`. (Not findings-only, not findings+conclusions.) |
| Where nodes live | **Derive-on-read** — a pure lib deriver behind the existing `GET /api/investigation-graph`; nothing persisted, no new endpoint. (Not a destructive persist/migrate.) |
| Free-prose extraction | **Out of scope** — reasoning over unstructured prose is agentic (Phase C). B2 lifts only the already-structured fields. |

## Current state (grounded)

- `study.yaml` (schema_version 4) records results as semi-structured fields:
  - `conclusion_verdicts: [{claim, verdict: supported|partial|refuted, basis}]` — present in ~20/23 studies; the consistent, rich source.
  - `findings.entries: [{signature, description, evidence: [...], ...}]` — rare (1 study), richly structured.
  - `claim` (headline), `gate_status: passed|pending|failed|partial` — common.
- B1 node shapes (from `lib/finding_views`/`chain_views`), keyed by FQ id:
  - finding: `{id:"finding/<id>", type, lifecycle_state, statement, runs:[...], provenance, ...}`
  - evidence: `{id:"evidence/<id>", type, lifecycle_state, findings:[fq ids], hypotheses:[...], statement, ...}`
  - decision: `{id:"decision/<id>", type, lifecycle_state, evidence:[fq ids], outcome, ...}`
  - conclusion: `{id:"conclusion/<id>", type, lifecycle_state, evidence:[fq ids], decisions:[fq ids], statement, ...}`
- `investigation_contracts.validate_chain(nodes)` invariants: finding→run (≥1 run); evidence→finding (≥1 resolved finding) + evidence→hypothesis (≥1 non-empty hypothesis string); conclusion→evidence (each resolves) + conclusion→accepted (evidence `lifecycle_state == "accepted"`) + conclusion→decision (a referenced accept-decision exists).
- B4 builder `lib/investigation_graph_views.build_investigation_graph` already calls `node_store.load_study_nodes(ws, slug)` per study, then `_build_chain` + `validate_chain`. It has the loaded `study_spec` in scope.

## Architecture

```
GET /api/investigation-graph        (EXISTING — no new route)
  build_investigation_graph(ws, inv)
    per study:
      nodes = node_store.load_study_nodes(ws, slug)     # authored nodes (real files)
      if not nodes:
          nodes = chain_derivation.derive_chain_nodes(study_spec, slug)   # NEW
      _build_chain(slug, nodes); validate_chain(nodes)  # unchanged
```

- **`lib/chain_derivation.py`** (new, pure) — `derive_chain_nodes(study_spec: dict, slug: str) -> dict[str, dict]`. Returns synthesized typed nodes keyed by FQ id, in the **same shape** `load_study_nodes` returns, so it is a drop-in supplement. Pure and deterministic: no I/O, no randomness, same input → same ids/output.
- **Suppression rule:** derivation runs only when `load_study_nodes` returns empty for the study. Authored nodes always win; once an agent authors a real chain for a study, derivation steps aside for it (no mixing of synthesized + authored).

### Component ① — the deriver (`lib/chain_derivation.py`)

`derive_chain_nodes(study_spec, slug)`:

For each `conclusion_verdicts[i]` entry `cv = {claim, verdict, basis}` (skip entries with no non-empty `claim`; `i` is the entry's index, used for stable ids):

- ids (deterministic, collision-free, namespaced):
  `finding/derived-<slug>-cv<i>`, `evidence/derived-<slug>-cv<i>`, `decision/derived-<slug>-cv<i>`, `conclusion/derived-<slug>-cv<i>`.
- `gate = str(study_spec.get("gate_status","")).strip().lower()`; `passed = gate in ("passed","pass")`.
- `verdict = str(cv.get("verdict","")).strip().lower()`.
- **Finding** (always): `statement = cv["claim"]`, `lifecycle_state = "asserted"`, `runs = ["run/<slug>"]` (satisfies finding→run).
- **Evidence** (always): `statement = cv.get("basis") or cv["claim"]`, `findings = [finding id]`, `hypotheses = ["hyp/derived-<slug>-cv<i>"]` (satisfies evidence→finding/→hypothesis), `lifecycle_state` per table.
- **Decision** (only when `passed` and `verdict in (supported,refuted,partial)`): `evidence = [evidence id]`, `outcome = {supported:"accept", refuted:"reject", partial:"defer"}[verdict]`, `lifecycle_state = "recorded"`.
- **Conclusion** (only when `verdict == "supported"` and `passed`): `statement = cv["claim"]`, `evidence = [evidence id]`, `decisions = [decision id]`, `lifecycle_state = "published"`.

Lifecycle table (the single source of the rules above):

| verdict | gate_status | Finding | Evidence | Decision | Conclusion |
|---|---|---|---|---|---|
| supported | passed | asserted | accepted | accept | published |
| refuted | passed | asserted | rejected | reject | — (none) |
| partial | passed | asserted | proposed | defer | — (none) |
| any | not passed | asserted | proposed | — (none) | — (none) |

This guarantees every derived chain passes `validate_chain` with **zero violations** (a Conclusion is emitted only for a sound `supported + passed` chain; evidence is `accepted` exactly when an accept-decision references it).

`findings.entries[]` (when present): each entry `fe` (index `j`) → one **Finding** node `finding/derived-<slug>-fe<j>`, `statement = fe.get("description") or fe.get("signature")`, `runs = ["run/<slug>"]`, `lifecycle_state = "asserted"`. No verdict chain attached (matching prose findings to verdicts would be agentic — out of scope).

**Provenance / honesty marker:** every derived node carries `provenance = {actor:"derived", agent_id:"chain-derivation", timestamp:"", source_objects:[<study ref>], justification:"derived from study.yaml conclusion_verdicts[<i>]", tool:"b2/chain-derivation", commit:""}`, plus `owner:"derived"` and `validation_status:"derived"`. (`timestamp:""` matches the B1 convention for non-event-sourced nodes; the deriver does no clock I/O.)

Result: a dict `{fq_id: node}`. Empty `{}` when the study has neither `conclusion_verdicts` nor `findings.entries`.

### Component ② — builder integration (`lib/investigation_graph_views.py`)

- After `nodes = load_study_nodes(ws_root, slug)`, add: `if not nodes: nodes = derive_chain_nodes(study_spec, slug)`.
- Surface a per-chain `derived` flag so the frontend can mark it: `_build_chain` (or the builder) sets `chains[slug]["derived"] = any(n.get("provenance",{}).get("actor") == "derived" for n in nodes.values())`. The chain-node payload entries are unchanged in shape; only the chain dict gains the boolean.
- Everything else (`_build_chain` edge derivation, `validate_chain`) is unchanged.

### Component ③ — frontend hint (`static/aig-graph.js`)

- `_chainBlockHtml(chain)` appends a subtle "· derived" suffix to the "Evidence chain" header **iff** `chain.derived` is truthy, so a lifted chain is visually distinct from a human-gated one. No other rendering change; an absent/empty chain still returns `''`.

## Data flow

Open an investigation → existing `GET /api/investigation-graph` runs the builder → for a study with no authored nodes, `derive_chain_nodes` lifts its `conclusion_verdicts`/`findings.entries` into typed nodes → `_build_chain` + `validate_chain` run exactly as for authored nodes → the card's chain block renders the derived chain with a "· derived" marker. Authoring a real node later (via the B1 endpoints) makes `load_study_nodes` non-empty → derivation is suppressed for that study.

## Error handling

Tolerant, never raises (mirrors the rest of the builder):
- `conclusion_verdicts` not a list, or an entry not a mapping / empty `claim` → that entry is skipped.
- missing `basis` → evidence `statement` falls back to the claim.
- missing/unknown `gate_status` → treated as not-passed (conservative: evidence `proposed`, no decision, no conclusion).
- `findings.entries` absent or not a list → no finding nodes from that source.
- Deterministic: ids and field values are a pure function of (`study_spec`, `slug`); no clock, no randomness.

## Testing

**`tests/test_chain_derivation.py`** (pure deriver — the deterministic core):
- `supported` + `gate passed` → 4 nodes; lifecycle = asserted/accepted/recorded(accept)/published; `validate_chain(nodes) == []`.
- `refuted` + `passed` → finding + evidence(rejected) + decision(reject), **no** conclusion; `validate_chain == []`.
- `partial` + `passed` → finding + evidence(proposed) + decision(defer), no conclusion; valid.
- `gate not passed` (pending) → finding + evidence(proposed) only, no decision/conclusion; valid.
- two `conclusion_verdicts` entries → two independent micro-chains with distinct `cv0`/`cv1` ids.
- `findings.entries[]` present → a `finding/derived-<slug>-fe0` node with the description as statement.
- neither source present → `{}`.
- every derived node has `provenance.actor == "derived"`.
- determinism: calling twice yields identical dicts (ids + values).

**`tests/test_investigation_graph_views.py`** (extend):
- study with `conclusion_verdicts` and **no** authored nodes → `chains[slug].nodes` is populated from derivation and `chains[slug].derived is True`; `violations == []`.
- study with an authored node present → derivation suppressed (`chains[slug].derived` is False / absent), authored nodes used.

**`tests/js/test_chain_block.js`** (extend): `_chainBlockHtml({nodes:[...], derived:true})` contains the "· derived" marker; with `derived` falsy it does not.

## Out of scope (later cycles)

- **v4 3-track-dict `conclusion_verdicts`** — the dashboard's narrative-spine persists `conclusion_verdicts` as a dict of three computed tracks (`regression_compatibility`/`biological_validation`/`explanatory_gain`) whose `result` is computed read-only at render (only `basis` is saved). B2 lifts the authored **list** form (`{claim, verdict, basis}`), which is what every study with conclusions in the current corpus uses (15/23; 0 persist the dict form). The deriver is graceful (returns `{}`) on the dict form. Handling it requires the computed-result path (`single_study_report._derive_conclusion_verdicts`) and is a deliberate follow-up — a natural fit for **B2b** (schema-version consolidation). Decided 2026-06-28 (final-review triage: ship B2 on the list form, defer the dict form).
- **B2a** — typed Study/Investigation container nodes (id/lifecycle/provenance/validation_status + invariants).
- **B2b** — collapse the v2/v3/v4 schema-version migration layers.
- **B2c (agentic)** — extracting findings/evidence from free prose (Phase C).
- Persisting derived nodes / promoting a derived node into an authored one (a Phase C affordance).
- Linking the study's legacy "Accepted" verdict badge to its derived/authored conclusion (a later B3/B-follow-up).
