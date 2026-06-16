# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

For the full data-architecture picture — what the dashboard does, where data
lives, the run/render lifecycles, and which companion repo owns which
transformation — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What this is

`vivarium-dashboard` is a local web UI for [process-bigraph](https://github.com/vivarium-collective/process-bigraph)
workspaces. You point it at a workspace directory (one containing `workspace.yaml`,
scaffolded from [pbg-template](https://github.com/vivarium-collective/pbg-template))
and it serves an interactive dashboard over that workspace's registry, composites,
studies, investigations, and reports. The dashboard reads and **writes** the
workspace's files — every action commits to a git branch in the workspace, giving
a full audit trail.

Crucial distinction: this repo is the *server/tooling*; the *data* it operates on
lives in a separate workspace directory passed via `--workspace`. The workspace is
where `studies/`, `composites/`, `.pbg/`, `runs.db` files, etc. live — never in
this repo (except under `tests/_fixtures/`).

## Commands

```bash
# Run the dashboard against a workspace (renders HTML once, then serves)
vivarium-dashboard serve --workspace /path/to/workspace          # picks a free port
vivarium-dashboard serve --workspace . --port 8000 --host 0.0.0.0

# Tests (pytest; requires editable install of this package + its deps)
pytest                                    # full suite
pytest tests/test_composite_runs.py       # one file
pytest tests/test_composite_runs.py::test_name -x   # one test, stop on first fail
pytest -k "csrf or origin"                # by keyword

# Export a workspace as a static read-only bundle (the "read-only dashboard")
vivarium-dashboard-publish --workspace /path/to/workspace --out /tmp/bundle

# One-shot legacy migration (investigations/<name>/spec.yaml → studies/, v2→v3)
vivarium-dashboard migrate-investigations --workspace /path/to/ws [--dry-run]
```

There is no separate build step (pure Python + static assets) and no linter
configured in the repo. Dependencies are managed with `uv` (`uv.lock`); install
editable with `pip install -e .` (or `uv pip install -e .`) into the venv that
will run it. Note `bigraph-loom` and `pbg-superpowers` are direct git/path deps.

## Architecture

### HTTP server (`vivarium_dashboard/server.py`, ~16k lines)
A single stdlib `BaseHTTPRequestHandler` subclass (`Handler`) is the entire web
layer — no framework. `do_GET`/`do_POST` (and `do_DELETE`) are long
`if self.path.startswith("/api/...")` dispatch chains routing to `_<name>(self)`
methods. There are ~100+ `/api/*` endpoints. When adding an endpoint: add the
`startswith` branch in the dispatcher AND the handler method; non-GET routes must
pass the `_csrf_ok()` origin check (see below). Most real logic lives in
`lib/` modules; handler methods are thin adapters that parse the request, call a
`lib` function, and `self._json(...)` the result. `serve()` at the bottom boots it;
`python -m vivarium_dashboard.server --workspace ... --port ...` is how tests
spawn it as a subprocess.

### `lib/` — the domain logic
Each module owns one concern and is independently testable. Key ones:
- `workspace_paths.py` — **canonical** resolution of a workspace's directory
  layout. Always resolve `studies/`, `composites/`, `.pbg/`, etc. through here,
  never hardcode the names — a workspace may relocate them via a `layout:` map in
  `workspace.yaml`.
- `_root.py` — holds the global workspace root (`set_workspace_root`/`get`),
  set once at server/CLI startup. Many lib functions read it.
- `composite_runs.py`, `run_runner.py`, `run_jobs.py`, `run_registry.py` — the
  simulation run subsystem (see below).
- `single_study_report.py` (~2.4k lines), `study_charts.py`, `report.py`,
  `comparative_viz.py` — rendering studies/investigations to HTML and charts.
- `investigations.py` (~1.8k lines), `simulations_index.py`, `study_seed.py`,
  `expected_behavior.py`, `scaffold_yaml.py` — investigation/study data model,
  scaffolding, and gating.
- `github_auth.py` — GitHub device-flow auth for the Branches tab.
- `spec_migration.py` / `investigation_migrate.py` — schema version migrations.

### Running simulations (detached process model)
Composite runs can take tens of minutes, far longer than an HTTP request. The
flow: an endpoint writes a **run-request JSON file**, then spawns
`vivarium-dashboard run-composite --request <file>` as a *detached* subprocess.
`run_runner.execute()` is pure — it reads everything from the request file, never
from argv or globals (this structurally avoids "Argument list too long" and lets
runs outlive the server). Each run writes results to
`studies/<slug>/runs.db` (SQLite) — that DB is the durable artifact. In-process
constructs like `run_jobs.py`'s background-thread job manager only track *progress*
for polling endpoints and are lost on restart.

### Static frontend (`static/` + `templates/`)
Vanilla JS (no bundler/build). `index.html.j2` is rendered once at serve time
(`lib/report.render_dashboard`). `client.js` is the SPA driver; `data-source.js`
abstracts whether data comes from the live server or a published static bundle.

### Publish / read-only bundle (`publish.py`)
Exports a workspace to a self-contained static bundle (`api/*.json` + per-study
HTML shells + assets). The same frontend JS runs against `api/*.json` files
instead of the live server. Recent fixes (commits "read-only dashboard /
snapshot") concern features that must degrade gracefully when there's no live
backend (no Launch buttons, no GitHub mark, etc.). When touching frontend
behavior, consider both the live and snapshot data sources.

## Conventions & gotchas

- **CSRF/origin guard**: every mutating (`POST`/`DELETE`) endpoint calls
  `_csrf_ok()`. Requests with no `Origin` (curl, local CLI) are allowed; a present
  `Origin` must match `Host`. Bypass for tests/tools with
  `VIVARIUM_DASHBOARD_DISABLE_CSRF=1`.
- **Atomic writes**: use `lib/atomic_io.py` for file writes that must not be seen
  half-written.
- **JSON serialization**: the server's `_json_default` handles numpy/dataclasses;
  `publish.py` writes with `allow_nan=False` on purpose (browser `JSON.parse`
  rejects `NaN`/`Infinity`) — sanitize non-finite floats before publishing.
- **Tests** spawn a real server subprocess via the `dashboard_client` fixture
  (`tests/conftest.py`) against a fixture workspace under `tests/_fixtures/`.
  Fixture workspaces' own Python packages are auto-added to `sys.path`.
- **Workspace package import**: at serve time the workspace root is prepended to
  `sys.path` so the workspace's own package (e.g. `pbg_<project>.core.build_core`)
  is importable for rendering composites.
- **Git workflow**: this repo merges via PRs to `main` (see recent history); the
  *workspace* the dashboard serves has its own git history that dashboard actions
  commit to — keep the two mental models separate.

## Companion projects
- **pbg-superpowers** — Claude Code plugin whose `/pbg-*` skills drive this
  dashboard's HTTP API (AI-assisted authoring). A runtime dependency.
- **pbg-template** — the workspace scaffold this dashboard serves; ships the
  canonical `.pbg/schemas/` validators the dashboard reads at save time.
- **bigraph-loom** — embedded state-tree (bigraph) explorer, served at
  `/loom-explore`.
