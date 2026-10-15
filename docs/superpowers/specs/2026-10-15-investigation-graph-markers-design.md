# Investigation-graph markers: verdict × evidence-tier rethink

**Date:** 2026-10-15
**Status:** approved design → spec
**Repos touched:** `vivarium-workbench` (renderer, legend, tests) and `viva-genesis` (study-schema field + derive script + 23 study.yamls)

## Intent

The Investigation-graph view (nodes = studies, edges = what each result leads to)
has two marker problems the user asked to fix:

1. **The confidence badge is a disguised binary.** It is derived purely from
   `gate_status` — 21/23 studies are `passed` → "Accepted ✓", 2 are `failed` →
   "Refuted ✗". A one-seed conceptual demonstration gets the same confident green
   ✓ as a result calibrated against data, and the two honest-negative capstones
   (Phase 2, Phase 9) render as a blunt red "Refuted." "Accepted" reads as too
   final for results that only carry partial evidence.
2. **The edge types are starved, not used.** Studies authored real relationships
   in a `parent_relations:` block (17/23 studies: `leads-to`, `alternative-to`,
   `refutes`, `precedes`), but **no code reads that block** — the renderer defaults
   every edge to `leads-to`. The legend advertises types the investigation never
   uses (`evidence`, `calibrates-threshold`) and omits the ones it does.

**Goal:** a two-axis node marker that separates *verdict* (what the study
concluded) from *evidence strength* (how much to trust it), and an edge
vocabulary wired to the relations studies actually author — so the graph is
honest about partial evidence and the edge types carry real meaning.

**Success looks like:** on the real 23-study investigation, honest-negatives read
as findings (not failures), a calibrated result looks stronger than a
demonstration, and edges show the authored leads-to / alternative-to / refutes /
precedes distinctions. Nothing breaks for a study that lacks the new fields.

## Vocabularies (confirmed with user)

- **verdict** ∈ `{supported, qualified, honest-negative, refuted, mixed}`
  - supported — claim holds; clean positive result.
  - qualified — holds but with a stated caveat (partial / structure-sensitive / marginal agreement).
  - honest-negative — a legitimate negative finding *by design* (Phase 2 structure-sensitive attribution; Phase 9 "not sufficient"). Explicitly not a failure.
  - refuted — a hypothesis genuinely disproven, or an adversarial control correctly rejected (og-adv).
  - mixed — sub-claims with differing outcomes.
- **evidence_tier** ∈ `{demonstration, mechanistic, calibrated}`
  - demonstration — proof-of-concept (single-seed / mean-field / illustrative).
  - mechanistic — a real mechanistic model's prediction, not yet calibrated.
  - calibrated — fitted/validated against real data (e.g. Phase-1 vs Lost City).
- **edge relation** ∈ `{leads-to, model-input, alternative-to, refutes, precedes}`
  (drop the unused `evidence`, `calibrates-threshold`).

## Architecture decision

`verdict` and `evidence_tier` are **real study fields** — authored once by a
derive script, then expert-correctable in the yaml — not live-derived in the
renderer. Durable, survives re-renders, overridable when the auto-call is wrong.
The renderer reads them and only falls back to gate-based derivation when they
are absent. (The live-derive alternative is the rendering-only option the user
rejected because the strength axis can't be expressed from existing fields.)

## Components

### A. Data model — `viva-genesis`

1. **Schema** (`viva-genesis/.pbg/schemas/study.schema.json` — the workspace's
   vendored copy, which `scripts/lint-workspace.py` validates against): add
   optional `verdict` (enum of the five values) and promote `evidence_tier` to a
   first-class optional enum of the three values. Both optional so
   existing/unmigrated studies validate. The *canonical* schema lives in
   `viva-template/.pbg/schemas/`; propagating the fields there (so every workspace
   blesses them) is a small follow-up, out of scope for this investigation.
2. **Derive script** `scripts/derive_study_verdict_tier.py`:
   - Globs `workspace/studies/*/study.yaml`.
   - Computes a best-effort initial `(verdict, tier)` from existing signals:
     - verdict: the two `gate_status: failed` studies are honest-negatives *by design* (og-01-cal-electrochemistry = Phase 2 structure-sensitive attribution; og-08-alt-robust-innovation-set = Phase 9 "not sufficient") → `honest-negative`; an adversarial control whose authored relation is `refutes` (og-adv-generic-nonequilibrium) → `refuted`; `gate_status: passed` with a caveat signal (tier=demonstration, marginal finding `verdict` row counts, structure-sensitive wording in `caveats`/`limitations`) → `qualified`; otherwise `passed` → `supported`; multiple finding outcomes → `mixed`. **The derivation is a heuristic seed, not an exact algorithm** — the human review step corrects any wrong call, so edge cases don't need to be nailed here.
     - tier: existing `evidence_tier` if set; calibration studies (og-cal) → `calibrated`; single-seed/mean-field wording → `demonstration`; else `mechanistic`.
   - Writes the two fields into each `study.yaml` (promote-into-canonical, lossless, same atomic-write pattern as the existing migration scripts).
   - `--check` dry-run prints the proposed pair per study and exits non-zero if any study would change.
   - **Idempotent**: re-running yields no diff. Experts edit the yaml to correct a call; the script never overwrites a hand-set value that differs from its derivation unless `--force`.
3. **The 23 study.yamls**: the derive script's output, reviewed. These are data,
   kept correctable.

### B. Server passthrough — `vivarium-workbench`

Verify the study payload the SPA receives (investigation summary + per-study)
carries `verdict` and `evidence_tier` straight from `study.yaml`. If the summary
builder whitelists fields, add these two. (Likely already passed through since
the SPA reads many raw study fields; confirm, don't assume.)

### C. Node marker — `vivarium-workbench/static/walkthrough.js`

Replace the gate-binary `_studyStatusState` / `_STATUS_META` (≈ lines 52–82) with
a two-axis model:

- **`_studyVerdict(s)`** → one of the five verdicts. Precedence: explicit
  `s.verdict` wins; else fall back to the current gate-based derivation mapped
  into the new set (`passed → supported`, `failed → refuted`), so unmigrated
  studies still render.
- **`_studyTier(s)`** → one of the three tiers, or `null` (ungraded) when absent.
- **`_VERDICT_META`**: color + icon per verdict —
  supported `◆` `#16a34a` · qualified `◇` `#0d9488` · honest-negative `⊖` `#d97706`
  · refuted `✗` `#dc2626` · mixed `◐` `#64748b`.
- **Marker render** (node card ≈ 11062–11085): card top-border = verdict color;
  verdict icon + label pill (reuse `.aig-status-badge`); a **tier grade** beside
  it — a 3-pip indicator (`●○○` demonstration, `●●○` mechanistic, `●●●` calibrated)
  with a tooltip; omit the pips when tier is `null`.

### D. Edges — `vivarium-workbench/static/walkthrough.js`

1. **Read the authored relation.** In the edge builder (`dagEdgesFor` /
   `_dagEdges`, ≈ 10655–10881), resolve each edge's relation from
   `parent_relations` (match on parent `study` slug) when present; else keep the
   current artifact→`model-input` / else `leads-to` synthesis. Do **not** let the
   server `study_edges` hardcode (`relation: e.artifact ? 'model-input' : 'leads-to'`)
   discard an authored relation — prefer `parent_relations` over the synthesized value.
2. **Style map** `_DAG_REL_STYLE` (≈ 10676–10690): keep `leads-to`, `model-input`;
   add `alternative-to` (slate `#64748b`, dash `6 3`) and `precedes` (grey
   `#94a3b8`, dash `2 3`); remove `evidence` and `calibrates-threshold`. Keep the
   `refutes → refutes-alternative` alias (red). Add aliases `alternative-to`,
   `precedes` so no authored value falls through to the default.
3. **Arrowhead** (≈ 11248–11251): make the marker inherit the edge color instead
   of the hardcoded grey (`fill="#94a3b8"`), e.g. per-edge marker or `context-stroke`.

### E. Legend — `vivarium-workbench/static/walkthrough.js` (≈ 11322–11356)

Regenerate from the objects (today it's a hardcoded duplicate of the colors).
Three rows: **Verdict** (5 states from `_VERDICT_META`), **Evidence** (3 tier
grades), **Edges** (4 relations from `_DAG_REL_STYLE`).

## Data flow

`study.yaml {verdict, evidence_tier, parent_relations}` → server study/summary
payload (passthrough) → SPA `walkthrough.js` `_studyVerdict`/`_studyTier` +
edge-relation resolver → node marker (verdict color+icon + tier pips) and SVG
edges (per-relation color/dash) + regenerated legend.

## Error handling / fallback

- Study missing `verdict`/`evidence_tier`: `_studyVerdict` falls back to the
  existing gate-based mapping; tier renders as ungraded (no pips). No breakage
  mid-migration.
- Unknown `verdict`/`tier`/`relation` value: default to `supported` / ungraded /
  `leads-to` respectively, never throw.
- Derive script on a study with no recognizable signal: default `supported` +
  `mechanistic`, and list it in `--check` output for human attention.

## Testing

- **viva-genesis**: a test that the schema accepts the new enums and rejects
  out-of-enum values; that `derive_study_verdict_tier.py --check` is clean after a
  full run (idempotent) and that all 23 studies end with a valid `verdict` +
  `evidence_tier`; lint stays green (schema ✓ dashboard ✓).
- **vivarium-workbench** (`tests/js/`): `_studyVerdict`/`_studyTier` precedence
  (explicit field wins; gate fallback for unmigrated); the
  `parent_relations`→edge-style mapping (each of the 4 relations resolves to its
  style; unknown → `leads-to`); legend generated from the objects (no stale
  hardcoded entries). Follow the existing `test_*_contract.js` pattern.
- **Manual**: load the investigation graph in the workbench; confirm the two
  honest-negatives read as amber `⊖`, og-cal shows `●●●` calibrated, and the
  five -alt studies show `alternative-to` edges.

## Delivery

Two PRs, each merged on green, then a dashboard republish:
1. **viva-genesis** — schema field + `derive_study_verdict_tier.py` + the 23
   derived study.yamls + the schema/idempotency test.
2. **vivarium-workbench** — `walkthrough.js` marker + edge + legend rework +
   `tests/js` + any server passthrough.

Order: land the viva-genesis data first (so the fields exist), then the workbench
renderer (which reads them, with the fallback covering the gap in between).

## Out of scope (YAGNI)

- No new graph layout / interaction changes; markers and edges only.
- No per-finding-level confidence rollup UI; verdict/tier are study-level.
- No retroactive change to `gate_status` or the gate logic — verdict is a
  separate, additional axis.
