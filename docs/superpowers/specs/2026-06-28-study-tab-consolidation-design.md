# Study Tab Consolidation — Design

**Date:** 2026-06-28
**Status:** Design — approved, pending spec review
**Repo:** `vivarium-dashboard` (branch `feat/study-tab-consolidation`, off `main`)
**Part of:** the "streamline the investigation structure around the 5 pillars" effort. Earlier slices: Overview declutter (merged), derivation consolidation (merged). This slice rationalizes the study-detail tab bar.

## Goal

The study-detail page has up to **12 tab buttons over 11 panels** — a flat, noisy nav. Group them into the **5 pillars** (Understand / Inquire / Compose / Simulate / Visualize) so the top-level nav is 5 clear choices, with a compact secondary sub-nav inside multi-member pillars. Keep every panel's content and panel-specific JS exactly as-is — restructure only the nav shell and the tab switcher.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| Pillar mapping | Understand←Overview · Inquire←Tests+Conclusions/Decide · Compose←Build+Baseline+Variants+Interventions · Simulate←Simulations+Runs · Visualize←Readouts/Observables+Visualizations |
| Multi-member presentation | **Secondary sub-nav pills** (not stacked panels) — pillar tab selects the member set; a pill row selects which member panel shows. |

## Current state (grounded)

- Tab bar (`study-detail.html` ~139–156): 12 `<button class="study-tab" data-kind="…" onclick="_setStudyTab('…')">` buttons, **schema-version-conditional**:
  - `_is_v3`: Overview, Build, Simulations, Readouts(`observables`), Runs, Tests, Visualizations, Decide(`conclusions`).
  - else (v4): Overview, Build, Baseline, Observables(`observables`), Variants, Interventions, Runs, Tests, Visualizations, Conclusions(`conclusions`).
  - So "Readouts"/"Observables" are the SAME `observables` panel under two version labels (never both on screen). "Simulations" is v3-only; Baseline/Variants/Interventions are v4-only.
- 11 panels (`<section class="study-tab-panel" data-kind="…" id="panel-…">`): overview, build, simulations, baseline, observables, variants, interventions, runs, tests, visualizations, conclusions. Each panel's content + panel-specific JS is keyed off its `data-kind`/`id`.
- `_setStudyTab(kind)` (study-detail.js:14): toggles `.active` on the `.study-tab` button and the `.study-tab-panel` matching `kind`, then runs kind-specific loaders (`tests`→`loadTestsTab`, `visualizations`→`_loadCharts`, `observables`→`_loadReadouts`). Called by ~8 deep links (e.g. `_setStudyTab('tests')`, `_setStudyTab('conclusions')`, `_setStudyTab('runs')`, `_setStudyTab('overview')`) that MUST keep working.

## Architecture

A **two-level nav**, panels untouched. The pillar membership is encoded on each member button as `data-pillar`, so the JS derives the pillar from the DOM — no hardcoded member list, automatically correct under the v3/v4 conditionals.

```
study-detail.html  (nav only)
  <nav class="study-pillars"> 5 buttons: data-pillar=understand|inquire|compose|simulate|visualize </nav>
  <nav class="study-subnav" id="study-subnav"> the EXISTING member buttons, each + data-pillar=<its pillar>,
       still Jinja-conditional; only the active pillar's members are shown </nav>
  <section class="study-tab-panel" …> 11 panels — UNCHANGED (id/data-kind/content/JS) </section>

study-detail.js
  _setStudyTab(kind)  → (enhanced) derive pillar from the kind's button's data-pillar;
                         activate that pillar button; show that pillar's member buttons (hide others);
                         existing .active panel toggle + kind-specific loaders (unchanged)
  _setStudyPillar(pillar) → activate pillar; reveal its member buttons; _setStudyTab(first visible member)
```

### Component ① — the tab bar (template)

Replace the single `study-tab` button row with:
- **Pillar row** — 5 always-present buttons: `<button class="study-pillar" data-pillar="understand" onclick="_setStudyPillar('understand')">Understand</button>` … (Inquire, Compose, Simulate, Visualize). The default-active pillar is **Understand**.
- **Sub-nav row** (`#study-subnav`) — the EXISTING member buttons, kept verbatim (same `data-kind`, `onclick="_setStudyTab('…')"`, same Jinja `{% if _is_v3 %}`/`{% endif %}` conditionals and labels), each gaining `data-pillar="<pillar>"`:
  - overview→understand; build/baseline/variants/interventions→compose; simulations/runs→simulate; observables/visualizations→visualize; tests/conclusions→inquire.
  - The "Readouts"(v3)/"Observables"(v4) label is unified to **"Readouts"** under Visualize (one entry; the version label divergence goes away).
- Only the active pillar's member buttons are visible (CSS: `.study-subnav .study-tab[data-pillar]` hidden unless the pillar is active — driven by a class on `#study-subnav` or by JS toggling each button's visibility). A pillar with a single member shows no pill chrome (its panel shows directly).

### Component ② — `_setStudyTab(kind)` (enhanced, study-detail.js)

Add, at the top of the existing function: derive the pillar from `document.querySelector('.study-tab[data-kind="'+kind+'"]')?.dataset.pillar`; set `.active` on the matching `.study-pillar` button; show the member buttons whose `data-pillar` equals that pillar and hide the rest (in `#study-subnav`). Then run the existing panel `.active` toggle + the kind-specific loaders unchanged. Result: every existing `_setStudyTab(kind)` deep link also lands on the right pillar + sub-nav, with zero changes to the callers.

`_setStudyPillar(pillar)`: set the active pillar button + show its member buttons, then call `_setStudyTab(firstMemberKind)` for that pillar (the first member button in DOM order with that `data-pillar`), so clicking a pillar opens its first panel.

### Component ③ — panels & panel JS

Unchanged. The 11 `<section>` panels keep their `id`/`data-kind`/content; `loadTestsTab`/`_loadCharts`/`_loadReadouts` and all panel-specific behavior are untouched.

## Data flow

Open a study → Understand pillar active, Overview panel shown. Click a pillar → its member buttons appear; the first member's panel shows (with its loader). Click a member pill → that panel shows. A deep link (`_setStudyTab('tests')` from a finding row) → activates the Inquire pillar, reveals its sub-nav, shows the Tests panel — exactly as before plus correct pillar state.

## Error handling / compatibility

- v3/v4 conditional-safe: member buttons remain Jinja-conditional, so each version renders only its valid members; the DOM-derived pillar mapping is always correct. A pillar whose only member isn't rendered for this version simply has no members (its pillar button can be hidden when empty — JS checks for ≥1 member button before showing the pillar button, or the button no-ops).
- Deep links unchanged (no caller edits) — `_setStudyTab(kind)` keeps its signature and behavior, just gains pillar/sub-nav side effects.
- Unknown/absent kind → the existing toggle simply matches nothing (no crash), as today.

## Testing

- **`tests/test_study_tabs_structure.py`** (assert on `study-detail.html` source):
  - 5 pillar buttons present (`data-pillar="understand|inquire|compose|simulate|visualize"`).
  - Every member button carries a `data-pillar` (no `study-tab` button without one).
  - The 11 panels still present with their `data-kind`/`id` (unchanged).
  - The `_setStudyTab('tests')`/`_setStudyTab('conclusions')` deep-link onclicks still exist.
- **JS:** `node --check study-detail.js`. A small pure helper `_pillarForKind(kind)` could be extracted + node-tested, but since pillar derivation reads the DOM, the structure test + manual verification cover it; if a pure helper is cheap, add a node test asserting the kind→pillar map.
- **Manual:** serve against `v2e-readouts`; open a v4 study (`param-uq-01-elongation`) and a v3 study → 5 pillars; clicking each shows the right member sub-nav + panel; the Tests/Decide deep links from findings land on the right pillar; Visualizations/Readouts loaders still fire.

## Out of scope (later streamline slices)

- Decluttering the CONTENTS of the non-Overview tabs (Build/Simulate/Visualize panels) — this slice only regroups the nav.
- Investigation-level nav.
- Renaming panels' internal headings to match pillar names.
- Persisting the last-active pillar/member across reloads.
