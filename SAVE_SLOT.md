# Checkpoint: PLAN 7 SHIPPED TO PR #467 (in review) + LIVE-VERIFIED — next session = record the demo

## ⭐ RESUME HERE (2026-07-14, post plan-7 implementation)

**Plan 7 (production-grade progress bar for the pinned-build run card) is DONE.**
Implemented WS-1…WS-4, committed, pushed, and opened as **PR #467**
(`feat/improved-visual-feedback` → `main`), then **live-verified end-to-end**: with the
`sms-proxy.sh -s smsvpctest` tunnel up and a local serve on this branch, clicking **Run
on remote** drove the new milestone bar through a **real pinned run** on the live
sms-api/Ray backend (Path B, no deploy). PR #467 is OPEN + MERGEABLE + `REVIEW_REQUIRED`.

**NEXT SESSION FOCUS:** execute the **narrated screen recording / demo** per
`demos/v2ecoli/WALKTHROUGH.md` (8-segment remote GovCloud walkthrough, all segments
verified live 2026-07-14). See `NEXT_STEPS.md` for the pre-flight + caveat.

## What shipped this session

Branch `feat/improved-visual-feedback`, 5 commits ahead of `main`:
- `18ccf8d` **feat(progress)** — the plan-7 implementation (7 files, +616/−5).
- `075bbb9` **docs(plan7)** — live-e2e-passed note.

### Files (plan 7)
- **NEW** `vivarium_workbench/static/progress-track.js` — dependency-free `ProgressTrack`
  IIFE + `module.exports`. Dual-shape model: `stages` (milestone bar + honest time-based
  soft-fill `min(elapsed/typical,0.9)` + spinner) and `measured` (`value/max`). Pure
  helpers `softFraction`/`measuredFraction`/`stageFraction`/`html`. a11y
  (`role=progressbar` + `aria-*` + `aria-live`) + reduced-motion. `render`/`tick` diff on
  a `data-sig` that excludes soft progress (tween repaints only the active fill).
- **NEW** `vivarium_workbench/static/progress-track.css` — namespaced `.ptrack-*`,
  palette matches `.inv-run-*`.
- **NEW** `tests/js/test_progress_track.js` — 24 assertions, `node` green.
- **EDITED** `vivarium_workbench/static/study-detail.js` — `_renderRemoteRunProgress` is
  now a thin adapter (`_rrDeriveStages`, stages `resolve→submit→queued→running→done→landed`),
  `setInterval(250ms)` soft-fill tween (`_startRrTween`/`_stopRrTween`), `_RR_TYPICAL_MS`
  (queued 480s, running 300s), `_rrSoftFor` stage-start tracking, legacy fallback
  `_renderRemoteRunProgressLegacy`, `[.rr-track][.rr-extras]` shell. `phase` threaded from
  `_pollRun` + unreachable-retry so Queued≠Running.
- **EDITED** `vivarium_workbench/templates/study-detail.html` — 2 asset includes
  (`progress-track.css` after `style.css` line 6; `progress-track.js` before
  `study-detail.js`) + snapshot-safety comment at the hide site.
- **EDITED** `tests/test_study_detail_page.py` — `test_study_detail_page_includes_progress_track_assets`.

## Verification (all done)

- `node tests/js/test_progress_track.js` → **ok** (softFraction clamp/cap/monotonic,
  measuredFraction, stageFraction, a11y, failed class/no-spinner, measured step text, sig stability).
- `uv run --no-sync pytest -q tests/test_study_detail_page.py::test_study_detail_page_includes_progress_track_assets` → **pass**.
- Headless walk → bar **4→22→42→58→83→100%** monotonic, spinner on active, snap-to-100 at Landed.
- **Live e2e → PASS** (Path B: local serve `:8099` + tunnel; real pinned run driven).
- **Pre-existing failures (NOT regressions, confirmed via stash):** 10 `test_study_detail_page`
  (legacy fixture lacks baseline/variants/runs.db) + 1 `test_remote_run_panel::test_view_run_button_routes…`
  + broken `tests/js/test_chain_block.js` (requires the pre-rename `vivarium_dashboard/static/` path).

## Deployment status — UNCHANGED (important)

Nothing was deployed. PR #467 is source-only; the `smsvpctest` pod still serves the
prior image (old text stepper). Plan-7 bar is visible only via Path B (proven) OR after a
deliberate merge→build→overlay-repoint→roll (NOT done — a coworker may be mid-deployment;
do not deploy without explicit go-ahead).

## Next steps (priority order)

1. **NEXT SESSION: record the demo** per `demos/v2ecoli/WALKTHROUGH.md` (pre-flight in
   `NEXT_STEPS.md`; Segment 7 Omics-Launch caveat = `[[project_ptools_segment7_routing]]`).
2. **Get PR #467 reviewed → merge** (no auto-merge). Only gate left for plan 7.
3. After merge: plans 8 + 9 branch off a fresh `main` (both refined, await "proceed").

## Quick reference

- Branch `feat/improved-visual-feedback` (off `main`@0.2.0); 5 ahead of `origin/main`.
- Tests: `uv run --no-sync pytest -q` (bare `uv run` fails — `../pbg-ptools` path dep) +
  `node tests/js/test_progress_track.js`.
- Cluster env: `export AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 KUBECONFIG=/Users/alexanderpatrie/.kube/kube_stanford_test.yml`.
- Tunnel: `~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest` → `localhost:8080`.
- Commits are SSH-signed; if locked → ask user to `ssh-add` via `!` (`[[project_ssh_commit_signing]]`).

## Related
- `NEXT_STEPS.md`, `demos/v2ecoli/WALKTHROUGH.md`, `.todo/plans/7-pinned-run-progress-ux.md`,
  PR #467. memory `[[project_plan7_progress_ux_pr467]]`, `[[project_pinned_build_remote_runs]]`,
  `[[project_index_html_render_pipeline]]`, `[[feedback_pr_review_required]]`.
