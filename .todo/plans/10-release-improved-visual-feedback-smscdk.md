# Plan 10 — Pre-merge verify, merge, and release feat/improved-visual-feedback to smscdk

## Name

Ops: close remaining doc/PR gaps on `feat/improved-visual-feedback` (PR #467,
Plan 7), container-verify it against `sms-api-stanford` (smscdk) without
touching the shared namespace durably, merge, then cut a real release and
deploy it there for good.

Linked tasks: closes out #7 (Plan 7's code is done; this is the remaining
"deploy-verified → merged → released" tail). Spans THREE repos:
`vivarium-workbench` (this repo — gap fixes, tag, release), `sms-api` (peer
repo, owns the `sms-api-stanford`/`sms-api-stanford-test` kustomize overlays
that pin `vivarium-workbench`'s image tag — no code changes, config-pin only),
and the `sms-api-stanford` k8s namespace itself (smscdk stack, live
deployment). No v2ecoli changes.

## Status: ✅ WS-2…WS-7 DONE — PR #467 merged (`1c51df2`, 2026-07-15T16:30:00Z); WS-8 tag `v0.3.0` + GitHub Release published (2026-07-15T16:30:15Z), `pyproject.toml` on `main` reads `0.3.0`. **WS-8 steps 2–3 (sms-api overlay pin to `0.3.0` on `sms-api-stanford`/`-test`, final live cutover) are in the separate `sms-api` repo/cluster — unconfirmed from this repo alone; check `~/sms/sms-api` before assuming done.**

Plan approved via `/plan` (`~/.claude/plans/quirky-snuggling-crystal.md`,
2026-07-15). Two open judgment calls were resolved with the user before
finalizing:
- **WS-4 deploy style = smoke-and-revert** (not soak): pin the pre-merge
  candidate image on smscdk just long enough to verify it, then immediately
  restore the current `0.2.0` pin. No durable sms-api commit for this step.
- **WS-8 sms-api overlay pin = small PR** to sms-api (not a direct commit),
  matching the user's usual "PRs need review" bar for shared infra, even
  though sms-api's own history has precedent for a direct commit here
  (`5fd48bf1`).

**Readjusted twice same day, before execution started:** (1) the unrelated
dirty `pyproject.toml`/`uv.lock` in the working tree are permanently out of
scope for this plan — see the "Out of scope, explicitly" bullet under Key
facts; (2) first pass — WS-1's doc/bookkeeping edits were made stage-now/
commit-later; (3) second pass (this one) — WS-1 is now **fully deferred**,
not just its commit: no edits, no staging, nothing happens on WS-1 until
execution reaches WS-5. **Execution order for this "proceed": WS-2 → WS-3 →
WS-4 → WS-5 (runs all of WS-1, then WS-5's own content) → WS-6 → WS-7.**

## Problem / context

PR #467's own body already claims code-complete + a passed live-tunnel e2e
("Path B": local `vivarium-workbench serve` + `sms-proxy.sh` tunnel straight
to the live sms-api/Ray backend — no image build/deploy involved). That claim
checked out under direct inspection (`node tests/js/test_progress_track.js`
24/24 green; `test_study_detail_page_includes_progress_track_assets` passes;
the 10+1 other test failures in that area are the same pre-existing ones the
plan-7 doc already names — confirmed identical with the branch's edits
stashed, no new regressions). But Path B only verified the UI/JS logic
against a live backend — it never verified the **container image** (build,
static-asset packaging, `/workbench` base-path routing) that's what actually
reaches the smscdk demo coworkers and the boss will see. That gap, plus a
handful of stale docs, is what this plan closes.

## Key facts (verified, not assumed)

- **Only CI gate**: `.github/workflows/types.yml` (mypy + 4 test files) —
  currently ✅ green on PR #467. No full-pytest CI gate exists, so the known
  pytest-hang issue cannot block merge.
- **Build trigger**: `.github/workflows/build-and-push.yml` fires on
  `release: published` **or** manual `workflow_dispatch` — a bare `git tag`
  push triggers nothing. Image tag = whatever version string the trigger is
  given.
- **Exact precedent for a pre-merge verification cycle**: annotated tag
  `build/demo-v2ecoli/7a9620c` (2026-07-14) — built via `workflow_dispatch`
  off a feature branch, deployed by pinning `newTag` in sms-api's
  `sms-api-stanford-test` overlay, tag message explicitly notes
  "build-provenance tag only ... does not trigger release CI." WS-2/3/4
  below replicate that pattern, aimed at `sms-api-stanford` (smscdk).
- **sms-api's documented Release Protocol** (`~/sms/sms-api/CLAUDE.md`):
  version bump lands on the feature branch before merge; the git tag is cut
  after merge, on `main`. Answers the "tag before or after merge" question —
  don't tag pre-merge (WS-6), just bump `pyproject.toml`.
- **Deployment is manual, not GitOps**: `kubectl kustomize
  kustomize/overlays/<overlay> | kubectl apply -f -` + `kubectl rollout
  restart deployment/<name> -n <ns>`, run by a human. No automated namespace
  lock — coordination is manual (`kubectl get pods`/`deployments` before
  touching it, a heads-up to coworkers).
- **Blast radius is narrower than it first looks**: this change only ever
  touches the `workbench` Deployment (single replica, RWO EBS+SQLite) in
  `sms-api-stanford`. It never touches the `api` Deployment or sms-api's own
  version — coworkers doing anything that isn't "use the live workbench UI
  on smscdk" are unaffected. But *any* `workbench` rollout, even ours, causes
  a real (brief) downtime window for whoever *is* on it at that moment.
- Repo convention confirmed: PRs merge via merge-commit, not squash (e.g.
  "Merge pull request #469 from ..." in `git log`).
- Current state: `pyproject.toml` version `0.2.0` == latest tag `v0.2.0` ==
  sms-api's current pin in both `sms-api-stanford` and
  `sms-api-stanford-test` overlays (three-way alignment, confirmed).
- **Out of scope, explicitly (user, 2026-07-15):** the working tree carries
  two unrelated uncommitted changes — a local `pyproject.toml` edit
  (`pbg-ptools` path dep `../pbg-ptools` → `../../sms/pbg-ptools`) and a
  `uv.lock` regen (staged-deleted + untracked replacement). Neither is part
  of Plan 10. Leave both untouched for the duration of this plan — every WS
  below works from **committed** state only; stage/commit steps use targeted
  `git add <path>` (never `-A`/`.`) so these two files are never swept in.
  Not re-flagged again unless something in this plan would actually collide
  with them.

## Workstreams

### WS-1 — Close gaps, make PR #467 accurate (fully deferred — executes inside WS-5, not now)
No functional code changes needed (Plan 7's own code/tests are complete and
verified). Doc/bookkeeping fixes only. **Reordered twice (user, 2026-07-15):
first to stage-now/commit-later, then further to fully deferred — do not
edit, stage, or touch any of these files until execution reaches WS-5.**
Execution for this pass starts at **WS-2**, runs WS-2 → WS-3 → WS-4, and only
then comes back to do all of WS-1's edits (as part of WS-5), since none of
this is code and none of it affects the image WS-2/3/4 builds and verifies
off current HEAD. This list is kept here as the content spec; WS-5 is where
it actually gets executed:
- `.todo/MANIFEST.md:113` — item 7's status line still reads "📋 PLANNED...
  awaits proceed before code," contradicting the plan-7 file itself (✅ all
  WS done, live-verified) and `NEXT_STEPS.md`. Update to reflect reality.
- `demos/v2ecoli/README.md` — Quick Start (~line 40) + Troubleshooting
  (~line 117) still say `stanford test`, left over from before the smscdk
  retarget (`WALKTHROUGH.md` already correctly uses bare `stanford`).
  `stanford test` points `KUBECONFIG` at the wrong cluster for any
  in-session `kubectl` troubleshooting. Fix both to `stanford`.
- `SAVE_SLOT.md` — stale `❌ PENDING` line for an action commit `69da5247`
  (which touched `SAVE_SLOT.md` itself) already resolved. Update to `✅ DONE`.
- Pytest-hang guard (`d231e5ec`, `timeout = 120` in `pyproject.toml`) has no
  documented root cause. Strongest suspects found by grep (not reproduced —
  reproducing means running the exact unbounded suite the guard exists to
  contain): `tests/test_visualization_endpoints.py` (5 bare
  `urllib.request.urlopen()` calls, no `timeout=`, lines 67/137/172/189 +
  a few in loops ~1256–1575) and `vivarium_workbench/lib/cli_runs.py:26`
  (source-level unbounded `urlopen`). **Approved (user, 2026-07-15) to bundle
  into the WS-5 doc-fix commit, conditional on it being safe** (small,
  mechanical `timeout=` additions only — no behavior change beyond bounding
  the hang; skip/flag instead if anything looks more involved than that).

PR #467 description edit moved to WS-5: rather than editing the PR body
twice (once for the "Also included" smscdk demo-retarget gap, once for
WS-4's "Container-deploy verified" note), do both in a single `gh pr edit`
pass once WS-4's findings are known.

Execution note: **do nothing here yet.** No file edits, no `git add`, no
commit. When execution reaches WS-5: edit these files, stage with targeted
`git add <path>` (never `-A`/`.`, see the out-of-scope dirty-tree note
above), then commit in one pass (hand over the copy-paste `git commit -m
"..."` one-liner per the usual protocol — never commit/push directly).

### WS-2/WS-3 — Build-provenance tag + CI build (reordered)
A tag push alone triggers nothing here, so build first, tag second (mirrors
how `build/demo-v2ecoli/7a9620c`'s message was written — it cites a run ID
and deploy outcome that don't exist until after the build):
1. Capture current HEAD short-sha (WS-1 hasn't started — doc-only, so it
   doesn't touch what gets built; no dependency on WS-1 in any form).
2. `gh workflow run build-and-push.yml --ref feat/improved-visual-feedback -f version=<shortsha>`, watch to completion.
3. Create annotated tag `build/improved-visual-feedback/<shortsha>` (image,
   commit, run id, deploy outcome — filled in after WS-4 runs), push it.

### WS-4 — Pre-merge verification deploy to sms-api-stanford (smscdk), smoke-and-revert
Highest blast-radius step — every sub-step needs explicit go-ahead in the
moment, not just this plan's approval:
1. Pre-flight: `kubectl get deployments/pods -n sms-api-stanford`, record
   the current `workbench` image (expect `...vivarium-workbench:0.2.0`) as
   the exact revert target. Heads-up to coworkers first (no scripted lock
   exists to detect this for us).
2. Locally edit (don't commit) sms-api's
   `kustomize/overlays/sms-api-stanford/kustomization.yaml` → `newTag:
   <shortsha>`.
3. `kubectl kustomize ... | kubectl apply -f -`, `rollout restart
   deployment/workbench -n sms-api-stanford`, `rollout status`.
4. Verify through the `sms-proxy.sh -s smscdk` tunnel: in-pod marker grep
   confirming the new asset landed, then drive a real (or representative)
   pinned-build run and confirm the milestone bar renders/advances in the
   actual deployed container — the thing Path B couldn't test.
5. Revert immediately: `newTag` back to `0.2.0`, re-apply, restart, re-verify
   rollback. sms-api git history stays untouched (nothing was ever
   committed).
6. Fill in the WS-2/3 tag's `deploy:` line with the real outcome, push it.

### WS-5 — Confirm value, transition to "merge-ready"
- Summarize WS-4's findings; if the real container/base-path routing
  surfaced anything Path B couldn't see, fix it (small targeted change +
  another quick WS-2→4 loop on the fix commit).
- **Now execute all of WS-1**: edit `.todo/MANIFEST.md`,
  `demos/v2ecoli/README.md`, `SAVE_SLOT.md` per WS-1's spec above (pytest-hang
  timeout follow-up stays gated on separate explicit go-ahead). Stage with
  targeted `git add <path>` (never `-A`/`.`), then commit in a single pass
  (hand over the copy-paste `git commit -m "..."` one-liner per the usual
  protocol — never commit/push directly), then push.
- Single consolidated `gh pr edit` pass on PR #467's body covering both: the
  "Also included" section (the diff also carries the smscdk demo-retarget
  work from commits `d3a30c85`/`69da5247` — `WALKTHROUGH.md`, `README.md`,
  new `ensure_latest_main_build.sh`, retired
  `verify_demo.py`/`populate_demo_runs.py`/`prep_remote_*.py`/`PLAN.md`/
  `NOTES.md`, confirmed via `gh pr diff 467 --name-only`) and the
  "Container-deploy verified" note from WS-4's findings.
- Final scoped sanity pass (CI-identical set + the two new test files —
  never a blind full-suite run):
  `uv run --frozen pytest tests/test_payload_models.py tests/test_generate_ts.py tests/test_api_app.py tests/test_investigation_status.py -q`,
  `uv run pytest tests/test_study_detail_page.py -k progress_track -q`,
  `node tests/js/test_progress_track.js`.

### WS-6 — Version bump (pre-merge, no tag)
Per sms-api's own Release Protocol: bump lands on the branch before merge;
tag comes after merge, on `main`. Bump `pyproject.toml` `0.2.0` → `0.3.0`
(minor: additive feature, no breaking changes), commit (one-liner handed
over, not run directly), update PR body/title if warranted.

### WS-7 — STOP and await "proceed" to merge
Confirm PR #467 is `MERGEABLE`, `types` check ✅, no unmerged upstream
`main` drift. **Check in and stand by — no merge without explicit
go-ahead.** On go-ahead: `gh pr merge 467 --merge` (merge-commit, matching
repo convention).

### WS-8 — Post-merge release + final deploy
1. On `vivarium-workbench` main: `git tag v0.3.0 && git push origin v0.3.0`,
   `gh release create v0.3.0 --title "v0.3.0 — pinned-run progress bar"
   --notes-file <notes.md>`. Publishing the release **auto-triggers**
   `build-and-push.yml` (unlike sms-api's, which is manual-only) → pushes
   `ghcr.io/vivarium-collective/vivarium-workbench:0.3.0`. Watch to
   completion.
2. Durable sms-api pin **via a small PR**: bump `newTag: 0.3.0` in both
   `sms-api-stanford` (the one that matters — smscdk) and
   `sms-api-stanford-test` (parity, matching precedent commit `5fd48bf1`).
   Review + merge per usual bar.
3. Final cutover on smscdk — same mechanics as WS-4 steps 1/3/4, but this
   time it's the permanent state, not a revert. Rollback path if needed:
   re-pin `newTag: 0.2.0` via a follow-up sms-api commit/PR (the known-good
   state stays git-tracked).
4. Close the loop: `.todo/MANIFEST.md` item 7 → `✅ DONE + RELEASED`, note
   the release in `NEXT_STEPS.md`.

## Files touched (summary)
- **This repo, WS-1 content, executed entirely inside WS-5**: `.todo/MANIFEST.md`,
  `demos/v2ecoli/README.md`, `SAVE_SLOT.md`; optionally
  `tests/test_visualization_endpoints.py` + `vivarium_workbench/lib/cli_runs.py`
  (timeout hardening, gated on explicit go-ahead).
- **This repo, WS-5**: edits + commits WS-1's files above; PR #467 body (via
  one consolidated `gh pr edit` — "Also included" + "Container-deploy
  verified").
- **This repo, WS-6**: `pyproject.toml` (version bump).
- **This repo, WS-8**: no new files — a git tag + GitHub Release.
- **sms-api, WS-4**: none durable (local-only edit, reverted).
- **sms-api, WS-8**: `kustomize/overlays/sms-api-stanford/kustomization.yaml`,
  `kustomize/overlays/sms-api-stanford-test/kustomization.yaml` (via PR).

## Notes / references
- Full phase-by-phase plan with exact commands: `~/.claude/plans/quirky-snuggling-crystal.md`.
- Precedent tag: `build/demo-v2ecoli/7a9620c` (this repo).
- sms-api Release Protocol: `~/sms/sms-api/CLAUDE.md` "Release Protocol" +
  "Stanford-Test Deploy Loop" sections.
- `deploy/README.md` (this repo): confirms vivarium-workbench intentionally
  owns no k8s manifests — all deployment config lives in sms-api/kustomize.
- Gate: literal word "proceed" required before any WS below WS-1 begins
  executing (per standing todo protocol) — WS-4/WS-7/WS-8 each carry their
  own additional in-the-moment confirmation gates beyond that, given their
  blast radius (shared namespace, PR merge, published release).