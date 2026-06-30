# Final-review fix report

## Changes

### 1. `--server` + `--dry-run` guard (`lib/cli_runs.py`)

Added an early-return guard at the top of `run_study`:

```python
if server and dry_run:
    return (
        {"error": "--dry-run is local-only; drop --server to preview a run"},
        400,
    )
```

This fires before `_post_server` is ever called, so no network request is made.
`run_composite` was verified to have no `server` parameter — only `run_study` is affected.

### 2. `rerun` help/docstring accuracy (`cli.py`, `lib/cli_runs.py`)

- `cli.py` subparser help: changed from `"Re-run a recorded run with its exact config"` to `"Re-run a recorded run (replays its composite + recorded params/steps)"`.
- `cli_runs.rerun`: added a docstring stating plainly that the function replays the recorded `spec_id` + params + `n_steps` as a composite run, and that study-origin runs are replayed as composite runs (not re-resolved through the study path). Behavior unchanged.

### 3. Safe `study_spec.get("name")` (`lib/report_views.py`)

Changed bare `study_spec["name"]` subscript at the `run_commands` attachment in `build_iset_detail` (line 654):

```python
"run_commands": study_run_commands(study_spec, study_spec.get("name") or ""),
```

The `_render_html` call site in `single_study_report.py` already used `.get("name") or ""` — left as-is per instructions.

## New test

`tests/test_cli_runs.py::test_run_study_server_dryrun_is_rejected` — passes `server="http://localhost:9"` and `dry_run=True` to `cli_runs.run_study`; asserts `code == 400` and `"local-only"` in the error string. Because the function returns 400 before reaching `_post_server`, no `ConnectionError` is raised even though port 9 is not listening.

## Test run

```
pytest tests/test_cli_runs.py tests/test_cli_run_commands.py -v
```

```
9 passed in 0.07s
```

No warnings. All pre-existing tests green.
