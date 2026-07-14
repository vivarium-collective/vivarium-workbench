# Plan 7 — Production-grade progress feedback for long-running UI-triggered processes (first case: the pinned-build run card)

## Name

Feat: sleek, UX-friendly visual feedback (progress bar + spinner) for any
long-running UI-triggered process, starting with "Run against pinned build" in
the Simulations tab of an investigation study
(`http://localhost:8080/workbench#investigations`).

Linked tasks: builds on #5 (pinned-build remote runs) — the run card whose submit
now fans out to ParCa → Ray MNP → land is exactly the process that currently gives
the user no rich progress signal. Independent of the Segment 7 work (#6). Source:
`.todo/_backlog.md` item (b). **Dashboard-only**, on
`vivarium-dashboard@feat/improved-visual-feedback` (cut from `main`, which now
carries the merged+released demo work, `0.2.0`). No sms-api / v2ecoli changes
required for iteration 1 (it consumes the polling the backend already exposes).

## Status: 📋 PLANNED + REFINED + RE-TARGETED (via /plan, 2026-07-14) — awaits the literal "proceed" before code

Design refinement folded in from `~/.claude/plans/mellow-tinkering-moth.md`. Two
design decisions were resolved with the user; feasibility was verified by a backend +
frontend code sweep (findings below). **Re-targeted 2026-07-14** (via /plan,
`~/.claude/plans/purrfect-wandering-narwhal.md`, approved): `demo-v2ecoli` was merged
into `main` and released (`0.2.0`), and the `vivarium_workbench` rename moved the
frontend under the package. Branch + paths updated accordingly; **the design is
unchanged and all code anchors were re-verified present** (line numbers hold). NOT
yet implemented.

## Re-target delta (2026-07-14)

- **Branch:** `demo-v2ecoli` → **`feat/improved-visual-feedback`** (already checked
  out; cut from `main`, which now contains PR #453 demo-v2ecoli + #466 bump 0.2.0).
- **Paths:** frontend moved by the rename — `static/…` → `vivarium_workbench/static/…`,
  `templates/…` → `vivarium_workbench/templates/…`. Tests stay at repo-root
  `tests/js/` + `tests/`.
- **sms-api:** confirmed on `main` (PR #163 `patch/db-filter` merged; `0.9.18`). Still
  **no sms-api change** iteration 1. Future substage work (WS-3c) → **new branch off
  sms-api `main`**, not now.

## Problem

Clicking "Run against pinned build" kicks off a multi-minute remote pipeline
(submit → Batch SUBMITTED/RUNNABLE = Ray MNP provisioning ≈ 8 min → STARTING →
RUNNING ≈ 5 min → completed → landed). Today the card shows only a **two-row text
stepper** (`build` / `run` with glyph icons + a text note) rendered by
`_renderRemoteRunProgress` — a demo viewer can't tell a slow-but-healthy run from a
stuck one. This is the first and most visible instance of a general gap: **no
attractive, robust visual feedback for long-running UI-triggered processes.**

## Feasibility verdict (evidence-backed) — can we show a real progress bar?

A backend sweep + a frontend sweep were run. Conclusion:

- **A true continuous 0–100% bar is NOT backed by data on this path.** The poller
  `GET /api/remote-run-poll` (`lib/remote_run_views.py`) forwards only a
  **categorical** `phase` (`built`/`queued`/`running`/`done`/`failed`/`unreachable`)
  + a `raw_status` label — **no numeric fraction, no timestep count, no denominator,
  and no SSE for remote runs** (`/api/events*` carry workspace state only). sms-api
  owns the AWS Batch/Ray state and never sends a percentage; the dashboard reads
  only `st.get("status")` so it would not forward one even if present. There is **no
  `_BATCH_STATE_MAP` and no "9 ParCa steps" enumeration in this repo** — those live
  on the sms-api side.
- **Two honest forms of progress ARE available** — combined per the user's chosen
  **hybrid** model:
  1. **Determinate at the milestone level** — the ordered phases *Resolve → Submit
     → Queued → Running → Done → Landed* are real observed transitions, so a
     **segmented milestone bar** with a known stage count is genuinely determinate.
  2. **Honest time-based soft-fill** — `SAVE_SLOT.md` records typical durations
     (Queued/Ray-provision+ParCa ≈ 8 min, Running ≈ 5 min). Within the two long
     stages the client animates a soft fill = `min(elapsed/typical, ~0.9)`, capped
     below full and **snapping to 100% on the real transition** — the *feel* of a
     moving bar while staying truthful. A spinner marks the active segment.
- **Bonus (informs reuse, not wired now):** the **local detached composite-run**
  path (a *different* subsystem) DOES expose a real fraction
  (`progress_step / n_steps` + `heartbeat_at`) via
  `GET /api/composite-run/{run_id}/status`. The component API is shaped to accept
  that measured input so a later task adopts it as a drop-in with a genuine bar.

## Decisions (resolved 2026-07-14)

- **Progress model = HYBRID**: segmented milestone bar + honest time-based soft-fill
  + spinner on the active stage. (Not strict-milestones-only; not spinner-only.)
- **Reuse scope = pinned card only this iteration**, with a **dual-shape component
  API** (`stages` mode + `measured` mode) and a **documented adoption note** for the
  local composite-run path. No second call site wired now — keeps the demo change
  small; the next adoption is a drop-in.

## Key existing anchors (reuse, don't reinvent) — re-verified on `feat/improved-visual-feedback`

- Mount point: `#remote-run-progress` div — `vivarium_workbench/templates/study-detail.html:1280`.
- Render fn to **wrap/upgrade as an adapter** (keeps ~11 call sites unchanged):
  `_renderRemoteRunProgress(opts)` — `vivarium_workbench/static/study-detail.js:1732`.
- Poll cadence: `_pollPhase` (`study-detail.js:1805`), `_pollBuild` (`:1833`),
  `_pollRun` (`:1867`); queued-vs-running label at `:1881`.
- State/timer to reuse: `_remoteRunTimer` (`:1701`), `_remoteRunState` (`:1702`),
  `_rrResetBtn` (`:1706`).
- Existing phase CSS to match: `.inv-run-*` + `@keyframes inv-run-pulse` —
  `vivarium_workbench/static/style.css:1661` / `:1688`.
- Snapshot already hides the whole card — `vivarium_workbench/templates/study-detail.html:2109-2113`
  (`__DASH_CONFIG__.mode === "snapshot"` hides `#remote-run-panel`).
- Script/link precedent: head `<link href="/style.css">` at `study-detail.html:6`;
  scripts `data-source.js:1974`, `configure-run.js:2087`, `study-detail.js:2088`.
- Dual-export test precedent: `vivarium_workbench/static/aig-graph.js:93-96`
  (`module.exports` + `window`) run by `tests/js/test_chain_block.js` via `node tests/js/test_*.js`.
- Design tokens: `:root` at `vivarium_workbench/static/style.css:1`.

## Workstreams

### WS-1 — Reusable component (new, additive)
- `vivarium_workbench/static/progress-track.js` (new) — dependency-free IIFE;
  `window.ProgressTrack` in the browser + `module.exports` in Node (mirror
  `aig-graph.js`). Public: `ProgressTrack.render(mountEl, model)`.
  - **`model` (dual-shape):** stages mode `{mode:'stages', stages:[{key,label}],
    done:[keys], active:key|null, failed:key|null, soft:{startedAt,typicalMs}|null,
    note?, detail?}`; measured mode `{mode:'measured', value, max, heartbeatAt?,
    note?, detail?}`.
  - **Pure, testable helpers** (exported for Node): `stageFraction`, `softFraction`
    (`min(elapsed/typical, cap)`, clamp ≥0), `measuredFraction`.
  - **Accessibility (first in the repo):** `role="progressbar"` +
    `aria-valuemin/max/now` + `aria-valuetext`; `aria-live="polite"` announces stage
    changes.
  - **Motion:** spinner + soft-fill tween respect
    `@media (prefers-reduced-motion: reduce)` → static fill (also a first for the repo).
- `vivarium_workbench/static/progress-track.css` (new) — namespaced `.ptrack-*`
  block matching the `.inv-run-*` palette. *(If we prefer one stylesheet, append to
  `style.css` instead; new file chosen to stay additive — will flag the one template
  `<link>`.)*

### WS-2 — Wire into the pinned-build card
- `vivarium_workbench/templates/study-detail.html`: add
  `<link rel="stylesheet" href="/progress-track.css">` near the head `style.css` link
  (line 6) and `<script src="/progress-track.js">` **before** `study-detail.js`
  (above line 2088, since the adapter depends on `ProgressTrack`).
  **Template is pre-rendered** — a `POST /api/render` + hard-refresh is needed for the
  change to appear (memory `[[project_index_html_render_pipeline]]`).
- `vivarium_workbench/static/study-detail.js`: keep `_renderRemoteRunProgress(opts)`
  as a **thin adapter** — translate the existing `{build, run, note, landBtn, landed,
  runDetail, ...}` opts into `ProgressTrack.render(el, {mode:'stages', ...})`. All
  existing callers stay unchanged → minimal blast radius.
  - Stage set (pinned): `Resolve → Submit → Queued → Running → Done → Landed`.
  - Map phases → stage/active/failed; thread the queued-vs-running distinction from
    `_pollRun` (it already knows `body.phase === 'queued'` at `:1881`) into the adapter.
- **Soft-fill tween:** small `requestAnimationFrame`/`setInterval(~250ms)` loop
  repainting only the active segment from `Date.now() - stageStartedAt` vs a named
  `TYPICAL_MS = {queued:480000, running:300000, building:…}`; record `stageStartedAt`
  in `_remoteRunState` on first sight of a phase; cancel on terminal/failed/reset
  (reuse `_remoteRunTimer`/`_rrResetBtn`).

### WS-3 — Graceful degradation + documented reuse
- **3a Snapshot:** verify the component never renders in read-only mode (card
  already hidden at `study-detail.html:2111`); component makes **zero network calls**
  → snapshot-safe by construction. Assertion + comment only.
- **3b Reuse note (design only):** document measured-mode adoption for the local
  composite-run path — `GET /api/composite-run/{run_id}/status` →
  `ProgressTrack.render(el, {mode:'measured', value:progress_step, max:n_steps,
  heartbeatAt:heartbeat_at})`. Not wired this iteration.
- **3c Future (out of scope, needs sms-api):** finer determinate substages
  (Ray-provision vs ParCa-dependency vs compute) require sms-api to forward the
  Batch substate (dashboard currently collapses to `queued`/`running`). Follow-up
  only → **new sms-api branch off `main`** when scheduled.

### WS-4 — Tests + verify
- **JS unit** (`tests/js/test_progress_track.js`, `node …`): `softFraction` clamps
  to `[0, cap]` + monotonic; `stageFraction` = done-count + active soft; `failed`
  stage renders failed class; measured mode maps `value/max`; `render` emits
  `role="progressbar"` + `aria-valuenow`.
- **pytest** (extend `tests/test_study_detail_page.py`): rendered page still has
  `#remote-run-progress` and now includes the `progress-track.js`/`.css` refs. No
  fixture write-endpoint smokes (memory `[[feedback_no_fixture_smoke_writes]]`).
- **End-to-end verify** (acceptance gate): tunnel up
  (`~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest` → `localhost:8080/workbench`)
  OR local `vivarium-workbench serve` against the tunnel; Simulations tab → **Run
  against pinned build**, watch the card walk **Resolve → Submit → Queued (soft-fill
  + spinner ~8 min) → Running (~5 min) → Done → Land → Landed** with no regression to
  submit/land. Confirm reduced-motion drops the animation to a static fill.

## Files touched (summary)
- **New:** `vivarium_workbench/static/progress-track.js`,
  `vivarium_workbench/static/progress-track.css`, `tests/js/test_progress_track.js`.
- **Edited:** `vivarium_workbench/static/study-detail.js` (`_renderRemoteRunProgress`
  → adapter + soft-fill tween), `vivarium_workbench/templates/study-detail.html`
  (2 asset includes), `tests/test_study_detail_page.py` (wiring assertion).

## Notes / references
- `SAVE_SLOT.md` "Ray/queued mechanism" + "Pinned-build live facts" have the
  authoritative phase timings and state map.
- Frontend is vanilla JS, no bundler — keep the component self-contained, no deps.
- Tests: `uv run --no-sync pytest -q` (bare `uv run` fails — `../pbg-ptools` path
  dep) + `node tests/js/test_progress_track.js`.
- Pre-existing unrelated failure to ignore:
  `test_remote_run_panel.py::test_view_run_button_routes_to_visualizations_not_dead_route`.
- Re-target plan: `~/.claude/plans/purrfect-wandering-narwhal.md`.
- memory `[[project_pinned_build_remote_runs]]`, `[[project_index_html_render_pipeline]]`.