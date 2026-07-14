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
| **Segment 7 live-verify** (browser, through tunnel) | ⏳ **NEXT** — WS-2 (needs your browser) |
| Segments 7–8 full browser drive + stamp | ⏳ (`Last verified` currently covers 1–6 only) |
| Narrated screen recording (the deliverable) | ⏳ after all 8 segments pass |
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
2. **Live-verify Segment 7 in the browser** (through the tunnel):
   - Interactive Plotly figures on a study's Visualizations tab resolve under
     `/workbench/reports/figures/...` (not the co-tenant PTools 404 at the root).
   - **PTools Omics Viewer "Launch"** paints the study's exported omics TSV onto the
     EcoCyc Cellular Overview. **OPEN RISK:** remote PTools is `sms-ptools:0.5.9`
     but the `celOv.shtml?…&url=` auto-load is documented against 0.8.2. If 0.5.9
     ignores `url=`, fall back to mounting the workspace into the ptools pod at
     `/ptools-data` and keep `ptools_data_dir` (see `[[project_ptools_segment7_routing]]`).
3. **Segment 8 (Wrap-up)** — architecture-pillars recap; then extend the
   `Last verified` stamp in `WALKTHROUGH.md` to cover all 8 segments.
4. **Record the narrated screen recording** (editable) — the actual deliverable.
5. **WS-F (post-completion):** PR #465 (`demo-v2ecoli`→`main`, open/REVIEW_REQUIRED)
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
