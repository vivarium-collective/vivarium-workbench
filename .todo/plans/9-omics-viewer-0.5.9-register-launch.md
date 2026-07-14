# Plan 9 — Make the PTools Omics Viewer Launch work on the deployed `sms-ptools:0.5.9` (register-then-launch)

## Name

Feat: adapt the workbench Omics Viewer launcher to Pathway Tools **0.5.9**'s
auto-load scheme so "Launch" actually paints a study's exported omics TSV onto the
EcoCyc Cellular Overview on the remote `sms-api-stanford-test` (smsvpctest)
deployment.

Linked tasks: closes the ❌ half of #6 WS-2 (the interactive-figures half already
PASSES live). Adjacent to #8 (auto-parameterize from Exports `.tsv`) — #8 should
consume whatever launch mechanism this plan proves. Spans `pbg-ptools`
(`pbg_ptools.workbench_viewers`, repo `vivarium-collective/pbg-ptools`) and
possibly the dashboard frontend + sms-api overlay. No v2ecoli changes.

## Status: 📋 PLANNED — DEFERRED by decision (2026-07-14)

**Sequencing (user decision):** keep the Omics Viewer Launch IN the demo, but do
this work **after Segment 8 is complete and before the narrated recording**.
Awaits "proceed" before code. Do not start until Segment 8 (#6 WS-3) is done.

## ⛔ HARD CONSTRAINT — Pathway Tools is PROPRIETARY THIRD-PARTY software

The Pathway Tools application inside the `sms-ptools` container is **NOT ours and
IS proprietary. We MUST NOT edit, patch, or adjust it in ANY way** — not its
source, not its JavaScript bundles, not its server config, not its templates.
Everything in this plan lives **entirely on our side** and interacts with PTools
**only through its existing, unmodified external interface**:
- our launcher code (`pbg_ptools.workbench_viewers`) — ours to change;
- PTools' **own** already-shipped HTTP endpoints/query-params (the same
  register/upload endpoint + `/get-registered-multiomics-data` its own UI uses) —
  we *call* them, we do not alter them;
- container-level knobs (image tag, k8s volume mounts, env) — infra, not the app.

If the only way to make the Omics Launch paint turns out to require changing
Pathway Tools itself, that path is **out of bounds** — fall back to upgrading the
`sms-ptools` image to a version whose shipped scheme already fits (see
Alternatives), or descope the Launch. Reading PTools' shipped JS to *understand*
its contract (as done in the root-cause) is fine; modifying it is not.

## Problem (root-caused live, 2026-07-14)

The launcher emits the **0.8.2** URL scheme
`celOv.shtml?omics=t&url=<tsv>&class=<cls>&column1=<cols>`. On the deployed
`sms-ptools:0.5.9`:
- `celOv.shtml` returns **byte-identical** HTML with or without omics params — it's
  a static shell; omics handling is entirely client-side JS.
- In `pathwayTools-overviews.js` (915 KB) the ONLY omics auto-load path is the
  query dispatcher `case "multiomics":` → `replayMultiOmicsParam`, which reads
  `urlParams.get('datafile')` + `get('datakeys')` and fetches
  `/get-registered-multiomics-data?key=<datafile>` — a **server-registered-data-KEY**
  flow. There is **zero** `.get('url')` / `case "omics"` in the entire bundle.
- ⇒ 0.5.9 silently ignores `omics=t`, `url=`, `class=`, `column1=`. The
  interactive figures + TSV HTTP delivery both work; only the auto-load is broken.
- **The `/ptools-data` filesystem fallback does NOT help** — both delivery modes in
  `build_ptools_launch_url` feed the same ignored `url=` param.

See memory `[[project_ptools_segment7_routing]]` for the full trace.

## Approach (register-then-launch, matching 0.5.9)

We make our launcher speak the request shape 0.5.9's **own, unmodified** UI already
uses (its JS registers omics data then references it by key). Nothing here changes
Pathway Tools — we only drive endpoints it already exposes.

1. **Discover the register/upload endpoint** on 0.5.9 that populates
   `/get-registered-multiomics-data?key=<key>` — i.e. how a TSV becomes a
   server-registered dataset with a `datafile` key (POST a FormData `file`, per the
   `replayMultiOmicsParam` upload path that builds a `FormData` and posts to the
   overview). Confirm the exact request against the live pod.
2. **Register the study TSV**: from the launcher (or a dashboard endpoint), POST the
   exported `ptools/*.tsv` to that endpoint, capture the returned key.
3. **Launch with the 0.5.9 scheme**:
   `celOv.shtml?multiomics=t&datafile=<key>&datakeys=<...>` (+ orgid). Keep the
   0.8.2 `url=` scheme available behind a version switch so a future PTools upgrade
   still works.
4. **Version-detect / config-gate**: pick the scheme from a
   `ui.ptools_omics_url_template` / a probed PTools version, defaulting to the
   scheme that matches the deployed image. Don't hardcode 0.5.9.

## Alternatives considered

- **Upgrade remote PTools to 0.8.2** — cleanest, but BLOCKED: CI builds only
  `sms-api`; no newer `sms-ptools` image exists on ghcr (the overlay pins 0.5.9 for
  exactly this reason). Revisit if an 0.8.x `sms-ptools` image gets published.

## Workstreams

### WS-1 — Probe 0.5.9's register/upload contract (live pod)
Trace `replayMultiOmicsParam`'s FormData POST target + `/get-registered-multiomics-data`;
reproduce a register→key→fetch round-trip by hand through the tunnel.

### WS-2 — Launcher change in `pbg_ptools.workbench_viewers`
Add the register-then-launch path + a version/config switch; keep 0.8.2 `url=`
behind the switch. Unit-test both URL builders.

### WS-3 — Wire + deploy
Update the coupled sms-api overlay/seed only if new config is needed; rebuild the
workbench image; roll out; re-verify.

### WS-4 — Verify (browser, through the tunnel)
Launch on `showcase-2-baseline-figures` and confirm the Cellular Overview paints
the study's omics data. This is the acceptance gate that #6 WS-2b left open.

## References
- memory `[[project_ptools_segment7_routing]]` (full JS trace + param evidence),
  `[[project_demo_branch_coupling]]`.
- `.todo/plans/6-segment7-ptools-omics-deploy-verify.md` WS-2b (the failing check),
  `.todo/plans/8-autoparam-ptools-from-exports-tsv.md` (consumes this mechanism).
