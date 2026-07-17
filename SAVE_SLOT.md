# Checkpoint — 2026-07-17: `demo-verification` branch, demo rewritten to open on Sources + live-verified against `smscdk`

## ⭐ ALL-HANDS-ON-DECK FOCUS (this branch, and short-term for whatever branch follows it)

**The overriding goal right now is: refine and record the v2ecoli GovCloud demo.**
Everything else (the three unrelated refactor PRs merged into `main` post-v0.3.0,
the dirty `pyproject.toml`/`uv.lock`, backlog items) is secondary until the demo
is recorded. If you're picking up fresh context on this repo, orient around
`demos/v2ecoli/` first — `WALKTHROUGH.md` is the presenter run-sheet,
`speaker/NARRATION.md` is the word-for-word script, `README.md` is the overview,
`VERIFICATION_REPORT.md` is the live-verification record. This framing should
persist across sessions until the demo is actually recorded — don't silently
drop back to routine refactor/maintenance framing without the user re-directing.

## ⭐ RESUME HERE

**Branch:** `demo-verification`. Upstream `origin/demo-verification` exists and
was up to date as of session start (unclear if still true after this session's
uncommitted changes — not pushed).

**Big-picture state carried from the prior checkpoint:** `v0.3.0` (Plan 7,
pinned-run progress bar) is merged + released, and — newly confirmed this
session — the `sms-api-stanford` (`smscdk`) overlay pin is verified live at
`vivarium-workbench newTag: 0.3.0`. That resolves the release-tail question the
prior checkpoint left open. No functional/code changes this session — this was
a **demo-content rewrite + live-data verification** session, docs only.

## Session goal

1. `/deep-breath` — re-assess the repo against the "all-hands-on-deck: refine +
   record the v2ecoli demo" goal, survey recent branch activity across
   `vivarium-workbench`, `sms-cdk`, `sms-api`, `v2ecoli`. **Done** — wrote
   `.perspective.md` (baseline pass, no prior perspective existed).
2. User-directed content rewrite: restructure `demos/v2ecoli/WALKTHROUGH.md`
   (+ 3 companion docs) to open on a new **Sources** segment telling the real
   scientific story — v2ecoli's whole-cell model is parameterized by ~135 real
   experimental datasets inherited from the Covert lab lineage (Macklin,
   Ahn-Horst, et al., *Science* 369, eaav3751, 2020), formally extensible via a
   schema-validated override mechanism. Planned via `EnterPlanMode`
   (plan file: `~/.claude/plans/deep-jumping-crescent.md`), approved, executed.
   **Done.**
3. User had the GovCloud tunnel up (confirmed targeting **`smscdk`**, the
   canonical recording-target stack) — re-verified every number in the rewrite
   against the live deployment rather than leaving them as source-code
   estimates or carrying forward the 2026-07-14 `smsvpctest`-based figures
   unchecked. **Done.**

## Progress table

| Item | Status |
|---|---|
| `.perspective.md` — baseline deep-breath pass | ✅ Done (uncommitted, untracked — new file) |
| Plan file for the Sources rewrite | ✅ Done — `~/.claude/plans/deep-jumping-crescent.md`, approved by user |
| New Segment 2 "Sources — The Scientific Foundation" added to `WALKTHROUGH.md` | ✅ Done |
| All 9 segments renumbered (was 8) across `WALKTHROUGH.md`, `speaker/NARRATION.md`, `README.md` | ✅ Done |
| "Why this matters scientifically" framing layered onto all 9 segments' narration | ✅ Done |
| `VERIFICATION_REPORT.md` updated — Sources row, numbering note on the historical (pre-2026-07-17) tables | ✅ Done |
| Citation verified via web search (not asserted from memory) | ✅ Done — Macklin, Ahn-Horst, et al., "Simultaneous cross-evaluation of heterogeneous E. coli datasets via mechanistic simulation," *Science* 369, eaav3751 (2020), doi:10.1126/science.aav3751 |
| Sources tab numbers verified live against `smscdk` (`GET /api/data-sources`) | ✅ Done — **135** total (131 inherited + **4** overrides: `dna_sites`, `equilibrium_reaction_rates`, `equilibrium_reactions`, `metabolic_reactions_added` — one more override than the source-level design doc named) |
| Pinned-build staleness (flagged in `.perspective.md`) checked live | ✅ Done — **already resolved**: `smscdk`'s pinned `v2ecoli@main` resolver is at `a08e20bd`, exactly matching the current GitHub main tip. All `70b5ec3` references updated to `a08e20b` across the docs. No gate-script re-run needed right now. |
| All other pillar numbers (173 processes/7 pkgs, 28 composites, 8 investigations, 9 ParCa steps/43 state entries, 58 viz classes, 35 seeded runs) re-confirmed live on `smscdk` | ✅ Done — exact match with the 2026-07-14 `smsvpctest`-based figures; the two stacks are in sync |
| Stack identity (`smscdk` vs `smsvpctest`) for this session's tunnel | ✅ Confirmed by user — `smscdk` |
| Commit the doc rewrite | ❌ PENDING — user has not asked for a commit yet |
| Commit/scope the pre-existing `pyproject.toml`/`uv.lock` change | ❌ PENDING — unrelated, still deliberately uncommitted (see below) |
| `.perspective.md` — commit or leave untracked? | ❓ UNRESOLVED — new file, not addressed this session re: git status |

## Key files touched (this session)

- **NEW, untracked:** `.perspective.md` — deep-breath baseline perspective;
  covers branch activity across all 4 repos, the demo-readiness assessment,
  and ends on the (now-answered) open question about WALKTHROUGH adjustments.
- **NEW, untracked (outside repo):** `~/.claude/plans/deep-jumping-crescent.md`
  — the approved plan for the Sources rewrite. Not part of git.
- **EDITED (unstaged):**
  - `demos/v2ecoli/WALKTHROUGH.md` (648 lines, +129/-diff) — new Segment 2
    (Sources), full renumbering 1→9, narrative framing per segment, Appendix C
    timing table updated, all internal `Segment N` cross-references fixed,
    pinned-build commit refs updated to `a08e20b`, Sources Key Number now
    exact live figures (was source-code estimate).
  - `demos/v2ecoli/speaker/NARRATION.md` (503 lines) — same structural
    changes in the file's conversational register; word-for-word Sources beat;
    timing card, Q&A, Presenter Must-Know, and the Appendix known-risks table
    all updated and renumbered.
  - `demos/v2ecoli/README.md` (131 lines) — segment table now 9 rows,
    "8-segment"→"9-segment" fixed in 2 places, `Segment 6/7` cross-refs fixed.
  - `demos/v2ecoli/VERIFICATION_REPORT.md` (590 lines) — added a third
    "pass" (2026-07-17 Sources + pinned-build spot-check) documented up top;
    Sources rows flipped from "pending" to live-verified with exact numbers;
    stack identity (`smscdk` vs `smsvpctest`) called out explicitly per pass,
    now that pass #1 is confirmed `smscdk`; historical 2026-07-09/07-14 tables
    left as-authored (dated records) with a numbering-note callout rather than
    retroactively renumbered.
- **NOT touched, still dirty from before this session (out of scope,
  confirmed real, deliberately unbundled per Plan 10's own note):**
  `pyproject.toml` / `uv.lock` — the `pbg-ptools` path-dep fix
  (`../pbg-ptools` → `../../sms/pbg-ptools`).

## Key design decisions / gotchas

- **The Sources segment's scientific claims are grounded, not invented.**
  Every claim traces to something read this session: the citation was
  confirmed via `WebSearch` (not asserted from model memory), the override
  mechanism's behavior was confirmed by reading
  `v2ecoli/processes/parca/reconstruction/ecoli/sources.py` and
  `v2ecoli/dashboard_sources.py`, and the exact counts were confirmed live via
  `GET /workbench/api/data-sources` against `smscdk` — not left as `~135`
  read-from-source estimates. If you add more Sources content later, keep
  this standard: don't assert a number without either reading it from live
  API output or explicit source code, and don't assert a citation without a
  web search.
- **Historical verification tables were deliberately NOT renumbered.** The
  2026-07-09 (local baseline) and 2026-07-14 (`smsvpctest` remote) sections of
  `VERIFICATION_REPORT.md` use the OLD 8-segment numbering, because they're
  dated records of what was actually tested on those dates under that
  numbering. A "Numbering note" callout explains the mapping to the new
  9-segment scheme instead of silently rewriting history.
  `[[project_v2ecoli_demo_sources_segment]]` (memory not yet written — see
  Next steps).
- **`smscdk` and `smsvpctest` are two different GovCloud stacks with their own
  independent pinned-build/simulator registries** (established fact, not new
  this session, but re-confirmed) — a build/verification on one does not
  imply anything about the other. This session's live checks were against
  `smscdk` specifically (user-confirmed), which is the *canonical recording
  target* per `README.md`/`WALKTHROUGH.md` — a more relevant check for
  recording-readiness than the 2026-07-14 `smsvpctest` pass, even though both
  agreed on every shared figure.
- **No code was touched this session** — pure markdown content edits across 4
  files under `demos/v2ecoli/`. No build/test verification applies; the
  "verification" for this session was live API calls against the deployed
  dashboard, documented inline in the edited files themselves.
- Continue honoring the standing hard rule: **never run bare/unscoped
  `pytest`** in this repo (known hang, see `[[feedback_never_run_full_pytest]]`)
  — not that it was relevant this session (no code changed), but it applies
  the moment code work resumes.

## Verification

- `git status` / `git diff --stat` — 4 demo docs modified (documented above),
  plus the pre-existing unrelated `pyproject.toml`/`uv.lock` diff, still
  uncommitted, still out of scope.
- Live API verification (this session, against `smscdk` via the user's tunnel):
  - `GET /workbench/api/data-sources` → 135 entries (131 inherited + 4
    override) — confirms the new Sources segment's Key Number exactly.
  - `GET /workbench/api/registry` → 173 processes (matches all prior claims).
  - `GET /workbench/api/composites` → 28 (matches).
  - `GET /workbench/api/investigation-summaries` → 8 (matches).
  - `GET /workbench/api/visualization-classes` → 58 (matches).
  - `GET /workbench/api/simulations` → 35 (matches).
  - `GET /workbench/api/composite-resolve?id=v2ecoli.composites.parca` → 9
    steps (`default_n_steps`), 43 state entries (matches).
  - `GET /core/v1/simulator/versions` (root ALB, sms-api) cross-checked
    against `git ls-remote https://github.com/vivarium-collective/v2ecoli
    main` → pinned build `a08e20bd` **exactly matches** live GitHub main tip.
    No `ensure_latest_main_build.sh` re-run needed right now.
- No test suite run — no code changed this session.

## Next steps (priority order)

1. **Nothing is blocking a recording on the content side anymore** — the
   9-segment `WALKTHROUGH.md` + `speaker/NARRATION.md` are both internally
   consistent and every number in them is live-confirmed against the
   canonical `smscdk` recording target as of today. The demo's own
   pre-recording checklist (pre-warm Registry, decide live-vs-pre-launched
   Segment 7 Part B run) still applies.
2. **Ask the user whether to commit this session's doc changes.** Per
   standing feedback (`[[feedback_suggest_commits]]`, `[[feedback_do_not_commit]]`
   if those memories exist — verify), stage narrowly: the 4 demo docs only,
   never the unrelated `pyproject.toml`/`uv.lock`. Hand over a commit
   one-liner; don't commit directly without confirmation.
3. **`.perspective.md` disposition** — decide whether it should be committed,
   gitignored, or left untracked/scratch. Not addressed this session.
4. **Consider writing memory** for the demo's new structure (Sources segment,
   9-segment scheme, the `smscdk`/`smsvpctest` stack distinction) — none was
   written this session; the checkpoint above is the durable record for now,
   but a `project_*` memory would let future sessions skip re-deriving this.
5. **Actually record the demo** — once committed, this is the actual
   all-hands-on-deck deliverable. Follow `demos/v2ecoli/speaker/NARRATION.md`
   for the word-for-word script; `WALKTHROUGH.md` for the technical run-sheet.
6. Parked, unchanged from before: Plan 9 (Omics Viewer 0.5.9 fix, refined,
   awaits "proceed"), Plan 8 (auto-param PTools, gated on Plan 9/6), backlog
   item (a) pydantic-settings `environment.py` (untracked WIP), the 5
   previously-untracked `demos/v2ecoli/` files from the prior checkpoint
   (`WALKTHROUGH-local-remote-compute.md`, `remote_commit_run.py`,
   `speaker/NARRATION.md` — now further edited this session,
   `speaker/three_layers.png`) — Plan 11 already logged these; unclear if
   Plan 11 itself has been committed yet (check `.todo/plans/11-*.md` status
   before assuming).

## Quick reference

- Branch: `demo-verification`.
- **Demo goal (short-term, all-hands-on-deck): refine + record
  `demos/v2ecoli/WALKTHROUGH.md`.** Orient here first on any fresh session
  against this branch or its successors, until told otherwise.
- Tests: **never bare `pytest`**. (Not exercised this session — no code
  changed.)
- Version: `pyproject.toml` → `0.3.0` (matches latest tag/release; unrelated
  uncommitted `pbg-ptools` path-dep diff still present, still out of scope).
- Demo docs map: `demos/v2ecoli/README.md` (overview) →
  `demos/v2ecoli/WALKTHROUGH.md` (9-segment technical run-sheet) →
  `demos/v2ecoli/speaker/NARRATION.md` (word-for-word script) →
  `demos/v2ecoli/VERIFICATION_REPORT.md` (live-verification record, 3 dated
  passes).
- Live verification pattern established this session: `curl` the
  `/workbench/api/*` endpoints through the tunnel, don't trust source-code
  estimates for anything that will be spoken aloud in a recording.

## Related memory
`[[project_v0.3.0_release_shipped]]`, `[[feedback_never_run_full_pytest]]`,
`[[project_stanford_zshrc_commands]]`, `[[project_cisco_empties_etc_hosts]]`,
`[[project_demo_latest_v2ecoli_main_constraint]]`,
`[[project_pinned_build_remote_runs]]`, `[[project_ptools_segment7_routing]]`,
`[[feedback_suggest_commits]]`, `[[feedback_pr_review_required]]`,
`[[feedback_do_not_commit]]`. **Consider adding this session:** a
`project_v2ecoli_demo_sources_segment` memory recording the new 9-segment
structure and the `smscdk`-live-confirmed numbers, so future sessions don't
have to re-read this checkpoint to know the demo's current shape.
