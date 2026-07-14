# Plan 6 — Deploy + live-verify Segment 7 (PTools Omics Viewer + interactive figures), then close out the demo

## Name

Feat/verify: land Segment 7 across the coupled pair (dashboard `demo-v2ecoli` +
sms-api `patch/db-filter`) — deploy the `/reports/` figure prefix + the Omics
Viewer overlay seed, live-verify Segments 7–8 through the tunnel, then record the
narrated screen recording and open the post-completion release PRs.

Linked tasks: continues #5 (Segment 6 Part B proven live). The two coupled
branches jointly deliver the whole demo — see memory
`[[project_demo_branch_coupling]]`. No `v2ecoli` changes.

## Status: 🔄 EXECUTING — code committed on both branches; deploy + verify + record remain

**2026-07-13:** Segment 7 code is committed but not yet deployed:
- dashboard `demo-v2ecoli` `7a9620c` — `_apply_live_base_path` base-path-prefixes
  `/reports/` embed URLs so interactive figures resolve to the dashboard, not the
  co-tenant PTools at the ALB root. WALKTHROUGH Segment 7 written.
- sms-api `patch/db-filter` `c2a337cd` — seed `ui.dashboard_public_base_url` +
  clear `ui.ptools_data_dir` so the in-cluster PTools Omics Viewer overlay fetches
  study TSVs over HTTP.

## Workstreams

### WS-1 — Deploy the coupled pair
1. Push `demo-v2ecoli` `7a9620c` and `patch/db-filter` `c2a337cd`.
2. Build a new workbench image (gh action) including `7a9620c`.
3. Repoint the overlay `newTag` `72e00b8`→new SHA; roll out to `sms-api-stanford-test`
   (confirm pod 1/1). Re-seed picks up `DASHBOARD_PUBLIC_BASE_URL` + cleared
   `ptools_data_dir`.

### WS-2 — Live-verify Segment 7 (browser, through the tunnel)
1. Interactive Plotly figures (e.g. showcase-2 dry-mass composition) on a study's
   Visualizations tab render inline under `/workbench/reports/figures/...` — no
   PTools 404 at the root.
2. PTools Omics Viewer **Launch** on `showcase-2-baseline-figures` paints the study's
   exported omics TSV onto the EcoCyc Cellular Overview.
   - **OPEN RISK:** remote PTools is `sms-ptools:0.5.9`; `celOv.shtml?…&url=` auto-load
     is documented against 0.8.2. If 0.5.9 ignores `url=`: mount the workspace into
     the ptools pod at `/ptools-data` and keep `ptools_data_dir` (filesystem
     delivery). See `[[project_ptools_segment7_routing]]`.
3. `demo()` previews on viz classes render.

### WS-3 — Segment 8 + acceptance stamp
1. Drive Segment 8 (Wrap-up / architecture-pillars recap).
2. Extend the `Last verified` stamp in `WALKTHROUGH.md` to cover all 8 segments.

### WS-4 — Record
Record the narrated screen recording (editable) — the deliverable.

### WS-5 — Post-completion release (no auto-merge)
PR #465 (`demo-v2ecoli`→`main`, open/REVIEW_REQUIRED) + open sms-api
`patch/db-filter`→`main`; review; version-bump releases into each `main`; repoint
the overlay from the dev SHA to the release tag. `[[feedback_pr_review_required]]`.
