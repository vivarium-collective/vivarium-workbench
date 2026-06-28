# Phase B1 — The Evidence-Chain Node Types (RFC-0002)

**Date:** 2026-06-28
**Status:** Design — approved decisions, pending spec review
**Implements:** RFC-0002 Phase B (Graph Contract) — the scientific-evidence chain · RFC-0001 Part III
**Repos touched:** `investigation-contracts` (node types + invariants), `vivarium-dashboard` (author endpoints)

## Goal

Complete the typed scientific chain. Phase A typed the `finding` node (+ `POST /api/finding` + `FindingCreated`). B1 adds **Evidence, Decision, Conclusion** as typed bigraph-schema nodes with lifecycle state machines, the cross-node chain **invariants** enforced, addressable node files, author endpoints, and events — so a published Conclusion provably traces to accepted Evidence through recorded Decisions. This is what turns simulation runs into *auditable knowledge* instead of prose. A thin slice, like Phase A: no UI, no migration of legacy prose.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| First Phase-B increment | **B1 — evidence-chain node types** (defer YAML promotion/migration = B2, dashboard rendering = B4). |
| Chain-invariant enforcement | **Hard 422 at publish time.** `POST /api/conclusion` runs `validate_chain` and refuses to publish if any referenced Evidence isn't `accepted` via a referenced Decision (the never-fabricate gate on the irreversible transition). |
| Hypotheses | **Free-text strings in B1.** `evidence.hypotheses: list[string]`; a typed Hypothesis node + `POST /api/hypothesis` is a later increment. |
| pbg-superpowers changes | **None.** The `EventClient` already dispatches arbitrary registered event types; agentic reactions to the new events are Phase C. |

## Current state (grounded)

- `investigation-contracts` has node types: `investigation_node` (base), `finding`; event types: `('FindingCreated',)`; `lifecycle.LIFECYCLES` has `finding` only; `validate_envelope`, `read_log`, `make_core`, pydantic mirror.
- The dashboard's Phase-A pattern to mirror: `lib/finding_views.create_finding(ws_root, body) -> (dict, int)` (layout-aware study resolve → `make_core().check("finding", node)` → `atomic_write_text(<study>/findings/<id>.yaml)` → `emit_event(FindingCreated)`); route `POST /api/finding` (`FindingCreateBody`, `Depends(get_workspace)`, `body,status = _finding_views.create_finding(...)`).
- Today the rest of the chain is **prose / ad-hoc**: `findings.entries[]` (varies by study), evidence as a *sub-field* of findings (`source_kind`/`source_ref`), conclusions as `conclusion_verdicts[]` (claim/verdict/basis) + `conclusion`/`biological_summary` prose. B1's typed nodes **coexist** with this legacy prose; B2 reconciles.
- Node files live per-type under the study dir: `<study>/findings/<id>.yaml` (Phase A). B1 adds `<study>/evidence/`, `<study>/decisions/`, `<study>/conclusions/`.

## Architecture

```
investigation-contracts (the contract)
  node types: finding(✓) + evidence, decision, conclusion   ← bigraph-schema types
  lifecycle:  + evidence/decision/conclusion transition tables
  events:     EVENT_TYPES += EvidenceLinked, DecisionRecorded, ConclusionPublished
  invariants: validate_chain(nodes) -> [Violation]          ← cross-node referential checks
        │
        ▼
vivarium-dashboard (the emitter)
  lib/chain_views.py: create_evidence / create_decision / create_conclusion
  lib/node_store.py:  load_study_nodes(ws, study) -> {node_id: node}   (resolves refs)
  routes: POST /api/evidence · /api/decision · /api/conclusion
          (write node atomically → emit; /api/conclusion gates on validate_chain → 422)
        │
        ▼  workspace/.pbg/events.jsonl  (Phase A log; new event types ride it)
```

### Component ① — `investigation-contracts`: node types

Each inherits `investigation_node` (id, type, lifecycle_state, owner, provenance, validation_status). Registered in `make_core()` after `finding`:

```python
core.register_type("evidence", {
    **INVESTIGATION_NODE_FIELDS,           # id,type,lifecycle_state,owner,provenance,validation_status
    "findings":   "list[string]",          # references "finding/<id>"; invariant >= 1
    "hypotheses": "list[string]",           # free-text in B1; invariant >= 1 non-empty
    "confidence": "float",
    "statement":  "string",
})
core.register_type("decision", {
    **INVESTIGATION_NODE_FIELDS,
    "evidence":   "list[string]",          # references "evidence/<id>"
    "outcome":    "decision_outcome",       # enum accept|reject|defer
    "rationale":  "string",
    "decided_by": "string",
})
core.register_type("conclusion", {
    **INVESTIGATION_NODE_FIELDS,
    "evidence":   "list[string]",          # references "evidence/<id>"; the accepted set
    "decisions":  "list[string]",          # references "decision/<id>"
    "hypotheses": "list[string]",
    "statement":  "string",
})
```
New enum: `decision_outcome = {'_type':'enum','_values':['accept','reject','defer']}`. Factor the shared base fields into a module constant `INVESTIGATION_NODE_FIELDS` (also retrofit `finding`'s registration to use it — DRY, no behavior change).

### Component ② — lifecycle tables

Extend `lifecycle.LIFECYCLES`:
```python
"evidence":   {"": ["proposed"], "proposed": ["accepted", "rejected"],
               "accepted": [], "rejected": []},
"decision":   {"": ["pending"], "pending": ["recorded"], "recorded": []},
"conclusion": {"": ["draft"], "draft": ["published"], "published": []},
```
`initial_state` returns the first state of `""`. `check_transition` unchanged.

### Component ③ — `validate_chain` (the referential invariants)

A pure function in a new `investigation_contracts/chain.py`, over the resolved node set:

```python
def validate_chain(nodes: dict[str, dict]) -> list[dict]:
    """nodes: {node_id -> node dict}. Returns a list of Violation dicts
    {node_id, invariant, message}. Empty list == the chain is sound."""
```
Invariants checked (each yields a Violation when broken):
1. **finding → ≥1 run** — every `finding` node has `len(runs) >= 1`.
2. **evidence → ≥1 finding + ≥1 hypothesis** — every `evidence` node has `len(findings) >= 1`, each referenced `finding/<id>` resolves to a `finding` node in `nodes`, and `>= 1` non-empty `hypotheses` string.
3. **conclusion → accepted evidence via decisions** — for every `evidence/<id>` a `conclusion` references: that evidence node resolves, its `lifecycle_state == "accepted"`, AND at least one referenced `decision/<id>` resolves with `outcome == "accept"` and lists that evidence id in its `evidence`. Otherwise a Violation.

Exported from `__init__`. Pydantic mirror: `EvidenceCreateBody`, `DecisionCreateBody`, `ConclusionCreateBody` (request bodies) + `Evidence`/`Decision`/`Conclusion` node models (transport), mirroring the bigraph-schema fields.

### Component ④ — vivarium-dashboard: node store + author endpoints

**`lib/node_store.py`** — `load_study_nodes(ws_root, slug) -> dict[str, dict]`: layout-aware study resolve, then read every `<study>/{findings,evidence,decisions,conclusions}/*.yaml`, keyed by each node's `id` field. Tolerant (missing dirs → skip; malformed yaml → skip). Used by `validate_chain` at conclusion-publish.

**`lib/chain_views.py`** — three workers mirroring `finding_views.create_finding`:
- `create_evidence(ws_root, body) -> (dict, int)` — build the node (`id="evidence/<e+uuid>"`, `lifecycle_state=initial_state("evidence")`, `owner="shared"`, stamped provenance), `make_core().check("evidence", node)` (400/500 on fail), require `len(findings)>=1` + `>=1` non-empty hypothesis (400 else), `atomic_write_text(<study>/evidence/<id>.yaml)`, **then** `emit_event(EvidenceLinked)`. Returns `{evidence_id, event_id}`.
- `create_decision(ws_root, body) -> (dict, int)` — `owner="human"`, `outcome ∈ {accept,reject,defer}` (400 else), `lifecycle_state="recorded"` (a decision is recorded directly), write `<study>/decisions/<id>.yaml`, emit `DecisionRecorded`.
- `create_conclusion(ws_root, body) -> (dict, int)` — build node `owner="human"`, `lifecycle_state="draft"`. **Gate:** `nodes = load_study_nodes(...)`; add the proposed conclusion to the set; `violations = validate_chain(nodes)`; if any violation is on this conclusion → **422** `{error, violations}` (do NOT write, do NOT emit). Else set `lifecycle_state="published"`, write `<study>/conclusions/<id>.yaml`, emit `ConclusionPublished`. Returns `{conclusion_id, event_id}`.

**Routes** in `api/app.py` (mirror `POST /api/finding`): `POST /api/evidence`, `/api/decision`, `/api/conclusion` with `EvidenceCreateBody`/`DecisionCreateBody`/`ConclusionCreateBody` from `investigation_contracts`; `body,status = _chain_views.fn(...)`; `if status != 200: return JSONResponse(...)`.

## Data flow (the chain authored end to end)

`POST /api/finding` (Phase A) → finding node → `POST /api/evidence {findings:[finding id], hypotheses:[text], confidence}` → evidence node (proposed) → `POST /api/decision {evidence:[evidence id], outcome: accept}` → decision (recorded) + (a follow-up advances the evidence to `accepted` — see below) → `POST /api/conclusion {evidence:[evidence id], decisions:[decision id], statement}` → `validate_chain` passes → conclusion published + `ConclusionPublished`.

> **Advancing evidence to `accepted`:** a Decision with `outcome=accept` is the human act that moves the evidence node's `lifecycle_state` `proposed → accepted` (one legal transition). B1 scope: `create_decision`, after writing the decision, advances each referenced evidence node per its outcome (`proposed → accepted` for `accept`, `proposed → rejected` for `reject`, no move for `defer`) via `check_transition`, rewriting the evidence file (emit nothing extra). So a recorded accept-decision leaves its evidence `accepted`, making the conclusion gate satisfiable. The decision is the single act that moves the evidence.

## Error handling

- **Drift guard:** every endpoint writes the node atomically BEFORE `emit_event`; conclusion emits only after a passed gate + committed write.
- **Hard gate:** `/api/conclusion` returns 422 with the `violations` list and writes/emits nothing when the chain is unsound — the irreversible publish cannot fabricate an unauditable conclusion.
- **Validation:** every node is `core.check`-validated before write (never-fabricate parity); malformed bodies → 400; study not found → 404.
- **At-least-once events** ride the Phase-A log; handlers (none in B1) stay idempotent by `event_id`.

## Testing

**investigation-contracts**
- Each new type validates a good node, rejects a bad one (missing field / bad enum outcome).
- Illegal lifecycle transitions rejected (`decision: recorded → pending`, `conclusion: published → draft`, `evidence: accepted → proposed`); legal ones pass.
- `validate_chain`: (a) sound chain → `[]`; (b) evidence with 0 findings → violation; (c) evidence whose `finding/<id>` doesn't resolve → violation; (d) conclusion citing evidence that is not `accepted` → violation; (e) conclusion citing accepted evidence but no accepting decision → violation; (f) the happy full chain (finding→evidence accepted-by-decision→conclusion) → `[]`.

**vivarium-dashboard**
- `node_store.load_study_nodes` reads nodes across all four dirs, keyed by id; tolerant of missing dirs.
- `POST /api/evidence`/`/api/decision`: write-then-emit (node file exists + event in log after); 400 on empty findings / bad outcome; 404 on missing study.
- `create_decision` advances referenced evidence `proposed → accepted` on `accept` (and `→ rejected` on `reject`).
- `POST /api/conclusion`: **422 + no write/emit** when chain unsound (evidence not accepted); **200 + ConclusionPublished** when the decision has accepted the evidence first.
- **End-to-end:** sequence finding → evidence → decision(accept) → conclusion via the TestClient; assert the conclusion publishes (200), the 4 node files exist, and `ConclusionPublished` is the last event; assert that posting the conclusion BEFORE the decision returns 422.

## Out of scope (later increments)

- **B2:** migrating the legacy `findings.entries[]` / `conclusion_verdicts[]` prose into typed nodes; promoting `investigation.yaml`/`study.yaml` to the common node schema; resolving the v2/v3/v4 `schema_version` debt.
- **B3:** typed lifecycle for the multi-axis `status:` fields + `pipeline_gate` edges.
- **B4:** dashboard rendering of the chain + transitions.
- Typed Hypothesis / Question nodes + their endpoints (the agentic-owned upstream).
- Agentic reactions to the new events (Phase C scheduler).
- **Transitive chain soundness:** the `/api/conclusion` gate enforces only *conclusion-level* invariants (its evidence is accepted via a referenced decision). The `finding→≥1 run` and `evidence→≥1 finding/hypothesis` invariants are computed by `validate_chain` but are advisory at write time — no endpoint rejects on an evidence- or finding-level violation. Gating evidence soundness (at `/api/evidence` or transitively at publish) is a B-series follow-up.

## Rollout notes

- Dashboard changes land on `feat/phase-b-evidence-chain` (worktree `/Users/eranagmon/code/vdash-phaseB`, off `origin/main` which includes merged Phase A). investigation-contracts changes on a branch in its repo; re-`pip install -e` is not needed (editable), but bump the package patch version.
- Keep additive on the FastAPI seam; do not touch `server.py` or the existing event/finding routes beyond the DRY base-fields retrofit.
- Shared venv `/Users/eranagmon/code/venv`.
