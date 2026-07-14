# Next Steps — Segment 6 Part B PROVEN LIVE; P7 doc rewrite + segments 7–8 + PRs remain

**Updated:** 2026-07-13 (execution session). Ground truth is `SAVE_SLOT.md`. The
demo's last blocker — Segment 6 Part B "Run on remote" — is **fixed, deployed, and
proven end-to-end live** via the pinned-build model (plan 5). A real run (sim 211)
resolved the pinned build, ran ParCa + a 3-node transient Ray cluster on GovCloud,
completed, and landed into the Simulations DB (now 36 runs) — all through the UI
with **no GitHub login**.

## State

| Item | Status |
|---|---|
| Pinned-build feature (dashboard) | ✅ `demo-v2ecoli` `72e00b84`; 14 tests, mypy clean |
| Pinned env (sms-api overlay) | ✅ `patch/db-filter` `2ef52c0a` |
| Image build + deploy | ✅ `72e00b8` built, deployed (`newTag` 72e00b8), rolled out |
| Headless verify | ✅ `/api/remote-run-config` → pinned:true, commit 70b5ec3, sim 69 |
| **Part B live e2e** | ✅ **PROVEN** — sim 211 → Ray MNP → landed `baseline__1783986815__08c5be` |
| P7 — WALKTHROUGH Segment 6 rewrite | ✅ **DONE** (pinned-build flow + drift fixes + stamp) |
| Full 8-segment WS-E drive (Seg 7–8) | ⏳ **next — needs browser; awaiting user's word** |
| WS-F PRs (no auto-merge) | ⏳ |

## Next (awaiting user's word to proceed)

1. **Finish the full 8-segment WS-E drive** — needs you at the browser:
   - **Segment 7 (Analyses)** — 58 viz classes, PTools omics viewer, `demo()` previews.
   - **Segment 8 (Wrap-up)** — rapid recap of the architecture pillars.
   After both pass, update the `Last verified` stamp to cover all 8 segments.
2. **WS-F PRs** (no auto-merge): PR #465 (demo-v2ecoli→main) + sms-api
   patch/db-filter→main. Then cut a release tag + repoint the overlay from the dev
   SHA (`72e00b8`) to it.

## P7 — DONE (2026-07-13)

`demos/v2ecoli/WALKTHROUGH.md` Segment 6 rewritten as the pinned-build flow:
Part A drift corrected (remote-☁️ = 0 until a live run lands; emitter sqlite 3 /
parquet 6 / xarray 3 / unrecorded 23; status 31 completed + 1 "complete" + 3
failed); Part B = pinned build (main @ 70b5ec3, no push/login) → ParCa → 3-node
Ray MNP cluster → land; corrected the "landed = local origin" claim to ray
`remote_origin`; timing table + offline numbers (52→35) fixed; pinned-mode
troubleshooting rows added; header stamped.

## Why this model (recap)

Only Phase 1 (build) pushed git / needed login. Pinning to the latest **built**
`main` simulator skips Phase 1: submit sims against the prebuilt simulator
(resolved from in-cluster sms-api). Drops Blockers B (git ownership) & C
(protected-main push); replaces A (login) with a declarative config gate since
submit/land do no GitHub write. See `.todo/plans/5-pinned-build-remote-runs.md`.

**See also:** `SAVE_SLOT.md`, `.todo/plans/5-pinned-build-remote-runs.md`, `.todo/MANIFEST.md`.
