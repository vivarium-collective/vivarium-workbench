# Workbench: vendor loom, default-parquet baseline, auto-Results, JSONL run log, sortable Sims DB

Date: 2026-07-21
Status: Approved (design) — pending implementation plan
Primary repo: `vivarium-workbench` (`vivarium-dashboard` checkout)
Secondary repo: `v2ecoli` (one bundle-locator repoint; study runners inherit JSONL via shared lib)

## Motivation

While running `baseline` in the Composite Explorer we hit five friction points. Two
are architectural (co-develop loom with the workbench; move run metadata off sqlite);
three are UX/correctness (default parquet emission, auto-advance to Results, always
show + sort Time/Emitter in the Simulations DB).

## Background (verified against the code)

- The Composite Explorer's tabbed UI **is** the `bigraph-loom` React/Vite app
  (`src/panels/{SetupRun,Results,Visualizations,Document}Panel.tsx`, `src/App.tsx`),
  embedded in the workbench as an iframe at `/bigraph-loom/`. The `bigraph_loom`
  Python package is a 1-file shim that ships the built `_dist` bundle.
  - Workbench consumes it via `bigraph-loom @ git+…@main` (`pyproject.toml:47`) and
    `from bigraph_loom import asset_dir` (`lib/static_serving.py:124`, `publish.py:964`).
  - Only external Python consumer: `v2ecoli/scripts/regenerate_viewers.py:304`
    (`import bigraph_loom` to locate `_dist`).
- The baseline composite **already declares** a default ParquetEmitter
  (`v2ecoli/composites/baseline.py:615`, `emitters=[{address:"local:ParquetEmitter",
  paths:["global_time","bulk","listeners"]}]`); a plain `baseline(...)` build yields it
  (test: `tests/test_baseline_default_emitter.py`). The explorer's run worker ignores it.
- Explorer run flow: loom `SetupRunPanel.handleRun` → `POST /api/composite-test-run`
  (`lib/composite_test_run_views.py:54`) → detached worker `lib/composite_subprocess.py`,
  which at `:388-390` injects a RAM `user_emitter` + a SQLite emitter from the UI's
  `emit_paths` (top-level stores → "agents, global_time"), **preferring the UI selection
  over the composite's declared emitter**.
- Auto-switch to Results is **already wired** in loom main: `App.tsx:742`
  `onCompleted={() => setTab('results')}`, fired at `SetupRunPanel.tsx:182-184`. The
  deployed `0.3.1` bundle may predate it.
- Simulations DB reads two sqlite sources — `<ws>/.pbg/composite-runs.db` (explorer) and
  per-study `studies/<slug>/runs.db` (also nested under `investigations/<inv>/…`) — via
  `lib/simulations_index.build_simulations_data`. `runs_meta` schema
  (`lib/composite_runs.py:18`) has **no emitter column**; `emitter_type` is derived at
  read time (blank/"—" for study runs). Time (`started_at`/`completed_at`) is stored.
  Frontend (`static/walkthrough.js` `_renderSimRow`/`_applySimFilter`; headers
  `templates/index.html.j2:1185`) filters but does **not** sort; no header click-to-sort.
- v2ecoli study runners (`library/sqlite_run.py`, `scripts/run_default_baseline.py`, …)
  write their `runs.db` **through `vivarium_workbench.lib.composite_runs`** — a single
  shared choke point for the metadata write path.

## Design

### Feature 1 — Vendor bigraph-loom (plain copy)

- Copy loom's source into the workbench repo under `vivarium_workbench/loom/`
  (`src/`, `package.json`, `vite.config.ts`, `tsconfig*`, `vitest.config.ts`,
  `index.html`, and the `bigraph_loom/__init__.py` asset shim). Exclude
  `node_modules/`, build artifacts, and `.git`.
- The workbench build compiles loom (`tsc -b && vite build`) to produce the `_dist`
  bundle that the Python shim serves. Wire this into the existing workbench build/publish
  path so a `pip`/wheel build still yields a populated `_dist`.
- Remove the `bigraph-loom @ git+…@main` dependency (`pyproject.toml:47`) and update the
  hatch/PEP-508 note at `pyproject.toml:107`.
- Update `from bigraph_loom import asset_dir` call sites
  (`lib/static_serving.py:124`, `publish.py:964`) to the vendored module path.
- Repoint `v2ecoli/scripts/regenerate_viewers.py:304` to locate the bundle via
  `vivarium_workbench` instead of `import bigraph_loom`.
- Archive (do not delete) the standalone `vivarium-collective/bigraph-loom` repo;
  note the vendor point + upstream commit in the workbench README.

### Feature 2 — Baseline emits-all via Parquet, auto-injected (inject both)

- In the run worker, when the composite declares a default emitter (via
  `pbg_superpowers.composite_generator.emitter_defaults` / an in-document `emitter`
  step), inject **both**:
  1. the declared **Parquet** emitter for persistence, with the emit-all schema
     (`["global_time","bulk","listeners"]`, which expands to the full listeners set +
     bulk), and
  2. a lightweight **RAM** emitter so the Results tab renders the live trajectory
     exactly as today.
- The declared emitter takes precedence over the UI's top-level-store default; the loom
  emit-selection UI defaults to the declared/emit-all set (still user-overridable in the
  Wiring tab).
- Touch points: `lib/composite_subprocess.py:388-390`,
  `lib/composite_runs.py` (`inject_emitter_for_paths` /
  `inject_emitter_for_declared_paths` / `inject_sqlite_emitter`), loom
  `App.tsx`/`SetupRunPanel.tsx` emit-set seeding.
- The Parquet run must still register in the run-metadata log (Feature 4) and be
  detectable by the Sims DB (`emitter_type` "Parquet").

### Feature 3 — Auto-switch to Results on completion

- Behavior already exists in loom main (`App.tsx:742`). Vendoring current loom ships it.
- Add a loom test locking `onCompleted → setTab('results')` so the bundled build can't
  silently regress.

### Feature 4 — JSONL run-metadata log (replaces sqlite runs.db for new writes)

- Introduce a single append-only, write-only event log per workspace:
  **`<workspace>/.pbg/runs.jsonl`**. One JSON object per line, event-sourced:
  ```json
  {"ts":"<iso8601>","run_id":"…","event":"started|completed|failed",
   "spec_id":"…","label":"…","study_slug":"…","investigation_slug":"…",
   "origin":"local|remote:<commit>","emitter":"parquet|sqlite|ram|…",
   "n_steps":0,"started_at":"…","completed_at":"…","status":"…","store_path":"…"}
  ```
- **Write path:** a shared `append_run_event()` in `vivarium_workbench.lib` (atomic
  `O_APPEND` line write) is the single choke point. `save_metadata`/`complete_metadata`
  emit `started`/`completed`/`failed` events. Because v2ecoli study runners already call
  into this lib, they capture **Time + Emitter automatically** — no per-script edits.
  Emitter is known at inject time, so it is never blank for new runs. Live
  progress/heartbeat stays ephemeral (per-run scratch, e.g. `.pbg/runs/<run_id>/`), not
  in the durable log, to keep it clean.
- **Read path:** `fold_runs_jsonl()` folds the log to latest-per-run;
  `simulations_index.build_simulations_data` reads it **and still reads legacy `*.db`**
  (read-only compat) so existing runs (e.g. the 7 comparison runs) don't vanish. Merge
  keys on `run_id`; JSONL wins when both exist.
- **Scope boundary (confirmed):** new writes → JSONL; legacy sqlite → **read-only compat,
  not removed** in this change. An optional one-shot backfill (adapt
  `scripts/backfill_runs_db.py`) migrates old sqlite rows → jsonl.
- **Single workspace-level log (confirmed)** rather than per-study logs; events are tagged
  with `study_slug`/`investigation_slug`, so the existing Sims-DB filters work on the
  folded records.

### Feature 5 — Click-to-sort Simulations DB columns

- Add click handlers on the `<th>`s (`templates/index.html.j2:1185-1193`) and a
  client-side sort state (column + direction) in `_applySimFilter`
  (`static/walkthrough.js:14678`) operating on `window._simRows` before
  `.map(_renderSimRow)`. Sort by any column (Investigation, Study, Run, Origin, Emitter,
  Time, Status). Toggle asc/desc with a header indicator. Server default stays
  newest-first.

## Cross-cutting

- **Build/deploy:** code changes only; the live smscdk workbench reflects them after a
  new `vivarium-workbench` image + `kubectl rollout restart deploy/workbench -n
  sms-api-stanford`. Iterate locally via `scripts/serve.sh`.
- **Branching:** feature branch `feat/loom-vendor-emitter-simsdb` off workbench `main`.
- **Testing:**
  - loom build smoke + Results-auto-switch test (F1, F3);
  - runner emitter-injection test — both RAM + Parquet present, emit-all schema (F2);
  - JSONL write/fold round-trip + `build_simulations_data` reads JSONL + legacy sqlite,
    Time/Emitter populated (F4);
  - Sims-DB sort unit test across columns/directions (F5).

## Out of scope

- Removing sqlite `runs.db` entirely (kept read-only for back-compat).
- Rewriting each v2ecoli study runner (they inherit JSONL via the shared lib).
- Making the Results tab read from parquet (RAM emitter continues to drive live view).
- Any change to the remote deployment topology (only image content changes).

## Open questions

None outstanding — JSONL scope boundary and single-log location confirmed with the user.
