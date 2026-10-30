# Always-fresh status displays — design

**Status:** draft for review (nothing built until approved) · revised after builder-check
**Date:** 2026-10-30
**Repo:** vivarium-workbench (lands on `main`)

## Intent / the why

Status badges across the workbench read **frozen top-level fields** in
`study.yaml` / `investigation.yaml` (`execution_status`, `simulation_status`,
`gate_status`, `evaluation_status`, `verdict`, …). The builder-check confirmed
these top-level fields are **not maintained by anything**: the post-run flush
updates `runs[]`, `runs[].outcomes`, `pipeline_gate.gate_evaluator`, and the
report-card artifacts — but **no code writes the top-level
`execution_status`/`gate_status`/`evaluation_status`**. They are hand-authored
scaffold defaults that stay frozen at their initial value while the evidence
underneath moves on. The badge then rots to "Planned / pending" even though the
evidence (runs.db, report-card verdicts) proves the study ran.

Found concretely: eukaryogenesis **S1–S5** each have committed runs (5/9/5/3/3)
and passing report cards (S3 2/2, S4 1/1, S5 1/1), yet all five cards read
`execution_status: not_run` / `gate_status: not_started` / `verdict: none` and
show "Planned / pending". The run/test counts on the same card are truthful
because they read evidence live — so each card shows two disagreeing
truth-sources.

A read-only survey confirmed the split is **systemic**: ~7 study/investigation
status surfaces read frozen fields, while the run counts, report-card tab,
rigor scorecard, audit ladder, and the discovery-cycle panel recompute from
evidence on every read. The discovery-cycle panel is the proof the fix works:
it persists nothing to a badge and recomputes pure projections per read.

**Fix:** make every mechanical status display derive from evidence on read — so
it can never go stale — backed by a minimal `reconcile` that heals the one
stored mechanical field (`execution_status`) when it should be committed for the
engine / CLI / git.

## The schema reality (from the builder-check — reshapes the design)

- The **canonical** viva-template schema has collapsed the axes:
  `simulation_status`, `evaluation_status`, **and** `status` →
  `execution_status`; `gate_status` → **`verdict`**. So canonically there is
  exactly **one mechanical field, `execution_status`**, plus the authored
  `verdict`. There is **no mechanical "gate" field** — its canonical home is
  `verdict`, which must stay authored.
- Nothing writes those top-level fields today (above). Reconcile would be the
  **first** writer of `execution_status`.
- The **served workspace (viva-genesis) uses an older schema**: `execution_status`
  is absent; `simulation_status ∈ {not_run, running, ran, failed}` (no
  `completed`); `gate_status ∈ {blocked, needs_calibration, passed, failed,
  stale}`. So a derived value must be **mapped to the served workspace's own
  fields/enums** before it can be written or validated.

Consequence: **derive-on-read is the real, schema-independent win** (it writes
nothing, so the collapsed/older schema doesn't matter). **Persist/reconcile is
minimal** — it writes only `execution_status`, mapped to the served workspace's
schema. Gate/evaluation pass-fail is derived for display and durably held by the
report-card `verdict.json` + runs.db that the derivation already reads; it is
**not** written to any stored field.

## Goals

1. Every mechanical status badge reflects the true evidence on **every read**,
   regardless of how a run landed or which schema the workspace uses.
2. **One shared** evidence-derivation layer feeds all surfaces.
3. A minimal `reconcile` that persists the one mechanical axis (`execution_status`)
   on demand and after a run lands, leaving authored axes alone.
4. Fix the two freshness bugs the survey found (below).

### Surfaces in scope (all read frozen fields today)

- Study-card **Progress** pill (execution)
- Study-card **verdict** pill
- Study-card **status dot** (Accepted/Investigating/Refuted/Blocked)
- Study-card **computed gate verdict**
- Study-card **rigor split** (pins X/Y)
- **Plan-progress board** (A→E rows)
- **Investigation header** status + verdict rollup; graph study nodes

### The two bugs to fix in the same pass

- **B1 — card "Tests X/Y passed" is not live.** It reads `runs[].outcomes` out
  of `study.yaml` (`behavior_test_card.build_behavior_tests_verdict`). Fix: keep
  the per-test counts + glyph row, but recompute them **live** from the latest
  run's evidence (the same computation `auto_evaluate` does) rather than from
  the frozen `runs[].outcomes`. (Not switched to report-card-verdict counts —
  that would change the number's meaning and break the per-test glyph row.)
- **B2 — investigation list is fresher than detail.** The list computes status
  multi-axis with a live heartbeat (`_study_display_status`); the detail rolls
  up members' raw `status` (`report_views.py:1206`). Fix below (Decision 6) —
  note it is a real refactor, not a one-line swap.

## Non-goals

- **`verdict` and `expert_review` stay authored** — human/engine adjudications,
  never auto-derived or auto-written. The gate/evaluation pass-fail is *derived
  for display* only and is **never persisted as a separate stored field** (no
  valid home; the canonical gate home is the authored `verdict`).
- **No discovery-engine change now.** The engine's
  `evidence_integrity.reconcile_evidence_state` classifies *findings* by
  provenance — it is a **different** projection, not the execution/gate axes, so
  there is nothing to reuse there. The probes are net-new in the workbench; the
  engine may adopt them later.
- **No change to the evidence sources** (runs.db schema, report-card format).

## Anti-goals (a failure even if it technically works)

- A derivation that silently overrides a genuine human `verdict`.
- `reconcile` writing a value or field invalid for the served workspace's schema
  (e.g. `execution_status: completed` into a workspace that has neither).
- A per-render cost that slows investigation pages; evidence is read once per
  study per request, not once per badge.
- Writing to yaml on every read. Persistence happens only via explicit
  `reconcile` and the post-run flush.

## Constraints

- vivarium-workbench only; lands on `main`; TDD; no AI attribution.
- The core derivation is a **pure function** of `(study_spec, evidence)`.
- **Precedence (load-bearing):**
  - *Mechanical (execution):* **evidence wins** — the latest run in runs.db
    determines execution; any stored `simulation_status`/`status` is a stale
    cache, even a stored `failed` that a newer successful run supersedes.
    (Nothing authors execution meaningfully.)
  - *Authored (verdict, expert_review):* **authored wins** — never overridden.
- **Schema-mapped writes:** `reconcile` resolves the served workspace's
  `study.schema.json` and writes `execution_status` into whatever mechanical
  field+enum that workspace actually has, validating before write (see Open
  question for how far to take this).

## Decisions made (my calls — check these)

1. **Reconcile persists exactly one mechanical field: `execution_status`**
   (schema-mapped). Gate/evaluation are not persisted (no valid home). Reconcile
   is the *first and only* writer of that field — no competing writer exists.
2. **Evidence probes** (new, pure) read the anchors the LIVE surfaces already
   use: runs.db (latest status + count) and `viz/report_card/*.verdict.json`
   (via `has_graded_report_cards` / the report-card verdict reader). They return
   a schema-neutral `StudyEvidence` bundle: `execution ∈
   {completed,failed,running,not_run}`, `gate ∈ {passed,failed,not_started}`,
   `evaluation ∈ {evaluated,not_evaluated}`, behavior-test pass/total. The
   reconcile writer maps `execution` to the workspace schema; display uses the
   bundle directly.
3. **Derive-on-read everywhere.** `derive_execution_status`/`derive_verdict`
   **and** `_study_display_status` + `compute_investigation_status` take the
   bundle and apply precedence. This is a real signature change for the latter
   two (status-strings → `(spec, evidence)`) and is the bulk of B2 — reusing
   `_study_display_status` alone does not help, as it too reads frozen fields.
   Callers read evidence once per study per request.
4. **Reconcile is explicit, not on-read.** `reconcile_study_status` writes only
   when drifted; `reconcile_investigation` loops. Endpoints
   `POST /api/study/{slug}/reconcile-status` and
   `POST /api/investigation/{slug}/reconcile-status`, plus a call from the
   post-run flush so future runs self-heal `execution_status`.
5. **B1** as above — live per-test counts, glyph row preserved.
6. **B2** — detail rollup uses the evidence-threaded
   `_study_display_status`/`compute_investigation_status`, so list and detail
   agree **and** are both evidence-fresh.
7. Mixed-state → `execution`: `completed` if ≥1 completed/ran row and none
   running; `failed` if runs exist and all failed; `running` if any running;
   `not_run` if none. `gate`: `passed` if all graded cards pass; `failed` if any
   fail; `not_started` if none graded.

## Write-back strategy (decided: schema-aware, no forced migration)

`reconcile` resolves the **served workspace's own** `.pbg/schemas/study.schema.json`
and writes the derived execution state into whatever mechanical field + enum
that workspace actually has — canonical → `execution_status: completed`;
viva-genesis's older schema → `simulation_status: ran` — validating the value
against that schema before writing. No workspace is migrated as a precondition;
the mixed 39-repo ecosystem is handled as-is, and reconcile never writes a value
or field invalid for the target schema. The field+enum resolution is a small
schema-driven mapping layer (read the schema's non-deprecated mechanical field
and its enum, map the bundle's canonical value to the nearest valid enum member:
`completed`→`ran` where `completed` is absent, etc.). Display freshness via
derive-on-read is independent of all this and needs no schema resolution.

## Left to the builder

- Whether probes live in `study_ontology.py` or a new `study_evidence.py`.
- The exact report-card verdict reader to reuse (the one
  `has_graded_report_cards` / the Report Cards tab use, so card and tab agree).
- Request-scoped caching of the evidence read.
- Surface staging order (all in scope; sequencing is a plan choice).

## Backfill / acceptance

Once built, every card is fresh on read regardless of reconcile. Running
`reconcile-investigation` on eukaryogenesis then persists each study's
`execution_status` to match evidence, schema-mapped (on viva-genesis's older
schema that is `simulation_status: ran`). Gate pass/fail (S3/S4/S5 passed,
S1/S2 failed) shows live on the cards from the report cards — not written to a
stored field. Acceptance: the five cards stop showing "Planned/pending" and show
their true execution state + live gate, and a future run that bypasses the flush
is correct on read with no reconcile.
