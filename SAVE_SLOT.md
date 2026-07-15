# Checkpoint — 2026-07-14 (late session): demo pinned-build hardening on `feat/improved-visual-feedback`

## ⭐ RESUME HERE

**Branch policy (user, explicit):** ALL of this session's work stays on
**`feat/improved-visual-feedback`** and ships in **its PR (#467)** — **NOT** the
`demo-v2ecoli` branch. This branch will soon be **merged + released** the same way
`demo-v2ecoli` was (PR review → merge to `main` → version bump/release → overlay
repoint). Do not move these changes onto the demo branch.

**Primary goal is unchanged:** record the 8-segment remote GovCloud v2ecoli demo
(`demos/v2ecoli/WALKTHROUGH.md`). This session removed two blockers and locked in a
new non-negotiable constraint.

## Session goal

Unblock the demo on the newly-chosen `smscdk` stack and enforce that the pinned
remote-run build is ALWAYS the latest `vivarium-collective/v2ecoli` main.

## Progress table

| Item | Status |
|---|---|
| **Demo target switched to `smscdk` (sms-api-stanford)** — recorded as WALKTHROUGH top DECISION note | ✅ Done |
| **`smscdk` seeded** with `v2ecoli@main` simulator — `simulator_id 40`, build **completed** | ✅ Done |
| **Constraint: pinned build == latest v2ecoli main** — enforced + verified | ✅ Done (id 40 == live tip `a08e20b`) |
| **NEW helper** `demos/v2ecoli/scripts/ensure_latest_main_build.sh` — remote check→reseed→poll gate | ✅ Done (moved into `scripts/` by user reorg) |
| **WALKTHROUGH §1.1 "Pinned-build gate"** pre-flight step added | ✅ Done |
| **`/etc/hosts` truncation fixed** (Cisco Secure Client, 181s timer) via `chflags uchg` lock | ✅ Done (lock held past 2 cycles) |
| Plan-7 progress UX (PR #467) | ✅ CODE-COMPLETE + live-verified; PR OPEN, `REVIEW_REQUIRED` |
| **Concurrent demo-dir reorg (user, in-flight, UNCOMMITTED)** — old scripts staged-deleted, new `scripts/` dir | 🔄 In progress (user-driven) |
| Stale Appendix-G refs to deleted `prep_remote_build.py` (WALKTHROUGH lines ~475, ~551) | ❌ PENDING (decide: update or drop) |
| PR #467 review → merge → release | ❌ PENDING |
| Demo recording | ❌ PENDING (next focus once pre-flight passes on smscdk) |

## Key files touched (this session)

- **NEW** `demos/v2ecoli/scripts/ensure_latest_main_build.sh` — fully-remote,
  idempotent gate. Resolves `git ls-remote …/v2ecoli main`, compares to the
  sms-api's newest built `v2ecoli@main` commit; if stale, POSTs the live tip to
  `/core/v1/simulator/upload` and polls `/core/v1/simulator/status` to `completed`.
  Exit 0 only when built == latest main. No push / no login / no venv (v2ecoli is
  public; `SmsApiClient` sends no auth token). Honors `SMS_API_BASE` (default
  `localhost:8080`). Syntax-checked + live-run PASS.
- **EDITED** `demos/v2ecoli/WALKTHROUGH.md` — (a) top-of-file **DECISION** note:
  demo now targets `sms-api-stanford` / `smscdk` (not `smsvpctest`); (b) new
  **§1.1 Pinned-build gate** pre-flight; (c) fixed the gate path to `scripts/…`
  and removed the (now-deleted) `prep_remote_build.py` comparison in that note.

## Key design decisions / gotchas

- **Per-stack registries.** Each sms-api (`smscdk`, `smsvpctest`) has its OWN
  simulator registry. The v2ecoli build (`id 69`) existed only on `smsvpctest`;
  switching the demo to `smscdk` surfaced "no built simulator for …v2ecoli@main"
  — NOT a code/merge/deploy issue. Fixed by seeding `smscdk` (`id 40`).
- **Newest-BUILT ≠ live tip.** The pinned resolver picks the newest *built* entry,
  so it goes stale on any v2ecoli merge → the gate script exists to close that.
- **The deployed dashboard CANNOT build** (runs `REMOTE_PINNED=1`, resolve-only;
  and even non-pinned hits 3 gaps: no `GH_CLIENT_ID`, dubious `.git` ownership,
  protected `main`). Seed via the remote sms-api call, never the deployed UI.
- **`vivarium-dashboard` merges are IRRELEVANT** to the simulator build — different
  repo. Simulator = `vivarium-collective/v2ecoli` (the model).
- **`/etc/hosts` workaround is a `uchg` lock**, reversible with
  `sudo chflags nouchg /etc/hosts`; may not survive a reboot → re-run restore+lock
  as demo pre-flight. Cisco emptying it is a real bug → file an IT ticket. `sudo`
  only works in a real Terminal, not the Claude `!` prefix.
- **Concurrent reorg:** the user is moving demo scripts into `demos/v2ecoli/scripts/`
  and deleting the old offline-flow scripts (`prep_remote_build.py`,
  `populate_demo_runs.py`, `prep_remote_land.py`, `verify_demo.py`, plus
  `NOTES.md`/`PLAN.md`). These deletions are **staged but uncommitted**. Two
  pre-existing WALKTHROUGH references to `prep_remote_build.py` (Appendix G, ~L475
  + ~L551) are now stale — left for the user to resolve as part of the reorg.

## Verification

- `bash -n scripts/ensure_latest_main_build.sh` → **syntax OK**; live run →
  **MATCH ✓** (smscdk `id 40` == live main `a08e20b`), exit 0.
- `node tests/js/test_progress_track.js` → **ok** (plan-7 unchanged, still green).
- `tests/test_study_detail_page.py::…progress_track_assets` → **passed** (run
  earlier this session).
- Full `pytest` NOT re-run — no Python source changed this session (docs + shell +
  remote data only). Pre-existing non-regression failures still stand (see prior
  checkpoint: 10 legacy `test_study_detail_page`, 1 remote-run-panel, broken
  `test_chain_block.js`).
- Remote: `smscdk` `/core/v1/simulator/status?simulator_id=40` = `completed`,
  `error_message: null`.

## Next steps (priority order)

1. **Finish the demo-dir reorg + resolve stale refs:** commit the staged deletions
   and the new `scripts/` dir; update or drop the two Appendix-G
   `prep_remote_build.py` mentions (WALKTHROUGH ~L475, ~L551).
2. **Stage this session's work for PR #467** (per branch policy). Do NOT `git add`
   CLAUDE.md/AGENTS.md/Makefile/todo.md/.pr-body-*.md. Agent stages; user commits
   via a shown one-liner (`[[feedback_suggest_commits]]`).
3. **Demo pre-flight on `smscdk`:** `stanford test` → `sms-proxy.sh -s smscdk` →
   restore+`uchg` `/etc/hosts` if needed → `./demos/v2ecoli/scripts/ensure_latest_main_build.sh`
   (must exit 0) → open `localhost:8080/workbench`.
4. **Record the demo** (Segment-7 Omics-Launch caveat unknown on `smscdk` — verify
   PTools version there; `[[project_ptools_segment7_routing]]`).
5. **PR #467 review → merge → release** (no auto-merge; `[[feedback_pr_review_required]]`),
   then overlay `newTag` repoint like the 0.2.0 / demo-branch flow.

## Quick reference

- Branch `feat/improved-visual-feedback`, **7 ahead of `origin/main`**; uncommitted:
  1 modified (WALKTHROUGH.md) + staged deletions + untracked `scripts/`, `bugs/no-main-build.png`.
- Tests: `uv run --no-sync pytest -q` (bare `uv run` fails — `../pbg-ptools` path dep) +
  `node tests/js/test_progress_track.js`.
- Seed/verify pinned build (fully remote): `SMS_API_BASE=http://localhost:8080 ./demos/v2ecoli/scripts/ensure_latest_main_build.sh`.
- Manual pinned check: `git ls-remote https://github.com/vivarium-collective/v2ecoli main` vs
  `curl -s localhost:8080/core/v1/simulator/versions`.
- Cluster env: `AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 KUBECONFIG=…kube_stanford_test.yml`.
- Tunnel: `~/sms/sms-cdk/scripts/sms-proxy.sh -s smscdk` → `localhost:8080`.
- `/etc/hosts` guard (real Terminal, sudo): restore + `sudo chflags uchg /etc/hosts`; undo `nouchg`.

## Related memory
`[[project_demo_latest_v2ecoli_main_constraint]]`, `[[project_pinned_build_remote_runs]]`,
`[[project_cisco_empties_etc_hosts]]`, `[[project_plan7_progress_ux_pr467]]`,
`[[feedback_suggest_commits]]`, `[[feedback_pr_review_required]]`,
`[[feedback_do_not_commit]]`, `[[project_ptools_segment7_routing]]`.
