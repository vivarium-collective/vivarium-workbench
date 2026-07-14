# Plan 8 — Auto-parameterize the embedded Pathway Tools Omics Viewer from a study's Exports `.tsv`

## Name

Feat: when a study's **Exports** tab has a relevant `.tsv` result file,
auto-parameterize the embedded Pathway Tools (PTools) Omics Viewer in the
workbench so the Cellular Overview loads pre-painted with that study's data — on
the remote AWS GovCloud `sms-api-stanford-test` (smsvpctest) deployment of sms-api.

Linked tasks: directly adjacent to #6 (Segment 7 Omics Viewer wiring) — #6
established HTTP delivery of a study TSV to the in-cluster ptools pod
(`ui.dashboard_public_base_url` + cleared `ui.ptools_data_dir`) and the
`celOv.shtml?…&url=` auto-load param. This plan generalizes that from a single
seeded study to **any study whose Exports carry a suitable `.tsv`**. Spans the
dashboard (`demo-v2ecoli`) and possibly the sms-api overlay/seed
(`patch/db-filter`). No v2ecoli changes. Source: `.todo/_backlog.md` Prompt Queue.

## Status: 📋 PLANNED — approved to create as a todo item; NOT yet implemented

Awaits "proceed" before code. Captured now to drain the backlog Prompt Queue into
a tracked plan. **Gated on #6 WS-2 outcome**: the OPEN RISK (`sms-ptools:0.5.9` may
ignore `celOv.shtml?…&url=`; 0.8.2 fallback = mount workspace at `/ptools-data`)
determines whether delivery is HTTP `url=` or filesystem — this plan must inherit
whichever mechanism #6 proves live.

## Problem

The Omics Viewer Launch is currently wired for one seeded study. A study's Exports
tab may already contain a `.tsv` (e.g. exported omics counts/concentrations) that
is exactly the data PTools' Cellular Overview wants. Nothing today detects that
`.tsv` and hands it to the embedded viewer automatically.

## Desired outcome

- For a study whose Exports include a PTools-compatible `.tsv`, the Omics Viewer
  Launch (or an auto-load on the Analyses/Exports view) opens the EcoCyc Cellular
  Overview already parameterized with that file — no manual URL wrangling.
- Works on the remote smsvpctest deployment (in-cluster HTTP delivery of the TSV
  to the ptools pod, per #6), respecting the base-path + co-tenant PTools routing.
- No-ops cleanly when no relevant `.tsv` is present, or in the read-only bundle.

## Workstreams

### WS-1 — Detect + classify Exports `.tsv`
1. From the Exports listing, identify which `.tsv` files are PTools-Omics
   compatible (schema/column heuristic; define what "relevant" means).
2. Resolve the browser-reachable URL for the chosen file under the workbench
   base path (`/workbench/...`) and the in-cluster URL for the ptools fetch.

### WS-2 — Parameterize the viewer
1. Build the `celOv.shtml?…&url=<tsv>` (or filesystem `ptools_data_dir`, per #6's
   proven mechanism) target from the detected file.
2. Surface it as an auto-parameterized Launch (and/or auto-load) on the study's
   Analyses/Exports view.

### WS-3 — Remote wiring
1. Ensure the in-cluster ptools pod can fetch the TSV (same-namespace Service URL,
   base path included) — reuse the #6 seed overlay pattern; extend only if a
   per-study path is needed beyond the single seeded case.

### WS-4 — Verify (browser, through the tunnel)
1. Pick a study with a real Exports `.tsv`, Launch, and confirm the Cellular
   Overview paints that study's data on smsvpctest.
2. Confirm no-op behavior for studies without a compatible `.tsv`.

## Notes / references

- **⛔ CONSTRAINT:** Pathway Tools inside `sms-ptools` is **proprietary third-party
  software — never edit/patch/adjust it**. "Auto-parameterize" means building the
  launch URL / driving PTools' **existing** endpoints from OUR code, never
  modifying PTools. Inherits the launch mechanism from plan #9 (see its constraint
  banner).
- Depends on the mechanism proven in #9 (register-then-launch on 0.5.9) — do not
  pick a delivery scheme here until that is settled live.
- memory `[[project_ptools_segment7_routing]]`, `[[project_demo_branch_coupling]]`.
- `.todo/plans/6-segment7-ptools-omics-deploy-verify.md` is the parent context.
