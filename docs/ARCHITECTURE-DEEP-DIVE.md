# vivarium-workbench — Architecture Deep-Dive (code-verified)

> **Status:** Written 2026-07-07 from a direct, code-first read of the
> `demo-v2ecoli` branch by eight parallel subsystem audits. Every claim here was
> checked against source (file:line citations throughout). Where this document
> disagrees with `docs/ARCHITECTURE.md`, `README.md`, `CLAUDE.md`, or an
> in-code docstring, **this document reflects what the code does** and the older
> claim is flagged as stale in §11.
>
> This is a *descriptive* audit, not a redesign. It documents architecture,
> functionality, design, and UX as they actually exist, and ranks the structural
> risks (§12) for a codebase that was built fast by a strong engineer newer to
> production software. It does not change any code.

---

## 1. Executive summary

`vivarium-workbench` is a **single-process, single-workspace FastAPI web application** that turns a [process-bigraph](https://github.com/vivarium-collective/process-bigraph) *workspace* (a directory of YAML specs + simulation outputs) into an interactive, git-backed research notebook. It authors YAML through a UI, orchestrates simulation runs, renders results into verdicts and charts, and commits every change to the workspace's git history. It ships **no science of its own** — it delegates simulation to process-bigraph and much of its "is this study OK?" logic to the `pbg-superpowers` runtime library.

**The skeleton is soundly designed.** The strangler-fig migration off the old 9.6k-line stdlib server genuinely landed: routes are thin, business logic lives in ~151 small `lib/` modules, and **95 of those modules take an explicit `ws_root` parameter** rather than reading globals — which is what makes them independently testable and is the single best structural property in the repo (~3,150 unit tests exercise them). There is a real read/write module split (`*_views.py` / `*_mutations.py`), a deliberately AI-free server process *enforced by a test* (`tests/test_no_ai_deps.py`), and a couple of genuinely clean boundaries (`investigation_contracts`, the bigraph-loom asset seam).

**The weaknesses are the ones a fast-moving, production-newer engineer would predictably under-weight**, and they cluster into five themes:

1. **God files that never got split** — `static/walkthrough.js` (15,776 lines), `api/app.py` (6,117 lines / 206 routes), `lib/models.py` (2,828 lines), `lib/single_study_report.py` (2,298 lines), `templates/study-detail.html` (2,118 lines / 570 `{% %}` blocks), `lib/investigations.py` (1,811 lines).
2. **A trust model that assumes "localhost forever" while shipping a `--host 0.0.0.0` flag** — no authentication on an API that spawns processes, writes files, and pushes to git; a CSRF guard that is bypassable; and a "read-only" mode that still exposes run-launch, delete, and workspace-switch.
3. **Two divergent run engines, only one documented**, and no restart-reconciliation for the durable one.
4. **Coupling to `pbg-superpowers` sprawled across 57 modules** (symbol-by-symbol, reaching into private `_REGISTRY` internals) instead of behind one adapter.
5. **The frontend is essentially unverified** (the one JS test is broken and points at a dead path), and the "typed client contract" (generated TypeScript) is committed but consumed by nothing.

None of these are fatal for the current use case (a researcher running the tool locally against their own workspace). All of them are load-bearing the moment the tool is shared, multi-tenanted, or exposed on a network.

---

## 2. What it is, and the repo/workspace split

Three layers (accurate as documented):

```
ENGINE      process-bigraph + bigraph-schema + pbg-emitters   (runs the science)
   │
TOOLING     vivarium-workbench  (THIS REPO)                    (UI + orchestration + git)
+ AI        pbg-superpowers (/pbg-* Claude skills)             (author the same files)
   │
DATA        a workspace directory (scaffolded from pbg-template) — the only source of truth
```

**Crucial split:** this repo is the *server/tooling*; the *data* lives in a separate `--workspace` directory. The dependency direction is that the **workspace depends on vivarium-workbench** (it's a pip dependency installed into the workspace's venv, run from there so it can import the workspace's own `build_core()` package). See `docs/USAGE.md` — that document is accurate.

Two git histories, kept distinct: this repo's history = dashboard software changes (PRs to `main`); the workspace's history = the scientific audit trail the dashboard writes on the user's behalf.

---

## 3. The three "planes" (running modes)

The README's framing — *one codebase, three configurations toggled by env vars* — is the correct top-level mental model, but the isolation between planes is **much weaker than the design spec claims** (`docs/superpowers/specs/2026-06-27-...three-plane-architecture-design.md`).

| Plane | Toggle | What it is | Isolation reality |
|---|---|---|---|
| **Local authoring** (default) | none | Serve a workspace, run on the local engine, commit every action | The real product |
| **Remote compute** | `SMS_API_BASE` | Build pinned `repo@commit` simulators and run batches on a remote **sms-api** backend (Ray → AWS Batch → zarr/parquet on S3) | **Not structurally isolated.** `SMS_API_BASE` defaults to `http://localhost:8080`; the `/api/remote-run-*` and `/api/source/build-remote|switch-build` routes are registered in *every* configuration and only fail at call time if no tunnel is up (`workspace_deps_views.py:24`, `remote_simulations.py:34`). |
| **Published read-only snapshot** | `vivarium-workbench-publish` / `VIVARIUM_WORKBENCH_READONLY=1` | A self-contained static bundle (`api/*.json` + HTML shells) servable with no backend | The **published bundle** is genuinely isolated (no server). But `READONLY=1` on a *live* server is **not read-only** — see below. |

**`_apply_readonly_filter` (app.py:400-447) is real route deletion, not middleware:** it rewrites `app.router.routes`, keeping GET/HEAD/OPTIONS plus a hardcoded whitelist `_READONLY_ALLOWED_MUTATIONS` (app.py:410-427). That whitelist **keeps local run launches (`/api/study-run-baseline`, `/api/composite-test-run`, `/api/investigation-run`), `/api/run-delete`, `/api/save-run-as-variant`, `/api/source/switch`, and the whole remote-run surface.** So "readonly" is really the config for a *private remote-client* deployment, double-booked as the public plane. The spec's "every mutating/compute route is stripped" is **not** what the flag does, and there are **zero tests** of the readonly route set.

The published-snapshot round-trip ("Sync to local" reproduces a published `repo@commit` locally: clone-at-commit + lockfile-pinned `uv sync` + optional cache rebuild) is implemented in `sync_workspace.py`/`sync_materialize.py`, but its **cache-rebuild leg is consumed-but-never-produced**: `sync_from_manifest` reads `manifest["post_sync"]`, yet `provenance_manifest.build_manifest` never emits that key (provenance_manifest.py:95-103).

---

## 4. The domain model (composites → studies → investigations)

### 4.1 The hierarchy

| Concept | Lives at | What it is |
|---|---|---|
| **Composite** | `<pkg>/composites/<name>.composite.{yaml,json}` or a `@composite_generator` fn | A runnable process-bigraph model: a nested state tree + named `parameters`. Referenced by a dotted id like `v2ecoli.composites.baseline.baseline`. Structure owned by process-bigraph, **not** this repo. |
| **Study** | `studies/<slug>/study.yaml` (legacy `spec.yaml`) | One experiment/hypothesis test: picks composite(s), declares what to run, what to measure, and pass/fail criteria. Owns its run results (`runs.db`). |
| **Investigation** | `investigations/<slug>/investigation.yaml` | A DAG of studies grouped into narrative "parts", with a baseline + comparison variants overlaid in charts. |

Studies resolve **nested-first**: `investigations/<inv>/studies/<slug>/` wins over flat `studies/<slug>/` (`workspace_paths.iter_study_dirs:143-162`). **Caveat:** a *second* resolver (`study_spec.study_dir:140-159`) adds fallbacks the canonical one lacks (a `spec.yaml`-only dir, the legacy `investigations/<name>/` path), so the two can disagree for the same slug.

### 4.2 Schema versions and the dual-v4 hazard

Migrations (`spec_migration.py`) are **auto-applied in memory on load** (`investigations.py:757-760`) and, except for one on-disk `composites→variants` rewrite, are never written back. v2 = variants-as-composites/legacy `composites:`; v3 = `baseline:` + `variants:` with overrides; v4 = adds `tests`/`references`/narrative blocks.

**The single biggest data-model hazard:** `schema_version: 4` is **ambiguous — two incompatible schemas share the number**, disambiguated only by the *presence of a top-level `conditions:` block* (`investigations.py:782`):
- With `conditions:` → the "redesign v4" (`question`/`conditions`/`tests` as a **list**), which is then **projected back onto legacy v3 field names in-memory** by a 170-line synthesizer (`_project_v4_redesign_to_legacy_view:337-507`) so the v3 renderer works unchanged.
- Without `conditions:` → "legacy v4 = v3 + extras" (`tests` is a **dict**).

Adding a `conditions:` key to a legacy-v4 study silently flips the entire validation + rendering path. `migrate_v3_to_v4` carries defensive special-casing (spec_migration.py:196-208) that is a direct symptom of this.

### 4.3 Behavior tests (`expected_behavior.py`)

A clean, stdlib-only DSL. Each entry extracts a measure from run history and evaluates an expectation. Actual operators (docstring undercounts these — see §11):
- **Measure kinds (6):** `bulk_count`, `listener_sum`, `listener_path`, `event_count`, `concentration`, `xy_correlation`.
- **Reduce modes (6):** `series`, `median`, `mean`, `first_and_last`, `pre_post_event_ratio`, `top_quartile_vs_bottom_quartile`.
- **Expect ops (8):** `in_range`, `rolling_cv_below`, `ratio_at_most`, `ratio_at_least`, `monotonic_decreasing`, `pearson_below`, `pearson_above`, `pre_post_event_ratio`.

`evaluate()` returns `(passed, message)` and the "never raises" claim is **narrowly true** — it only catches two custom exceptions; a malformed entry that raises `KeyError`/`TypeError` on `entry["measure"]`/`entry["expect"]` still propagates.

### 4.4 Gate / verdict / effective_status — and the DAG that isn't enforced

- **`effective_status(spec)`** (`investigations.py:1376`) is a multi-axis precedence rollup; the *selection* is local, but the axes number **6, not 5** (adds `expert_review_status`), and the **gate-verdict content it selects is computed in `pbg_superpowers`** (`study_verdict`, `study_status`, `pipeline_gate`) via ~15 best-effort lazy imports in `study_spec.load_study_detail_spec:477-704`. If pbg-superpowers is absent, the entire gate/verdict/derived-status layer *silently vanishes* (fields just go missing).
- **The study-to-study DAG is display-only.** `build_investigations` computes `blocked`/`blocked_by` for the UI (`investigations_index.py:324-338`), but **nothing consults it before launching a run.** A downstream study can run before its predecessors pass. This directly contradicts `docs/ARCHITECTURE.md:127-129` ("Predecessor studies must pass their gate before a downstream study proceeds"). The only run-time gating is a *different* concept — a study's own `conditions.model_settings` entries marked `gate: required-before-run` with an unset value (`run_jobs.enumerate_unblocked:138`).

---

## 5. The run subsystem — two engines, only one documented

This is the most important divergence from the docs. `ARCHITECTURE.md` §4 describes the detached-subprocess model as *the* run model; in reality there are **two entirely separate run engines**, and the durable, scientifically-important path is the weaker one.

| | **Engine A — "detached"** | **Engine B — "blocking subprocess"** |
|---|---|---|
| Entry | `POST /api/composite-test-run` (Composite Explorer scratch runs) | `POST /api/study-run-baseline` / `-variant` (the core workflow) |
| Code | `composite_test_run_views.py` → `run_registry.spawn_detached` → `run_runner.execute` | `study_runs.py` → `composite_subprocess.run_composite_subprocess` |
| Mechanism | writes a pure-config `request.json`, spawns a detached `cli run-composite` process | builds a ~240-line `python -c` script string, `subprocess.run(..., timeout=1800)` **synchronously inside the HTTP request** |
| Survives server restart? | **Yes** (`start_new_session=True`, PID tracked, `reconcile_stale_runs` on boot) | **No** — child of the request handler; interrupted runs are left `status='running'` forever (study `runs.db` files are never reconciled — `startup.py:58` reconciles only `composite-runs.db`) |
| PID / heartbeat / self-terminate at `MAX_RUNTIME_SEC=1800` | Yes | None (only the hard `subprocess.run` timeout) |
| Concurrency cap | Yes (`CONCURRENCY_CAP=4`) | **None** — N blocking 30-minute subprocesses can pile up |
| DB | `.pbg/composite-runs.db` | `studies/<slug>/runs.db` |

Everything the docs advertise about "detached, decoupled from the request, PID-tracked, self-terminating" is true **only for Engine A** (the scratch/Explorer path). The "Argument list too long / pure executor" design win (`run_runner.execute` reads everything from `request.json`) applies only to Engine A. Engine B's giant interpolated `python -c` string (composite_subprocess.py:202-444, five branch gates, multiple silent sqlite fall-backs) is dumped to a `.subprocess.py` sidecar precisely because it is otherwise un-inspectable.

### 5.1 Storage and emitters

- **`runs_meta`** (the dashboard's per-run bookkeeping table) is owned by this repo (`composite_runs.py:18`). The per-step **`history`/`simulations`** trajectory tables are created by process-bigraph's emitter, joined on `history.simulation_id == runs_meta.run_id`. There are **three separate hand-maintained `runs_meta` DDLs** in the repo, and a latent `emitter_path` column drift (present in the vendored `run_registry.RUNS_META_DDL` + `backfill` INSERT but not in the dashboard's own migration — a landmine if backfill is ever wired against a dashboard-created study DB).
- **`DEFAULT_EMITTER = "xarray"`** now (`emitters.py:32`) — the SQLite-centric narrative in `ARCHITECTURE.md`/`CLAUDE.md` is stale. Backend selection is a fragile stack of runtime gates; the xarray default routinely under-fills its size-3 buffer on short runs and silently falls back to SQLite via a content-probe, so the "default" emitter frequently produces a *different* backend than declared.
- **`simulations_index.py`** is the unifier: it walks `.pbg/composite-runs.db`, `.pbg/default-baseline/runs.db`, and every `studies/*/runs.db` (both layouts), merging `runs_meta` rows + emitter `simulations` rows + `study.yaml`-recorded numpy runs + parquet hives, keyed on `run_id`. Under DB contention some readers swallow `OperationalError` and return `[]`, so a run can transiently vanish from the index.
- **Coordinated investigation runs** stamp a shared `generation_id` (read from a workspace pointer file inside `run_composite_subprocess`, not passed in the POST) so baseline + variants form one "generation". Safe only because `prepare_study` runs studies sequentially; concurrent preps in one workspace would clobber the current-generation pointer.

### 5.2 Remote runs (sms-api) — three coexisting implementations

1. **Legacy threaded pipeline** (`POST /api/remote-run-start` → `RemoteRunManager`, `remote_run_jobs.py`): push→build→run→poll→download→land on a daemon thread; in-process state, lost on restart (weaker than even Engine A).
2. **Thin-client two-phase** (`/api/remote-run-build|submit|land|poll`): each route is one stateless sms-api call; durability lives in sms-api's Postgres; the JS panel drives the sequence. This is the architecturally correct answer, and comments say it will delete #1 ("R5") — but both still exist.
3. **CLI `run-remote`** (`lib/remote_run.py`): a different sms-api surface (`/compose/*`), exports the composite to `.pbg` with `extra_pip_deps=[git+origin@sha]`, blocks polling.

The good design move: `remote_run_landing.land_remote_run` copies the remote native store (zarr/parquet) *unmodified* to where local chart readers expect it and records a `runs_meta` row, so **a remote run becomes indistinguishable from a local one** to the rest of the app. The sms-api *client* (`sms_api_client.py`) has **no auth** (the security boundary is the SSM tunnel) and is coupled to sms-api's quirks by folklore (response-shape sniffing, `.git`-suffix 500 workarounds, a "required param that doesn't filter"), with a dead `401` handler that can never fire. The deployment name `"smsvpctest"` is hardcoded into durable run provenance.

---

## 6. Rendering & reporting

There are **three loosely-coupled render pipelines**, not one:

1. **Workspace dashboard** — `report.render_dashboard()` → Jinja2 `templates/index.html.j2`, rendered **once at serve time** (autoescaped).
2. **Study-detail page** — `study_page.render_study_detail_html()` → Jinja2 `templates/study-detail.html`, rendered **per-request** and **reused verbatim by `publish.py`** (good: live and static share the renderer). Note the file is a Jinja template *despite the `.html` extension* and holds **570 `{% %}` control blocks** — the heaviest logic-in-template in the repo.
3. **Single-study report** — `single_study_report.render_single_study_report()` → a 2,298-line module that **builds the whole HTML document as Python f-strings** with inline CSS, no template. Escaping *is* applied consistently via `_h()` (XSS risk low), but it is the largest single renderer and hardest to modify safely.

**Charts are almost entirely server-side, hand-rolled inline SVG** (`study_charts._render_svg`: polylines, target bands, threshold lines, pure stdlib). **Plotly appears in exactly one place** — `comparative_viz.py` (multi-run baseline/variant overlays), and it loads **from `cdn.plot.ly`, not inlined** — so its docstring's "works offline" claim is false and comparative charts silently fail to render under a strict CSP or offline (relevant to the published bundle). `study_charts.py` also still carries **hardcoded v2ecoli/DnaA chart specs** (`CHART_SPECS`, magic monomer index `3861`) alongside the newer generic v4 `tests[].measure` path.

**Viz freshness** works as documented: each chart writes a `<chart>.svg.meta.json` sidecar recording `source_run_id`/`generation_id`/`rendered_at`/`content_hash`; a chart is `fresh|stale|unrendered` relative to the study's latest run (`viz_freshness.py`, vendored from pbg-superpowers with a drift test). One trap: a *second*, different `<chart>.meta.json` convention (no `.svg`) carries display metadata — two sidecar files for the same chart.

The **repo-contributed analysis viewers** system (recent "PTools relocation") is a genuinely clean plugin seam: a workspace/`pbg-*` package exposes a `workbench_viewers.get_viewers(ws_root)` module; the workbench discovers `launcher`/`embed` viewers, namespaces them, and never 500s on a broken contributor. No ptools-specific strings remain in the workbench.

---

## 7. Frontend & UX

**No bundler, no framework, no modules** — ~15 hand-written vanilla-JS IIFEs attaching to `window`, loaded by plain `<script>` tags, across **two separate HTML documents** (the main SPA shell `index.html.j2`, and the per-study page `study-detail.html` + `study-detail.js`, reached by full navigation or `<iframe>`).

- **`client.js` (60 lines) is vestigial** (a legacy guidance-poller/SSE/click-relay). **`walkthrough.js` (15,776 lines / 834 KB) is the actual SPA driver** — it owns navigation, all nine top-level pages, modals, forms, the composite explorer, the DAG renderer, the report/notebook generators, and even builds *entire secondary apps as concatenated JS strings* injected into popout windows/iframes. ~411 functions in one IIFE; state is held in **60+ ad-hoc `window._*` globals** with no store and manual, buggy cache-invalidation (there's a documented retry hack for a memoization recovery bug).

### 7.1 Information architecture (the actual UI)

Left-rail hash-routed navigation (`_switchPage`), grouped **Workspace** then **Studies**. Nine screens:

| Rail label | `data-page` | Purpose |
|---|---|---|
| Sources | `workspace-inputs` | datasets / references / expert docs; drag-drop upload |
| Registry | `registry` | Modules + discovered `build_core()` registry snapshot |
| Composites | `simulation-setup` | discoverable `.composite.yaml`; "Explore" → Composite Explorer |
| Investigations | `investigations` | iset cards → detail with **DAG canvas**, needs-attention, report gen — the primary workflow |
| Simulations DB | `simulations` | workspace-wide run list + delete; auto-refresh poll |
| Analyses | `visualizations` | registered visualization/analysis classes + repo-contributed viewers |
| Studies | `studies` | legacy flat study list (redirected to Investigations in snapshot mode) |
| Composite Explore | `composite-explore` | **bigraph-loom** state-tree (iframe) + Configure&Run widget |
| Source / GitHub | `github` | device-flow login, branches, workspace/repo navigator |

The **study-detail sub-app** uses a two-level pillar→tab nav (Overview · Hypotheses · Model · Simulations · Results · Exports). Primary flows: author an investigation → open a study (iframe or popout) → generate report/notebook; run a sim via the Configure&Run widget; explore the state tree (bigraph-loom in an iframe over a `postMessage` protocol: `explore:ready|inspect|emit-changed|run-complete`); explore results in the Data Explorer (Plotly/d3-voronoi/escher); GitHub login + branch/PR management.

### 7.2 Client/server contract and its seams

- **`data-source.js` (323 lines)** is the intended live-vs-snapshot seam (branch on `__DASH_CONFIG__.mode`, exponential-backoff retry for GitHub-Pages rate limits). **But it's leaky and only partially adopted:** in `walkthrough.js` there are **145 raw `fetch()` vs 35 `DataSource.` calls**, and three other modules (`explorer.js`, `configure-run.js`, `branch-source.js`) each reinvent snapshot handling independently. Snapshot mode is *also* signalled a second way via a `body.snapshot` CSS class, so "is this read-only?" is split across two mechanisms.
- **The generated TypeScript types are unconsumed.** `static/types/domain.generated.d.ts` is emitted from `models.py` by `generate_ts.py` and guarded by a staleness test, but **no JS references it** (no tsconfig/jsconfig/`@ts-check`). The "derived client contract" delivers zero editor/type safety — it's documentation.
- CSRF needs no client cooperation (server relies on Origin==Host). Charting libs (Plotly, d3, escher) load from external CDNs, undercutting the self-contained-bundle promise.
- Error UX is primitive: ~100 `alert()` + ~25 `confirm()/prompt()`, no toast system, inconsistent surfacing; accessibility is minimal (a handful of aria attributes, 66 inline `onclick`).

---

## 8. Workspace resolution, git audit trail, auth, infra

- **Path resolution** (`workspace_paths.py`): `LAYOUT_DEFAULTS` maps 12 logical dir names, overridable per-workspace via a `layout:` map. **But the "never hardcode paths" convention is not enforced for writers** — ~44 direct `ws_root / "studies"`-style joins remain across ~17 modules, *including the git staging pathspec* (`work_state.py:161-165` hardcodes `studies/`, `investigations/`, …) and the migration command. A workspace that relocates `studies:` via `layout:` would have its studies **discovered but not committed** — a silent audit-trail hole.
- **Git as audit trail** (`work_state.active_branch_action`): ensure-branch → refuse if dirty → run the write → *scoped* `git add` over a fixed allow-list → commit as a **fixed synthetic identity** `pbg-template@local` → return the SHA. Solid for the happy path, but **commit semantics are inconsistent across three code paths**, and **catalog install/uninstall commit *nothing*** under the FastAPI seam (they mutate `workspace.yaml`/`pyproject.toml`/submodules and leave the tree dirty). So `CLAUDE.md`/README's "every action commits… full audit trail" is **not literally true**.
- **GitHub auth** (`github_auth.py`): device-flow OAuth (no client secret) + pasted-token fallback; token masked in logs, injected into subprocess env, never written to disk. The **keyring service name is intentionally still `vivarium-dashboard`** (and `~/.config/vivarium-dashboard/`) for back-compat — verified, and consistent with the documented exception.
- **Registry & catalog:** composites discovered from 3 sources (workspace package, installed `pbg-*` dists, `@composite_generator` fns); `build_registry` runs `build_core()` in a 15s subprocess and classifies classes. Catalog install supports PyPI (`uv pip install`) or git-submodule modes, gated by a system-deps check.
- **CLI:** `serve`, `run-composite` (internal detached worker), `prepare-investigation`, `run-remote`, `sync`, `migrate-investigations`, and user-facing `run`/`rerun`/`runs`/`status`/`logs`. `publish.py` is a separate console script.
- **Publish bundle** (`publish.py`): emits `api/*.json` + per-study HTML shells + assets + an explorer snapshot; writes JSON with **`allow_nan=False`** (browser `JSON.parse` rejects `NaN`/`Infinity`) — load-bearing for graceful degradation. Reuses the live `render_study_detail_html`, so minimal live/static duplication.
- **The rename (vivarium-dashboard → vivarium-workbench)** is functionally complete (a meta-path finder shim package, dual-read env vars via `env_compat.py`, aliased console scripts) but **cosmetically leaky**: stale `vdash`/`vivarium-dashboard` strings in user-facing CLI hints, several docstrings, and an external User-Agent sent to Crossref.

---

## 9. Boundaries & companion coupling

| Package | Import sites | Nature |
|---|---|---|
| `pbg_superpowers` | **170 refs across 57 lib modules** | Deep runtime library — verdicts, status, rigor, catalog, composite generators |
| `process_bigraph` | 38 | Engine (`Composite`, emitters), lazy-imported |
| `bigraph_schema` | 19 | Serialization codec |
| `investigation_contracts` | 15 | **Clean, narrow, versioned schema seam** (event-log validators + `*CreateBody` models) |
| `pbg_emitters` | 16 | Emitter library |
| `bigraph_loom` | **3 (asset_dir only)** | Very thin — iframe asset serving; clean seam |

**`pbg_superpowers` is the dominant and leakiest coupling.** 38% of `lib/` imports it, symbol-by-symbol with no facade, and **at least 6 modules reach into its private `_REGISTRY`** (`composite_lookup.py:271`, etc.). Tellingly, `composite_lookup.py:3-6` *claims* it is "self-contained: no dependency on pbg-superpowers" while importing that private registry — the boundary is already misunderstood by its own authors. The imports are defensively guarded (degrade if absent), yet `pbg-superpowers>=0.14.0` is a **hard** dependency, so the "optional" framing is aspirational.

**Layering is real but convention-only.** Direction is clean (exactly one `lib→api` import, the composition root; zero `lib→server`). The `*_views`/`*_mutations` split holds. DI dominates (95 modules take `ws_root`; only ~13 read the global root). But nothing mechanical enforces the tiers — only `test_no_ai_deps` guards *one* boundary (no LLM SDKs in the server process — a genuinely good architecture-as-code guardrail).

**The `.pbg/` file-handoff is the primary integration boundary with the AI side** and mirrors the run subsystem's design: `POST /api/visualization-create` drops `.pbg/viz-requests/<name>.md`; a `/pbg-*` skill writes `.pbg/viz-responses/<name>.py`; the dashboard polls for it and stages it. Deliberate, decoupled, and LLM-free on the dashboard side — but a **schema-less filesystem convention** (unlike the validated `investigation_contracts` event log), so drift is caught only at runtime in the browser.

---

## 10. Cross-cutting: state, concurrency, security, testing

- **Process-global state** (`_root._WS_ROOT`/`_WS_PATHS`, `registry` cache, `github_auth` session cache, `run_jobs` thread registry) hard-wires a **single-workspace, single-process** assumption. `serve_fastapi` even `os.chdir`es into the workspace. `/api/source/switch` does only *half* a switch — it updates `set_workspace_root` + caches but leaves CWD and `sys.path` (and the old `pbg_*` modules in `sys.modules`) pointing at the previous workspace (api agent F3).
- **Concurrency:** all 206 handlers are sync `def` (run in the anyio threadpool). The unbounded `/api/events/log` SSE stream is a `while True: time.sleep(1)` **sync** generator, so **each connected client permanently parks one threadpool worker** — a handful plus slow subprocess-backed GETs can stall the whole API.
- **Security (all low-risk *if* strictly localhost, escalating sharply otherwise):**
  - No authentication on an API that spawns/kills processes, writes workspace files, and pushes to git — while `--host 0.0.0.0` is a documented flag.
  - The CSRF guard allows any request with no `Origin` and otherwise only checks `Origin.netloc == Host` (no `Host` allowlist → DNS-rebinding-bypassable).
  - "readonly" mode still exposes run-launch/delete/switch (§3).
  - `study_detail_route` returns `traceback.format_exc()` to the client on 500 (app.py:1357) — contradicts the catch-all handler's own "details go to logs, not the client" policy.
  - The catch-all static route serves the whole workspace tree (`.git/`, `.pbg/…`, `events.jsonl`) with only a `..` check.
- **Error handling:** a canonical `{"error": ...}` envelope exists and the shape is consistent, but the prescribed `raise APIError(...)` mechanism has **zero adopters** — instead there are 155 hand-rolled `{"error": ...}` literals plus the lib `(body, status)` tuple convention. Escaping across all three renderers is disciplined (low XSS). `models.py` is **85% `extra="allow"`** (156/183 classes) with **zero `extra="forbid"`** and 31 field-less passthroughs — the "typed contract" validates far less than it appears to; only 87/206 routes declare a `response_model`.
- **Testing:** ~3,150 `def test_` across 274 files, ~93% unit / 7% integration. The `dashboard_client` fixture spawns a real FastAPI subprocess and waits on a readiness file. **Well-covered:** lib domain logic, read/write API surface, the AI-free gate. **Gaps:** the entire frontend (the one JS test is broken and points at the dead `vivarium_dashboard/static/aig-graph.js` path), live↔snapshot data-source parity, the loom `postMessage` contract, the readonly route set, and end-to-end run→render beyond the 16 integration files.

---

## 11. Where the code diverges from the docs

| Claim (source) | Reality |
|---|---|
| "detached subprocesses decoupled from the HTTP request… PID… self-terminates" as *the* run model (ARCHITECTURE.md §4) | True only for **Engine A** (Explorer scratch). Durable **study runs use Engine B** — synchronous, HTTP-blocking, uncapped, never reconciled (§5) |
| "Predecessor studies must pass their gate before a downstream study proceeds" (ARCHITECTURE.md:127) | **False** — the DAG is display-only; no run-time enforcement (§4.4) |
| SQLite-centric run/emitter narrative (ARCHITECTURE.md, CLAUDE.md "default 'sqlite'") | **`DEFAULT_EMITTER="xarray"`** has shipped (§5.1) |
| "every action commits to a git branch… full audit trail" (README, CLAUDE.md) | **False for catalog ops** (deferred, tree left dirty) and for writers under a custom `layout:` (staging pathspec hardcodes `studies/`) (§8) |
| "there is no separate event log" (ARCHITECTURE.md §6) | There **is**: `.pbg/events.jsonl` + `GET /api/events/log`, plus the `/api/click` append log |
| "All ~178 /api/* routes"; "every mutating endpoint calls `_csrf_ok()`" (CLAUDE.md) | **206 routes**; `_csrf_ok()` **does not exist** — it's one middleware calling `lib.csrf.is_request_allowed` (§10) |
| `READONLY` / published plane strips "every mutating/compute route" (3-plane spec) | Whitelist keeps run-launch, delete, and workspace-switch; live `READONLY=1` is **not read-only** (§3) |
| comparative_viz "carries the Plotly CDN inline so it works offline" (docstring) | **Not inlined** — references remote `cdn.plot.ly`; breaks offline/CSP (§6) |
| `composite_lookup` "self-contained: no dependency on pbg-superpowers" (docstring) | Imports `pbg_superpowers.composite_generator._REGISTRY` (§9) |
| `effective_status` = 5-axis rollup (ARCHITECTURE.md:135) | **6 axes** (adds `expert_review_status`) |
| `evaluate()` "never raises" (ARCHITECTURE.md:124) | Only catches 2 custom exceptions; `KeyError`/`TypeError` on malformed entries propagate |
| `expected_behavior` docstring: 7 expect ops / 4 reduce modes | **8 ops / 6 reduce modes** in code |
| `investigations.py` = "spec loading + expansion" (docstring) | Also holds results aggregation, viz building, and a full run orchestrator (§4, a god module) |
| study.yaml = "8-section" (ARCHITECTURE.md) vs "14-section narrative spine" (scaffold) | Inconsistent section count between docs and scaffold |
| Generated TS = "derived client contract" the browser shares (ARCHITECTURE.md:81) | Emitted and staleness-tested, but **consumed by no JS** — zero type safety (§7) |
| `server.py` = ~40-line shim, six symbols | **Accurate** (43 lines, six symbols) |
| USAGE.md (deployment/dependency direction) | **Accurate** |

---

## 12. Ranked architectural risks

Consolidated across all eight subsystem audits, most structurally significant first.

1. **God files with no decomposition or type safety.** `walkthrough.js` (15,776 lines, untested), `api/app.py` (6,117 / 206 routes, one closure), `models.py` (2,828, 85% permissive), `single_study_report.py` (2,298, HTML-in-Python), `study-detail.html` (2,118, 570 `{% %}`), `investigations.py` (1,811, ≥5 concerns). These are the dominant maintainability liability; the smaller, single-purpose lib modules prove the team *can* decompose — these just never were.

2. **Trust model assumes localhost forever while shipping `--host 0.0.0.0`.** No auth on a process-spawning, file-writing, git-pushing API; DNS-rebinding-bypassable CSRF; "readonly" that isn't; traceback disclosure; whole-workspace static serving. Fine locally, dangerous the moment it's exposed.

3. **`pbg_superpowers` sprawl — 57 modules, symbol-scattered, into private `_REGISTRY`.** A minor upstream bump can break the dashboard in dozens of unrelated places at once. The cheapest high-value fix in the repo: route it all through one `lib/superpowers_api.py` adapter and never touch `_`-prefixed symbols.

4. **Two run engines; the durable one is the weaker one and is never reconciled.** Study runs block the HTTP request for up to 30 minutes, aren't capped, and are left `status='running'` forever on restart. Unifying study runs onto the Engine-A request-file/detached model (and reconciling their `runs.db` on boot) would close this.

5. **Process-global singletons preclude multi-workspace/multi-worker and a correct workspace-switch.** The lib layer is *already* 95-modules-ready for per-request `ws_root`; the ceiling is `_root`, the caches, `run_jobs`, and `os.chdir`. The half-implemented `/api/source/switch` (stale CWD/`sys.path`) is the concrete bug this produces today.

6. **The frontend is unverified.** 21k lines of JS, one broken test pointing at a dead path, a leaky/duplicated live-vs-snapshot seam, and generated types nobody consumes. Any UI change is validated only by manual clicking.

7. **Dual-v4 schema under one version number** (disambiguated by a side-channel key) and **two divergent study-dir resolvers** — data-model ambiguities that will bite as specs evolve.

8. **Demo-specific knowledge leaked into "generic" tooling** — `explorer_data.py` (`agents/0/` unwrapping, RNA/protein/metabolite classes, `fg`/`mmol·s⁻¹` units) and `study_charts.CHART_SPECS` (DnaA monomer index `3861`). The genericity is advertised but not real, and the smell will recur with each new demo.

9. **Schema-less `.pbg/` file handoffs** (viz-request/response, run-request) — drift caught only at runtime. Contrast the validated `investigation_contracts` seam, which is the model to copy.

10. **Layering is convention, not enforced.** The clean direction and read/write split are real but unprotected — no import-linter, no layering test. Adding one is cheap *because* the current state is clean, and would stop regression.

### What is genuinely good (keep it)

- Pure `ws_root`-parameterized lib functions and the read/write `*_views`/`*_mutations` split — the reason the stdlib server could be deleted and the reason ~3,150 unit tests exist.
- `test_no_ai_deps.py` — architecture-as-code enforcing the AI-free server boundary.
- The `investigation_contracts` seam and the bigraph-loom asset seam — narrow, versioned, clean; the templates for fixing the leaky ones.
- "Materialize a remote build as a plain local workspace" and "land remote results in the native store format" — remote compute adds almost no conditional surface to the rest of the app.
- Disciplined HTML escaping across all three renderers (low XSS), the `allow_nan=False` publish sanitization, and the consistent error-envelope *shape*.

---

## 13. One-paragraph bottom line

The bones are right: a clean tooling/data/engine split, a dependency-injected lib layer that made a 9.6k-line server deletion possible, a real read/write module discipline, and a deliberately AI-free process enforced by a test. What a fast-moving, production-newer engineer predictably under-weighted is all around the edges — files that grew past the point of splitting and never got split (especially the 15.7k-line frontend driver and the 6.1k-line route file), a security posture that silently assumes localhost while offering a network flag, a companion-library dependency allowed to sprawl into 57 modules and private internals, and a durable run path that quietly lacks the safety properties the docs advertise for the *other* run path. None of it blocks a single researcher using the tool locally today; most of it becomes load-bearing the instant the tool is shared, exposed, or asked to hold more than one workspace.
