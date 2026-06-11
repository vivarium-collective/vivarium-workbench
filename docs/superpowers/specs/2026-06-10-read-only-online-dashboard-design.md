# Read-only, online-hostable vivarium-dashboard — Design

**Date:** 2026-06-10
**Status:** Draft (awaiting review)
**Scope:** A program design + the detailed spec for **sub-project #1**. Sub-projects #2–#4 are scoped here but get their own spec → plan cycles.

---

## 1. Goal

Move vivarium-dashboard toward production-grade online deployment: a **read-only, view-only hosted version** that anyone can browse (investigations, studies, findings, charts, verdicts), backed by **sms-api** for live simulation results — while the dashboard **still runs trivially locally** (`vivarium-dashboard serve`) for default authoring/use. One shared frontend; two data sources.

## 2. Decisions (settled in brainstorming)

1. **Hosted = view-only published viewer.** No run-triggering, no workspace mutation, no auth tier (for now). Just the read surface.
2. **Data model: static narrative + live sms-api results.** The authored narrative is published as a static snapshot; results/observables are fetched live from sms-api at view time.
3. **Frontend: incremental client-fetch.** Reuse the existing JS renderers (they already render client-side from `window._study`); change them to *fetch* their data through a `DataSource` instead of consuming Jinja-embedded data. One frontend serves local + hosted.

## 3. The two planes (why an organizing layer is needed)

- **sms-api** is the **run + results plane**: per-run provenance (config, simulator version, `experiment_id`, job status) + results (`WorkerEvent` mass/time streams, parquet downloads), keyed by run id. It has **no concept of study/investigation** — it's a flat catalog of runs.
- **The study/investigation model** (the dashboard's YAML) is the **organizing layer**: investigations → studies; per study the objective, readouts, tests + acceptance bands, verdicts, findings, narrative — **and which sms-api runs realize each study** (run-id references). The run ids are the **join** between organization and data.

So sms-api cannot be the organizer. The organization lives in the study YAML and is **published** to the hosted viewer; sms-api supplies the live numbers behind each run reference.

## 4. GitHub's role

The organization layer **is a git repo** (the workspace, e.g. `v2e-invest`):
- **Repo** = where the investigations/studies/narrative live and are versioned.
- **Branches** = states of the science (working branch, collaborator branch, `main`); **commits** = its history; **PRs** = the authoring workflow.
- A **published snapshot is pinned to a commit/ref** → reproducible, citable: "investigation X as of commit `abc123`". Snapshot identity = `(repo, ref) → snapshot.json`.
- **Publishing is a GitHub Action** (sub-project #4): push/merge/tag → CI exports the snapshot + deploys the static frontend (GitHub Pages / object store) → hosted dashboard updates. The repo is the pipeline.
- **Run provenance threads to simulator commits:** each sms-api run was produced by a specific vEcoli/model commit + config (`runs[].provenance.model_commit_hash`), giving an end-to-end chain: workspace-commit (narrative) → run id → simulator-commit → results.

The dashboard's existing git/GitHub *write* integration (branches tab, PR creation, commit-all — it shells out to `git`/`gh`) is the **local-full authoring** flow; it stays local and is never hosted. Git re-enters hosted only on the **publish** side (which ref to export).

## 5. Target architecture

```
        ┌──────────── shared frontend (existing JS renderers, made data-driven) ───────────┐
        │  renders narrative + results from a DataSource (fetch), not Jinja-embedded data    │
        └───────────────┬──────────────────────────────────────────┬──────────────────────┘
                        │ LOCAL mode                                │ HOSTED mode (view-only)
          vivarium-dashboard serve                        static frontend on a CDN
          (stdlib server → JSON API;                       ├─ narrative ← published snapshot.json (repo@ref)
           narrative ← local YAML,                         └─ results   ← sms-api (live)
           results ← runs.db or sms-api;
           authoring/POST still works)
```

The **seam** is a `DataSource` the frontend reads through: `LocalServerSource` (local) vs `SnapshotSource` + `SmsApiResultsSource` (hosted). Local keeps full authoring; hosted is static + sms-api, **zero write surface**.

## 6. Decomposition (4 sub-projects; the seam is the spine)

1. **Client-fetch seam** *(this spec; do first)*. Decouple the frontend from Jinja-embedding: the JS fetches narrative data from the server's read endpoints through a `DataSource`. Local mode behaves identically. **Foundational, self-contained, unblocks the rest.**
2. **Narrative export ("publish")**. A CLI that exports investigations/studies/findings (+ the `runs[].sms_api_run_id` references) from the workspace repo at a ref into a static `snapshot.json` + frontend assets → a CDN-hostable view-only bundle. Adds the `sms_api_run_id` join to the run data model.
3. **sms-api results source**. A `SmsApiResultsSource` that fetches results from sms-api and maps its `WorkerEvent`/parquet model to the dashboard's chart/observable inputs; the frontend uses it for results in hosted mode. (Has its own sms-api↔dashboard data-contract mapping to nail down.)
4. **Production infra + hardening**. GitHub Action publish pipeline + static hosting, CORS coordination with sms-api, and the `server.py` god-file split (extract the read API cleanly). Overlaps the parked P1/P2 hardening backlog.

Build order: 1 → 2 → 3 → 4, each its own spec/plan cycle.

---

## 7. Sub-project #1 — Client-fetch seam (detailed design)

**Goal:** the frontend *fetches* its narrative data through a `DataSource` instead of consuming Jinja-embedded `window._study`, establishing the seam the later sub-projects plug into. **Local mode looks and behaves identically.**

### Components
1. **Uniform JSON data endpoints (server).** Each page's data is available as a clean JSON GET returning *exactly* the dict Jinja embeds today:
   - `GET /api/study/<slug>` → the `_study_detail_spec(slug)` dict (today embedded as `window._study`).
   - `GET /api/investigation/<id>` → the iset/investigation detail (partly exists as `/api/iset/<id>`; normalize naming).
   - `GET /api/workspace` → the home/index data (what `index.html.j2` renders from).
   The builders already exist (`_study_detail_spec`, `_get_iset_detail`, the home renderer's data); this exposes them uniformly as JSON. No new data computation.
2. **Frontend data layer — `static/data-source.js`.** A small module the renderers call: `loadStudy(slug)`, `loadInvestigation(id)`, `loadWorkspace()`. It reads a **source config** (default: same-origin local server → `fetch('/api/...')`). This is the seam — `SnapshotSource`/`SmsApiResultsSource` implement the same interface later without touching the renderers.
3. **Thin page shells.** `study-detail.html` (and the investigation + home shells) become minimal: mount the JS + the page's own id (slug), and let the JS fetch + render. Remove the `window._study = {{ study|tojson }}` embed (replaced by a fetch). Keep the Jinja shell skeleton (head/scripts/mount points).
4. **Source config — `window.__DASH_CONFIG__`.** A tiny object (default `{ mode: "local-server" }`) so hosted mode can later set `{ mode: "snapshot", snapshotUrl, smsApiBase }` without code changes.

### Data flow
`/studies/<slug>` shell loads → JS reads `__DASH_CONFIG__` (local) → `dataSource.loadStudy(slug)` → `fetch('/api/study/<slug>')` → existing `study-detail.js` renders the returned dict (same shape as today's `window._study`). Investigation + home pages analogous.

### Error handling
- Fetch failure (network / missing endpoint) → a clear in-page error state (not a blank page), with a retry.
- **Transitional fallback:** during migration, if `window._study` is still present (Jinja embed not yet removed for a page), the data layer uses it; otherwise it fetches. This lets pages convert one at a time without breakage.
- Local mode parity is a hard requirement — the converted pages must look/behave identically to today.

### Testing
- **Parity test:** `GET /api/study/<slug>` returns the same dict the Jinja template embedded (compare against `_study_detail_spec`); same for investigation + workspace.
- **Render-from-fetch:** a page renders correctly from fetched data (the renderers already consume this shape) — assert no reliance on the embed once converted.
- **Source-overridable:** swapping `__DASH_CONFIG__`/the data-layer source changes where it fetches (unit test the seam) — proves #2/#3 can plug in.
- **No regression:** the local server + the converted pages work end-to-end; existing dashboard tests stay green.

### Boundary (YAGNI — explicitly NOT in #1)
No static export, no sms-api, no auth, no Docker/ASGI, no `server.py` split, no rebuild of the renderers. #1 *only* introduces the fetch seam in local mode. Every later sub-project slots into the `DataSource` it creates.

### Scope of pages converted in this pass
`study-detail`, `investigation` (iset), and the `workspace`/home index — the three primary narrative surfaces. Other GET endpoints (composite-state, simulations, etc.) stay as-is for now; they convert as needed when #3 wires results.

## 8. Non-goals (program-wide, this phase)
- Run-triggering / authoring from the hosted version (view-only).
- An authenticated interactive tier.
- Replacing the local-full authoring server or its git/subprocess write side.
- A frontend framework rewrite (reuse the existing renderers).
- Hosting/organizing studies *online* (the local dashboard remains the authoring source of truth; hosted is a published read-only view).

## 9. Risks & open questions
- **sms-api ↔ dashboard result mapping** (#3): sms-api's `WorkerEvent`/parquet vs the dashboard's chart/observable inputs — needs its own grounding + a versioned data contract (ties to the P1/P2 "cross-repo data-contract" item).
- **sms-api CORS** (#3/#4): currently `allow_origins=["*"]` with a TODO to restrict — coordinate an allowlist for the hosted frontend origin.
- **Run-reference capture** (#2): how a study's `runs[]` acquires its `sms_api_run_id` (recorded when a run executes via sms-api, or back-filled). Needs a small data-model addition.
- **Per-`main`-only vs per-branch/tag** published views (#4): decide whether to host one canonical view or multiple.
- **`server.py` is 13K LOC** (#4): the read-API extraction should align with the god-file split rather than fight it.
