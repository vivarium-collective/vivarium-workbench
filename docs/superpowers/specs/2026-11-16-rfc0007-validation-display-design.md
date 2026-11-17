# Investigation / Study field model → Discovery Engine → Workbench display

**Date:** 2026-11-16
**Status:** reference + proposed PR scope
**Scope:** vivarium-workbench (display), discovery-engine (source of truth), viva-template (schemas)

This doc maps the investigation/study field model to what the discovery engine
(DE) computes, where those outputs land, and how much of them the workbench
renders — then scopes the one display gap worth closing next: a panel for the
unified **RFC-0007 validation records** that the recent contract/audit
consolidation produced but the UI does not yet surface.

All file:line anchors below were verified against `origin/main` on 2026-11-16.

---

## 1. The field catalog

Authored in viva-template `template/.pbg/schemas/`:

- **study.schema.json** — **64** top-level fields
- **investigation.schema.json** — **25** top-level fields

They fall into four natural groups:

| Group | Study fields (representative) | Investigation fields |
|---|---|---|
| **Human-authored prose / design** | `purpose`, `question`, `hypothesis`, `claim`, `experiment`, `result`, `key_assumptions`, `limitations`, `biological_summary`, `report`, `study_card`, `model_change`, `implementation_requirements` | `lead`, `biological_story`, `glossary`, `how_to_read`, `executive` (prose) |
| **Structured model / run spec** | `baseline`, `conditions`, `variants`, `simulation_set`, `interventions`, `readouts`, `observables`, `contract`, `enforced_params`, `runs` | — |
| **Status & verdict family** | `design_status`, `implementation_status`, `simulation_status`, `evaluation_status`, `expert_review_status`, `gate_status`, `execution_status`, `verdict`, `evidence_tier` | `status`, `closed_at` |
| **Graph / relational** | `parent_studies`, `parent_relations`, `pipeline_gate`, `builds_on`, `findings`, `followup_proposals`, `discovery_implications` | `argument`, `knowledge`, `acceptance_criteria`, `studies` |

Deprecated/legacy fields still resolving: study `conclusion`, `objective`,
`observables`, `builds_on`, `upstream_state_consumed`, top-level `runs`.

---

## 2. What the DE fills — derive-first, authored-wins

The DE almost never overwrites a human field. When it computes a status or a
verdict it writes a **parallel `computed_` slot** beside the authored one and
only *flags divergence*; the prose stays human-owned.

**Genuinely DE-computed scientific values:**

| Output | Function | Written where |
|---|---|---|
| `simulation_status`, `evaluation_status` | `study/status.py` | **in-memory only** — derived on read, disagreement flagged, authored field untouched |
| study verdict roll-up | `study/verdict.py` `roll_up_verdict` | `pipeline_gate.gate_evaluator` (parallel slot) |
| finding quantitative slots (`evidence.observed`, `expected.range`, `divergence_factor`, provenance) | `study/finding_observations.py` | fills empty code-slots in `findings[]`; never the prose (`statement`/`explanation`) |
| investigation acceptance roll-up | `investigation/status.py` | `executive.computed_verdict_status` / `computed_acceptance` (parallel) |
| rigor scorecard, audit (L0–L5), lint, argument/knowledge/narrative/synthesis | `reporting/rigor`, `study/audit`, `reporting/linter`, `projection/*` | **in-memory projections** (lint → `reports/*.html`); nothing to YAML |

**DE-writes-mechanically (not science):** `runs[]` metadata + `computed_outcomes`,
schema migrations (`conditions`/`readouts`/`simulation_set` canonicalization),
band-cite provenance, and child-study authoring (`model_change`, child
`study.yaml`, `followup_proposals[].status`). The human-recorded `verdict` write
is a validated passthrough (off-vocab rejected), not a derivation.

**Auto-stamped lifecycle:** investigation `status`/`closed_at`/`report_url`/
`contributors` (the last from `git log`) on close.

**DE-reads-only (you author, it consumes):** `claim` (the cycle's sole
proposition), `acceptance_criteria`, `design_status`/`implementation_status`/
`gate_status`/`expert_review_status`, `hypothesis`, `contract`,
`literature_anchors`, and the authored halves of `argument`/`knowledge`/
`executive`.

**DE never touches:** `biological_summary`, `discovery_implications`,
`evidence_tier`, `execution_status`, `limitations`, `study_card`, `runtime`,
`description`, `glossary`, `lead`, `scientific_argument`.

---

## 3. Where the DE's outputs go

Three destinations — and most are **not** the spec YAML:

1. **In-memory, recomputed every cycle (never persisted).** `run_cycle()` is
   pure: it returns one bundle `{knowledge, argument, narrative, audit, gates,
   open_calls, validations, suggestions, signals, revision_proposals, …}`
   (`discovery_engine/discover/cycle.py:97`). The full projections, the
   **RFC-0007 validation records** (`validations` =
   `run_validators(ctx)`, `cycle.py:132`), rigor, and audit all live here and
   vanish after the call.

2. **Persisted to investigation.yaml as DE-managed extension blocks** (outside
   the authored schema; validated against `investigation-extensions.schema.json`):
   a **slim `cycle_state`** summary (not the full graphs), `validation_snapshot`
   (**signatures only**, for staleness via `snapshot_of` / `stale_records`),
   `revision_proposals`, `viz_proposals`, `capability_gaps`, `finding_candidates`,
   `executed_actions`, `discoveries`. `persist_cycle` at `cycle.py:305`.

3. **Persisted as parallel computed slots** next to authored fields:
   `gate_evaluator`, `computed_outcomes`, `executive.computed_*`, close metadata.
   All via atomic comment-preserving round-trips that re-read disk first.

So the validator **records themselves are ephemeral** — only a status census and
a signature snapshot survive a cycle.

---

## 4. What the workbench displays today — and the gap

**Well surfaced.** The study-detail page renders the 6-axis status ladder, the
verdict, and (Assurance › Audit) the rigor scorecard, the L0–L5 reproducibility
audit, and test-sufficiency. The investigation SPA shows investigation-rigor,
framework-metrics, the argument/AIG graph, the report linter, and a live
Discovery-cycle panel driven by `run_cycle`. These read the **older, direct**
derivations — `reporting.rigor.study_rigor` / `investigation_rigor` /
`framework_metrics` (`lib/models.py:831,846`, `lib/system_info.py:35`),
`reporting.linter` (`lib/composite_lookup.py:767`), `reporting.report`
(`lib/models.py:3019`).

**The RFC-0007 validation layer is almost entirely unsurfaced.** The workbench
has **zero** references to `discovery_engine.reporting.validation` — it never
imports `run_validators`, `coverage_matrix`, `epistemic_separation_violations`,
or `validation_record` (verified: grep over `vivarium_workbench/` returns
nothing). The validator **records** reach the UI *only* through the `run_cycle`
bundle's `validations` key, and only in two reduced forms:

- **An aggregate census** — `investigation_report.py:1003-1035` collapses the
  records to `validator_census` (counts by `execution_status`),
  `validator_pass_rate`, and a `stale_validations` count. No per-record detail.
- **A narrow visual-fix extraction** — `cycle_trace.py:648` pulls only
  `type==visual` / `scope==figure` records to recover `derivable_fix` addresses
  for viz frontier items. Not a validation display.

So three things the layer computes have **no display surface at all**:

1. **The individual validation records** — type × scope × target, with
   `execution_status`, `scientific_judgment`, evidence, and reason. Only a
   census of them shows.
2. **`coverage_matrix()`** — the **8 validator types × 7 scopes** grid
   (`VALIDATOR_TYPES`, `VALIDATOR_SCOPES` in `reporting/validation.py:36,41`)
   showing which (type, scope) cells have validators and which are blank.
3. **`epistemic_separation_violations()`** — the check that execution-only
   types (`structural`, `computational`, `visual`) never assert a
   `scientific_judgment` (`_EXECUTION_ONLY_TYPES`, `validation.py:54`).

**Also noted (adjacent gaps, not in this PR's scope):**

- **RFC-0006 knowledge projection** (`vivarium_workbench/lib/knowledge_step.py`)
  is wired to nothing — only its own test imports it.
- **Rigor is absent from the published/exported report** (live SPA only).
- **Finding-observations compute** has no read-view (mutation endpoint; results
  show only inside finding cards).

**The through-line:** the consolidation routed the DE's audits onto the unified
RFC-0007 record, but the workbench still renders the pre-consolidation surfaces
and reduces the unified records to a census. The unified audit layer is, for
display purposes, **backend-only**.

---

## 5. Proposed PR — RFC-0007 validation panel

**Goal.** Give the unified validation records a first-class read-view in the
investigation SPA: the records themselves (inspectable, grouped), the coverage
matrix, and any epistemic-separation violations — the "unbuilt half" of the
consolidation.

**Backend (one read-only route).** Add a view that returns, for an
investigation:
- the full `validations` records from the cycle bundle (already computed by
  `run_cycle`; no recompute needed — reuse `cyc.get("validations")`);
- `coverage_matrix()` over the active registry;
- `epistemic_separation_violations(records)`;
- the existing `validator_census` / `validator_pass_rate` / `stale_validations`
  for the header.

Degrade to `None` on an older engine (same pattern as
`investigation_report.py` and `build_generation_requests`), so the panel never
breaks a workspace predating the layer.

**Frontend (one SPA panel).**
- **Header:** pass/fail/error census + pass-rate + stale count (what exists
  today, promoted to its own panel).
- **Coverage matrix:** 8 types × 7 scopes grid; filled cells link to their
  records, blank cells read as "no validator." Execution-only types visually
  marked (they can't carry a scientific judgment by rule).
- **Record list:** grouped by type → scope, each row showing `target`,
  `execution_status`, `scientific_judgment` (or "n/a — execution-only"), and
  expandable evidence/reason.
- **Epistemic-separation violations:** a flagged section; empty = a green
  "separation holds" note.

**Tests.** Static JS-presence assertions + a Python test that the route reduces
a known cycle bundle to the expected census/matrix/violations shape, plus a
degrade-to-None test on a stubbed older engine.

**Non-goals.** No new persistence (records stay ephemeral by design); no change
to `run_cycle` or the DE validation layer; the older rigor/audit surfaces stay
as they are (this panel is additive). RFC-0006 knowledge, published-report
rigor, and the finding-observations read-view are separate follow-ups.
