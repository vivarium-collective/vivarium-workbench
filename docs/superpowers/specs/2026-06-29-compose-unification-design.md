# Compose Pillar Unification — Design

**Date:** 2026-06-29
**Status:** Design — approved, pending spec review
**Repo:** `vivarium-dashboard` (branch `feat/compose-unification`, off `main`)
**Part of:** the 5-pillar streamline. Prior slices merged: Overview declutter, derivation consolidation, tab consolidation (5 pillars), Conclusions declutter, `types` CI fix. This unifies the last fragmented pillar — **Compose**.

## Goal

The **Compose** pillar still reads as 4 separate sub-panels (Build / Baseline / Variants / Interventions) behind a sub-nav. Merge them into a single scrollable **Compose** page so model composition reads as one unit, and drop the Compose sub-nav.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| Compose presentation | **One scrollable page** (not a 4-pill sub-nav). |
| Section reading order | Model → Baseline → Conditions → Variants → Interventions → Model change / Implementation requirements. *(With one robustness caveat — see below.)* |

## Current state (grounded)

`templates/study-detail.html`: four panels under the Compose pillar (each `data-pillar="compose"` in the sub-nav, post tab-consolidation):
- `panel-build` (~690–915, guard `{% if _is_v3 or study.model_change or study.implementation_requirements %}`): `<h3>` sections **Model** (composite + editable Model-settings table), **Conditions**, **Model change**, **Implementation requirements**.
- `panel-baseline` (~1140, guard `{% if not _is_v3 %}` upstream): `baseline[]` entries — name, composite FQN, params, **Run**/**Remove** buttons (`.baseline-entry`, `[data-baseline-name]`, `.btn-run-baseline`).
- `panel-variants` (~1200, not-v3): variants list + the parameter-override editor (`.variant*` selectors).
- `panel-interventions` (~1265, not-v3): interventions (`[data-editable-intervention]`).

**Verified couplings:** no JS references the 4 wrapper ids (`#panel-build` etc.); the only `_setStudyTab('build'|'baseline'|'variants'|'interventions')` calls are the four tab buttons themselves (no other deep links); all panel-specific JS uses **inner** selectors (`.btn-run-baseline`, `.variant…`, `[data-editable-intervention]`, `[data-baseline-name]`). So merging the panels is safe as long as the inner markup + ids + `{% if %}` guards are preserved.

The tab switcher (`study-detail.js`): `_setStudyTab(kind)` derives the pillar from the kind's button `data-pillar`, reveals the pillar's member buttons, toggles the panel + kind-loaders; `_setStudyPillar(pillar)` reveals the sub-nav + opens the first member.

## Architecture

Physically **merge the 4 panels into one `panel-compose`** (`data-kind="compose"`), preserving every inner section verbatim (markup, ids, JS-targeted classes, and each section's `{% if %}` guard). The Compose pillar then has a single member → the switcher opens `panel-compose` like any one-member pillar; the redundant single sub-nav pill is suppressed.

### Component ① — merged `#panel-compose` (template)

Replace the 4 separate `<section …>` panels with one:

```
<section class="study-tab-panel" data-kind="compose" id="panel-compose" hidden>
  {% if _is_v3 or study.model_change or study.implementation_requirements %}
    {{ panel-build INNER content verbatim: Model (composite + Model-settings table),
       Conditions, Model change, Implementation requirements — guard + inner ids preserved }}
  {% endif %}
  {% if not _is_v3 %}
    {{ panel-baseline INNER content (baseline-list, Run/Remove) }}
    {{ panel-variants INNER content (variants + override editor) }}
    {{ panel-interventions INNER content }}
  {% endif %}
</section>
```

Each former panel's inner content moves under its existing guard, in the order **Build block → Baseline → Variants → Interventions**. Add an `<h2 class="overview-label">` lead-in for the merged page's top (e.g. "Model composition") and keep the existing `<h3>` section headers (Model / Conditions / Model change / Implementation requirements / the baseline-list / Variants / Interventions) as the in-page sections.

**Order caveat (honest deviation from the literal order):** the literal request was Model → **Baseline** → Conditions. But Build's Model and Conditions sit under the Build guard (`_is_v3 or model_change/impl_reqs`) while Baseline sits under the `not _is_v3` guard — interleaving Baseline *between* them would split the Build block across two different `{% if %}` conditions, which is fragile (e.g. a v4 study with no model_change/impl_reqs renders no Build sections at all). So the Build block (Model + Conditions + Model change + Impl reqs) renders whole, then Baseline → Variants → Interventions. Net reading: Model/Conditions… then Baseline/Variants/Interventions — composition still reads top-to-bottom as a unit, with the Build guard intact. (If splitting Build is preferred despite the fragility, that's a follow-up.)

**Empty-Compose edge:** if a study satisfies neither guard (e.g. v4 with no baseline/variants/interventions AND no model_change/impl_reqs), `panel-compose` is empty → render a one-line empty-state (`<p class="empty-message">Nothing to compose yet.</p>`) so the page isn't blank.

### Component ② — tab bar (template)

Replace the four Compose member buttons in `#study-subnav` with ONE:
```html
<button class="study-tab" data-kind="compose" data-pillar="compose" onclick="_setStudyTab('compose')">Compose</button>
```
(Keeps the switcher uniform — Compose is now a one-member pillar like Understand.)

### Component ③ — suppress single-member sub-nav (study-detail.js)

In `_showPillarSubnav(pillar)`, after revealing the pillar's member buttons, if the pillar has ≤1 visible member button, hide the sub-nav row (so the lone "Compose" pill under the "Compose" pillar isn't shown). One small addition; multi-member pillars (Inquire/Simulate/Visualize) are unchanged. No change to `_setStudyTab`'s panel-toggle or kind-loaders.

## Data flow

Open a study → click **Compose** pillar → `_setStudyPillar('compose')` opens `panel-compose` (its single member), sub-nav suppressed → the merged page renders Model/Conditions/… + Baseline/Variants/Interventions per the schema-version guards. The variants override-editor, baseline Run/Remove, and intervention-edit controls work unchanged (inner selectors preserved).

## Error handling / compatibility

- Inner markup + ids + JS-targeted selectors + every `{% if %}` guard preserved verbatim → no panel-specific JS breakage (verified no wrapper-id coupling).
- Schema-version-safe: Build block renders under its guard, baseline/variants/interventions under `not _is_v3` — same as today, just in one panel.
- Empty-state covers the no-content edge.
- The removed `_setStudyTab('build'|'baseline'|'variants'|'interventions')` button calls have no other callers, so removing them breaks nothing.

## Testing

- **`tests/test_compose_unification.py`** (assert on `study-detail.html` source):
  - One `id="panel-compose"` with `data-kind="compose"`; the 4 old panel wrapper ids (`id="panel-build"`, `panel-baseline`, `panel-variants`, `panel-interventions`) are GONE.
  - One Compose member button (`data-kind="compose" data-pillar="compose"`); the 4 old compose member buttons gone.
  - Inner hooks preserved inside `panel-compose`: `.baseline-entry`/`btn-run-baseline`, the variants override-editor selector, `data-editable-intervention`, the Model-settings table, and the `{% if not _is_v3 %}` / build guards.
  - `study-detail.js`: `_showPillarSubnav` hides the sub-nav for a single-member pillar (assert the new branch present).
- **Jinja parses** (`Environment().parse`); **`node --check study-detail.js`**.
- **Live render (strong gate):** serve `v2e-readouts`; open a study → Compose pillar → `panel-compose` renders the merged sections (HTTP 200, no error, no sub-nav pill); for a v4 study with baseline/variants, the baseline Run/Remove + variant editor work; for a v3 study, the Build sections render.

## Out of scope (later)

- Splitting the Build block to interleave Baseline mid-Build (fragile; deferred).
- B2b schema-debt collapse.
- Investigation-level page.
- Decluttering the contents within Build's sub-sections (this slice merges panels; it doesn't restructure Build internally).
