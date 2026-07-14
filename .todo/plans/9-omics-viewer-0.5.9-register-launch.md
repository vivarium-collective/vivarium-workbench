# Plan 9 — Frictionless semi-manual Omics Viewer paint on PTools 0.5.9

> **Filename kept for stable cross-refs.** The original "register-then-launch"
> approach in this file's name was **invalidated** by live investigation (see
> "Corrected root cause"); the chosen approach is now **frictionless semi-manual
> upload** (user decision, 2026-07-14). Refined via `/plan` →
> `~/.claude/plans/validated-roaming-catmull.md` (approved).

## Name

Feat: make the PTools Omics Viewer **Launch** actually paint a study's exported
omics onto the EcoCyc Cellular Overview on the deployed `sms-ptools:0.5.9`, via a
**frictionless semi-manual upload** — Launch opens the overview and the dashboard
hands the presenter the study TSV with a one-click download + a clear "upload this
in the Omics dialog" prompt. One upload click paints it, reliably, today, using
only PTools' existing UI.

Linked tasks: closes the ❌ half of #6 WS-2b (interactive-figures half already
PASSES live). Adjacent to #8 (auto-parameterize from Exports `.tsv`) — #8 consumes
this launch mechanism. Spans **three** repos: `pbg-ptools`
(`vivarium-collective/pbg-ptools`, the launcher — a NEW third coupled repo),
`vivarium-dashboard@demo-v2ecoli` (frontend helper + Dockerfile ref), and
possibly `sms-api@patch/db-filter` (likely no change). No v2ecoli changes.

## Status: 📋 PLANNED + REFINED — DEFERRED slot (after Segment 8 ✅, before recording); awaits "proceed"

Segment 8 is done, so this is the next scheduled item. Implementation (WS-1 spike +
WS-4 verify) needs the tunnel/live pod (currently down → `aws sso login` +
`sms-proxy.sh -s smsvpctest`) and a local clone of `pbg-ptools`.

## ⛔ HARD CONSTRAINT — Pathway Tools is PROPRIETARY

Pathway Tools inside `sms-ptools` is proprietary third-party software — **we never
edit/patch it** (source, JS, config, templates). This approach only *uses* PTools'
existing Omics **upload dialog**; all new code is ours (our launcher + our
dashboard frontend). See `[[project_ptools_proprietary_constraint]]`.

## Corrected root cause (live investigation, 2026-07-14)

The original "register the TSV → get a key → launch `?multiomics=t&datafile=<key>`"
premise is **not viable on 0.5.9**:

- 0.5.9 has **no register-and-return-key endpoint**. Its only omics endpoints are
  `/overview-multi-omics-process` (paints the *currently-open* overview from a
  direct file upload) and `/save-omics-prefs`.
- `?multiomics=t&datafile=<key>` (`replayMultiOmicsParam` in
  `pathwayTools-overviews.js`) only *reads* server-registered data via
  `/get-registered-multiomics-data?key=<key>`; nothing in PTools' client mints such
  a key. So no URL we can build carries a fresh TSV into 0.5.9.
- The dashboard's viewer contract is URL-only ("compute a URL, open a tab") — it
  cannot deliver data to 0.5.9.

**What works:** PTools' own Omics upload dialog (`handleMultiOmicsSubmit` → POST
the TSV to `/overview-multi-omics-process` → paints). The semi-manual approach
signposts exactly that.

## Approach — scheme switch + frictionless-upload helper

Reuse what already exists:
- `pbg_ptools.workbench_viewers._launch()` / `build_ptools_launch_url()` **already
  return `tsv_url` + `available`** (the study's `ptools/*.tsv` relative paths) —
  the frontend ignores them today.
- The dashboard **already serves** those TSVs at
  `/workbench/workspace/studies/<slug>/ptools/*.tsv` (verified HTTP 200).
- Viewer contract + `GET /api/analysis-viewer/{uid}/launch`
  (`lib/analysis_viewers.py`) + frontend `_launchViewer`
  (`static/walkthrough.js:1433`, which `window.open`s `b.url`).
- `window.DataSource.basePath()` for base-path-aware download URLs.

## Changes by repo

### 1. `pbg-ptools` (third coupled repo) — `pbg_ptools/workbench_viewers.py` (+ tests)
- Add a **scheme switch** from config `ui.ptools_scheme` ∈ `{"manual","url"}`,
  **default `"manual"`** (matches deployed 0.5.9 → no sms-api overlay change needed;
  `"url"` is opt-in for a future 0.8.x image).
- `manual`: `_launch` returns the **clean overview** URL
  (`{server}/overviewsWeb/celOv.shtml?orgid=<orgid>`) + a `manual_upload` payload
  (`available` TSV relpaths + instruction string). Do NOT emit the inert
  `omics=t&url=` params.
- `url`: keep the existing 0.8.2 `?omics=t&url=…` auto-load builder unchanged.
- Add a sibling clean-overview URL builder; no network calls (no registration).

### 2. `vivarium-dashboard` (`demo-v2ecoli`)
- `static/walkthrough.js` — extend `_launchViewer` (line 1433): after
  `window.open(b.url,…)`, if `b.manual_upload` present, render a small helper
  (modal/docked panel) with download button(s) built from
  `b.manual_upload.available` + `window.DataSource.basePath()` (browser-reachable —
  sidesteps the in-cluster `dashboard_public_base_url`) + the instructions. Degrade
  cleanly when absent + in the snapshot data source.
- `lib/analysis_viewers.py` — confirm `launch_viewer` forwards `manual_upload`/
  `available` JSON-safely (resolver already returns the launch dict).
- `Dockerfile` — bump `PBG_PTOOLS_REF` to the new pbg-ptools commit.

### 3. `sms-api` (`patch/db-filter`) — likely NO change
Defaulting `ptools_scheme="manual"` in code needs no overlay edit. (Optional: seed
`ui.ptools_scheme: manual` for explicitness.)

## Workstreams (sequenced)

- **WS-1 (live spike, tunnel):** confirm our exported `ptools_proteins.tsv` uploads
  cleanly via PTools' Omics dialog and paints (format check — PTools runs
  `removeComments`/`fixEmptyCols`/`hasExtraTables` on the `$`-header TSV). Validates
  the approach before code; a format tweak, if needed, is a pbg-ptools export fix.
- **WS-2 (pbg-ptools):** scheme switch + clean-overview builder + `manual_upload`;
  unit-test **both** builders. Commit/push in `../pbg-ptools`.
- **WS-3 (dashboard):** `_launchViewer` helper + `analysis_viewers` passthrough +
  `PBG_PTOOLS_REF` bump; rebuild image; roll out to `sms-api-stanford-test`.
- **WS-4 (live verify, tunnel):** Launch on `showcase-2-baseline-figures` → overview
  opens + helper shows download + instructions → download → upload in the Omics
  dialog → overview paints. Closes #6 WS-2b.

## Risks / open items

- TSV format compatibility with the Omics upload (WS-1 settles; low risk).
- `pbg-ptools` is **not checked out locally** (`../pbg-ptools` absent — why bare
  `uv run` fails). Must `git clone vivarium-collective/pbg-ptools` as the sibling.
- Tunnel + live pod required for WS-1 + WS-4.
- New frontend helper panel (small, vanilla JS, no bundler) must degrade cleanly.

## Verification

1. **Unit (pbg-ptools):** `uv run pytest` in `../pbg-ptools` — both scheme builders
   assert exact URL/dict shapes (manual → clean URL + available + instructions;
   url → `?omics=t&url=…`).
2. **Dashboard:** `/api/analysis-viewer/{uid}/launch` forwards `manual_upload`/
   `available`; JS check the helper renders download links + instructions.
3. **Live e2e (WS-4, tunnel):** Launch on `showcase-2-baseline-figures`, follow the
   on-screen download + upload, confirm the Cellular Overview paints the omics.

## Docs/state to update ON COMPLETION
`.todo/MANIFEST.md`, `SAVE_SLOT.md`, `NEXT_STEPS.md`, memory
`[[project_ptools_segment7_routing]]`, and `demos/v2ecoli/WALKTHROUGH.md` Segment 7
(replace the "Launch caveat" with the download-then-upload step). Plan #8 then
consumes this launch mechanism.

## References
- Approved refined plan: `~/.claude/plans/validated-roaming-catmull.md`.
- memory `[[project_ptools_segment7_routing]]`, `[[project_ptools_proprietary_constraint]]`,
  `[[project_demo_branch_coupling]]`. Parent: `.todo/plans/6-segment7-ptools-omics-deploy-verify.md`.
