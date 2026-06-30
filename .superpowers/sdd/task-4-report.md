# Task 4 Report: CLI subcommands

## Status
DONE

## Commit
`563080e feat(cli): run/rerun/runs/status/logs subcommands`

## TDD Evidence
- **RED**: Both tests in `tests/test_cli_run_commands.py` failed with `SystemExit: 2` (argparse rejecting unknown subcommands `run` and `runs`).
- **GREEN**: Both tests pass after implementation. Full 7-test suite (cli_runs + cli_run_commands + run_composite_cli) passes.

## Rename Details
**Old references to `cmd_run_composite` (worker):**
- `vivarium_dashboard/cli.py:193` — function definition
- `vivarium_dashboard/cli.py:320` — `p_run.set_defaults(func=cmd_run_composite)`

**Updated:**
- Definition renamed to `cmd_run_composite_worker` (line 193)
- Subparser `run-composite` `set_defaults` updated to `cmd_run_composite_worker` (line 421)
- New user-facing `cmd_run_composite` added at line 248 for `run composite <spec_id>`
- New `run composite` subparser wired to `cmd_run_composite` (line 515)

**Confirmed**: `vdash run-composite --request <file>` still dispatches through `cmd_run_composite_worker` → `run_runner.execute()`. Test `test_run_composite_cli.py::test_run_composite_subcommand_executes_request` passes.

## Files Changed
- `vivarium_dashboard/cli.py` — renamed worker, added `_emit`, `_parse_params`, 7 new handler functions, 7 new subparsers
- `tests/test_cli_run_commands.py` — new (2 TDD tests)

## Self-Review
- `json` was already imported at the top of `cli.py` — no new dependency needed.
- All handlers are thin adapters; no run logic in `cli.py`.
- `--param key=value` JSON-parses values with bare-string fallback via `_parse_params`.
- Next-step hints (`Follow:` / `Rerun:`) are only printed on non-dry-run runs with a `run_id`.
- `_add_common` is defined locally inside `main()` to avoid polluting module scope.

---

## Minor Review Fixes (feat/run-cli follow-up)

### Changes made

**Fix 1 — `--seed` forwarded as param (`cmd_run_study`)**
- `cli.py:225`: build `params = _parse_params(args.param)` then inject `params["seed"] = args.seed` when seed is set, before passing to `cli_runs.run_study`.

**Fix 2 — `--follow` on `logs` implemented (`cmd_logs`)**
- Added `import time` to module imports.
- Defined `_TERMINAL_STATUSES = {"completed","failed","cancelled","error","complete","orphaned"}` module-level constant.
- `cmd_logs`: non-follow path unchanged. When `args.follow` is set: after printing current log, check if run is already terminal (return immediately); otherwise poll every 1s printing newly-appended bytes, stopping at terminal status or 1800s cap.

**Fix 3 — composite hints added (`cmd_run_composite`)**
- After a real (non-dry-run) successful composite run with a `run_id`, print `Follow:  vdash status <id>` and `Rerun:   vdash rerun <id>`, matching the study handler format.

### Tests added to `tests/test_cli_run_commands.py`

- `test_run_study_seed_becomes_param`: dry-run with `--seed 5`; asserts `request["overrides"]["seed"] == 5`.
- `test_logs_follow_terminal_run_returns_promptly`: writes a log file into the recorded run, calls `main(["logs", run_id, "--workspace", str(ws), "--follow"])`, asserts rc==0 and log content present; terminal status causes immediate return (no sleep).

### Test command and output

```
/Users/eranagmon/code/v2ecoli/.venv/bin/python -m pytest tests/test_cli_run_commands.py -v
```

```
============================= test session starts ==============================
platform darwin -- Python 3.12.12, pytest-9.0.3, pluggy-1.6.0
collected 4 items

tests/test_cli_run_commands.py::test_run_study_dry_run_prints_request PASSED [ 25%]
tests/test_cli_run_commands.py::test_runs_list_json PASSED               [ 50%]
tests/test_cli_run_commands.py::test_run_study_seed_becomes_param PASSED [ 75%]
tests/test_cli_run_commands.py::test_logs_follow_terminal_run_returns_promptly PASSED [100%]

============================== 4 passed in 0.04s ===============================
```

No warnings. No hangs. 0.04s total.
