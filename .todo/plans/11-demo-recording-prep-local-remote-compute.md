# Plan 11 — Demo-recording prep: local-authoring/remote-compute walkthrough + narration script

## Name

Docs/tooling: log and land the demo-prep artifacts authored ahead of the
v2ecoli GovCloud demo recording — a new "local dashboard, remote compute"
companion walkthrough, a generalized register→build→run script, a full
word-for-word narration script, and a supporting image asset.

Linked tasks: prep work for the recording session described in
`NEXT_STEPS.md` ("NEXT SESSION — record the screen-recording / execute the
demo"); companion to `demos/v2ecoli/WALKTHROUGH.md` (the existing in-cluster
demo run-sheet) and Plan 5/6/7 (pinned-build remote runs, Segment 7/8,
progress UX) which these artifacts document/exercise. No `v2ecoli` changes.
Dashboard-repo-only (`vivarium-workbench`).

## Status: ✅ Artifacts authored (untracked, pre-existing from before this session) — this plan exists to log + commit them; no new content written here.

Discovered via `/orientation` (2026-07-17) as 5 untracked files with no
owning plan entry. Read in full and confirmed to be finished, coherent
prep work, not stray/orphaned WIP:

- `demos/v2ecoli/WALKTHROUGH-local-remote-compute.md` (174 lines) — sibling
  to `WALKTHROUGH.md` Appendix G. Documents "Path B" (per `NEXT_STEPS.md`
  terminology): run `vivarium-workbench serve` locally against a real
  `v2ecoli` checkout, but execute simulations remotely on GovCloud via
  `sms-proxy.sh -s smscdk` → sms-api. Distinct from both the full in-cluster
  presenter demo and the fully-offline local dev flow.
- `demos/v2ecoli/scripts/remote_commit_run.py` (211 lines) — generalizes
  `ensure_latest_main_build.sh` (which only gates the pinned v2ecoli@main
  build) into register→poll→run→land for an arbitrary repo@commit. Reuses
  `vivarium_workbench.lib.remote_run_views` directly (the same calls the
  dashboard's "Run on remote" card makes) so it can't drift from the UI.
- `demos/v2ecoli/speaker/NARRATION.md` (444 lines) — full word-for-word
  narration script for the ~20-min GovCloud demo recording, companion to
  `WALKTHROUGH.md`'s technical run-sheet. Numbers cited (173 processes/7
  packages, 28 composites, 8 investigations, 35→36 seeded runs, 58 viz
  classes) are last-verified 2026-07-14 — **flagged in the file itself** to
  re-confirm against the live deployment before recording.
- `demos/v2ecoli/speaker/three_layers.png` — supporting image asset
  (referenced by the narration/slide material).

## Why this plan exists

Per repo convention (`.todo/MANIFEST.md` mirrors `.todo/plans/*`), no
artifact should sit untracked with no owning plan. This plan's only job is
bookkeeping: log the above artifacts' purpose and scope, then commit them
under a normal, narrowly-scoped commit. It does not propose new work beyond
what's already written.

## Workstreams

### WS-1 — Log in MANIFEST (this pass)
Add an item 11 entry to `.todo/MANIFEST.md` pointing at this file, mirroring
the existing item format.

### WS-2 — Commit
Stage narrowly (targeted `git add <path>`, never `-A`/`.`): the doc
reconciliation already pending from last session
(`.todo/MANIFEST.md`, `NEXT_STEPS.md`, `SAVE_SLOT.md`,
`.todo/plans/10-release-improved-visual-feedback-smscdk.md`) plus this
plan's 5 new files. Explicitly **exclude** `pyproject.toml`/`uv.lock` (the
unrelated `pbg-ptools` path-dep fix + lockfile churn — out of scope per
Plan 10's own note). Hand over a commit one-liner; do not commit directly
without the user's go-ahead on the message.

### WS-3 — (out of scope for this plan) Execute the recording
Belongs to the `NEXT_STEPS.md` "next session" thread, not this plan. Once
WS-1/WS-2 land, the presenter chooses between the original in-cluster flow
(`WALKTHROUGH.md`) and the new local+remote variant
(`WALKTHROUGH-local-remote-compute.md` + `NARRATION.md`) for the actual
recording.

## Files touched (summary)
- **New (this plan, WS-1)**: `.todo/plans/11-demo-recording-prep-local-remote-compute.md` (this file).
- **Edited (WS-1)**: `.todo/MANIFEST.md` (new item 11 entry).
- **Committed as-is (WS-2, pre-existing content, not modified here)**:
  `demos/v2ecoli/WALKTHROUGH-local-remote-compute.md`,
  `demos/v2ecoli/scripts/remote_commit_run.py`,
  `demos/v2ecoli/speaker/NARRATION.md`,
  `demos/v2ecoli/speaker/three_layers.png`.
- **Also committed (WS-2, pre-existing uncommitted edits from last session,
  unrelated content but same commit for convenience)**: `NEXT_STEPS.md`,
  `SAVE_SLOT.md`, `.todo/plans/10-release-improved-visual-feedback-smscdk.md`.

## Notes / references
- `NEXT_STEPS.md` — states the next-session focus is the recording itself.
- `demos/v2ecoli/WALKTHROUGH.md` — the existing, live-verified 8-segment
  run-sheet this plan's artifacts are siblings/companions to.
- `[[project_v0.3.0_release_shipped]]`, `[[project_pinned_build_remote_runs]]`.
