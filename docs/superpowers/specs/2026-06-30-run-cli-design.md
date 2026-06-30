# Design: `vivarium-dashboard` run CLI + report advertising

**Date:** 2026-06-30
**Status:** Approved (brainstorm) → ready for implementation plan

## Problem

Rerunning a study / investigation / individual run, or configuring a new run,
currently requires either the dashboard web UI buttons or the AI slash-commands
(`/pbg-study run-baseline`, `/pbg-investigation run`) that POST to `/api/study-run-*`.
There is **no command a human can type in a terminal**. Reviewers reading an
investigation report cannot run slash-commands, so the work is hard to reproduce.

Goal: a real shell CLI to rerun any investigation/study/run and configure new
runs, with the relevant command advertised throughout the rendered
investigation/study reports so it is trivial to copy-paste and run.

The run *logic* already lives in reusable lib functions
(`lib/study_runs.run_study_baseline` / `run_study_variant`, `lib/run_runner.execute`,
`lib/composite_runs.py`, `lib/investigation_run_*`), so the CLI wraps existing
code rather than reimplementing the run path.

## Decisions (from brainstorm)

1. **Execution model:** local/in-process by default; optional `--server <url>`
   delegates to a running dashboard.
2. **Command surface:** Standard — run study/investigation/composite (with config
   flags), `rerun <run-id>`, plus inspect: `runs`, `status`, `logs`.
3. **Advertising:** per-item copy chips (study card, each `simulation_set` row,
   each variant row) **and** a per-study "Reproduce this study" block, across the
   single-study report, the investigation report SPA, and the study-detail page.
4. **Alias:** add a short `vdash` console-script alongside `vivarium-dashboard`.
5. **Run mode default:** foreground with a live progress line; `--detach` to
   background. **Output:** plain text + `--json` for piping (no Rich dependency).

## Architecture

### CLI layer (`vivarium_dashboard/cli.py`)
Extend the existing `argparse` dispatcher with a `run` sub-parser group and the
top-level `rerun` / `runs` / `status` / `logs` commands. Handlers stay thin
adapters: parse args → call a lib function → print result (or `--json`). Both
`vivarium-dashboard` and `vdash` map to `cli:main` via `[project.scripts]`.

```
vdash run study <slug>         [--variant X --steps N --seed S --param k=v (repeatable)
                                --dry-run --detach --server URL --json]
vdash run investigation <slug> [--studies a,b --server URL --json]
vdash run composite <id>       [--steps N --emit p1,p2 --dry-run --detach]
vdash rerun <run-id>           [--steps N (override) --detach --server URL]
vdash runs <slug>              [--json]          # list a study's recorded runs
vdash status <run-id>          [--json]          # one run's state + progress
vdash logs <run-id>            [--follow]
vdash run-remote <composite>   # existing command, re-homed under the same help tree
```

### Run resolution + execution seam (`lib/cli_runs.py`, new)
Pure, server-free functions the CLI calls (and that `--dry-run` can render):

- `resolve_study_run(ws_root, study, *, variant=None, steps=None, seed=None,
  param_overrides=None) -> RunRequest` — resolves the composite + params from
  `conditions.baseline` (or `conditions.variants[variant]`), layering CLI
  overrides on top. Reuses the resolution already in `study_runs` /
  `investigation_run_one_views` rather than duplicating it; refactor the shared
  resolution into a callable if it is currently inline in a handler.
- `resolve_composite_run(ws_root, spec_id, *, steps, emit_paths) -> RunRequest`.
- `resolve_rerun(ws_root, run_id) -> RunRequest` — reads the recorded run's
  `spec_id`, `params_json`, `n_steps`, `emit_paths` from the relevant runs DB
  (study `runs.db` or `.pbg/composite-runs.db`) and rebuilds the identical
  request. `--steps` may override.
- `execute_run(request, *, detach=False, on_progress=None) -> RunResult` — when
  `detach=False`, runs `run_runner.execute` in-process and streams progress via
  `on_progress`; when `detach=True`, spawns the existing detached
  `vivarium-dashboard run-composite --request <file>` worker and returns the
  run_id immediately.
- `submit_via_server(base_url, kind, payload) -> dict` — `--server` path: POST
  to the existing endpoint for that kind and return its JSON.

The `runs` / `status` / `logs` readers are thin queries over
`composite_runs.query_run_meta` / the study `runs.db` / `run.log`.

### `rerun` run-id resolution
A run-id may belong to a study `runs.db` or the workspace `.pbg/composite-runs.db`.
`resolve_rerun` looks in both (study DBs discovered via `iter_study_dirs`), errors
clearly if the id is ambiguous or absent.

### Command-string generator (`lib/run_commands.py`, new) — single source of truth
`study_run_commands(spec, slug) -> StudyRunCommands` returns the canonical `vdash …`
strings for a study:
```
{
  "baseline":  "vdash run study <slug>",
  "variants":  [{"name": v, "cmd": "vdash run study <slug> --variant <v>"} , ...],
  "simulations":[{"name": s, "cmd": "vdash run study <slug> [--steps N]"}, ...],
  "rerun_hint":"vdash rerun <run-id>",
}
```
Every advertising surface and the CLI's own `--help`/examples consume this one
function so the advertised commands never drift from what the CLI accepts.

### Advertising surfaces
1. **Single-study report** (`lib/single_study_report.py`, server-rendered): a
   "Reproduce this study" block + a per-row command chip in the "What we ran"
   and variants tables, built from `study_run_commands`.
2. **Investigation report SPA** (`static/walkthrough.js`): the command strings
   are precomputed server-side into each study's payload (extend the study /
   `iset` detail spec with a `run_commands` field) so the JS can render chips +
   the block without re-deriving anything.
3. **Study-detail page** (`templates/study-detail.html` + `static/study-detail.js`):
   a "CLI equivalent / Reproduce" card on the Simulations tab next to the existing
   Run buttons, plus a copy chip in the study header. Reads `run_commands` from
   `/api/study/<slug>`.

Chips are copy-to-clipboard (`navigator.clipboard`); the per-study block is a
small framed code list. Degrades gracefully in the published static bundle
(commands are plain strings, no live backend needed).

## Data flow

```
vdash run study X --variant v --steps N
  └─ cli.py: parse → lib/cli_runs.resolve_study_run(ws, X, variant=v, steps=N)
       └─ reads study.yaml conditions, builds RunRequest
  └─ --dry-run? print RunRequest JSON and exit
  └─ execute_run(req, detach=?)
       ├─ detach=False → run_runner.execute() in-process, stream progress
       └─ detach=True  → spawn `vivarium-dashboard run-composite --request f`
  └─ print result + next-step hints (Rerun: vdash rerun <id>)

--server URL → submit_via_server(URL, "study-baseline"|"study-variant", payload)
                POSTs existing /api/study-run-* endpoint, prints response
```

## Error handling
- Unknown study/investigation/composite/run-id → clear message + nonzero exit.
- `--server` unreachable → connection error message naming the URL (mirror the
  sms-api error-panel idea, plain-text form).
- Dirty/ambiguous `rerun` target → explain and list candidates.
- `--param k=v` parse error (no `=`, bad value) → precise message.
- All command handlers return an int exit code via `main()`.

## Testing
- `lib/run_commands`: command-string generation for a study with/without
  variants and simulation_set (fixture spec).
- `lib/cli_runs.resolve_study_run` / `resolve_composite_run` / `resolve_rerun`:
  correct RunRequest fields (composite, params, steps, overrides) against a
  fixture workspace, including `--param`/`--steps` layering and rerun-from-recorded.
- `runs` / `status` readers against a fixture runs DB.
- CLI smoke test: `vdash run study <fixture> --dry-run` prints the expected
  request JSON; `vdash runs <fixture> --json` parses.
- A render test asserting the advertised command appears in the single-study
  report HTML and in the `run_commands` payload field.

## Scope / YAGNI
- **In:** the command surface above; in-process + `--server` execution; the
  three advertising surfaces; `vdash` alias.
- **Out:** config-file scaffolding / named saved configs; interactive prompts;
  Rich/TUI output; any new server endpoints (reuse existing); the Full-scope
  `export`/`config` commands.

## Open items deferred to the plan
- Exact refactor needed to expose the study-run resolution as a callable lib
  function if it is currently inline in the API handler.
- Progress-streaming detail for in-process foreground runs (reuse
  `run_runner`'s progress callback).
