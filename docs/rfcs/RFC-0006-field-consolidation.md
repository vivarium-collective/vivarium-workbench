# RFC-0006 — Field Consolidation onto the Formal Normal Form

**Status:** Draft · additive-first · non-destructive · continues RFC-0005
**Depends on:** RFC-0005 (the formal objects are the consolidation target) and the
existing `vivarium-workbench migrate-fields` tooling (`deprecated` + `x-replaced-by`).

> **Thesis.** RFC-0005 made the six formal objects a *normal form*. A field that
> the formal layer can now **derive** should stop being authored/stored, and the
> parallel vocabularies should become **one** canonical vocabulary. This RFC is
> the field-level cleanup that follows — done derive-first and back-compatibly, so
> no investigation breaks and nothing is lost.

Measured on the live genesis investigation (21 studies, **51 top-level study
keys**). The schema already marks **8 deprecations**; the data was never migrated,
and the formal layer lets us go further.

## Principles (all moves obey these)

1. **Derive-first.** The consolidated value is *computed* from the formal layer or
   from `runs`; the old fields stay readable and feed the projection. Nothing is
   deleted until a reader never needs it.
2. **Back-compatible.** Deprecate via `deprecated: true` + `x-replaced-by` in the
   schema (the `migrate-fields` CLI already applies provably-safe 1:1 renames; the
   value-aware collapses it *reports* are the ones the formal roll-up now makes
   safe). v1/v2/v3 specs keep validating.
3. **Non-destructive migration.** Every data migration runs **dry-run first**,
   prints a per-study before→after, and is applied only on explicit approval —
   never an in-place rewrite of real investigations without review.
4. **Phased by leverage × safety.** A pure rename is safe now; a field a runner or
   the DAG reads is a *rewire*, scheduled later.

## The moves

### Move 1 — Collapse the six progress axes → one (highest leverage, safest)
Today every study carries `status · design_status · simulation_status ·
evaluation_status · gate_status · phase` (+ `execution_status`). Four are already
schema-deprecated → `execution_status`. The formal layer derives progress from
`runs` (`study_ontology.derive_execution_status`).

- **Keep:** `execution_status` — the single ordinal lifecycle position.
- **Deprecate → `execution_status`:** `design_status`, `simulation_status`,
  `evaluation_status`, `implementation_status` (done), plus `status`, `phase`.
- **Ordinal mapping** (the value-aware collapse): `proposed` < `planned`
  (design approved) < `running` (building/simulating) < `completed` (ran +
  evaluated) < `failed`. A study with `design_status: approved`,
  `simulation_status: ran`, `evaluation_status: evaluated` → `execution_status:
  completed`. Derived from runs when present; the authored statuses are the
  fallback.
- **`gate_status` is NOT a progress field** — it is a verdict/gating projection;
  see Move 2 (deprecate) and Move 6 (rewire the DAG off it).

**6 → 1.** This is the most visible slop and the schema already intends it.

### Move 2 — Unify the verdict carriers → `verdict` + qualifiers
`verdict` (canonical 5) · `gate_status` · `result`/`conclusion` (deprecated) ·
`confidence` · `evidence_tier` · `gate_reason`.

- **Keep:** `verdict` (canonical), `confidence`, `evidence_tier` (qualifiers),
  `gate_reason` (the rationale).
- **Derive:** `gate_status` from `verdict` + `runs` (it adds no independent info).
- **Drop:** `result`/`conclusion` (already deprecated → `result`).

### Move 3 — One typed dependency list, in the CANONICAL field
Four keys named the same parent studies with different facets:
`parent_studies` (`{study, condition}`) · `parent_relations` (`{study, relation}`) ·
`builds_on` · `upstream_state_consumed` (`{from, provides}`).

The framework already has a canonical home: **`pipeline_gate.prerequisites`**
(Section 2 of the 8-section structure). `parent_studies` is the *legacy* fallback
(`investigations.py` reads `pipeline_gate.prerequisites` first with a
DeprecationWarning; `study_canonicalize` deletes `parent_studies`), and its
entries already carry `condition` / `required_gate_status` / `outputs_used`.

- **Enrich `pipeline_gate.prerequisites` entries** to carry all three formal
  relation classes as distinct facets: `condition` (organizational gate),
  `relation` (epistemic: builds-on/tests/refutes/…), `provides`/`outputs_used`
  (computational). The entry is schema-permissive and the DAG normalizer passes
  extra fields through, so this is additive.
- **Deprecate → `pipeline_gate.prerequisites`:** `parent_studies` (Tier 2b),
  `parent_relations`, `builds_on`, `upstream_state_consumed`.
- **Tier 2a** first merged the four into enriched `parent_studies` entries (one
  unified accessor, `study_relations`, derive-first); **Tier 2b** moves that home
  to the canonical `pipeline_gate.prerequisites` and retires `parent_studies`,
  aligning with the framework instead of inventing a competing `relations` field.

**4 → 1 (canonical).**

### Move 4 — Drop study-level grouping that is now derived
`concept` · `plan_id` tie a study to its gate, but the gate↔member mapping lives
in `investigation.yaml:plan_gates.member_studies` and the synthesis spine already
derives it (RFC-0005 `synthesize_gates`).
- **Deprecate `concept`, `plan_id`** on the study; derive the gate membership.

### Move 5 — One recursive Concept/gate hierarchy at the investigation level
`plan_gates` (7, fine: `criterion`/`extends`) **and** `plan_acts` (5, coarse:
`primary_question`/`decisive_exit_condition`, each containing `gates`) both carry
`member_studies`/`off_spine`/`pending_studies`.
- **New:** acts *contain* gates; an act's members are **derived** from its child
  gates. Stop restating `member_studies`/`off_spine` at the act level.
- This is the Concept layer at two granularities — one structure, two depths.

### Move 6 — Authored argument becomes a thin overlay over derived claims
`key_claims` (7) · `scientific_argument` · `competing_frameworks` (5) overlap with
the Claims + challenge/alternative edges the argument layer now derives.
- **Reframe** as an authored *overlay* (authored-wins / empty-never-clobbers, like
  the knowledge store) over the derived claims — not a parallel hand-maintained
  list. The Background tab then reads the derived claims.

## Net effect

`study.yaml`: **~51 → ~30** meaningful keys, mapping one-to-one onto the six formal
objects; one canonical vocabulary for progress, verdict, and relations across every
tab. `investigation.yaml`: one grouping structure instead of two; the argument
fields derived.

## Rollout (phased; each phase dry-run → review → apply)

| Phase | Move | Kind | Reader to rewire |
|---|---|---|---|
| P1 | 1 progress collapse | schema deprecate + derived ordinal | none (derive_execution_status exists) |
| P2 | 3 typed `relations` | new field + deprecate 4 | argument/DAG/dataflow readers read `relations`, fall back to the old keys |
| P3 | 2 verdict unify + 6 `gate_status` derivation | rewire | DAG unblocking (`_condition_satisfied`) reads derived gate, not stored `gate_status` |
| P4 | 4 drop `concept`/`plan_id` | derive | overview/grouping readers |
| P5 | 5 acts⊕gates merge; 6 argument overlay | investigation schema | plan/background tabs |

**P1 is the recommended start:** schema already wants it, the formal roll-up makes
the value-aware collapse safe, it removes the most slop, and it rewires nothing
(the derivation already exists).

## Risks / non-goals

- **`gate_status` is load-bearing** for DAG unblocking — deprecating it is a
  *rewire* (P3), not a rename; the gate condition must read the derived verdict
  first. Do not drop `gate_status` before P3 lands.
- **The progress collapse is intentionally lossy** (6 granular milestones → 1
  ordinal). The milestone detail survives in `runs`/git history; the ordinal is
  the single source of truth for display and gating.
- **Not a re-authoring of science.** Every move preserves the authored content;
  this is schema normalization, not content change.
