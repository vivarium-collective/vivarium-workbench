# Checkpoint — 2026-07-17: `demo-verification` branch, v0.3.0 already shipped; docs reconciled, release-tail unconfirmed

## ⭐ RESUME HERE

**Branch:** `demo-verification` (not `feat/improved-visual-feedback` — that
branch's work is DONE; see below). Upstream `origin/demo-verification` is
gone (`git branch --unset-upstream` needed if you want to re-push cleanly).

**Big-picture state:** `feat/improved-visual-feedback` (Plan 7 — pinned-run
progress bar, PR #467) **merged into `main` and shipped as `v0.3.0`**
(merge-commit `1c51df2`, 2026-07-15T16:30:00Z; tag + GitHub Release published
2026-07-15T16:30:15Z; confirmed via `gh pr view 467` / `gh release list`).
`demo-verification` already carries that `main` (via merge-commit `3b4dd60`,
which also pulled in three unrelated refactor PRs: import-linter ports/
adapters gate #474, ScientificContent port #473, staging-policy split #472).
`pyproject.toml` on this branch reads `0.3.0`.

This session was **pure orientation + doc reconciliation** — no functional
code was touched. `.todo/MANIFEST.md`, `.todo/plans/10-...md`, and
`NEXT_STEPS.md` had all gone stale (still describing PR #467 as open/
pre-merge); they're now updated to reflect the actual merged/released state.

## Session goal

1. `/orientation` — survey repo state, note the `main`→`demo-verification`
   merges and uncommitted work, confirm the `feat/improved-visual-feedback`
   merge+release and update stale plans/memory accordingly. **Done.**
2. `/checkpoint` — write this file. **Done** (this write).

## Progress table

| Item | Status |
|---|---|
| Confirm PR #467 merged + `v0.3.0` released | ✅ Done (`gh pr view 467`, `gh release list`) |
| `.todo/MANIFEST.md` item 7 status → DONE + RELEASED | ✅ Done (uncommitted edit) |
| `.todo/plans/10-release-improved-visual-feedback-smscdk.md` status updated | ✅ Done (uncommitted edit, untracked file) |
| `NEXT_STEPS.md` header/state table updated | ✅ Done (uncommitted edit) |
| Memory: `project_v0.3.0_release_shipped.md` saved | ✅ Done |
| Memory: `feedback_never_run_full_pytest.md` saved | ✅ Done — see "Key design decisions" below, **read before running any tests** |
| Scoped test verification | ✅ Done — see Verification |
| Full unscoped `pytest` | ❌ **DO NOT RUN** — hangs; was launched once this session and had to be force-stopped via `TaskStop` |
| Commit the doc/memory changes | ❌ PENDING — user has not asked for a commit yet |
| sms-api overlay pin (`newTag: 0.3.0`) + final smscdk cutover (Plan 10 WS-8 steps 2–3) | ❓ UNCONFIRMED — lives in the separate `sms-api` repo/cluster, not checkable from here |
| Scope/ownership of 5 new untracked `demos/v2ecoli/` files (see below) | ❓ UNRESOLVED — apparent unlogged demo-prep work, no owning plan entry yet |

## Key files touched (this session)

- **EDITED (unstaged, uncommitted):**
  - `.todo/MANIFEST.md` — item 7 status line flipped from "awaits proceed" to
    `✅ DONE + RELEASED (v0.3.0, 2026-07-15)`. (This file also already had a
    local, pre-session, unstaged addition of the item-10 block — not touched
    further this session.)
  - `NEXT_STEPS.md` — header rewritten (was dated 2026-07-14, described PR
    #467 as in-review); added a note about the 5 untracked `demos/v2ecoli/`
    files; state table's Plan-7/PR-467 rows flipped to merged/released.
- **EDITED (untracked new file, not yet committed):**
  - `.todo/plans/10-release-improved-visual-feedback-smscdk.md` — top status
    line updated from "PLANNED... awaits proceed" to reflect WS-2…WS-7 done
    and WS-8 (tag+release) done, with WS-8 steps 2–3 (sms-api side) flagged
    unconfirmed. (This file itself is pre-existing/untracked from before this
    session — the status-line edit is this session's only change to it.)
- **NOT touched, still dirty from before this session (out of scope,
  confirmed via Plan 10's own text):** `pyproject.toml` / `uv.lock` — a
  `pbg-ptools` path-dep change (`../pbg-ptools` → `../../sms/pbg-ptools`).
  **Verified this session that `../pbg-ptools` does not exist locally but
  `../../sms/pbg-ptools` does** — so this uncommitted edit is not stray, it's
  a real environment fix, just deliberately left uncommitted/unbundled per
  Plan 10's explicit scope note.
- **NOT touched, still untracked from before this session:** 5 new files
  under `demos/v2ecoli/` — see "Unresolved" below.
- **Memory (outside repo)**
  `~/.claude/projects/-Users-alexanderpatrie-vivarium-app-vivarium-workbench/memory/`:
  - NEW `project_v0.3.0_release_shipped.md` — durable record of the
    merge/release facts above, so future sessions don't re-propose "PR #467
    needs review."
  - NEW `feedback_never_run_full_pytest.md` — **hard rule**, see below.
  - `MEMORY.md` updated with both pointers.

## Key design decisions / gotchas

- **NEVER run the bare/unscoped `pytest` (or `uv run pytest`, `uv run
  --no-sync pytest -q` with no path/`-k` filter) in this repo.** There is a
  known unresolved hanging test (Plan 10 WS-1 names the leading suspects:
  unbounded `urllib.request.urlopen()` in `tests/test_visualization_endpoints.py`
  and `vivarium_workbench/lib/cli_runs.py:26`). A full run was launched this
  session during checkpoint verification and had to be force-killed via
  `TaskStop` after running past its 300s foreground window — **the user's
  reaction was severe; treat this as an absolute rule, not a soft
  preference.** `[[feedback_never_run_full_pytest]]`
- **Bare `uv run pytest` also fails outright** on the CURRENT (uncommitted)
  `pyproject.toml` state because of the `pbg-ptools` path-dep pointing at a
  path that only resolves with `--no-sync` skipping a resync — use `uv run
  --no-sync pytest <scoped target>`.
- **v0.3.0 is out; stop treating PR #467/Plan 7 as open.** Any future
  planning (Plan 8, Plan 9, new work) should branch off current `main`/
  `demo-verification`, not reference the old pre-merge state.
- **sms-api-side release tail is unconfirmed.** Don't assume the live smscdk
  deployment is actually pinned to `0.3.0` yet — that requires checking
  `~/sms/sms-api` (separate repo) before either claiming it's done or
  proposing a redundant deploy step.

## Verification

- `gh pr view 467` → `state: MERGED`, `mergedAt: 2026-07-15T16:30:00Z`.
- `gh release list` → `v0.3.0` latest, published `2026-07-15T16:30:15Z`.
- `grep version pyproject.toml` → `0.3.0`.
- Scoped pytest (CI-equivalent set from Plan 10 WS-5):
  `uv run --no-sync pytest tests/test_payload_models.py tests/test_generate_ts.py
  tests/test_api_app.py tests/test_investigation_status.py -q` →
  **510 passed, 51 warnings in 56.84s.** (Warnings are pre-existing —
  optional-dependency import skips (`cplex`, v2ecoli subpackages) and one
  legacy-`status`-field deprecation notice, none new.)
- `node tests/js/test_progress_track.js` → **ok**.
- Full unscoped `pytest` — **deliberately NOT run** (see gotcha above); one
  was started this session and stopped via `TaskStop` before completion, no
  result to report.

## Next steps (priority order)

1. **Nothing is blocking** — this was a doc-reconciliation session, not a
   feature session. Ask the user what's next; don't assume "record the demo"
   (the old `NEXT_STEPS.md` framing) is still the priority without checking.
2. If continuing the demo-recording thread: figure out what the 5 new
   untracked files are for — `demos/v2ecoli/WALKTHROUGH-local-remote-compute.md`,
   `demos/v2ecoli/scripts/remote_commit_run.py`,
   `demos/v2ecoli/speaker/NARRATION.md`, `demos/v2ecoli/speaker/three_layers.png`
   — they look like a new "local dashboard + remote compute" demo variant
   plus a full narration script, but have no owning `.todo/plans/*` entry and
   weren't mentioned as this session's work. **Ask the user before assuming
   scope or committing them.**
3. If the user wants the doc fixes committed: stage narrowly (`.todo/MANIFEST.md`,
   `.todo/plans/10-release-improved-visual-feedback-smscdk.md`, `NEXT_STEPS.md`
   only — never `-A`/`.`, per Plan 10's own out-of-scope note about the dirty
   `pyproject.toml`/`uv.lock`) and hand over a commit one-liner; do not commit
   directly.
4. If picking the release thread back up: confirm sms-api's overlay state
   (`~/sms/sms-api` — `kustomize/overlays/sms-api-stanford*/kustomization.yaml`
   `newTag`) before assuming Plan 10 WS-8 steps 2–3 are done or need doing.
5. Parked, unchanged from before: Plan 9 (Omics Viewer 0.5.9 fix, refined,
   awaits "proceed"), Plan 8 (auto-param PTools, gated on Plan 9/6), backlog
   item (a) pydantic-settings `environment.py` (untracked WIP).

## Quick reference

- Branch: `demo-verification`. Upstream detached (see above).
- Tests: **never bare `pytest`**. Scoped CI-equivalent:
  `uv run --no-sync pytest tests/test_payload_models.py tests/test_generate_ts.py
  tests/test_api_app.py tests/test_investigation_status.py -q` + `node
  tests/js/test_progress_track.js`.
- Version: `pyproject.toml` → `0.3.0` (matches latest tag/release).
- Serve: `vivarium-workbench serve --workspace <path>` (must run from a
  workspace venv with this package installed editable, per `AGENTS.md`).

## Related memory
`[[project_v0.3.0_release_shipped]]`, `[[feedback_never_run_full_pytest]]`,
`[[project_stanford_zshrc_commands]]`, `[[project_cisco_empties_etc_hosts]]`,
`[[project_demo_latest_v2ecoli_main_constraint]]`,
`[[project_pinned_build_remote_runs]]`, `[[project_ptools_segment7_routing]]`,
`[[feedback_suggest_commits]]`, `[[feedback_pr_review_required]]`,
`[[feedback_do_not_commit]]`.
