# Shared "Configure & Run" Widget (SP-C) — Design

**Date:** 2026-06-28
**Status:** Design (brainstormed + grounded in a UI survey; approved by user)
**Author:** Eran Agmon (with Claude)
**Builds on:** SP-A+B (`lib/run_core.invoke_run`, durable runs, `composite_runs.delete_run`,
`study_variants.save_run_as_variant`) — branch `feat/unified-run-core` / PR #403.

## 1. Context & goal

The dashboard does "configure a composite and run it" three divergent ways (UI survey):
- **Composite Explorer** — the richest, but its config form lives *inside* the bigraph-loom iframe
  (opaque, not embeddable); RUN = `POST /api/composite-test-run` (standalone, durable, polled).
- **Study Runs tab** — **no ad-hoc config form**; runs use study.yaml-defined params via
  `study-run-baseline`/`-variant` (which fire a viz/analyses/outcomes pipeline), or the two-phase
  remote-run flow.
- **Composites list** — browse only; "Explore" just navigates to the Explorer.

Common shape: **pick composite → configure params → run → results → (save as variant)** — but the
configure step is iframe-trapped and absent from two surfaces.

**Goal (SP-C):** one **native, reusable "Configure & Run" widget** embedded in all three surfaces,
turning SP-A+B's plumbing (unified runner, durable runs, save-as-variant) into a single coherent run
experience. This is the user-facing payoff of the unified-run-interface decomposition (SP-A→D).

### Approved decisions
1. **Native dashboard widget** (not the loom iframe) — embeddable, dashboard-owned, introspectable.
   The loom iframe stays available for the rich *wiring* view in the Explorer; Configure/Run is native.
2. **Context-aware run target** — study context → study run/variant (keeps the pipeline; the config
   IS the variant); ad-hoc (Explorer/list) → standalone durable run + save-as-variant; remote-build
   source → the two-phase deployment flow (409 seam until SP-D). All route through SP-A's `invoke_run`.

## 2. The widget (anatomy)

A native module `static/configure-run.js` + a template partial, mounted via
`ConfigureRun.mount(el, ctx)` where `ctx = {composite, target, study}`:
- `composite`: a composite ref, or `null` (then the widget shows a picker).
- `target`: `"adhoc"` | `"study"` (governs the run endpoint + post-run affordances).
- `study`: the study slug when `target === "study"` (or for save-as-variant).

Top→bottom:
1. **Composite header** — name / id / module; a searchable **picker** when `composite` is null
   (reuses `/api/composites`).
2. **Config form** — auto-generated from `/api/composite-resolve?id=…`'s `parameters`
   (`{name: {type, default, description}}`): one input per param keyed by type
   (number→`<input type=number>`, bool→checkbox, string/other→text), pre-filled with `default`,
   `description` as a hint/title. Collected into an `overrides` dict (type-cast on read). A "reset to
   defaults" affordance.
3. **Steps** — `n_steps` (default from `default_n_steps` or 5); when `target` needs them,
   `num_generations` / `num_seeds`.
4. **Run** button — context-aware routing (see §3).
5. **Live status** — phase chips (queued/running/done/failed), tunnel-blip tolerant (reuse the WS1
   poll cadence + `reachable=false` surfacing).
6. **Results** — on done, a **"View results"** hand-off that opens the run in the existing per-run
   charts view (no new inline chart rendering). The loom wiring view stays separate.
7. **Persist** — **Save as variant** (ad-hoc → attach to a study) + **Delete**. Runs are durable +
   Sim-DB-tracked (SP-B; no prune).

## 3. Run routing (context-aware; all via SP-A `invoke_run` underneath)
| Context | Endpoint(s) | Notes |
|---|---|---|
| `target="study"`, local source | `POST /api/study-run-baseline` or `/api/study-run-variant` | keeps viz/analyses/outcomes pipeline; the overrides = the variant config |
| `target="adhoc"`, local source | `POST /api/composite-test-run` | standalone durable run (poll `…/status`); then Save-as-variant |
| remote-build source (`.viv-build.json`) | two-phase `remote-run-build → poll → submit → poll → land` | reuses WS1 panel logic; ad-hoc generator composites hit the 409 seam until SP-D |

The widget chooses the endpoint from `ctx.target` + the active source (a `GET`-able "is this a remote
build" signal, e.g. the existing workspace/source state the rail already shows). It does NOT
re-implement the run mechanism — those endpoints already route through `invoke_run` (SP-A).

## 4. Backend additions (thin routes wiring SP-B libs)
- `POST /api/save-run-as-variant` `{run_id, source_db?, study, variant_name}` →
  `study_variants.save_run_as_variant(...)` → `(payload, status)` as JSONResponse.
- `POST /api/run-delete` `{run_id, db_path}` → `composite_runs.delete_run(...)` + remove the run's
  `.pbg/runs/<run_id>/` artifacts; JSONResponse.
- **v4 save-as-variant path** — extend `study_variants.save_run_as_variant` so a `schema_version: 4`
  study writes to `conditions.variants` (via the existing `investigations._project_*`/projection
  helpers) instead of silently appending an ignored top-level `variants:` (final-review finding #2).
  v3 keeps top-level `variants:`. A v4 test accompanies it.

The run-launch endpoints already exist (no changes); SP-C only adds the two persist routes + the v4
save path.

## 5. Components (each independently testable)
- `static/configure-run.js` *(new)* — `ConfigureRun.mount/unmount`, config-form generation, override
  collection + type-cast, run routing, poll, save/delete handlers. Window-exposed for inline mounts.
- A template partial (Jinja include / macro) for the widget markup.
- Embedding edits: Explorer (`index.html.j2 #page-composite-explore` — replace the loom Configure/Run
  with the widget, keep `#composite-explore-frame` for wiring), Composites list (a "Configure & Run"
  action per card → mount the widget), study Runs tab (`study-detail.html #panel-runs` — a "New run"
  affordance → mount with `target:"study"`).
- `lib/study_variants.py` — v4 path (extend existing fn).
- `api/app.py` — the two new routes (+ readonly-allowlist entries).

## 6. Error handling
| Case | Behavior |
|---|---|
| composite-resolve fails (e.g. remote generator composite) | the widget shows the resolve error inline (reuse the defensive-parse pattern); no config form, Run disabled |
| Run on a remote build (ad-hoc generator) | 409 from the seam → a clear "runs on the deployment (coming in SP-D)" message |
| save-as-variant: missing study / dup name | 404 / idempotent overwrite (SP-B semantics), surfaced inline |
| delete | confirm, then `run-delete`; row + artifacts removed; the Sim-DB/Runs list refreshes |
| tunnel blip during a remote/poll run | tolerated (WS1 pattern); `reachable=false` shown, retried |

## 7. Testing
- **JS/partial string-presence** (repo convention): the widget mounts, the four endpoints are
  referenced, config-form generation + override collection present, save/delete handlers exist,
  window-exposed.
- **Config-form generation render test** — a small headless render/JS-unit (or a Python render check
  if that's the repo's pattern) over a sample `parameters` dict → asserts an input per param with the
  right type + default.
- **Backend route tests** (fakes): `save-run-as-variant` (v3 AND v4) and `run-delete` happy/404 paths.
- **v4 save-as-variant lib test** — writes `conditions.variants` for a schema_version-4 study.

## 8. Scope boundaries
**In:** the native widget + its 3 embeddings; the two persist routes; the v4 save-as-variant path.
**Out:** SP-D (deployment composite execution — ad-hoc remote generator runs stay on the 409 seam);
extending the 409 seam to the investigation batch-runners (separate follow-up); reworking the loom
wiring view; new inline chart rendering (results hand off to the existing per-run view).

## 9. Open questions (resolve in planning)
1. **Config-form test harness** — does the repo have a JS unit harness, or do we string-presence the
   generation logic + add a Python-side render check? (Survey says string-presence is the convention;
   confirm whether a real param→input render can be asserted without a browser.)
2. **"Is this a remote build" signal for the widget** — reuse the rail's existing source/workspace
   state (preferred), or a small `GET` the widget calls on mount? Pick the existing signal if present.
3. **Explorer cutover** — replace the loom Configure/Run entirely, or mount the widget alongside it
   first (feature-flag) for a safe transition? Lean: replace, since the widget supersedes it.
