# Investigation as a process-bigraph Composite — design

**Date:** 2026-08-01
**Status:** design (for approval before implementation)
**Repo:** `vivarium-workbench` (core substrate) + `v2ecoli` (comparison as first consumer)
**Supersedes:** the orchestration-layer approach in
`2026-08-01-investigation-execution-hook-design.md` (PR #712). A1's hand-rolled
toposort and A2's separate analyses phase are both replaced by the composite
graph. See "Disposition of #712" below.

## Motivation / what exists today

An **investigation is not a composite today.** `run_investigation`
(`lib/investigations.py:1622`) is a plain Python loop over `spec.runs`, each run
fired as a separate `python -c` subprocess with a 300 s timeout, in list order,
with **no dependency scheduling**. `prepare_investigation` (#712) is the HTTP-POST
loop. `pipeline_gate.prerequisites` is declared DAG metadata used only for
gating/status — never wired into execution.

The *pattern* is nonetheless proven at the process level: **ParCa** is 9 `Step`s
wired through shared stores, run as one `Composite` in dependency order, each step
doing minutes of blocking work (`v2ecoli/processes/parca/composite.py`). The
process-bigraph runtime supports exactly what we want (confirmed against
`process_bigraph/scheduling.py:307,479` and `composite.py:2253-2333,2747`):

- **Dependency ordering** — `build_step_network` computes each step's input/output
  store paths and `determine_steps` runs a step only once every producer of its
  input paths has run. Wire study B's input to a store study A writes → A runs
  before B. Guaranteed, not incidental.
- **Blocking `update()`** — steps are invoked synchronously inline with no
  timeout; ParCa already blocks for minutes per step. Independent sibling steps
  parallelize via the engine's opt-in `ThreadPoolExecutor`.

We lift this pattern from the process level to the investigation level.

## Architecture

Four pieces. The first three are the substrate; the fourth unifies the runners.

### 1. `StudyStep` (a `process_bigraph.Step`)

One per member study. It is the seam between the composite scheduler and the
study-execution protocol.

- `config`: `{workspace, study_slug}` (+ optional run selectors).
- `inputs()`: one port per `pipeline_gate.prerequisites` edge, each wired to the
  upstream study's output store (see the generator). These ports are what create
  the ordering edges; their value is the upstream study's output ref (a study
  need not use it — presence is the ordering constraint).
- `outputs()`: `{result: "any"}` (or `{run_refs, verdict}`) written to the store
  `investigation/<slug>/result` — the study's run refs + its verdict/outcome, so
  downstream steps (dependent studies, analyses) can read it.
- `update(state)`: dispatch the study run to the persistent worker
  (`run_study` capability, §2), **block on the reply**, return the result dict.
  Blocking is correct here — the StudyStep is the parallelism boundary; the
  engine runs independent StudySteps concurrently.
- Modeled as a **`Step`, not a `Process`** (non-temporal, dependency-driven) —
  misclassifying it as an interval Process makes the clock never advance
  (the "metabolism_redux tick-2" trap; guarded by
  `tests/test_inject_step_scheduling.py`).

A StudyStep runs the study's **native baseline + variants** internally (via the
worker) — the workbench baseline/variant model is preserved *within* a study;
the composite orders *across* studies.

### 2. `run_study` worker capability (persistent JSON-RPC worker)

Extend `env_worker.py` (`_CAPABILITIES` ~:87, dispatch ~:2360) with
`run_study(params) -> {run_refs, verdict, errors}`. Given
`{workspace, study_slug, run_spec}` it runs the study **to completion,
synchronously** (blocking) — reusing the run-resolution logic of
`lib/study_runs.run_study_baseline` / `run_study_variant` (which already resolve
the composite + params + sim_name and write to `studies/<slug>/runs.db`), but
**awaiting** the run rather than detaching (the StudyStep, not HTTP, is the
async boundary now). Returns the run refs + the computed verdict. Never raises;
errors come back in the reply. Reuses the proven socket/framing protocol
(`_read_frame`/`_write_frame`) and the warm workspace core the worker already
holds.

This same capability is the home for the **investigation-level analysis**
dispatch that #712 deferred: an analysis Step (§1-style) invokes an Analysis
directly — `ANALYSIS_REGISTRY[name]({config}, core=allocate_core()).update()`
reading its native output keys (e.g. `comparison_matrix` → `{matrix_html}`) —
bypassing the parquet-coupled `run_analyses` path. (Confirmed viable:
`comparison_matrix` needs only its `config_verdicts` config + a core.)

### 3. `InvestigationComposite` generator (`@composite_generator`)

> **Status (as shipped):** this shipped as a **plain build function**, not a
> registered `@composite_generator`:
> `vivarium_workbench/lib/investigation_execution.py` `build_investigation_composite(ws_root, inv_slug) -> dict`
> returns the composite state dict, and `run_investigation_composite(...)` runs it as
> `Composite({"state": …, "run_steps_on_init": True})`. The `StudyStep` /
> `InvestigationAnalysisStep` classes live in `lib/investigation_steps.py` (referenced by
> `local:<Class>` address). The design's engine mechanics held up — synthetic serial
> edges for no-prereq members, store nodes, `"node"` port type, `run_steps_on_init: True`,
> prerequisite wiring ordering steps are all as described below. The rest of this section
> is accurate to the shipped graph; only the "registered generator" framing changed to a
> direct function.

Compiles `investigation.yaml` (members + `pipeline_gate.prerequisites`) into a
composite **state dict**:

- one `StudyStep` per member study;
- each prerequisite edge `{study: A}` on study B → a wire from `StudyStep(B)`'s
  prereq input port to the store `investigation/A/result` that `StudyStep(A)`
  writes → the ordering edge;
- **analysis steps** (e.g. `comparison_matrix`) as Steps whose inputs are wired
  to every relevant study's `.../result` store — so ordering (analysis runs
  after its studies) **and** the cross-study data-flow (`config_verdicts` flows
  in from each study's verdict) both come from the wiring. **This eliminates the
  `<run>::comparison_cards` token entirely.**
- **Declared-order preservation for no-prereq investigations**: **empirically
  confirmed against the real engine** — independent steps (no wiring between
  them) run in the engine's hash/set order, NOT declared order, and `priority`
  is only a *cycle* tiebreak (`scheduling.py:504`), so it does NOT order a ready
  layer. Therefore v1 imposes **synthetic serial edges**: the generator wires
  study[i]'s prereq input to study[i-1]'s result store when no real prerequisite
  already constrains it, turning independent studies into a deterministic
  declared-order chain (verified stable across trials). Intra-layer parallelism
  stays off (v1 serial). Enabling parallelism later = dropping the synthetic
  edges for genuinely-independent studies.
- **Confirmed engine mechanics** (walking skeleton, Task 1): register step
  classes via `allocate_core(top={"StudyStep": StudyStep})`; store nodes are
  empty dicts `{}` (ParCa pattern), not typed nodes; port types are `"node"`
  (there is no `"any"` type in this `bigraph_schema` 1.7.0); step-doc shape is
  `{_type: step, address: local:<Class>, config, inputs: {port:[store]},
  outputs: {port:[store]}}`; `run_steps_on_init: True` fires ready steps at
  construction. Prerequisite wiring orders steps (incl. transitive chains) —
  confirmed.

Returns `{state, run_steps_on_init: True}` (the ParCa pattern), or the runner
constructs `Composite({state}, core)` and calls `.run()`.

### 4. Runner unification

> **Status (as shipped):** the runners were **not** unified onto the composite. Only
> **`prepare_investigation`'s full-run branch** builds and runs the `StudyStep` composite
> in-process. **`run_investigation` / `vwb run investigation` route to the inline
> `subprocess` path instead**, not to the composite. And the **canonical** investigation
> orchestrator today is neither: it is the **`run_jobs`** manager behind
> `POST /api/investigation-run-unblocked` (async 202 + poll, via `study_runs`), made
> canonical by `docs/run-orchestration-consolidation.md` Decision 1. The composite path
> is reachable only via `prepare-investigation` and the tests — it is **non-default**,
> serial-only, fail-loud, not UI-wired, and **refuses on the `deployment` run target**.
> So three orchestrators coexist (`run_jobs` canonical · inline-subprocess
> `/api/investigation-run` · `prepare-investigation` composite); "one execution path" is
> design intent, not the shipped state. The design below is preserved as the original
> intent.

`run_investigation` and `prepare_investigation` both **build the
InvestigationComposite and run it in-process** (`Composite({state}, core).run()`
— an in-process Composite run is already a load-bearing workbench path, e.g.
`env_worker.py:1560` viz rendering). One execution path; the scheduler is the
single source of ordering + cross-study flow. Existing entry points keep their
signatures and return shapes (additive), so the dashboard/CLI callers are
unaffected.

## How this absorbs the prior work

- **A1 (ordering)** → composite wiring + `determine_steps`. The
  `investigation_order.py` toposort is no longer needed.
- **A2 (investigation-level analyses)** → analysis Steps wired into the graph;
  the deferred dispatch lands as the `run_study`-sibling analysis invocation in
  the worker.
- **Comparison re-model (old Phase B)** → the comparison investigation compiles
  to an InvestigationComposite: per config a StudyStep (native baseline=candidate
  `ecoli_baseline` + variant=reference `vecoli`), a `comparison_matrix` analysis
  Step wired to all config studies, ParCa as an upstream StudyStep. The native
  baseline+variant model + `comparison_cards` per study still apply within each
  StudyStep.

## Disposition of PR #712

#712 (orchestration-layer A1 + A2 skeleton) is **superseded** by this design.
Recommendation: **do not merge #712**; keep its spec/plan/tests as reference and
fold the still-valid pieces (the golden backward-compat test intent; the
`deferred` vs `error` status idea; the strict `pipeline_gate.prerequisites`
reading) into the new substrate's tests. The `investigation_order.py` helper and
the `prepare_investigation` reorder are dropped (the scheduler replaces them).
Decision to confirm with the user before closing #712.

## Backward-compatibility & rollout

- No-prereq investigations: declared-order-preserving (priority by member index,
  serial). Golden test carried over from #712.
- Unify (not full-replace): both runners delegate to the composite; the imperative
  subprocess loop's *observable* behavior (which runs happen) is preserved for
  existing investigations, only the *ordering mechanism* changes. The 300 s
  per-run timeout of the old loop is replaced by the worker's run-to-completion
  (a study legitimately takes longer than 300 s — this is a fix, but flag it).
- The `run_study` worker capability is new surface; hermetically testable with a
  fake study/worker; the real study execution is gated behind the workspace env
  (process_bigraph present) as an e2e.

## Testing strategy

- **StudyStep** unit: dispatches to a stubbed worker, writes its result store,
  reads prereq inputs. Hermetic.
- **Scheduler ordering** (the load-bearing property): a 3-StudyStep composite
  with prereq wiring runs in dependency order; a no-prereq composite runs in
  declared (priority) order. Assert against the real `process_bigraph` engine
  using stub StudySteps (no real runs) — the venv with `process_bigraph` (the
  v2ecoli `.venv`) is required for these.
- **`run_study` worker**: hermetic with a fake workspace; e2e (gated) with a real
  study.
- **InvestigationComposite generator**: compiles a fixture `investigation.yaml`
  (with + without prerequisites) to the expected step set + wiring; the analysis
  step is wired to the study result stores.
- **Runner unification**: `run_investigation`/`prepare_investigation` produce the
  same run set as before for a no-prereq fixture (golden), plus dependency order
  for a prereq fixture.
- **e2e** (gated, on the mini / v2ecoli venv): the comparison investigation runs
  candidate+reference per config in dependency order (ParCa first) and the matrix
  renders from wired verdicts — entirely through the composite.

## Risks

- **Engine coupling / in-process run duration.** The investigation composite run
  is a long-lived in-process orchestration (studies block in worker calls). It
  must not run on the HTTP request thread — the runner launches it detached (as
  the runners already do for long work) and reports progress via runs.db /
  generation, not a blocked HTTP response.
- **Parallelism correctness.** Enabling intra-layer `ThreadPoolExecutor` for
  independent studies is a follow-up optimization, not v1 (v1 = serial,
  declared-order-preserving). Flag before turning it on: `apply_updates` is
  single-threaded but the study subprocess launches must be process-safe.
- **`run_study` blocking vs the detached run API.** The existing
  `run_study_baseline` detaches; the worker path must await completion. Reusing
  its resolution logic while changing the wait semantics is the main integration
  risk — isolate it.
- **Store-path schema for results.** `investigation/<slug>/result` must be a
  stable typed store the generator wires consistently; a mismatch silently drops
  an ordering edge (the `triggers()` "silent input" caveat — keep prereq inputs
  in the default trigger set).

## Non-goals (v1)

- Distributed / cross-host study execution (the worker is local).
- Enabling intra-layer parallel study execution (v1 is serial).
- Full deletion of the imperative runners (we unify/delegate; deprecation is a
  later step once the composite path is proven).
- Re-running arbitrary cross-investigation DAGs.
