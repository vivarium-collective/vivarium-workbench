# Next Steps — MVP is SHIPPABLE; next session = MERGE/RELEASE the coupled pair into `main`, THEN tackle plans 7 & 9

**Updated:** 2026-07-14 (planning session). Ground truth is `SAVE_SLOT.md`.

**⭐ SEQUENCING DECISION (2026-07-14):** the demo on `demo-v2ecoli` is a **shippable
MVP** for a smooth, completely-remote demo (7½ of 8 segments verify live; the only
gap is the PTools Omics-Viewer **paint**, a soft-fail that opens the overview
unpainted with no error). We are **flipping the old order**. Next session **STARTS
with the version bumps / merges / releases into the `main` branches of BOTH repos**
(sms-api + dashboard `./`) — i.e. ship the MVP. **ONLY AFTER that lands** do we
begin plans **7** (progress-bar/spinner UX) and **9** (Omics 0.5.9 fix) on
dedicated post-merge branch(es)/PR(s). The narrated recording can be done now with
the Omics beat skipped/caveated, or re-shot after plan 9 — presenter's call.

The demo is delivered jointly by two spiritually-coupled branches — dashboard
`demo-v2ecoli` ↔ sms-api `patch/db-filter` (memory `[[project_demo_branch_coupling]]`).
Segment 6 Part B ("Run on remote") is **proven live**; Segment 7 figures + TSV
delivery **PASS**; Segment 8 recap **verified live**; pod runs `7a9620c`, 1/1.

## State

| Item | Status |
|---|---|
| Bugs 1/2/3 (study-detail, CSRF allowlist, bigraph-loom in image) | ✅ deployed + verified |
| Pinned-build remote runs (Segment 6 Part B) | ✅ deployed + **proven live** (sim 211 → Ray MNP → landed) |
| WALKTHROUGH Segment 6 rewrite | ✅ done |
| Segment 7 — `/reports/` figure prefix (dashboard) | ✅ committed + **pushed** `demo-v2ecoli` `7a9620c` (`b33b7ca..7a9620c`) |
| Segment 7 — Omics Viewer overlay seed (sms-api) | ✅ committed + **pushed** `patch/db-filter` `c2a337cd` (`00d456f2..c2a337cd`) |
| WALKTHROUGH Segment 7 text | ✅ written |
| **Segment 7 deploy** — Action 1 (push) | ✅ **DONE** — both branches level with origin |
| **Segment 7 deploy** — Action 2 (build image `7a9620c`) | ✅ **DONE** — gh run `29299423533` success; GHCR tag `7a9620c` confirmed; build-provenance tag `build/demo-v2ecoli/7a9620c` pushed |
| **Segment 7 deploy** — Action 3 (repoint overlay `newTag` `72e00b8`→`7a9620c` + roll out) | ✅ **DONE** (2026-07-14) — pod 1/1 on `7a9620c`; seed env stamped; headless pre-verify GREEN |
| **Segment 7 live-verify** — interactive figures | ✅ **PASS** (2026-07-14, headless) — 5/5 figures 200 under `/workbench/reports/...`, root → 404 |
| **Segment 7 live-verify** — TSV HTTP delivery | ✅ **PASS** — dashboard serves omics TSV (200, ~355 KB) at the PTools-fetched path |
| **Segment 7 live-verify** — Omics Viewer auto-load | ❌ **FAIL on `sms-ptools:0.5.9`** — 0.5.9 reads `multiomics=t&datafile=<key>`, not our `omics=t&url=`. **DEFERRED fix** → plan 9 |
| **Segment 8 (Wrap-up)** recap figures | ✅ **VERIFIED live** (2026-07-14, headless) — 173 proc / 7 pkgs, 9 ParCa steps, 8 investigations, 58 viz, 36 runs (35 seeded + 1 landed). In-browser click-through recap = presenter's action at demo time |
| `Last verified` stamp extended to all 8 segments | ✅ **DONE** — stamp now covers 1–8 (with Segment 7 Omics-Launch 0.5.9 caveat) |
| **⭐ MVP merge/release into `main` (both repos)** | ⏳ **STEP 1 — next session STARTS here** (PR #465 review→merge + sms-api `patch/db-filter`→main + version-bump releases + overlay repoint) |
| Plan 7 — pinned-run progress UX | ⏳ **STEP 2** (post-merge branch/PR); REFINED, awaits "proceed" |
| Plan 9 — Omics Viewer 0.5.9 semi-manual upload fix | ⏳ **STEP 2** (post-merge branch/PR); REFINED, awaits "proceed" |
| Narrated screen recording (the deliverable) | ⏳ post-merge — Omics beat skipped/caveated now, or re-shot after plan 9 |

## Next session — START HERE (iterative action protocol; standby between actions)

**STEP 1 — SHIP THE MVP: version bumps / merges / releases into `main` (BOTH repos).**
This is the first thing next session does. The coupled pair merges in tandem — the
remote demo drifts if only one side lands.
   - **1a — dashboard `./`:** PR #465 (`demo-v2ecoli`→`main`) is OPEN + MERGEABLE but
     `REVIEW_REQUIRED` → get the review approval first (NO auto-merge,
     `[[feedback_pr_review_required]]` + `[[project_ssh_commit_signing]]`), then merge.
   - **1b — sms-api:** open `patch/db-filter`→`main`, review, merge.
   - **1c — version-bump releases** into each `main` (cut the release tags), then
     **repoint the k8s overlay `newTag`** from the dev SHA `7a9620c` to the release
     tag. Keep the untracked `vivarium_workbench/environment.py` (pydantic-settings
     WIP, backlog item a) OUT of the merge — provenance stays clean.
   - Optional at this point: **record** the narrated demo with the Omics beat
     skipped/caveated (soft-fail = unpainted overview, no error), or re-shoot that
     ~15 s beat after STEP 2's plan 9. Presenter's call.

**STEP 2 — ONLY AFTER the MVP has shipped: tackle plans 7 & 9** on dedicated
post-merge branch(es)/PR(s) off the freshly-released `main`. Both are refined + await
"proceed".
   - **Plan 7 — progress-bar/spinner UX** for the pinned-build run card
     (`.todo/plans/7-pinned-run-progress-ux.md`, REFINED via /plan). Dashboard-only,
     additive (`static/progress-track.{js,css}` + a wrapped `_renderRemoteRunProgress`
     adapter + soft-fill tween). HYBRID model (milestone bar + honest time-based
     soft-fill + spinner). Full design: `~/.claude/plans/mellow-tinkering-moth.md`.
   - **Plan 9 — Omics Viewer 0.5.9 fix** (`.todo/plans/9-omics-viewer-0.5.9-register-launch.md`,
     REFINED via /plan). Frictionless semi-manual upload: Launch opens the clean
     overview + dashboard serves the TSV with a one-click download + "upload in the
     Omics dialog" prompt; `ui.ptools_scheme` switch in `pbg_ptools.workbench_viewers`
     + a `_launchViewer` helper. Spans THREE repos (adds `pbg-ptools`). Needs tunnel
     + a local `pbg-ptools` clone. Full plan: `~/.claude/plans/validated-roaming-catmull.md`.

### Already DONE (context — no action needed)
- **Segment 7 deploy** (Actions 1–3): pushed both branches; built image `7a9620c`
  (gh run `29299423533`); overlay repointed `72e00b8`→`7a9620c`; pod 1/1, seed env
  stamped; headless pre-verify GREEN.
- **Segment 7 live-verify:** interactive figures PASS (5/5 → 200 under
  `/workbench/reports/...`); TSV HTTP delivery PASS; Omics auto-load FAIL on
  `sms-ptools:0.5.9` (soft-fail → plan 9). `[[project_ptools_segment7_routing]]`.
- **Segment 8 (Wrap-up):** recap figures verified live (173 proc / 7 pkgs, 9 ParCa
  steps, 8 investigation summaries, 58 viz, 36 runs). `WALKTHROUGH.md` `Last verified`
  stamp covers all 8 segments.

## Parked backlog (not blocking the recording)

`.todo/_backlog.md`: (a) pydantic-settings for all env-var definitions — impl lands
in `vivarium_workbench/environment.py` (untracked WIP), mirroring
`~/sms/sms-api/sms_api/config.py`. The two queued requests are now **promoted to
tracked plans** (Prompt Queue drained 2026-07-14): (b) progress-bar/spinner UX for
the pinned-build run card → `.todo/plans/7-pinned-run-progress-ux.md`;
auto-parameterize embedded PTools from a study's Exports `.tsv` →
`.todo/plans/8-autoparam-ptools-from-exports-tsv.md` (gated on #6 WS-2 delivery
mechanism). Both await "proceed" before code.

**See also:** `SAVE_SLOT.md`, `.todo/plans/6-segment7-ptools-omics-deploy-verify.md`,
`.todo/plans/5-pinned-build-remote-runs.md`, `.todo/MANIFEST.md`.
