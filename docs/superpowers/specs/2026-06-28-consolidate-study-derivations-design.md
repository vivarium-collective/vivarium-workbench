# Consolidate Study Derivations — Design

**Date:** 2026-06-28
**Status:** Design — approved, pending spec review
**Repo:** `vivarium-dashboard` (branch `feat/consolidate-study-derivations`, off `main`)
**Part of:** the "streamline / cut overhead and indirection" effort (the architectural layer, beneath the visual declutter).

## Goal

The same study **derivation rules** — the 3-track conclusion verdicts (`biological_validation` / `regression_compatibility` / `explanatory_gain`), the overall verdict, the insight, and the key metrics — are implemented **three times, hand-kept-identical**:
- `lib/single_study_report.py` (`_derive_conclusion_verdicts`, `_derive_verdict`, `_derive_insight`, `_derive_key_metrics`, `_norm_gate_result`, `_GATE_RESULT_NORM`, `_GATE_TO_VERDICT`) — Python, for the generated report.
- `static/study-detail.js:1273` (`_deriveConclusionVerdicts`, `_GATE_RESULT_NORM`, `_normGateResult`) — JS, *"Rules kept IDENTICAL to single_study_report.py."*
- `static/walkthrough.js:6367` (`_deriveConclusionVerdicts`, `_normGateResult`, …) — JS, *"kept IDENTICAL to single_study_report.py and study-detail.js."*

Three copies that must be hand-synced and can drift. Consolidate to **one canonical Python source** that the report, the study page, and the investigation graph all consume — so the rules live in one place and nothing needs hand-syncing.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| Approach | **A — Backend-canonical.** One Python source of the derivations; the server embeds the computed values into the payloads each surface already reads; the JS copies are deleted (JS becomes pure rendering). (Not B, a shared-JS module, which would leave Python+JS as two synced sources.) |
| Scope | The `_derive_*` family (conclusion verdicts, verdict, insight, key metrics + the gate/run/finding normalizers) — the explicitly-"kept IDENTICAL" duplication. |
| Safety | **Parity-preserving** — the canonical module reproduces today's rules exactly; verified by tests. Any latent drift between the three old copies is *resolved* to the canonical, not changed arbitrarily. |

## Current state (grounded)

- Canonical Python derivations live in `single_study_report.py` (lines ~431–555): `_GATE_TO_VERDICT`, `_derive_verdict(spec)->str`, `_latest_outcomes(spec)->dict`, `_derive_key_metrics(spec)->list[dict]`, `_derive_insight(spec)->str`, `_GATE_RESULT_NORM`, `_norm_gate_result(val)->str`, `_derive_conclusion_verdicts(spec)->{biological_validation,regression_compatibility,explanatory_gain: {result, basis}}`.
- **Server-compute precedent exists:** `report_views.build_iset_detail` (report_views.py:640) already computes `computed_gate_verdict` and attaches it to each `studies[]` entry; the template reads `study.computed_gate_verdict` and walkthrough reads `s.computed_gate_verdict`. This is the pattern to extend.
- **Study page:** `study-detail.html` sets `window._study = null` (line 1926), populated by `_bootstrapStudy()` in `study-detail.js`; the JS then recomputes the 3-track verdicts into badges.
- **Investigation graph:** `walkthrough.js` renders study cards from `d.studies` (the investigation-detail payload from `build_iset_detail`), recomputing the 3-track verdicts (line 6601) for the per-card verdict surfaces.

## Architecture

```
lib/study_derivations.py            (NEW, pure — the single source of the rules)
  conclusion_verdicts(spec) -> {bio,regression,explanatory: {result, basis}}
  verdict(spec) -> str ; insight(spec) -> str ; key_metrics(spec) -> [..]
  + the gate/run/finding normalizers
        ▲ import            ▲ import (compute the `derived` block)
        │                   │
single_study_report.py   report_views.build_iset_detail + the study-page payload
  (report renders          → attach study["derived"] = {conclusion_verdicts,
   from the module)            verdict, insight, key_metrics} to each study
                                       │ embedded in the payloads JS already reads
                                       ▼
   study-detail.js (window._study.derived.*) + walkthrough.js (s.derived.*)
   → READ the precomputed values; their _derive*/_GATE_RESULT_NORM copies DELETED
```

### Component ① — `lib/study_derivations.py` (canonical, pure)

Extract the `_derive_*` family + the `_GATE_*`/`_norm_*` helpers out of `single_study_report.py` into a new pure module with public names: `conclusion_verdicts(spec)`, `verdict(spec)`, `insight(spec)`, `key_metrics(spec)`, `latest_outcomes(spec)`, `norm_gate_result(val)`, and the `GATE_TO_VERDICT`/`GATE_RESULT_NORM` tables. No behavior change — same logic, relocated. `single_study_report.py` imports and calls them (its private `_derive_*` become thin aliases or direct calls). The module has no I/O and no dashboard imports → importable anywhere.

### Component ② — server embeds the `derived` block

A single helper `study_derivations.derived_block(spec) -> {conclusion_verdicts, verdict, insight, key_metrics}` packages the four. Attach it as `study["derived"]` wherever the server builds study data for a surface:
- **Investigation payload:** in `report_views.build_iset_detail`, next to the existing `computed_gate_verdict` line, add `"derived": study_derivations.derived_block(study_spec)` to each `studies[]` entry. (Covers the graph cards.)
- **Study-page payload:** the route/builder that populates `window._study` (the `_bootstrapStudy` source) attaches the same `derived` block. (Covers the study page.)

Backward-compatible: `derived` is additive; existing keys (`computed_gate_verdict`, etc.) stay.

### Component ③ — `study-detail.js` reads, deletes its copy

Replace the in-file `_deriveConclusionVerdicts` / `_GATE_RESULT_NORM` / `_normGateResult` (≈line 1273) with a read of `window._study.derived.conclusion_verdicts` (fallback to the existing computed fields / `{}` if absent). Delete the now-unused derivation functions. The verdict-badge rendering stays; only the *source* of the values changes (precomputed instead of recomputed).

### Component ④ — `walkthrough.js` reads, deletes its copy

Same for the investigation cards: read `s.derived.conclusion_verdicts` (and `s.derived.verdict`) from the `d.studies[]` payload instead of recomputing (≈line 6367/6601). Delete the duplicated `_deriveConclusionVerdicts` / `_GATE_RESULT_NORM` / normalizers. The card/badge rendering is unchanged.

## Data flow

Open the report → `single_study_report` calls `study_derivations` (unchanged output). Open a study page → its payload carries `derived`; `study-detail.js` renders the badges from `window._study.derived` (no recompute). Open an investigation → `build_iset_detail` attaches `derived` per study; `walkthrough.js` renders the card verdicts from `s.derived` (no recompute). One rule set, three readers.

## Error handling / compatibility

- `derived_block` is tolerant (the underlying `_derive_*` already handle missing fields → PENDING/GAP/""); never raises.
- JS reads defensively: `(window._study.derived || {}).conclusion_verdicts` / `(s.derived || {})…` with the same empty-state fallback the old code had, so a payload without `derived` (older snapshot) still renders (degrades to the empty/PENDING state rather than erroring).
- Additive payload keys → no break to other consumers.

## Testing

- **`tests/test_study_derivations.py` (parity, the core safety net):** for representative specs (gate passed/failed/partial/pending; runs all-completed / some-errored / mixed / none; findings with interpretation-tier / plain / none), assert `conclusion_verdicts`/`verdict`/`insight`/`key_metrics` equal the values the current rules produce. These pin the canonical behavior so the refactor can't silently change it.
- **`single_study_report` regression:** its existing report test(s) still pass (it now imports the module) — proves the report output is unchanged.
- **Embed:** a test that `build_iset_detail` attaches `derived` (with the four keys) to each study; and that the study-page payload carries it.
- **JS:** `node --check` on study-detail.js + walkthrough.js after deletion; assert (grep test) that `_GATE_RESULT_NORM`/`_deriveConclusionVerdicts` no longer appear in either JS file and that `.derived` is read. Manual: the verdict badges on a study page + the investigation cards render the same values as before.

## Staging (each independently shippable + parity-verified)

1. **Extract `study_derivations.py`** + `single_study_report` imports it. Pure refactor; parity tests; report regression green. No surface change.
2. **Embed `derived`** in both payloads (`build_iset_detail` + the study-page source). Additive; nothing consumes it yet.
3. **`study-detail.js`** reads `window._study.derived`; delete its derivation copy.
4. **`walkthrough.js`** reads `s.derived`; delete its derivation copy.

After stage 4, the rules exist only in `study_derivations.py`.

## Out of scope

- The card **confidence badge** computation in `walkthrough.js` `_renderInvestigationDag` (Accepted/Investigating/Refuted from gate, line ~5594) — a *different* derivation; consolidating it is a clean follow-up once the `derived` block is flowing (it can read `s.derived.verdict`).
- The presentation split (interactive page vs static report) — intentionally preserved; only the derivation source is unified.
- The typed-AIG `chain_derivation.py` (B2) overlaps conceptually (it also derives from verdicts); aligning the two derivation sources is a later step, not this slice.
- Schema-debt collapse of the underlying fields (B2b).
