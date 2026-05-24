# vivarium-dashboard — agent guide

See also `CLAUDE.md` for the Claude Code–maintained guide; the two are kept consistent.

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

## Entrypoints

| What | Where |
|---|---|
| CLI | `vivarium_dashboard/cli.py` — subcommands: `serve`, `migrate-investigations`, `run-composite` |
| HTTP server | `vivarium_dashboard/server.py:serve()` — stdlib `ThreadingHTTPServer`, 9863 lines |
| `python -m vivarium_dashboard.server --workspace <path> --port <num>` | Also runnable standalone |

## Architecture

This is a single-process, stdlib-only web app (no Flask/FastAPI). The shape that matters:

- **`vivarium_dashboard/server.py` (~9.8k lines)** is the whole HTTP layer: one `ThreadingHTTPServer`, one handler class, module-level globals (`WORKSPACE: Path`, `_REGISTRY_CACHE`, `LOCK`). All routes live here. Pure-function helpers intended for unit testing are suffixed `_*_for_test()`.
- **Business logic** lives in `vivarium_dashboard/lib/` (25 modules). Prefer adding non-HTTP logic here over growing `server.py`.
- **Static assets**: `vivarium_dashboard/static/` is vanilla JS (no build step, no framework). Templates: Jinja2 in `vivarium_dashboard/templates/`. Both shipped via `pyproject.toml` `force-include` and served directly by `server.py`.
- The dashboard serves a [pbg-template](https://github.com/vivarium-collective/pbg-template) workspace (one containing `workspace.yaml`). It validates writes against the workspace's own `.pbg/schemas/`.
- **Server registers** in `~/.pbg/servers/<name>.json` for cross-worktree discovery.

### Study/investigation layout — two formats coexist

Workspaces can carry both shapes; resolution precedence is `studies/` first:

- **v3 canonical**: `studies/<name>/study.yaml` with the 8-section structure (Purpose · Pipeline gate · Build · Simulations · Readouts · Tests · Limitations · References).
- **Legacy v2**: `investigations/<name>/spec.yaml`. The `migrate-investigations` CLI converts these in place (`--dry-run` to preview).

`pipeline_gate.prerequisites` is the canonical edge source for the investigation DAG; `parent_studies` is a fallback (commit `d984e52`).

### Headline status

`effective_status()` derives the single-chip headline from multi-axis fields (commit `f81b12e`). Don't read raw status fields directly when displaying — go through `effective_status`.

### Runs

`runs.db` (SQLite) is the canonical source of truth for run state (commit `1329d3d`); filesystem artifacts are derived/secondary. The `run-composite` CLI runs in a detached subprocess and reads state from a JSON request file, not argv.

### Visualization lifecycle

Four POST endpoints, in order: **described → requested → created → added → committed**. Each step has its own handler; don't collapse them.

## Testing

```bash
pytest                                                  # all tests
pytest tests/test_foo.py                                # single file
pytest tests/test_foo.py::test_bar                      # single test
```

- **No pre-commit, no linter, no formatter, no CI config** in this repo.
- Dev extras: `pytest>=7.4`, `pytest-timeout`, `pytest-json-report`.
- Fixture workspace at `tests/_fixtures/ws_increase_demo/`.
- `conftest.py` provides `dashboard_client(workspace=Path) -> _Client` fixture factory (spawns `python -m vivarium_dashboard.server` as a subprocess, returns wrapper with `.get()/.post()/.json()`).
- Tests that mutate the workspace **copy the fixture to `tmp_path`** to avoid polluting the repo.
- Tests that import pure helpers from `server.py` must `monkeypatch` the `WORKSPACE` global. Forgetting this is the most common test failure.

### Quirks

- `WORKSPACE` is a module-level `Path` in `server.py` — set exactly once by `serve()`. Tests using server helpers directly must `monkeypatch` it.
- Study slugs allow underscores (e.g. `study-monod_kinetics-096184`) — `_SLUG_RE = r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$"`.
- Investigation-set slugs are stricter: kebab-case only (`^[a-z0-9][a-z0-9-]*$`).
- The `run-composite` CLI runs in a detached subprocess; state comes from a JSON request file, not argv.
- JSON response serializer has custom handlers for numpy arrays, Paths, sets, and non-finite floats (`_json_sanitize` + `_json_default`). Add new non-JSON-native types there rather than at call sites.
- Every UI action commits to the active workstream branch — the GitHub Branches tab is the audit trail. When adding write endpoints, follow the existing commit-per-action pattern instead of batching.
- Optional `viz` extra (`matplotlib`, `imageio`) is opt-in; don't add unconditional imports of those at module top level.

## Dependencies

Core: jinja2, pyyaml, jsonschema[format-nongpl], pypdf, numpy, process-bigraph, bigraph-schema, pbg-superpowers.
Opt-in: `viz = ["matplotlib", "imageio"]`.
