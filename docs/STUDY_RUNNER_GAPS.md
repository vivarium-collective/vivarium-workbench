# Study runner gaps

Snapshot of where the dashboard's Study-tab runner can and can't drive
the v3 study schema as of the multi-baseline runner work (`/api/study-run-all-baselines`).

## What works today

| Pattern | Handler | Notes |
|---|---|---|
| Single-baseline run | `POST /api/study-run-baseline {study, composite, steps?}` | One entry, one run, canonical viz rendered from `entry.visualizations` + spec.visualizations. |
| Multi-baseline run (sequential) | `POST /api/study-run-all-baselines {study, steps?}` | Iterates `spec.baseline[]`, dispatches each through the per-entry handler, aggregates results. Returns 200 / 207 / first-failure-code. |
| Variant run | `POST /api/study-run-variant {study, variant, steps?}` | Layers `parameter_overrides` onto the variant's `base_composite` entry. |

Render path: `_render_study_visualizations` walks the merged viz list
(decorator defaults ∪ spec entries), calls
`render_visualizations(spec, study_dir, …, build_and_run=…)`. Each viz
is materialized via `build_viz_composite`, which:

1. Resolves the viz class through `core.link_registry`.
2. Inspects `viz_class.inputs()` for declared port shapes.
3. Looks up each port name (overridden by `config.inputs_map`) against
   `gather_emitter_outputs(study_dir/runs.db).by_sim[<sources>]`.
4. Runs the viz composite for one step; captures `output_store` HTML.

This means every viz must consume **per-tick emitter trajectories**
keyed by observable name.

## What still doesn't work

### 1. Visualizations that consume composite specs (e.g. `CompareVisualization`)

`CompareVisualization` (v2ecoli) expects
`update({"composite_specs": [<3 graph_data dicts>]})`. The graph_data
comes from `build_graph(composite, layers)` — i.e. **the built composite
itself**, not from emitter trajectories. There are no observables to
look up.

Today, declaring `CompareVisualization` in a Study's `visualizations:`
list goes through `build_viz_composite`, which sees an empty
`inputs()` dict and produces no usable wiring; the viz Step is invoked
with empty inputs and outputs an error stub or empty HTML.

Three plausible fixes:

**(A) Special-case the address in `_render_study_visualizations`.**
Hard-code a branch: if `viz_spec['address'].endswith(':CompareVisualization')`,
re-route through a new `_render_compare_viz(study_dir, spec)` helper
that imports v2ecoli's `build_execution_layers` / `build_graph` per
architecture and dispatches manually. Pros: contained, fast. Cons:
puts v2ecoli-specific knowledge in the generic dashboard.

**(B) Refactor `CompareVisualization` to be self-driven from `config`.**
Change `config` to take a list of composite refs:

```yaml
- name: comparison
  address: local:CompareVisualization
  config:
    composites:                # NEW: dotted refs, not emitter sources
      - v2ecoli.composites.baseline.baseline
      - v2ecoli.composites.departitioned.departitioned
      - v2ecoli.composites.reconciled.reconciled
```

`CompareVisualization.update({})` looks up each ref in
`pbg_superpowers.composite_generator._REGISTRY`, materializes,
extracts graph_data, renders. The dashboard only needs to know how to
call `update({})` for viz with empty `inputs()`. Pros: clean
separation, generalizes to any "self-driven" viz. Cons: requires
changing the Visualization Step contract on the v2ecoli side and
teaching `build_viz_composite` that empty-input viz are valid (today
they fall through to an error stub).

**(C) Generic `gather_inputs(spec, study_dir, registry)` method on the
`Visualization` base class.** Each viz subclass overrides if it needs
something non-standard; default = current emitter-trajectory walk. The
dashboard calls `gather_inputs` instead of doing the wiring inline.
Pros: most generic. Cons: needs coordinated changes in
`bigraph_schema` / `pbg_superpowers` / every existing viz Step.

Recommended path: **(B)**. It keeps v2ecoli-specific behavior in
v2ecoli, only requires a small dashboard tweak (`build_viz_composite`
should yield `inputs_store = {}` and let `viz.update({})` decide what
to do), and naturally extends to any future viz that wants to drive
itself from a config payload.

### 2. Lineage (`spec.lineage`)

Nothing reads `spec.lineage` server-side. The daughter-carry-forward
loop lives only in `reports/multigeneration_report.py:run_multigeneration`
(in v2ecoli). To drive lineage from the dashboard:

1. Add `_post_study_run_lineage_for_test(ws_root, body)` that reads
   `spec.lineage.{generations, max_duration, seed_strategy}`.
2. Refactor `run_multigeneration` into a v2ecoli library helper that
   accepts a pre-built composite + a fresh-composite factory, so the
   dashboard can call it without copying the orchestration code.
   (Today `run_multigeneration` builds the composite itself.)
3. Persist each generation's run into `study_dir/runs.db` with a
   `generation` tag, OR write a separate `lineage_history.json`.
4. Wire `MultigenerationVisualization` to consume that tagged history
   (similar resolution problem as `CompareVisualization`).

The cleanest split is (B) above plus a new
`MultigenerationVisualization` config that pulls per-generation
emitter slices from `study_dir/runs.db` and feeds them into
`update({history: [...rows with generation tag...]})`.

### 3. UI affordances

- No "Run all baselines" button yet; `study-detail.js` only binds
  per-entry `.btn-run-baseline` handlers. Wiring a header-level
  button to `/api/study-run-all-baselines` is straightforward and
  belongs in the next study-detail JS PR.
- No lineage UI at all (depends on the runner above existing first).
