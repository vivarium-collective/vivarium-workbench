# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See also `AGENTS.md` for the human-maintained agent guide; the two are kept consistent.

## Setup

```bash
pip install -e ".[dev]"    # or: uv pip install -e ".[dev]"
```

Python >=3.10. No linter, formatter, pre-commit, or CI is configured in this repo.

## Run

```bash
vivarium-dashboard serve --workspace <path>           # installed CLI
python -m vivarium_dashboard.server --workspace <path> --port <num>   # equivalent
```

CLI subcommands (`vivarium_dashboard/cli.py`): `serve`, `migrate-investigations`, `run-composite`.

## Test

```bash
pytest                                                 # whole suite
pytest tests/test_foo.py                               # one file
pytest tests/test_foo.py::test_bar                     # one test
```

- Fixture workspace: `tests/_fixtures/ws_increase_demo/`. Tests that mutate the workspace **copy it to `tmp_path`** to avoid polluting the repo — preserve this when adding tests.
- `tests/conftest.py` exposes a `dashboard_client(workspace=Path) -> _Client` factory that spawns `python -m vivarium_dashboard.server` as a subprocess and returns a wrapper with `.get()/.post()/.json()`.
- Tests that import pure helpers from `server.py` must `monkeypatch` the module-level `WORKSPACE` global (see below).

## Architecture

This is a single-process, stdlib-only web app (no Flask/FastAPI). The shape that matters:

- **`vivarium_dashboard/server.py` (~9.8k lines)** is the whole HTTP layer: one `ThreadingHTTPServer`, one handler class, module-level globals (`WORKSPACE: Path`, `_REGISTRY_CACHE`, `LOCK`). All routes live here. Pure helpers intended for unit testing are suffixed `_*_for_test()`.
- **`vivarium_dashboard/lib/`** holds the domain logic the server delegates to — study/investigation/run registries, composite recipe resolution, spec migration, PDF/BibTeX handling, work-state, etc. Prefer adding non-HTTP logic here over growing `server.py`.
- **`vivarium_dashboard/static/`** is vanilla JS (no build step, no framework). `vivarium_dashboard/templates/` is Jinja2. Both are shipped via `pyproject.toml` `force-include` and served directly by `server.py`.
- The dashboard serves a [pbg-template](https://github.com/vivarium-collective/pbg-template) workspace (one containing `workspace.yaml`). It validates writes against the workspace's own `.pbg/schemas/`.
- **Servers self-register** in `~/.pbg/servers/<name>.json` so multiple worktrees can discover each other.

### Study/investigation layout — two formats coexist

Workspaces can carry both shapes; resolution precedence is `studies/` first:

- v3 canonical: `studies/<name>/study.yaml` with the 8-section structure (Purpose · Pipeline gate · Build · Simulations · Readouts · Tests · Limitations · References).
- Legacy v2: `investigations/<name>/spec.yaml`. The `migrate-investigations` CLI converts these in place (`--dry-run` to preview).

`pipeline_gate.prerequisites` is the canonical edge source for the investigation DAG; `parent_studies` is a fallback (commit `d984e52`).

### Headline status

`effective_status()` derives the single-chip headline from multi-axis fields (commit `f81b12e`). Don't read raw status fields directly when displaying — go through `effective_status`.

### Runs

`runs.db` (SQLite) is the canonical source of truth for run state (commit `1329d3d`); filesystem artifacts are derived/secondary. The `run-composite` CLI runs in a detached subprocess and reads state from a JSON request file, not argv.

### Visualization lifecycle

Four POST endpoints, in order: **described → requested → created → added → committed**. Each step has its own handler; don't collapse them.

## Quirks worth knowing

- `server.WORKSPACE` is set exactly once by `serve()`. Unit tests that call helpers directly must `monkeypatch` it; forgetting this is the most common test failure.
- Slug rules differ by entity:
  - Study slug: `^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$` — underscores allowed (e.g. `study-monod_kinetics-096184`).
  - Investigation-set slug: `^[a-z0-9][a-z0-9-]*$` — kebab-case only.
- JSON responses go through `_json_sanitize` + `_json_default` to handle numpy arrays, `Path`, `set`, and non-finite floats. Add new non-JSON-native types there rather than at call sites.
- Every UI action commits to the active workstream branch — the GitHub Branches tab is the audit trail. When adding write endpoints, follow the existing commit-per-action pattern instead of batching.
- Optional `viz` extra (`matplotlib`, `imageio`) is opt-in; don't add unconditional imports of those at module top level.
