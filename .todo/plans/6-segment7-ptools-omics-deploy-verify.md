# Plan 6 — Deploy + live-verify Segment 7 (PTools Omics Viewer + interactive figures), then close out the demo

## Name

Feat/verify: land Segment 7 across the coupled pair (dashboard `demo-v2ecoli` +
sms-api `patch/db-filter`) — deploy the `/reports/` figure prefix + the Omics
Viewer overlay seed, live-verify Segments 7–8 through the tunnel, then record the
narrated screen recording and open the post-completion release PRs.

Linked tasks: continues #5 (Segment 6 Part B proven live). The two coupled
branches jointly deliver the whole demo — see memory
`[[project_demo_branch_coupling]]`. No `v2ecoli` changes.

## Status: 🔄 EXECUTING — DEPLOYED to sms-api-stanford-test (rollout in flight); live-verify + record remain

**2026-07-14:** WS-1 complete — Segment 7 code is deployed. Action 1 (push both
branches) ✅; Action 2 (build workbench image `7a9620c`, gh run `29299423533`,
GHCR-confirmed HTTP 200 + provenance tag `build/demo-v2ecoli/7a9620c`) ✅; Action 3
(overlay `newTag` `72e00b8`→`7a9620c`, `kubectl apply -k` → `deployment.apps/workbench
configured`) ✅ applied, rollout to pod 1/1 in flight. Next: WS-2 live-verify.

**2026-07-13 (baseline):** Segment 7 code committed:
- dashboard `demo-v2ecoli` `7a9620c` — `_apply_live_base_path` base-path-prefixes
  `/reports/` embed URLs so interactive figures resolve to the dashboard, not the
  co-tenant PTools at the ALB root. WALKTHROUGH Segment 7 written.
- sms-api `patch/db-filter` `c2a337cd` — seed `ui.dashboard_public_base_url` +
  clear `ui.ptools_data_dir` so the in-cluster PTools Omics Viewer overlay fetches
  study TSVs over HTTP.

## Workstreams

### WS-1 — Deploy the coupled pair ✅ (rollout in flight)
1. ✅ Push `demo-v2ecoli` `7a9620c` and `patch/db-filter` `c2a337cd`.
2. ✅ Build a new workbench image (gh action) including `7a9620c` — gh run
   `29299423533` success; GHCR manifest for `7a9620c` returns HTTP 200.
3. ✅ Repoint the overlay `newTag` `72e00b8`→`7a9620c` in
   `kustomize/overlays/sms-api-stanford-test/kustomization.yaml`; `kubectl apply -k`
   → `deployment.apps/workbench configured`; rollout to `sms-api-stanford-test`
   in flight (confirm pod 1/1). Re-seed picks up `DASHBOARD_PUBLIC_BASE_URL` +
   cleared `ptools_data_dir`.

### WS-2 — Live-verify Segment 7 (headless through the tunnel, 2026-07-14)
1. ✅ **Interactive figures PASS** — all 5 `showcase-2-baseline-figures` figures
   200 under `/workbench/reports/figures/...`; identical path at the ALB root →
   404 (the exact collision the base-path prefix fixes). Renders inline in-browser.
2. ✅ **TSV HTTP delivery PASS** — dashboard serves the omics TSV (200, ~355 KB) at
   `.../workbench/workspace/studies/<slug>/ptools/ptools_proteins.tsv`, the path
   the PTools pod fetches server-side.
2b. ❌ **OMICS AUTO-LOAD FAIL ON 0.5.9 — open risk resolved NEGATIVELY.** The
   launcher emits the 0.8.2 scheme `celOv.shtml?omics=t&url=<tsv>&class=&column1=`,
   but 0.5.9's `pathwayTools-overviews.js` has NO `url=`/`case "omics"` reader —
   its only omics auto-load path is `case "multiomics":` → reads
   `datafile`/`datakeys` and fetches `/get-registered-multiomics-data?key=<datafile>`
   (server-registered-KEY flow). celOv HTML is byte-identical with/without our
   params. **The `/ptools-data` filesystem fallback does NOT help** — both delivery
   modes feed the same ignored `url=`. Fix: (a) upgrade remote PTools to 0.8.2
   (blocked — no newer `sms-ptools` image on ghcr); or (b) adapt
   `pbg_ptools.workbench_viewers` to register the TSV then launch
   `?multiomics=t&datafile=<key>`. See `[[project_ptools_segment7_routing]]`.
3. `demo()` previews on viz classes render (browser, not yet exercised).

**DECISION (2026-07-14):** keep the Omics Viewer Launch IN the demo, but DEFER the
0.5.9 fix — do it **after Segment 8 (WS-3) is complete and before the recording
(WS-4)**. Tracked as **`.todo/plans/9-omics-viewer-0.5.9-register-launch.md`**
(register-then-launch: POST the TSV to PTools' **own** register endpoint, get a
key, launch `?multiomics=t&datafile=<key>`). #6 WS-2b stays open until plan 9's
WS-4 passes. New Segment-7 execution order: **WS-3 (Segment 8) → plan 9 (Omics
fix) → WS-4 (record)**.

**⛔ CONSTRAINT:** Pathway Tools inside `sms-ptools` is **proprietary third-party
software — we MUST NOT edit/patch/adjust it in any way**. The entire fix lives in
OUR launcher (`pbg_ptools.workbench_viewers`) driving PTools' existing unmodified
endpoints; if a paint requires changing PTools itself, that path is out of bounds
(fall back to an image upgrade or descope). Full constraint in plan 9.

### WS-3 — Segment 8 + acceptance stamp ✅ (2026-07-14)
1. ✅ Segment 8 (Wrap-up) recap figures re-verified against the LIVE deployment
   (headless, through the tunnel): **173 processes / 7 packages** (registry warm),
   **9 ParCa Steps** (initialize…final_adjustments), **8 investigations** (summaries
   view; the raw `/api/investigations` = 41 is a different, uncurated view — not a
   drift), **58 viz classes**, composites baseline/Millard/PDMP present (28 total),
   **Simulations DB = 36** (35 seeded + 1 landed live; status 32 completed / 1
   complete / 3 failed; origin 1 remote / 35 local). Only stale figure was the flat
   "35 runs" in the recap bullet → updated to 36 with a live-callback note.
2. ✅ `Last verified` stamp in `WALKTHROUGH.md` extended to all 8 segments (with the
   Segment 7 Omics-Launch 0.5.9 caveat called out).
   NOTE: numbers verified via API probes through the tunnel; the in-browser rapid
   tab click-through recap itself is the presenter's action at demo time.

### WS-4 — Record
Record the narrated screen recording (editable) — the deliverable. **Gated on
plan 9 (Omics fix) passing**, per the 2026-07-14 decision: order is WS-3 →
plan 9 → WS-4.

### WS-5 — Post-completion release (no auto-merge)
PR #465 (`demo-v2ecoli`→`main`, open/REVIEW_REQUIRED) + open sms-api
`patch/db-filter`→`main`; review; version-bump releases into each `main`; repoint
the overlay from the dev SHA to the release tag. `[[feedback_pr_review_required]]`.
