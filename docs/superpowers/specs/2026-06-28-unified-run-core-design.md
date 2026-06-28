# Unified Run Core + Durable Persistence (SP-A + SP-B) — Design

**Date:** 2026-06-28
**Status:** Design (brainstormed + grounded in a code survey of the three run surfaces; approved by user)
**Author:** Eran Agmon (with Claude)

## 1. Context & goal

The dashboard runs simulations from three divergent, partly-buried surfaces that are *the same
operation underneath*:
- **Composite Explorer "Configure & Run"** — `composite C + overrides → run_id → runs_meta row →
  subprocess`, persisted to `.pbg/composite-runs.db` (async; **pruned to the last 20** — the
  "not persistent" problem).
- **Study runs (baseline / variant)** — `composite C + params/variant-overrides → run_id →
  runs_meta row → subprocess`, persisted to `studies/<study>/runs.db` (sync; + viz/analyses/outcomes
  side-effects).
- **WS1 remote runs** — the same intent, executed on the deployment (sms-api) instead of locally.

A code survey confirmed all three share a common core (`generate_run_id`, `runs_meta`,
`run_composite_subprocess`) and the Simulations DB (`lib/simulations_index.list_simulations`) already
aggregates them. They diverge only in **where they persist, sync-vs-async, side-effects, and where
they execute**.

**The unifying model (approved):**
> A **Run** = a composite + a config, executed where its source lives (local subprocess or the
> deployment), persisted durably and tracked in the Sim DB. A **Variant** = a Run you've named and
> attached to a study.

This spec covers the **foundation — SP-A (unified run core) + SP-B (durable, Sim-DB-native
persistence)**. The shared UI (SP-C) and deployment-side composite execution (SP-D) are separate
specs that build on this. **SP-A+B is local-execution only**; the deployment path is a clean seam
filled in by SP-D.

### Decomposition (full feature, for orientation)
- **SP-A** — unified run core (this spec).
- **SP-B** — durable persistence + save-as-variant (this spec).
- **SP-C** — shared "Configure & Run" UI component embedded in Composites / Explorer / study Runs.
- **SP-D** — deployment-side composite execution (sms-api), so generator composites run on remote
  builds. Routes through SP-A's `target="deployment"` seam.

## 2. Approved decisions
1. **Execution routes by source** — local workspace → local subprocess; remote build → deployment
   (sms-api). SP-A+B implements `local`; `deployment` is a seam that raises a clear "needs SP-D".
2. **All runs durable + Sim-DB-tracked** — drop the explorer's prune-to-20; deletion becomes
   explicit. A study **variant IS a saved (composite, config) run** attached to a study.

## 3. Architecture — one core, thin wrappers

A new `vivarium_dashboard/lib/run_core.py` with one entry point that owns the shared mechanism:

```python
def invoke_run(
    workspace: Path, *,
    spec_id: str,            # the composite ref (e.g. "v2ecoli.composites.baseline")
    config: dict,            # param/override dict (the "variant config")
    db_path: Path,           # which runs_meta store to persist into (caller policy)
    label: str | None = None,
    n_steps: int | None = None,
    target: str = "local",   # "local" (impl) | "deployment" (SP-D seam)
) -> RunHandle:              # {run_id, spec_id, db_path, status}
```

`invoke_run` does ONLY the common core, in order:
1. `run_id = composite_runs.generate_run_id(spec_id, _run_params(config, n_steps))` (deterministic).
2. `composite_runs.save_metadata(conn, spec_id, run_id, params=config, label=label, …)` → a
   `runs_meta` row with `status="running"`.
3. Dispatch by `target`:
   - `"local"` → return a handle + a **`launch()`** callable the caller invokes per its own
     sync/async policy (see below). `launch()` wraps `composite_subprocess.run_composite_subprocess`
     and calls `composite_runs.complete_metadata(...)` on finish.
   - `"deployment"` → `raise RunTargetUnavailable("deployment execution requires SP-D")`.

**Why a returned `launch()` rather than running inline:** the two callers genuinely differ —
the explorer spawns **detached** (`run_registry.spawn_detached`) and returns 202 immediately;
study runs execute **inline (sync)** and then fire viz/analyses/outcomes side-effects. `invoke_run`
unifies id+persist+runner-construction; the caller chooses *how* to launch and *what* to do after.
This keeps `invoke_run` single-responsibility and each caller's observable behavior unchanged.

### Thin wrappers (refactor, behavior-preserving)
- `lib/composite_test_run_views.py` → builds `config` from the request `overrides`, calls
  `invoke_run(db_path=<ws>/.pbg/composite-runs.db, target=<derived>)`, then `spawn_detached` via the
  handle (async, no side-effects). Same 202 `{run_id, status:"running"}` response.
- `lib/study_runs.py` (baseline + variant) → resolves the composite + merged params from
  `study.yaml`, calls `invoke_run(db_path=studies/<study>/runs.db, target=<derived>)`, runs
  `launch()` inline (sync), then its existing post-run pipeline (viz, post-run scripts, analyses,
  outcomes sync). Same 200 response shape.

### Source → target derivation
A small helper `run_target_for(workspace) -> "local" | "deployment"`: `"deployment"` iff the
workspace carries a `.viv-build.json` (a materialized remote build — the WS3 marker), else
`"local"`. Callers pass the derived target; for SP-A+B a `"deployment"` target surfaces a clean
4xx ("run this composite on a local workspace, or wait for SP-D") rather than a crash.

## 4. Data flow

```
configure (composite + config)
   → invoke_run: run_id + runs_meta(status=running) + handle
   → launch() : run_composite_subprocess → complete_metadata(status=completed|failed)
   → simulations_index aggregates the runs_meta row → Simulations DB (durable)
   → [optional] save_as_variant(run_id, study) → study.yaml variants[] += {name, composite, config}
```

## 5. Persistence (SP-B)

- **Durable:** remove the prune-to-20 from the explorer path (`composite_runs.PRUNE_KEEP` /
  the prune call in `composite_test_run_views`). Runs persist until explicitly deleted. (A generous
  safety cap MAY remain, but default is no silent eviction — the user asked for durability.)
- **Explicit delete:** deletion is a Sim-DB action (a `delete_run(db_path, run_id)` in
  `composite_runs` that removes the row + its `.pbg/runs/<run_id>/` artifacts), surfaced later by
  SP-C; not auto-eviction.
- **Sim-DB-native:** no new aggregation needed — `simulations_index` already reads every
  `runs_meta` store; durability + dropping the prune is what makes runs stick.

### Save-as-variant
`save_run_as_variant(workspace, run_id, source_db, study, variant_name) -> dict`:
1. Read the run's `spec_id` (composite) + `params_json` (config) from `runs_meta` in `source_db`.
2. Append `{name: variant_name, composite: <spec_id>, parameter_overrides: <config>}` to the target
   `study.yaml`'s `variants[]` (v3) / `conditions.variants[]` (v4 — project via the existing
   `investigations._project_*` helpers), idempotent on `name`.
3. Link the run to the study (copy/move the `runs_meta` row into `studies/<study>/runs.db` or record
   the association — decided in planning; the run's store stays put).

Builds on the explorer's existing **"Save as Study" / "Begin Study from Composite"** affordance
(template `#ce-begin-study-bar`, `_beginStudyFromComposite`, `_ceOpenSaveAsStudyModal`) — unify with
it, don't add a parallel path.

## 6. Components (each independently testable)
- `lib/run_core.py` *(new)* — `invoke_run`, `RunHandle`, `RunTargetUnavailable`, `run_target_for`.
- `lib/composite_runs.py` — add `delete_run`; remove/neutralize `PRUNE_KEEP` eviction; (existing
  `generate_run_id`/`save_metadata`/`complete_metadata` reused unchanged).
- `lib/composite_test_run_views.py` — refactor onto `invoke_run` (behavior-preserving).
- `lib/study_runs.py` — refactor baseline+variant onto `invoke_run` (behavior-preserving).
- `lib/study_variants.py` *(new or fold into study_runs)* — `save_run_as_variant`.

## 7. Error handling
| Case | Behavior |
|---|---|
| `target="deployment"` (remote build) in SP-A+B | `RunTargetUnavailable` → 409 with a clear message; no crash |
| Subprocess fails | `complete_metadata(status="failed")`; the run still persists (visible in Sim DB as failed) |
| Unknown composite ref | resolve error surfaced as the existing 404/JSON (no behavior change) |
| save-as-variant to a missing study / dup name | 404 / idempotent no-op with a clear message |
| `runs_meta` write fails | surfaced to caller; no orphaned subprocess (id+persist happen before launch) |

## 8. Testing
Unit, fakes only (no real subprocess/network/sms-api):
- `invoke_run` generates a stable deterministic id, writes a `runs_meta` row (status=running), and
  hands back a `launch()` that calls the (faked) runner + `complete_metadata`.
- `target="deployment"` raises `RunTargetUnavailable`.
- The two refactored wrappers still produce their existing response shapes (parity tests against the
  current `composite_test_run` / `study_run_baseline|variant` behavior).
- **Durability:** running >20 composite runs leaves all rows present (the prune is gone).
- `save_run_as_variant` writes the right `study.yaml` `variants[]` entry (v3 and v4-projected),
  idempotent on name.
- `delete_run` removes the row + artifacts.
Mirrors existing `test_composite_runs` / `test_study_runs` style; run from the worktree with the
v2ecoli venv on `PYTHONPATH`.

## 9. Scope boundaries
**In:** SP-A (`invoke_run` core + local execution + the routing seam) + SP-B (durable persistence,
delete, save-as-variant) — local-execution only, behavior-preserving refactor of the two existing
callers.
**Out:** SP-C (shared UI), SP-D (deployment execution — fills the `"deployment"` seam); changing the
run results/emitter format; reworking the post-run side-effect pipeline (kept as study-runs caller
policy); a unified single run store (callers keep their own `db_path`).

## 10. Open questions (resolve in planning)
1. **Run linkage on save-as-variant:** copy the `runs_meta` row into the study's `runs.db`, or record
   an association and leave it in `.pbg/composite-runs.db`? (Copy is simpler for the Sim-DB study
   grouping; decide in planning.)
2. **Prune replacement:** no cap at all, or a generous opt-in cap with explicit delete? Default to no
   auto-eviction; confirm whether a safety ceiling is wanted.
3. **`launch()` shape:** a returned callable vs `invoke_run(..., launcher=...)` injection — pick the
   form that keeps the two callers' sync/async cleanest during planning.
