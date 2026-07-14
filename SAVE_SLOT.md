# Checkpoint: PLAN 7 RE-TARGETED + APPROVED — STANDING BY FOR "proceed" BEFORE CODE

## ⭐ RESUME HERE (2026-07-14, planning session)

The MVP demo shipped: **`demo-v2ecoli` merged into `main` + released `0.2.0`** (PR
#453 + #466), and **sms-api `patch/db-filter` merged into `main`** (PR #163;
`0.9.18`) — both done overnight by a coworker, as the prior checkpoint's STEP 1
sequenced. We have now moved on to **plan 7** on a dedicated branch.

**This session was planning-only.** No product code was written. It revised plan 7
to reflect the new post-merge reality, got the revised plan approved, and updated the
tracking docs. **Next action is gated on the user typing the literal word "proceed"**
(memory `[[feedback_todo_protocol]]`), after which WS-1…WS-4 implementation begins.

## Session goal

Revise/re-target plan 7 (progress-bar UX for long-running UI-triggered processes)
for the post-merge world (demo→main→0.2.0 + `vivarium_workbench` rename), present it
for approval, and update the tracked plan + MANIFEST — then standby for "proceed".

## Progress table

| Step | Status |
|---|---|
| Verify branch/base topology (on `feat/improved-visual-feedback` off `main`@0.2.0) | ✅ Done |
| Re-verify all plan-7 code anchors exist + line numbers hold (post-rename) | ✅ Done |
| Confirm sms-api on `main` (PR #163) — no sms-api change needed iter 1 | ✅ Done |
| Present revised plan 7 via /plan → **approved by user** | ✅ Done |
| Rewrite `.todo/plans/7-pinned-run-progress-ux.md` (branch + paths) | ✅ Done |
| Update `.todo/MANIFEST.md` item 7 (retarget note) | ✅ Done |
| Remove `.todo/MANIFEST.md` + `.todo/plans/*` from do-not-commit list (memory) | ✅ Done |
| **Implement WS-1…WS-4** | ❌ PENDING — awaits literal **"proceed"** |

## Key files touched THIS session (docs only — no product code)

- `.todo/plans/7-pinned-run-progress-ux.md` — full rewrite. Retargeted branch
  `demo-v2ecoli` → `feat/improved-visual-feedback`; all frontend paths → the
  `vivarium_workbench/` package; added a "Re-target delta" section; anchor line
  numbers re-verified and refreshed.
- `.todo/MANIFEST.md` — item 7 status → `PLANNED + REFINED + RE-TARGETED`; branch +
  path + sms-api-on-`main` note folded in.
- `.todo/_backlog.md` — pre-existing modification carried in from a prior session
  (item b promotion note); not edited this session beyond what was already staged.
- memory `feedback_do_not_commit.md` — added an explicit "COMMITTABLE" clause:
  `.todo/MANIFEST.md` + `.todo/plans/*` are NOW committable (user request
  2026-07-14). Root `todo.md` stays gitignored.
- `~/.claude/plans/purrfect-wandering-narwhal.md` — the approved /plan revision doc.

## Plan 7 — the design (UNCHANGED; only branch/paths were re-targeted)

**Goal:** sleek, production-grade progress feedback (bar + spinner) for long-running
UI-triggered processes; first case = the "Run against pinned build" card in a study's
Simulations tab. Dashboard-only iteration 1.

**Feasibility verdict:** a true continuous 0–100% bar is NOT backed by data on the
remote path — `GET /api/remote-run-poll` forwards only a categorical `phase`/`raw_status`
(no fraction, no SSE for remote runs). → **HYBRID model** (user-chosen):
1. determinate **milestone bar** over real transitions Resolve → Submit → Queued →
   Running → Done → Landed;
2. honest **time-based soft-fill** in the two long waits (Queued ≈ 8 min, Running ≈ 5
   min): `min(elapsed/typical, ~0.9)`, capped <100%, snaps to 100% on the real
   transition; spinner on the active stage.

Reuse scope = **pinned card only** this iteration, but ship a **dual-shape component
API** (`stages` + `measured`) + a documented adoption note for the genuinely-
determinate local composite-run path (`progress_step`/`n_steps` via
`GET /api/composite-run/{id}/status`). Wrap the existing `_renderRemoteRunProgress`
as a thin **adapter** so ~11 call sites stay unchanged.

## Plan 7 — files the eventual implementation will touch

- **New:** `vivarium_workbench/static/progress-track.js`,
  `vivarium_workbench/static/progress-track.css`, `tests/js/test_progress_track.js`.
- **Edited:** `vivarium_workbench/static/study-detail.js` (adapter + soft-fill tween),
  `vivarium_workbench/templates/study-detail.html` (2 asset includes — `<link>` near
  head `style.css` line 6, `<script>` **before** `study-detail.js` at line 2088),
  `tests/test_study_detail_page.py` (wiring assertion).

## Plan 7 — re-verified anchors (all present on `feat/improved-visual-feedback`)

- `#remote-run-progress` — `vivarium_workbench/templates/study-detail.html:1280`.
- `_renderRemoteRunProgress(opts)` — `vivarium_workbench/static/study-detail.js:1732`.
- Pollers/state: `_pollPhase:1805`, `_pollBuild:1833`, `_pollRun:1867`;
  `_remoteRunTimer:1701`, `_remoteRunState:1702`, `_rrResetBtn:1706`;
  queued-vs-running label `:1881` (`body.phase === 'queued'`).
- `.inv-run-*` CSS `style.css:1661`; `@keyframes inv-run-pulse:1688`; `:root` tokens `:1`.
- Snapshot hide `study-detail.html:2109-2113` (`__DASH_CONFIG__.mode === "snapshot"`
  hides `#remote-run-panel`).
- Dual-export precedent `aig-graph.js:93-96`; Node runner `tests/js/test_chain_block.js`.

## Key design decisions / gotchas for the next agent

- **The `vivarium_workbench` rename moved the frontend** — everything that used to be
  at repo-root `static/`/`templates/` is now under `vivarium_workbench/static/` +
  `vivarium_workbench/templates/`. Tests stay at repo-root `tests/` + `tests/js/`.
- **Template is pre-rendered** — after editing `study-detail.html`, a `POST /api/render`
  + hard-refresh is required for the change to appear (memory
  `[[project_index_html_render_pipeline]]`).
- **No sms-api change iter 1.** If a future iteration needs finer determinate
  substages (WS-3c), cut a **new branch off sms-api `main`** — do NOT reuse
  `patch/db-filter` (it's merged).
- **`.todo/` is now committable** (this session's memory update). Root `todo.md` is not.
- **`environment.py`** (pydantic-settings WIP, backlog item a) is still untracked and
  unrelated to plan 7 — keep it out of any plan-7 commit.

## Verification

- **Build/test NOT run this session** — planning-only, zero code changed, so the
  ~903-test suite offers no signal about this work. The eventual implementation's
  gate is below.
- **Implementation verify (WS-4, for later):**
  - `uv run --no-sync pytest -q tests/test_study_detail_page.py`
  - `node tests/js/test_progress_track.js`
  - E2E: tunnel `~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest` → `localhost:8080/workbench`
    OR local `vivarium-workbench serve` against the tunnel; Simulations tab → Run
    against pinned build; watch Resolve → Submit → Queued (soft-fill+spinner ~8 min)
    → Running (~5 min) → Done → Land → Landed; confirm reduced-motion → static fill.

## Next steps (priority order)

1. **AWAIT the literal "proceed"** from the user. Then implement WS-1 (component) →
   WS-2 (wire adapter + soft-fill tween) → WS-3 (snapshot assertion + reuse note) →
   WS-4 (tests + e2e verify). Update `.todo/plans/7-*` progress after each approved
   edit (memory `[[feedback_progress_docs_after_each_edit]]`).
2. Per suggest-commits protocol: agent runs `git add` (now incl. `.todo/`), then
   shows a copy-paste `git commit -m "..."` one-liner; never commit/push myself
   (memory `[[feedback_suggest_commits]]`).
3. PR flow ends at `gh pr create` — review required, no auto-merge (memory
   `[[feedback_pr_review_required]]`).

## Quick reference

- Branch: `feat/improved-visual-feedback` (off `main`@0.2.0). sms-api on `main`@0.9.18.
- Cluster env: `export AWS_PROFILE=stanford-sso AWS_DEFAULT_REGION=us-gov-west-1 KUBECONFIG=/Users/alexanderpatrie/.kube/kube_stanford_test.yml`
- Tunnel: `~/sms/sms-cdk/scripts/sms-proxy.sh -s smsvpctest` → `localhost:8080/workbench` (dies on SSO expiry → `aws sso login` + restart).
- Tests: `uv run --no-sync pytest -q` (bare `uv run` fails — missing `../pbg-ptools` path dep).
- Pre-existing unrelated failure to ignore: `test_remote_run_panel.py::test_view_run_button_routes_to_visualizations_not_dead_route`.
- Commits are SSH-signed; if locked → ask user to `ssh-add` via `!` (memory `[[project_ssh_commit_signing]]`).

## Related

- `.todo/plans/7-pinned-run-progress-ux.md` (ground truth), `.todo/MANIFEST.md`,
  `~/.claude/plans/purrfect-wandering-narwhal.md` (approved revision).
- memory `[[project_pinned_build_remote_runs]]`, `[[project_index_html_render_pipeline]]`,
  `[[feedback_todo_protocol]]`, `[[feedback_no_fixture_smoke_writes]]`.
- Prior demo/MVP history: git log on `main` (PR #453/#466) + sms-api `main` (PR #163).
