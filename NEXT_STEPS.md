# Next Steps — Plan 7 merged + released as v0.3.0; NEXT SESSION = record the v2ecoli demo

**Updated:** 2026-07-17. PR #467 merged (`1c51df2`, 2026-07-15) and released as
`v0.3.0` (tag + GitHub Release published 2026-07-15T16:30:15Z); `pyproject.toml`
on `main` reads `0.3.0`. Plan 10 WS-2…WS-8 (repo-side) are done — see
`.todo/plans/10-release-improved-visual-feedback-smscdk.md`. **Unconfirmed
from this repo:** whether the sms-api overlay pin (`newTag: 0.3.0` on
`sms-api-stanford`/`-test`) and final live cutover (Plan 10 WS-8 steps 2–3)
have landed — check `~/sms/sms-api` before assuming so. Ground truth for the
pre-release history below is still `SAVE_SLOT.md`; note it predates the merge.

There are also five new untracked files under `demos/v2ecoli/` on the current
branch (`WALKTHROUGH-local-remote-compute.md`, `scripts/remote_commit_run.py`,
`speaker/NARRATION.md`, `speaker/three_layers.png`) not yet reflected in
`.todo/MANIFEST.md` — apparent unlogged demo-prep work; confirm scope/owning
plan before the next session's recording pass.

## ⭐ NEXT SESSION — record the screen-recording / execute the demo

The next session's focus is the **actual demo execution / narrated screen recording**
described in **`demos/v2ecoli/WALKTHROUGH.md`** (8-segment remote GovCloud walkthrough,
`Last verified` 2026-07-14). Everything the recording needs is already live-verified:

- **Pre-flight** (WALKTHROUGH §1): `stanford test` (AWS SSO) → Terminal 1
  `sms-proxy.sh -s smsvpctest` (localhost:8080 → ALB:80) → Terminal 2 verify + open
  `localhost:8080/workbench`.
- **Segments 1–8** all pass live. **Segment 6 Part B** (pinned-build remote run) now
  also shows the **new plan-7 progress bar** — optionally re-shoot that beat to feature it.
- **One documented caveat** (WALKTHROUGH header + `[[project_ptools_segment7_routing]]`):
  Segment 7 PTools Omics Viewer **Launch** does NOT auto-paint on deployed
  `sms-ptools:0.5.9` (scheme mismatch → **plan 9**). Demo it with the caveat or skip the
  Launch; the interactive figures + omics-TSV delivery DO work.

Presenter's call whether to record now (plan-7 bar visible only if the deployment carries
this branch — it does NOT yet; see below) or after PR #467 merges + a release/deploy.

### ⭐ Proposed next-session sequence (pre-merge deploy → record → merge → release)

Mirror yesterday's demo-v2ecoli flow: prove it on the real deployment on a **dev tag
first**, then do the proper release. **Do NOT start until the coworker confirms they are
not mid-deployment** — the overlay `newTag` repoint + pod roll is a shared-resource
mutation (`[[project_hpc_integration_state]]`).

1. **Pre-merge tag/deploy of THIS branch** (throwaway dev build, NOT a substitute for
   review): build the image at `feat/improved-visual-feedback` HEAD → repoint the k8s
   overlay `newTag` to that SHA → roll the pod.
2. **Manual-test on the live deployment under `/workbench`.** Highest-value check: the
   plan-7 assets resolve under the ALB prefix — `GET /workbench/assets/progress-track.js`
   + `.css` = 200, and the bar renders on a Simulations-tab pinned run. **This is the
   specific risk Path B did NOT cover** (Path B ran at root, not under the `/workbench`
   base-path where the render's `/assets/` rewrite + `_apply_live_base_path` run — the
   same layer that caused the Segment 7 `/reports/` misroute, `[[project_ptools_segment7_routing]]`).
   Optionally **record** the demo now against this dev deployment.
3. **PR #467 → review → merge** (no auto-merge, `[[feedback_pr_review_required]]`).
4. **Proper version bump + release** into `main` (cut the release tag), then **repoint the
   overlay `newTag` from the dev SHA to the release tag** and roll — i.e. the deployment
   ends on a released tag, not a dev SHA (same as the 0.2.0 flow, PR #466).

Net: two overlay repoints (dev SHA for the test, release tag after merge) — expected and
matches yesterday's pattern.

## State

| Item | Status |
|---|---|
| **Plan 7 — pinned-run progress UX** | ✅ **DONE + RELEASED**; PR #467 MERGED (`1c51df2`, 2026-07-15), shipped in `v0.3.0` |
| Plan 7 — JS unit + pytest wiring | ✅ green (`node tests/js/test_progress_track.js`, `test_study_detail_page.py`) |
| Plan 7 — headless walk + live e2e | ✅ bar drove a REAL pinned run via Path B (local serve + tunnel, no deploy) |
| **PR #467 review → merge** | ✅ merged 2026-07-15; `v0.3.0` tagged + released same day |
| Demo recording (`demos/v2ecoli/WALKTHROUGH.md`) | ⏳ **NEXT SESSION** — all 8 segments verified live |
| Plan 9 — Omics Viewer 0.5.9 fix | ⏳ REFINED, awaits "proceed"; 3 repos + tunnel + local `pbg-ptools` clone |
| Plan 8 — auto-param PTools from Exports `.tsv` | ⏳ gated on plan #6 WS-2 delivery mechanism |
| Backlog (a) — pydantic-settings `environment.py` | ⏳ untracked WIP; keep OUT of plan-7 commits |

## Important: the deployment does NOT carry plan 7 yet

PR #467 is source-only. The running `smsvpctest` pod still serves the previous image
(old text stepper). To see the plan-7 bar in a recording you either (a) run **Path B**
(local serve on this branch + tunnel — proven), or (b) merge PR #467 → build image →
repoint the k8s overlay `newTag` → roll the pod (a deliberate, separate deploy step —
NOT done, do not do without explicit go-ahead; a coworker may be mid-deployment).

### Path B recipe (zero-deploy, reusable)
```
VIVARIUM_WORKBENCH_REMOTE_PINNED=1 \
VIVARIUM_WORKBENCH_REMOTE_REPO_URL=https://github.com/vivarium-collective/v2ecoli \
VIVARIUM_WORKBENCH_REMOTE_BRANCH=main \
.venv/bin/python -m vivarium_workbench.cli serve --workspace <ws> --port 8099
# then: sms-proxy.sh -s smsvpctest (→ localhost:8080); SMS_API_BASE defaults there.
# For the component ONLY (no cluster): skip the tunnel, drive window.ProgressTrack from the console.
```

## Parked backlog
`.todo/_backlog.md` item (a) pydantic-settings → `vivarium_workbench/environment.py`
(untracked WIP). Plans 8 + 9 are refined and await "proceed" (branch off a fresh `main`
after PR #467 merges).

**See also:** `SAVE_SLOT.md`, `demos/v2ecoli/WALKTHROUGH.md`,
`.todo/plans/7-pinned-run-progress-ux.md`, `.todo/MANIFEST.md`, PR #467.
