# Next Steps — Segment 7 (PTools/Omics) DEPLOYING; live-verify 7–8, then record

**Updated:** 2026-07-13 (execution session — deploy in flight). Ground truth is
`SAVE_SLOT.md`. The demo is delivered jointly by two spiritually-coupled branches
— dashboard `demo-v2ecoli` ↔ sms-api `patch/db-filter` (see memory
`[[project_demo_branch_coupling]]`). Segment 6 Part B ("Run on remote") is
**proven live**. Segment 7 (Analyses / PTools Omics Viewer) is **coded + pushed on
both branches**; deploy Action 1 (push) ✅ done, Action 2 (build image `7a9620c`,
gh run `29299423533`) ✅ done + GHCR-confirmed + provenance-tagged, Action 3
(overlay repoint `72e00b8`→`7a9620c` + roll out) ⏳ next.

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
| Omics Viewer 0.5.9 semi-manual upload fix | ⏳ **NEXT (deferred slot)** — plan 9 (REFINED via /plan, approved); awaits "proceed" |
| `Last verified` stamp extended to all 8 segments | ✅ **DONE** — stamp now covers 1–8 (with Segment 7 Omics-Launch 0.5.9 caveat) |
| Narrated screen recording (the deliverable) | ⏳ after plan 9 |
| WS-F PRs + version-bump releases into `main` (both repos) | ⏳ post-completion |

## Next (iterative action protocol — proceed one action at a time, standby between)

1. **Deploy Segment 7 across the coupled pair** — ✅ **DONE (2026-07-14)**:
   - ✅ **Action 1** — pushed `demo-v2ecoli` `7a9620c` + `patch/db-filter` `c2a337cd`.
   - ✅ **Action 2** — built workbench image `7a9620c` (gh run `29299423533`,
     success); GHCR tag `7a9620c` confirmed present; build-provenance tag
     `build/demo-v2ecoli/7a9620c` created + pushed (WS-F semver release still deferred).
   - ✅ **Action 3** — repointed the overlay `newTag` `72e00b8`→`7a9620c`; `kubectl
     apply -k` → `workbench configured`; rollout done (pod 1/1 on `7a9620c`). Seed
     picked up `DASHBOARD_PUBLIC_BASE_URL` + cleared `ptools_data_dir`. Headless
     pre-verify GREEN (basePath + `/reports/` shim serving; pinned-config OK).
2. **Segment 7 live-verify — DONE headless (2026-07-14):** interactive figures
   PASS (5/5 → 200 under `/workbench/reports/...`, root → 404); TSV HTTP delivery
   PASS. Omics Viewer auto-load **FAIL on `sms-ptools:0.5.9`** — root-caused to a
   scheme mismatch (0.5.9 auto-loads via `multiomics=t&datafile=<registered-key>`,
   not the launcher's `omics=t&url=<tsv>`; the `/ptools-data` fallback also fails
   since both feed the ignored `url=`). See `[[project_ptools_segment7_routing]]`.
3. **Segment 8 (Wrap-up)** — ✅ **DONE (2026-07-14)**: all recap figures re-verified
   live (173 proc / 7 pkgs, 9 ParCa steps, 8 investigations summaries, 58 viz, 36
   runs). `WALKTHROUGH.md` stamp extended to all 8 segments; recap "35 runs" → 36
   (live callback). In-browser tab click-through is the presenter's action at demo
   time — no code/verify gap remains.
4. **Omics Viewer 0.5.9 fix** — ⏳ **NEXT** (the deferred slot: after Segment 8 ✅,
   before recording). Plan `.todo/plans/9-omics-viewer-0.5.9-register-launch.md`
   (**REFINED via /plan, approved** — frictionless semi-manual upload: Launch opens
   clean overview + dashboard serves the TSV with a one-click download + "upload in
   the Omics dialog" prompt; `ui.ptools_scheme` switch in `pbg_ptools.workbench_viewers`
   + a `_launchViewer` helper. Needs tunnel + a local `pbg-ptools` clone). Awaits
   "proceed". Full plan: `~/.claude/plans/validated-roaming-catmull.md`.
5. **Record the narrated screen recording** (editable) — the actual deliverable,
   after plan 9.
6. **WS-F (post-completion):** PR #465 (`demo-v2ecoli`→`main`, open/REVIEW_REQUIRED)
   + open sms-api `patch/db-filter`→`main`; review, then version-bump releases into
   each `main`; repoint the overlay from the dev SHA to the release tag. No
   auto-merge (`[[feedback_pr_review_required]]`).

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
