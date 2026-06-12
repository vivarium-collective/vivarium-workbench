# Spine Presentation — Thread B (Connect the evidence) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Program:** Spine Presentation (spec: `docs/superpowers/specs/2026-06-11-spine-presentation-design.md`), thread B — connect the computed evidence to its source so a reader can trace a number → its run → the band that judged it, with authored-vs-computed visually separate. Builds on thread A (merged).

**Goal:** Make the spine's evidence *traceable and trustworthy*: finding ↔ test ↔ run ↔ band links, per-test computed outcomes (not aggregate), readout-validation badges. Plus demote the `viz_stale` lint noise so the readiness panel leads with substantive gaps. Follow the param-enforcement-banner pattern (surfaced · connected · code-vs-authored).

**Tech:** Python + vanilla JS; pytest. Repos: vivarium-dashboard (B1/B2/B3 rendering) + pbg-superpowers (the lint demote). `.venv/bin/python`. Rendering has no JS harness → structural-test (assert the JS references the data + renders the markup) + a manual visual verify. AI-free.

**Grounded anchors:** `_renderFinding` `walkthrough.js:6354`, `from_test`/`from_run` plain code at `:6364-6365`; per-test computed-outcomes tally (aggregate) `study-detail.js:685-709`; the merged outcome blob `walkthrough.js:6536-6552`/`6584-6588`; the Runs table `study-detail.html:~1417-1494` (no outcomes column); the readouts table `study-detail.html:~1088-1106` (authored `o.status`); SP2b-i `/api/study-observable-check` (merged, currently unconsumed by any frontend).

---

## Task 1 — B3: Per-test computed outcomes (the most-used data)

**Files:** `static/study-detail.js` (Tests tab + Runs table), `templates/study-detail.html` (Runs table column), `static/walkthrough.js` (report test cards); Test: structural.

- [ ] **Step 1: Implement (study-detail Tests tab).** Beside each test in the Tests list, render its latest-run computed outcome as a styled row (mirroring the param-enforcement banner): `measured_value`, `result`, `operator`, `evaluated_by: code` — visually SEPARATE from any authored `outcome` (two labeled columns/chips: "code computed" vs "authored"), and badge `reconcile: divergent` prominently when they disagree. Replace the *aggregate-only* tally (`study-detail.js:685-709`) with per-test rows (keep a one-line summary header). Each measured value links to its run (`#run-<id>` or a popover) and shows the `pass_if` band it was judged against.
- [ ] **Step 2: Implement (Runs table outcomes column).** Add a "Test results" column to the Runs table (`study-detail.html` + the JS that builds rows): per run, ✓/✗/◐ badges from that run's `computed_outcomes` (so you can see which run produced which results without leaving the table).
- [ ] **Step 3: Implement (report test cards).** In `walkthrough.js` (`:6536-6552`/`:6584-6588`), stop dumping the merged blob as raw `k:v`; render measured_value as a styled evidence row, keep authored vs code-computed visually separate, badge `reconcile:divergent`, and link the value to its run + band.
- [ ] **Step 4: Structural tests** — assert `study-detail.js` renders per-test `measured_value`/`evaluated_by`/`reconcile` (not just a tally) + a Runs-table outcomes column; `walkthrough.js` separates authored vs computed (no merged raw dump). **Step 5: Commit** — `feat(spine-present): per-test computed outcomes (measured_value/reconcile), Runs-table outcome column, authored-vs-computed separated`

## Task 2 — B1: Finding ↔ test ↔ run ↔ band traceability

**Files:** `static/walkthrough.js` (`_renderFinding` ~6354), `templates/study-detail.html` + `static/study-detail.js` (findings cards); Test: structural.

- [ ] **Step 1: Implement.** In `_renderFinding` (and the study-page findings cards): render `evidence.from_test` and `from_run` as **clickable links** (anchor to the test card `#test-<name>` / the run `#run-<id>`, or a popover showing the test's outcome + run) instead of plain `<code>`. Surface the currently-dropped computed fields: `evidence.divergence_factor` (e.g. "×2.3 vs expected") and `provenance.run_ids` (linked). When the finding cites a test, **inline that test's `pass_if` band** next to `expected` so the reader sees what "passing" meant without hunting. Keep observed ("what we saw") clearly run-derived.
- [ ] **Step 2: (Optional, if cheap) reverse-link** on the test card: "cited in findings: F-03" backlinking any finding whose `from_test` matches.
- [ ] **Step 3: Structural test** — `walkthrough.js` renders `from_test`/`from_run` as anchors + surfaces `divergence_factor` + `provenance.run_ids`. **Step 4: Commit** — `feat(spine-present): finding<->test<->run<->band traceability + divergence_factor + provenance links`

## Task 3 — B2: Readout validation badges

**Files:** `static/study-detail.js` (Readouts tab fetch + badge), `templates/study-detail.html` (readouts table); Test: structural.

- [ ] **Step 1: Implement.** On the Readouts tab, fetch `GET /api/study-observable-check?study=<slug>` (SP2b-i, currently unwired) and badge each readout row with the COMPUTED validation status (`ok` / `unresolved` / `not_in_structure` / `aspirational`) BESIDE the authored `o.status` — clearly labeled "validated against the composite" vs the authored status, so a phantom readout (`not_in_structure`) is visible at the source. Tolerate the endpoint failing/absent (no badge, no error). Link `not_in_structure` to the re-author guidance (`/api/observables`).
- [ ] **Step 2: Structural test** — `study-detail.js` fetches `/api/study-observable-check` + renders the validation badge. **Step 3: Commit** — `feat(spine-present): readout validation-status badges from /api/study-observable-check`

## Task 4 — Demote the viz_stale lint noise (pbg-superpowers)

**Files:** (pbg-superpowers, branch `feat/spine-present-B-lint-demote` off origin/main) `pbg_superpowers/report_linter.py`; Test `tests/test_report_linter.py`.

- [ ] **Step 1: Failing test** — a study with N unregistered on-disk charts produces ONE `viz_stale_vs_latest_run` finding (severity `info`), not N warnings.
- [ ] **Step 2: fail. Step 3: implement** — fold the per-chart `viz_stale_vs_latest_run` findings into a single per-study `info`-severity finding ("N chart(s) on disk are not registered in visualizations[] — register or remove") so it no longer counts as a "gap" (gaps = error+warning) and the readiness panel leads with substantive findings (`needs_human` readouts, uncited bands). Keep the underlying check; just aggregate + demote.
- [ ] **Step 4: pass. Step 5: Commit** — `fix(report-linter): fold + demote viz_stale_vs_latest_run to a single info finding`

## Task 5 — Golden + manual verify

- [ ] **Step 1 (Python golden where applicable, skipif v2e-invest absent, READ-ONLY):** the lint demote — `_report_lint`/`lint_workspace_report` on v2e-invest now yields ≤1 `viz_stale` finding per study at `info` severity (the gap count drops); a real study's `/api/study-observable-check` returns statuses. No writes to v2e-invest.
- [ ] **Step 2:** the structural tests + the lint test green; existing server/linter suites no new failures (pre-existing environmental verified via base).
- [ ] **MANUAL VERIFY (pending — no JS harness):** serve v2e-invest; on a study: per-test computed outcomes show with code-vs-authored separated + `reconcile:divergent` badged; the Runs table has an outcomes column; a finding's `from_test`/`from_run` are clickable and `divergence_factor` shows; the readouts table badges validation status; the readiness panel leads with substantive gaps (viz_stale demoted).
- [ ] **Step 3: Commit** — `test(spine-present): thread-B golden + suite`

---

## Self-Review
- Coverage: per-test outcomes + Runs column (T1/B3), finding traceability + divergence (T2/B1), readout badges (T3/B2), viz_stale demote (T4), golden+manual (T5). Matches thread B + the user's noise request.
- Pattern: every surface connects the value to its source (run/test/band) + separates authored from code-computed, per the param-enforcement banner.
- AI-free; renders the spine's already-computed data; no second source of truth.
- No placeholders: grounded anchors. Rendering is structural-tested + manual (no JS harness) — flagged.
- Deferred: thread C (restructure + report DAG).

## Notes for the executor
- `.venv/bin/python -m pytest`. The Python layer (the lint demote) is TDD'd; the JS rendering is structural-tested + the manual step.
- COPY the param-enforcement banner pattern (`walkthrough.js:7126-7143`): connect every value to its source, label code-vs-authored, flag divergence.
- Render the persisted/computed data (computed_outcomes, findings.evidence, the observable-check statuses); do not recompute or add AI.
- The B2 endpoint (`/api/study-observable-check`) needs the composite to build (~3s, cached) — tolerate failure gracefully (no badge).
- Don't modify the real v2e-invest; goldens are read-only.
