# vivarium-workbench — Design & Data Architecture

This document explains **what the dashboard does**, **where data lives**, **how
data flows and is transformed**, and **which repository is the source of truth**
for each artifact. It is the "big picture" companion to `CLAUDE.md` (which covers
commands and code layout). For how the dashboard is *deployed* with a workspace
(pip dependency, run from the workspace venv) see [USAGE.md](USAGE.md).

> Status: descriptive of the code as of mid-2026, reconciled against a
> code-first audit on 2026-07-07 (stale claims corrected in place). Schema
> versions (study/spec v2→v3→v4) and emitter backends (SQLite / Parquet /
> XArray) coexist; where a field or table is version- or backend-specific this
> is called out. For the exhaustive, file:line-cited version of this audit —
> including the ranked architectural risks and the full reality-vs-docs
> divergence table — see [ARCHITECTURE-DEEP-DIVE.md](ARCHITECTURE-DEEP-DIVE.md).
> Passages below marked **⚠ Correction** were inaccurate in earlier revisions.

---

## 1. What this actually is

`vivarium-workbench` is a **local, single-process web server that turns a
process-bigraph *workspace* into an interactive, git-backed research notebook.**
It is *not* a simulation engine and it has *no database of its own* — it reads
and writes plain files in a workspace directory and delegates simulation to
[process-bigraph](https://github.com/vivarium-collective/process-bigraph).

> **Renamed from `vivarium-dashboard`.** The `vivarium_dashboard` import package,
> the `vivarium-dashboard`/`vdash`/`vivarium-dashboard-publish` CLIs, and the
> `VIVARIUM_DASHBOARD_*` env vars keep working as deprecated aliases during the
> migration window (removed in a future major release). The published static
> bundle is still the "read-only dashboard".

The mental model is three layers:

```
   ENGINE            process-bigraph + bigraph-schema     (runs the science)
      │
   TOOLING           vivarium-workbench  (THIS REPO)      (UI + orchestration + git)
   + AI TOOLING      pbg-superpowers (/pbg-* Claude skills)
      │
   DATA              a workspace directory                (the only source of truth)
                     (scaffolded from pbg-template)
```

The dashboard's job is to **author** the workspace's YAML specs through a UI,
**orchestrate** simulation runs, **persist** results, **render** specs+results
into status verdicts and charts, and **commit** every change to git so there is
a full audit trail.

### The crucial split: repo vs. workspace

This repository is the *server/tooling*. The *data* it operates on lives in a
**separate workspace directory** passed via `--workspace`. Everything the user
authors and every result produced lives in the workspace, never in this repo
(except `tests/_fixtures/`). Keep these two git histories distinct:

- **This repo's** git history = changes to the dashboard software (PRs to `main`).
- **The workspace's** git history = the scientific audit trail the dashboard
  writes on the user's behalf.

### The HTTP layer

The dashboard is served by a **FastAPI app** (`api/app.py`) run under uvicorn:
`vivarium-workbench serve` → `cli.py` → `lib/startup.serve_fastapi` →
`uvicorn.run(app, ...)`. All routes (read and write, static/SPA serving, and the
SSE stream) are defined there and back onto the `lib/` functions.

The old ~9.6k-line stdlib `http.server` handler (`server.py`) has been
**deleted**. The strangler-fig migration is complete: every route and all real
logic now live in `api/app.py` + `lib/`, and the `dashboard_client` test fixture
spawns the live FastAPI app. `server.py` is now only a ~40-line deprecation shim
re-exporting six symbols (`_json_default`/`_json_sanitize`/`_json_body` and
`_build_iset_summary_for_test`/`_build_iset_detail_for_test`/`_observables_for_ref`)
from their `lib` homes, retained solely for external consumers (v2ecoli,
sms-ecoli, pbg-superpowers) until they migrate to the `lib` paths.

Supporting the typed contract:

- **`lib/models.py`** — pydantic models for the JSON payloads (`SimRow`,
  `ChartPayload`, `RemoteRunJob`, the `runs_meta` row shape, …). Note many of the
  richest payloads are still `extra="allow"` passthroughs; tightening these into
  real declared-field models is tracked in the hardening plan.
- **`mypy`** (scoped, widening) gates the typed modules, and the browser-side
  **TypeScript types are generated from the pydantic models**
  (`lib/generate_ts.py`) — so the client contract is *derived*, not hand-copied.

---

## 2. The domain model

The workspace organizes computational biology research into a three-level
hierarchy, all expressed as YAML + simulation output:

| Concept | Lives at | What it is |
|---|---|---|
| **Composite** | `<package>/composites/<name>.composite.{yaml,json}` or a `@composite_generator` function | A runnable process-bigraph **model**: a nested *state tree* of stores/processes plus named `parameters` that can be overridden per run. |
| **Study** | `studies/<slug>/study.yaml` | One **experiment / hypothesis test**: picks composite(s), declares what to run, what to measure (readouts), and the pass/fail criteria (behavior tests / gate). Owns its run results. |
| **Investigation** | `investigations/<slug>/investigation.yaml` | A **DAG of studies** forming a research arc, grouped into narrative "parts", with a baseline + comparison variants that get overlaid in charts. |

### Composites and the "registry"

The **registry** is the union of all composites the workspace can import,
discovered from three sources (see `lib/composite_lookup.py`):
1. the workspace's own package — `<package>/composites/*.composite.*`
2. every installed `pbg-*` distribution's `composites/` directory
3. `@composite_generator`-decorated factory functions discovered via
   `pbg_superpowers.composite_generator`

A composite is referenced by an id like `v2ecoli.composites.baseline.baseline`.
The state tree itself (its structure and type system) is owned by
process-bigraph / bigraph-schema, **not** by this dashboard.

### Studies: sections, behavior tests, and the gate

A `study.yaml` is a structured narrative (the "8-section" canonical view in the
UI — Purpose · Pipeline gate · Build · Simulations · Readouts · Tests ·
Limitations · References; the v4 schema expands this with authored
`report`/`study_card`/`biological_summary` narrative blocks). The load-time
machinery auto-migrates older v2/v3 specs in memory (`lib/spec_migration.py`,
`lib/investigations.py`).

> **⚠ Schema hazard worth knowing:** `schema_version: 4` is **ambiguous — two
> incompatible study shapes share the number**, disambiguated only by whether a
> top-level `conditions:` block is present. With it, the "redesign v4"
> (`question`/`conditions`/`tests`-as-a-*list*) is validated and then projected
> back onto legacy v3 field names in-memory by a ~170-line synthesizer so the v3
> renderer works unchanged; without it, "legacy v4" (`tests`-as-a-*dict*) is
> used. Adding a `conditions:` key to a legacy study silently flips the entire
> validation + render path. (There are also two study-directory resolvers that
> can disagree for the same slug — see the deep-dive §4.)

Two derived concepts drive the UI's "is this study OK?" signal:

- **Behavior tests / expected behavior** (`lib/expected_behavior.py`): a small
  declarative DSL. Each entry extracts a measure from run history (a trajectory
  or scalar) and evaluates it against an expectation. The actual operators are
  broader than older revisions of this doc (and the module docstring) claimed:
  **8 expect ops** (`in_range`, `rolling_cv_below`, `ratio_at_most`,
  `ratio_at_least`, `monotonic_decreasing`, `pearson_below`, `pearson_above`,
  `pre_post_event_ratio`), **6 reduce modes**, and **6 measure kinds**.
  `evaluate()` returns `(passed, message)`. **⚠ Correction:** it does *not*
  categorically "never raise" — it catches only the two custom
  `Missing{Measure,Expect}Error`s; a malformed entry raising `KeyError`/
  `TypeError` on `entry["measure"]`/`entry["expect"]` still propagates.
- **Pipeline gate / verdict**: a study's acceptance verdict, computed (not
  authored) from test outcomes + run status + findings. The investigation
  declares a study-to-study dependency DAG (`pipeline_gate.prerequisites`, with
  a legacy `parent_studies` fallback). **⚠ Correction:** this DAG is currently
  **advisory/display-only** — `build_investigations` computes `blocked`/
  `blocked_by` for the UI, but **no run endpoint consults it**, so a downstream
  study *can* be launched before its predecessors pass. (Earlier revisions
  claimed predecessors "must pass before a downstream study proceeds"; that is
  not enforced in code.) The only run-time gating is a study's *own*
  `conditions.model_settings` entries marked `gate: required-before-run` with an
  unset value (`run_jobs.enumerate_unblocked`).

**`effective_status`** is a multi-axis precedence rollup: the most-downstream
axis that is set wins. **⚠ Correction:** there are **6 axes**, not 5 —
`gate > evaluation > simulation > implementation > design > expert_review`.
This is why a study can show "passed" even while a later sim is still "running".
Rollup logic lives largely in `pbg_superpowers` (`study_verdict`, `study_status`)
and is computed on read — it is not persisted.

### Investigations: DAG, baseline vs. variants, coordinated runs

An investigation groups studies into ordered "parts" and wires the
study-to-study DAG. It defines a **baseline** plus **comparison variants**
(parameter-overridden versions). `vivarium-workbench prepare-investigation`
(→ `lib/prepare_investigation.py`) is the coordinated orchestrator: it runs the
baseline + every variant and re-renders the comparative overlays as one
"generation", so all traces on a chart come from a consistent run set. See
`docs/investigation-narrative-schema.md` for the per-study narrative fields.

---

## 3. Where data lives (workspace layout)

All directory names are resolved through `lib/workspace_paths.py` — **never
hardcode them**, because a workspace may relocate any of them via a `layout:`
map in `workspace.yaml`. Defaults (`LAYOUT_DEFAULTS`):

```
<workspace>/
├── workspace.yaml              # workspace identity + config (name, package_path, layout, ui, ...)
├── <package>/                  # the workspace's own Python package (package_path)
│   └── composites/             #   *.composite.yaml — model definitions
├── studies/<slug>/             # one experiment per dir
│   ├── study.yaml              #   the spec (source of truth for the experiment)
│   ├── runs.db                 #   SQLite — run results for this study (durable output)
│   ├── parquet-runs/…          #   alt emitter output (Parquet hive) — backend-dependent
│   └── viz/…                   #   rendered charts (+ .meta.json freshness sidecars)
├── investigations/<slug>/      # a research arc
│   ├── investigation.yaml      #   parts + study DAG + variants
│   ├── studies/<slug>/…        #   studies may be NESTED here (investigation-scoped)
│   └── viz/…                   #   comparative multi-run charts
├── composites/                 # (legacy) workspace-root composites
├── references/                 # papers.bib + uploaded PDFs (+ .cache.json enrichment, gitignored)
├── datasets/  notes/  experiments/  reports/  scripts/  tests/  docs/
└── .pbg/                       # derived/transient state — gitignored
    ├── schemas/                #   JSON-Schema validators (shipped by pbg-template)
    ├── runs/<run_id>/          #   per-run scratch: request.json, run.log, viz.json, emitter output
    ├── composite-runs.db       #   SQLite — Composite Explorer scratch runs
    ├── state.json              #   per-developer workstream state (active branch, PR)
    └── server/, dashboard/, …  #   runtime metadata for orchestration & discovery
```

Note studies can live **either** flat under `studies/<slug>/` **or** nested
under `investigations/<inv>/studies/<slug>/`. Resolution is nested-first
(`lib/investigations.py: iter_study_dirs / study_dir`).

### Sources of truth, by artifact

| Artifact | Source of truth | Owner / writer |
|---|---|---|
| Workspace identity & config | `workspace.yaml` | dashboard + `/pbg-*` skills (both write it) |
| Experiment design | `studies/<slug>/study.yaml` | dashboard + `/pbg-*` skills |
| Research-arc structure | `investigations/<slug>/investigation.yaml` | dashboard + `/pbg-*` skills |
| Model definition | `*.composite.yaml` / generator fn | workspace package / installed pbg-* / pbg-superpowers |
| **Run results** | `studies/<slug>/runs.db` (or backend emitter output) | written by process-bigraph emitter during a run |
| Audit trail | the workspace's **git history** (+ `.pbg/events.jsonl` event log) | dashboard commits most mutations; catalog ops are an exception (see §6) |
| Validation schemas | `.pbg/schemas/*.json` | **pbg-template** (read-only here) |
| Cross-dashboard discovery | `~/.pbg/servers/` | `pbg_superpowers.workspace_catalog` |
| Derived status/verdicts | *(not persisted — computed on read)* | `pbg_superpowers` rollups |

The key invariant: **the workspace's YAML files are the only durable store of
authored work, and `runs.db` is the durable store of results.** Everything the
dashboard shows is a projection of those plus on-the-fly computation. There is
no separate dashboard database.

---

## 4. Data lifecycle: a simulation run

This is the most important flow to understand. Runs can take tens of minutes.

> **⚠ Correction — there are actually *two* run engines, and the diagram below
> describes only one of them.** The clean detached-subprocess model (request
> file → `spawn_detached` → `run_runner.execute`, PID-tracked, heartbeat,
> `MAX_RUNTIME_SEC` self-terminate, reconciled on restart) is **Engine A**, used
> by `POST /api/composite-test-run` — the *Composite Explorer scratch* path
> (writes `.pbg/composite-runs.db`). The core scientific path —
> `POST /api/study-run-baseline` / `-variant` (writes `studies/<slug>/runs.db`)
> — is **Engine B** (`study_runs.py` → `composite_subprocess.py`): it builds a
> ~240-line `python -c` script and runs it **synchronously inside the HTTP
> request** via `subprocess.run(..., timeout=1800)`. Engine B has **no** PID
> tracking, heartbeat, self-terminate, concurrency cap, or restart
> reconciliation — an interrupted study run is left `status='running'`
> permanently (`startup.reconcile_stale_runs` only ever runs against
> `composite-runs.db`). The steps below are accurate **for Engine A**; Engine B
> is the weaker path despite owning the durable results. See the deep-dive §5.

```
1. TRIGGER   User clicks "Run" → POST /api/composite-test-run | /api/study-run-baseline | …
                │  (must pass the CSRF/origin check; see §6)
                ▼
2. REQUEST   Server writes a pure-config run-request JSON to .pbg/runs/<run_id>/request.json
                │  RunRequest = {run_id, spec_id, pkg, workspace, overrides, steps,
                │                emit_paths, db_file, log_path}
                │  Inserts a runs_meta row: status='running', started_at=now()
                ▼
3. SPAWN     run_registry.spawn_detached() launches, fully detached (start_new_session=True):
                │     python -m vivarium_workbench.cli run-composite --request <file>
                │  Records child PID in runs_meta. Returns 202 immediately.
                ▼
4. EXECUTE   lib/run_runner.execute() (the PURE detached executor):
                │  • reads EVERYTHING from request.json (never argv/globals →
                │    structurally avoids "Argument list too long")
                │  • resolves composite state (generator or file spec) + applies overrides
                │  • injects emitters to capture the declared readout paths
                │  • core = workspace package's build_core(); Composite({'state':…}, core)
                │  • loops composite.run(1) per step, updating progress + heartbeat
                │  • self-terminates if it exceeds MAX_RUNTIME_SEC (1800s)
                ▼
5. PERSIST   • runs_meta updated: status='completed', completed_at, actual n_steps
             • per-step state trajectory written by the process-bigraph emitter
               (SQLite `history`/`simulations` tables, or Parquet/XArray output)
             • .pbg/runs/<run_id>/run.log (stdout/stderr), viz.json (rendered viz)
                ▼
6. POLL/READ Browser polls GET /api/composite-run/<run_id>/status (reads runs_meta).
             On open: GET /api/composite-run/<run_id> reads the trajectory back;
             charts read runs.db and render Plotly HTML.
```

### runs.db schema

The dashboard owns the **`runs_meta`** table (one row per run — the run's
lifecycle and bookkeeping), defined in `lib/composite_runs.py`:

```sql
CREATE TABLE runs_meta (
  run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, label TEXT, params_json TEXT,
  started_at REAL NOT NULL, completed_at REAL, n_steps INTEGER,
  status TEXT NOT NULL, sim_name TEXT
);
-- nullable columns added by migration: pid, progress_step, log_path, heartbeat_at, generation_id
```

> **⚠ Correction:** this DDL is the dashboard's own; the repo actually carries
> **three separate hand-maintained `runs_meta` DDLs** (this one, a byte-vendored
> copy in `run_registry.py`, and an inline one in `copy_run_to_new_db`). They
> have drifted: the vendored copy + `backfill_runs` write an `emitter_path`
> column the dashboard's own migration never adds — a latent
> `no such column: emitter_path` if backfill is ever wired against a
> dashboard-created study `runs.db`.

The **per-step trajectory** tables (`history`, `simulations`) are created and
written by **process-bigraph's `SQLiteEmitter`**, not by this repo. By
convention `history.simulation_id == runs_meta.run_id`, so the dashboard joins
metadata (its own) to trajectory (the engine's) on that id.

### Emitter backends and backfill

Run output may land in different backends depending on the composite/workspace:
SQLite (`runs.db`), a **Parquet hive** (`studies/<slug>/parquet-runs/…`), or
**XArray/Zarr** (`.pbg/runs/<id>/store.zarr`). **⚠ Correction:** the **default
emitter is now `xarray`** (`emitters.DEFAULT_EMITTER`, flipped from SQLite by
"Task 6") — the SQLite-centric framing elsewhere in this section is historical.
In practice the xarray path is fragile on short runs (it under-fills a size-3
buffer and *silently falls back to SQLite* via a content probe), so the emitted
backend is often not the declared one. `lib/simulations_index.py` is the
unifier — it discovers runs across *all* backends and presents one normalized
list. `lib/backfill_runs.py` registers on-disk emitter output that was produced
outside dashboard tracking back into `runs_meta`, so externally-run sims still
appear. In-memory job tracking (`lib/run_jobs.py`) is *only* progress signalling
for multi-variant investigation runs and is lost on restart — `runs.db` is the
durable artifact.

---

## 5. Data lifecycle: spec → rendered dashboard

The read/render path transforms **YAML specs + run data → verdicts → charts → HTML**:

```
study.yaml / investigation.yaml
   │  load + in-memory migrate (v2→v3→v4), validate against .pbg/schemas/
   ▼
runs.db (history) ─────────────┐
   │                           │
   ▼                           ▼
expected_behavior.evaluate()   study_charts.py / comparative_viz.py
   → pass/fail per test          → Plotly HTML (baseline + variant overlays)
   │                           │
   ▼                           ▼
gate verdict + effective_status   viz/<name>.html  (+ <name>.meta.json freshness sidecar)
   │                           │
   └──────────────┬────────────┘
                  ▼
   lib/report.py render_dashboard()  →  reports/index.html (Jinja2) + static/ assets
   lib/single_study_report.py        →  self-contained per-study HTML report
```

**Viz freshness** (`lib/viz_freshness.py`): each rendered chart writes a
`.meta.json` sidecar recording the source `run_id` and render time. A chart is
*stale* if it predates the study's latest run or references an older run — this
stops users from deciding on outdated figures. `lib/refresh_viz.py` re-renders.

The same rendering also feeds the **read-only "publish" bundle** (`publish.py`):
it pre-computes `api/*.json` + per-study HTML shells so the *same* frontend JS
(`static/data-source.js` abstracts live-server vs. static-file) runs without a
backend. This is why recent fixes concern features degrading gracefully in the
snapshot (no Launch buttons, no GitHub mark, etc.).

> **⚠ Two caveats on "self-contained / read-only":** (1) charts are hand-rolled
> inline SVG and survive offline, **except** the multi-run *comparative* charts
> (`comparative_viz.py`), which reference Plotly from `cdn.plot.ly` rather than
> inlining it — so they silently fail to render offline or under a strict CSP,
> despite a docstring claiming otherwise. (2) The `VIVARIUM_WORKBENCH_READONLY=1`
> mode on a *live* server is **not** fully read-only: its allow-list still
> exposes run-launch, `run-delete`, `save-run-as-variant`, and workspace-switch.
> Only the statically-published bundle (no backend at all) is truly read-only.

---

## 6. Git as the audit trail

Most mutating endpoints run their file writes inside
`lib/work_state.py: active_branch_action` (and the per-mutation commit helpers),
which:
1. ensures work is on a workstream branch (creating one if needed),
2. performs the file writes (`action_fn`),
3. stages the authored paths and `git commit`s with a conventional message,
4. returns the new commit SHA to the UI.

So **the workspace's git history is the primary audit trail.** **⚠ Corrections
to two over-broad claims in earlier revisions:**
- **There *is* a separate event log** alongside git: `.pbg/events.jsonl` (typed
  RFC-0002 events, served by `GET /api/events/log`) plus the `/api/click`
  append log. Git is the *durable* trail; the event log is an additional stream.
- **Not literally every mutation commits.** Most do (via `active_branch_action`),
  but **catalog install/uninstall commit nothing** under the FastAPI seam — they
  mutate `workspace.yaml`/`pyproject.toml`/submodules and leave the tree dirty.
  And because `active_branch_action` **hardcodes its git-staging pathspec**
  (`studies/`, `investigations/`, …) rather than resolving it through
  `workspace_paths`, a workspace that relocates a dir via `layout:` would have
  its files *discovered but never staged/committed* — a silent audit-trail hole.

The GitHub Branches tab (`lib/github_auth.py`, device-flow OAuth, token in the
OS keychain) pushes the branch and opens a PR. Merging to the workspace's `main`
makes the work permanent. Per-developer workstream state (active branch, PR
number) is cached in `.pbg/state.json` (`lib/work_state.py`), which is
gitignored. Commits are authored under a fixed synthetic identity
(`pbg-template@local`), not the authenticated GitHub user.

Mutating requests are guarded by a **CSRF/origin middleware** (`api/app.py` →
`lib/csrf.is_request_allowed`): requests with no `Origin` (curl, local CLI) are
allowed; a present `Origin` must match `Host` (bypass with
`VIVARIUM_WORKBENCH_DISABLE_CSRF=1`). **⚠ Correction:** there is no `_csrf_ok()`
function — that name survives only in docstrings from the retired stdlib server.
Note this guard has **no `Host` allowlist and no token**, so it is bypassable by
DNS rebinding and by any `Origin`-less client; combined with the API having **no
authentication** (while `--host 0.0.0.0` is a documented flag), the security
model assumes a trusted-localhost deployment. See the deep-dive §10.

---

## 7. Companion repos — who owns which transformation

| Transformation / artifact | Owner repo | How the dashboard uses it |
|---|---|---|
| Composite instantiation & **simulation execution** | **process-bigraph** | `Composite(state, core).run(n)` inside the detached run subprocess. The dashboard never simulates anything itself. |
| Bigraph **type system** + JSON (de)serialization | **bigraph-schema** | `BigraphJSONEncoder` / `bigraph_json_hook` when persisting/transporting state. |
| Interactive **state-tree (bigraph) explorer** | **bigraph-loom** | Embedded viewer; the dashboard feeds it composite-state JSON. Assets copied into the publish bundle. |
| Workspace **scaffold** + `.pbg/schemas/` validators | **pbg-template** | Read-only here. Schemas are loaded at save time (`lib/workspace_yaml.py` → `Draft7Validator`); invalid YAML is rejected with HTTP 400 *before* any commit. pbg-template owns the schema versions. |
| **AI-assisted authoring** + many runtime computations | **pbg-superpowers** | A *runtime library*, not just a plugin. Provides `workspace_catalog` (multi-dashboard discovery via `~/.pbg/servers/`), `composite_generator`, visualization discovery, and the verdict/status/rigor rollups the dashboard computes on read. Its `/pbg-*` Claude Code skills write the **same** workspace files the dashboard does. |

> **⚠ Coupling note:** this is the deepest and leakiest dependency in the repo —
> **~170 references across 57 of ~151 `lib/` modules**, imported symbol-by-symbol
> with no facade, and (in ≥6 modules) reaching into the private
> `composite_generator._REGISTRY`. Calls are defensively guarded (they degrade if
> the package is absent), so the dashboard *runs* without pbg-superpowers — but
> the entire gate/verdict/derived-status layer then silently vanishes, and
> `pbg-superpowers>=0.14.0` is in fact a **hard** dependency. A single upstream
> version bump can affect dozens of unrelated modules at once. (`composite_lookup.py`
> even carries a docstring claiming it is "self-contained: no dependency on
> pbg-superpowers" while importing that private registry.) By contrast,
> `investigation_contracts` and the bigraph-loom asset seam are narrow and clean.

### Dual authoring (dashboard ⇄ /pbg-* skills)

Both the dashboard's HTTP API and pbg-superpowers' `/pbg-*` Claude Code skills
mutate the **same** `workspace.yaml` / `study.yaml` / `investigation.yaml`
files, synchronized through git. They also hand off via `.pbg/` directories:
e.g. the dashboard's "Create visualization" writes a `.pbg/viz-requests/<name>.md`
and returns a `/pbg-viz <name>` command; the skill generates code into
`.pbg/viz-responses/<name>.py`, which the dashboard then stages and commits.
The workspace files + git are the integration boundary; there is no shared
process or database between the two tools.

---

## 8. One-paragraph summary

You point `vivarium-workbench serve` at a process-bigraph workspace. The
workspace's YAML files (`workspace.yaml`, `studies/*/study.yaml`,
`investigations/*/investigation.yaml`) are the source of truth for *design*;
`runs.db` files are the source of truth for *results*; the workspace's git
history is the *audit trail* (with an auxiliary `.pbg/events.jsonl` event log).
The dashboard authors those YAML files through a UI (committing most changes),
orchestrates simulations — either as detached `run-composite` processes
(Explorer scratch runs) or as synchronous in-request subprocesses (study runs;
see the §4 correction) that delegate to process-bigraph and write `runs.db` —
then renders specs+results into verdicts and charts, for both the live server
and a static read-only bundle. It is schema-validated by pbg-template,
AI-co-authored by pbg-superpowers, and uses bigraph-loom to visualize state
trees. The dashboard itself stores nothing outside the workspace. For the
code-verified detail behind every claim here, see
[ARCHITECTURE-DEEP-DIVE.md](ARCHITECTURE-DEEP-DIVE.md).
