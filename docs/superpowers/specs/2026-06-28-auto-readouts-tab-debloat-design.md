# Auto-covered Readouts + investigation-tab de-bloat

**Date:** 2026-06-28
**Status:** Design — approved questions, pending spec review
**Repos touched:** `vivarium-dashboard` (primary), `pbg-superpowers` (`report_linter`, `readout_validation` reuse), `v2ecoli` (study.yaml migration + validation target)
**Branch / worktree:** `feat/auto-readouts-tab-debloat` @ `/Users/eranagmon/code/vdash-readouts` (off `origin/main`)

## Goal

Make investigation/study pages clear, complete, valid, and auto-filled with real
information — starting with two tabs, applied dashboard-wide:

1. **Readouts** — auto-list the table from the composite's **emit plan** (the paths
   the simulation actually saves), with `name` and `store_path` always present, and
   **lint** that authored `store_path`s are valid emit paths. Remove the manual
   "+ Add observable from bigraph state" panel.
2. **Visualizations** — remove the "REGISTERED VISUALIZATION MODULES" section and the
   "+ Add visualization" button (keep the auto-generated latest-run charts).
3. **Runs** (folded in) — remove the "Compare selected" and "Clear all runs" buttons.

The unifying principle: **derive real information automatically; remove
manual-authoring and destructive bloat.** The dashboard now boots the FastAPI app
under uvicorn (`cli.py:122`; the legacy stdlib `server.serve` path is retired), so all
new server work lands on the typed FastAPI seam.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| Truth source for "valid store_path" | **Emit plan from composite** — `available_observables(...).leaves`, derived from `collect_input_ports` (the emitter capture set), pre-run. |
| How rows reach the table | **Auto-list from emit plan** — every emitted path is a row; authored `study.yaml readouts:` overlay descriptions/units/notes on top. |
| First-spec scope | **Two tabs, dashboard-wide** (+ the Runs-tab button removal), validated on `param-uq-00-screen`. |
| Readouts implementation | **Typed FastAPI worker** (`lib/readouts_views.py` + `GET /api/study-readouts`). |
| Visualizations data | **Leave `study.yaml visualizations:` data + report embeds untouched; remove the UI section only.** |

## Current state (as found)

- **Readouts table** — `templates/study-detail.html:1263-1317` renders `study.readouts or
  study.observables` (columns: Name, Status (authored), Validated against composite,
  Store path, Indexed by, Description), with a `#auto-readouts` fallback div when none
  are declared.
- **"+ Add observable from bigraph state"** — `study-detail.html:1319-1513` (the
  `<details>` block + embedded JS), backed by `GET /api/study-bigraph-paths`
  (`app.py:608-636` → `study_viz_views.build_study_bigraph_paths`).
- **Per-readout validation** — `GET /api/study-observable-check` (`app.py:1694+` →
  `observables_views.build_study_observable_check` → `validate_readouts`), status ∈
  `ok | unresolved | not_in_structure | aspirational`. Rendered into `.readout-validation`.
- **Emit-plan engine** — `pbg_superpowers.readout_validation.available_observables(core,
  state, schema)` → `{leaves: [dotted emit paths], catalogs: {observable: [labels]}}`
  via `collect_input_ports`. Surfaced by `GET /api/observables?ref=` (`observables_views.build_observables`, ~3 s build, TTL-cached).
- **study.yaml `readouts:`** — currently `{name, notes}` only; the store path lives as
  prose inside `notes` (e.g. `"listeners.mass.instantaneous_growth_rate — …"`), so the
  table shows "—" for Store path and "—" for validation.
- **Visualizations tab** — "LATEST-RUN VISUALIZATIONS (auto from runs.db)" (keep) +
  "REGISTERED VISUALIZATION MODULES" listing `study.yaml visualizations:` (e.g.
  `address: image:charts/screen_response.png`) + "+ Add visualization" button.
- **Runs tab** — `study-detail.html:1744-1745`: `.btn-compare-selected` "Compare
  selected" and `.btn-clear-runs danger` "Clear all runs"; handlers in
  `study-detail.js:807` (compare) and a `_clearRuns(...)` function.
- **Report linter** — `pbg_superpowers.report_linter.lint_workspace_report` →
  `lib/report_views.build_report_lint` → findings filtered by study → the readiness
  "⚠ N gaps" panel (`static/study-detail.js:1696-1754`). New checks roll in automatically.

## Design

### Component A — Emit-plan Readouts table (FastAPI worker)

**New `lib/readouts_views.py` worker + `GET /api/study-readouts?study=<slug>`.**

The worker:
1. Resolves the study + its baseline composite ref (same resolution as
   `build_study_observable_check`).
2. Gets the emit plan via the existing path: `build_composite_state_for_observables`
   → `augment_lineage_aliases(available_observables(...))` → `{leaves, catalogs}`.
   Reuses the existing TTL cache (whole-cell builds are ~3 s).
3. Loads `study.yaml readouts:` as an **annotation overlay**, indexed by `store_path`
   (fallback: `name`).
4. Emits a typed payload — a row list that is the **union** of:
   - **emit-plan rows**: one per emit leaf (and/or catalog observable). `store_path` =
     the leaf; `name` = authored name if an overlay matches, else a derived short name
     (leaf basename / catalog label). `annotated` = whether an overlay covers it.
     `emit_status = "emitted"`.
   - **authored-orphan rows**: authored readouts whose `store_path` is **not** in the
     emit plan AND whose `status` is `available`/concrete → `emit_status =
     "not_in_emit_plan"` (the never-fabricate / invalid flag).
   - **derived rows**: authored readouts with `status: derived-needed | aspirational`
     (computed analysis scalars, e.g. `effective_knob_count`, not raw emit leaves) →
     shown, `emit_status = "derived"`, **exempt** from the emit-plan check. This
     preserves the existing `validate_readouts` semantics.

Each row: `{store_path, name, description, units, index_by, annotated, emit_status}`.
Payload validated by a pydantic model (`StudyReadouts` / `ReadoutRow`) — matches the
`lib/models.py` typed-payload convention.

**Template** (`study-detail.html`): replace the authored-only table + `#auto-readouts`
fallback with a single table fed by `/api/study-readouts`. Columns:

> **Name · Store path (always) · Emitted? (✓ / ✗ invalid / ⏳ derived) · Indexed by · Units · Description**

This collapses today's separate "Status (authored)" + "Validated against composite"
columns into one **Emitted?** validity column.

**Remove** the "+ Add observable from bigraph state" `<details>` block (`study-detail.html:1319-1513`)
and its JS. Retire `GET /api/study-bigraph-paths` + `study_viz_views.build_study_bigraph_paths`
**only after** confirming no other consumer (grep `study-bigraph-paths`,
`build_study_bigraph_paths` across `vivarium-dashboard` + `pbg-superpowers`).

### Component B — store_path lint → readiness gaps

New check in `pbg_superpowers.report_linter` (or a dashboard-side linter pass feeding
`build_report_lint`), per authored readout in a study:

- **error** — `status: available` (or a concrete store_path) but **no** `store_path` field.
- **error** — `store_path` of an `available` readout is **not** an emit-plan leaf
  (resolved via `available_observables` + `augment_lineage_aliases`) → "not an emittable
  leaf path (never-fabricate)".
- `derived-needed` / `aspirational` readouts → no finding (exempt).

Findings carry `{study, check: "readout-store-path", severity, message, field_path}` and
roll into the existing "⚠ N gaps" readiness panel automatically — making "lint that
these are valid" a first-class gate, not just an inline badge.

### Component C — study.yaml readouts schema + migration

- Promote `store_path` to a **structured, required** field on each `available` readout
  (today it's prose inside `notes`).
- Update the scaffold template (`lib/scaffold_yaml.py:224-230`) to document/emit it.
- **One-shot migration** for existing studies: lift the leading dotted path out of
  `notes` into `store_path` (leaving `notes` prose intact). Validate on
  `param-uq-00-screen`: `instantaneous_growth_rate` → `store_path:
  listeners.mass.instantaneous_growth_rate`; `effective_knob_count` stays `derived`
  (no store_path). Migration is idempotent and skips readouts that already have
  `store_path`.

> Note: because the table auto-lists from the emit plan, completeness no longer depends
> on authored readouts — the migration's job is to make authored **annotations attach**
> (overlay match) and to give the linter a structured field to validate.

### Component D — Visualizations tab de-bloat

- Remove the "REGISTERED VISUALIZATION MODULES" section + "+ Add visualization" button
  from `study-detail.html`. Keep "LATEST-RUN VISUALIZATIONS (auto from runs.db)".
- **Leave** `study.yaml visualizations:` data and the downloadable-report embeds
  untouched (avoids regressing report embeds — the registered entries may feed the HTML
  report's static images).

### Component E — Runs tab de-bloat

- Remove `.btn-compare-selected` ("Compare selected") and `.btn-clear-runs danger`
  ("Clear all runs") from `study-detail.html:1744-1745`.
- Remove their now-dead JS handlers in `study-detail.js` (the `.btn-compare-selected`
  bind at ~807 and the `_clearRuns` wiring for this view). Confirm `_clearRuns` isn't
  shared by another live view before deleting the function itself (it also appears in
  `walkthrough.js:13682`).

## Testing

- **Unit (`lib/readouts_views`)** — given a fake emit plan (`leaves`/`catalogs`) + a
  `study.yaml readouts:` overlay: asserts auto-list rows, overlay match by store_path,
  authored-orphan flag (`not_in_emit_plan`), and derived-exempt rows. No composite build
  in the unit test (inject the emit plan).
- **Unit (linter check)** — authored readout missing store_path → error; `available`
  store_path absent from emit plan → error; valid → no finding; derived → no finding.
- **Unit (migration)** — `notes`-prose path lifted into `store_path`; idempotent;
  derived readouts untouched.
- **Manual on `param-uq-00-screen`** (worktree dashboard restart) —
  - Readouts tab auto-lists emit paths with `store_path` filled; `instantaneous_growth_rate`
    annotated + ✓ emitted; `effective_knob_count` shown as ⏳ derived.
  - "+ Add observable" panel gone.
  - Visualizations tab: no "REGISTERED VISUALIZATION MODULES" / Add button; latest-run
    chart still renders.
  - Runs tab: no "Compare selected" / "Clear all runs".
  - Readiness gaps reflect any invalid/missing store_path.

## Implementation & rollout notes

- **Do not** reinstall `-e` from this worktree over the canonical `main` install
  (memory `reference_dashboard_editable_install_from_main_only`). To test, restart the
  dashboard against the worktree (`/pbg-dashboard restart` pointed at the worktree, or a
  dedicated worktree venv) — never leave the global install on a feature branch.
- `pbg-superpowers` lives behind the plugin single-source (memory
  `reference_pbg_skills_single_sourced_plugin`); the linter/validation edits land in the
  `pbg-superpowers` repo and propagate via plugin update, not by editing the cache.
- Keep the changes additive on the FastAPI seam; do not revive `server.serve`.

## Out of scope (north-star follow-ups)

- Other tabs (Overview, Build, Simulations, Tests, Decide) auto-fill/de-bloat.
- Stripping orphaned `study.yaml visualizations:` data + report-embed reconciliation.
- A unified "auto-fill + lint" pass across all study sections.
