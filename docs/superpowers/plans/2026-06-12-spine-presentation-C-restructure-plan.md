# Spine Presentation — Thread C (Restructure) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Program:** Spine Presentation (spec: `docs/superpowers/specs/2026-06-11-spine-presentation-design.md`), thread C. Builds on A (surface verdicts) + B (connect evidence), both merged.

**Goal (chosen scope):** C1 — a "Spine at a glance" summary panel at the top of the study page (pulling A+B's computed content into one place) + targeted de-duplication and un-burying; C2 — an investigation narrative + verdict DAG in the report. NON-destructive: keep the existing tabs for detail; add the summary, de-dup, un-bury. Follow the param-enforcement-banner pattern (surfaced · connected · code-vs-authored).

**Tech:** vanilla JS + Jinja2 + pytest. Repo: vivarium-dashboard. `.venv/bin/python`. Rendering has no JS harness → structural-test (assert the markup/data) + a manual visual verify. AI-free; render the spine's computed data.

**Grounded anchors (review + branch):** multi-axis status `<details class="status-detail-panel">` (study-detail.html:74, collapsed); `follow_up_studies` in 3 places (Overview :603/:95, Conclusions tab, `discovery_implications.followup_study_proposals`); collapsed `overview-section` `<details>` at :426 (purpose), :495 (behavioral tests), :519/:528 (key assumptions / pipeline gate); discovery implications buried at the bottom of Conclusions (~:1889); per-test outcomes are now canonical in the Tests tab (thread B); the report builder is `v3StudySection`/`_buildInvestigationReportHtml` in `walkthrough.js`; study DAG ordering uses `pipeline_gate.prerequisites` (walkthrough.js:~5690-5720).

---

## Task 1 — C1a: "Spine at a glance" summary panel (top of study page)

**Files:** `templates/study-detail.html` (a panel near the header, before the tabs ~:80), `static/study-detail.js` (populate it); Test: structural.

- [ ] **Step 1: Implement.** Add a compact summary panel at the TOP of the study page (above the tabs) — the spine in one glance, pulling the already-computed A+B content, each item linking to its detail tab/section:
  - **Verdict** — `computed_gate_verdict.result` + the code-vs-authored divergence chip (from A2). Labeled code-computed.
  - **Why** — the primary finding's one-line statement + its `divergence_factor` / evidence (from B1), linked to the finding card.
  - **Acceptance** — the investigation criterion this study covers + its computed result (from A1's `computed_acceptance`), linked to the investigation.
  - **Readiness** — the ✓/⚠ gaps summary (from A3's readiness panel), linked to/embedding the existing dropdown.
  - **Next** — the top `next_action` / follow-up.
  Each field tolerates absence (omit the row). Reuse the existing computed data already on `window._study` + the `/api/report-lint` fetch — do NOT recompute.
- [ ] **Step 2: Structural test** — `study-detail.html`/`study-detail.js` render a `spine-summary` panel referencing `computed_gate_verdict`, the finding, `computed_acceptance`/readiness, with links to the detail tabs. **Step 3: Commit** — `feat(spine-present): 'Spine at a glance' summary panel (verdict/why/acceptance/readiness/next)`

## Task 2 — C1b: De-duplicate follow-ups + outcomes

**Files:** `templates/study-detail.html`, `static/study-detail.js`; Test: structural.

- [ ] **Step 1: Implement (follow-ups 3→1).** Pick ONE canonical follow-up surface (recommend the Conclusions/Decide tab's follow-up section, since follow-ups are a Decide-phase output). The Overview occurrence (:603) and the `discovery_implications.followup_study_proposals` occurrence (~:1951) become either a single shared render or a short "N follow-ups → see Conclusions" link to the canonical one — no triplicated cards. If `follow_up_studies` and `discovery_implications.followup_study_proposals` are distinct field families, render them together in the canonical section, clearly distinguished (authored vs discovered), de-duplicated by id.
- [ ] **Step 2: Implement (outcomes 2-3→1).** The Tests tab is canonical for per-test outcomes (thread B). The Conclusions-tab "latest run outcomes" table (~:1745) becomes a link to the Tests tab (or is removed) so outcomes live in one place. Keep the verdict form in Conclusions.
- [ ] **Step 3: Structural test** — assert follow-ups render once (no triplicated card markup) + the Conclusions outcomes table links to/defers to the Tests tab. **Step 4: Commit** — `feat(spine-present): de-duplicate follow-ups (3->1) + outcomes (canonical Tests tab)`

## Task 3 — C1c: Un-bury the spine-critical content

**Files:** `templates/study-detail.html`; Test: structural.

- [ ] **Step 1: Implement.** Surface the spine-critical content that is collapsed-by-default or buried:
  - Auto-expand (remove the default-collapsed `<details>` → make `open`, or promote to a visible block): **purpose** (:426 — question/mechanism/expected_outcome), the **behavioral-tests summary** (:495), the **multi-axis status** (:74).
  - **Elevate discovery implications** (resolved/remaining uncertainties, alternate hypotheses, mechanism updates) from the bottom of Conclusions to the top of the Conclusions tab (or a clearly-visible block) — it's high-signal for next studies.
  Keep genuinely-secondary content (key assumptions, pipeline gate internals) collapsible.
- [ ] **Step 2: Structural test** — purpose / behavioral-tests-summary / multi-axis-status are no longer `<details>`-collapsed-by-default (or are `open`); discovery implications render above the conclusion text. **Step 3: Commit** — `feat(spine-present): un-bury spine-critical content (purpose, tests summary, status, discovery implications)`

## Task 4 — C2: Investigation narrative + verdict DAG in the report

**Files:** `static/walkthrough.js` (the report builder); Test: structural.

- [ ] **Step 1: Implement.** In the generated investigation report (`_buildInvestigationReportHtml` / near the generation banner), add:
  - **A verdict-annotated study DAG** — nodes = member studies, edges from `pipeline_gate.prerequisites`/`parent_studies`, each node badged with its `computed_gate_verdict.result` (✅/⚠/⛔) — so a reader sees the dependency structure + where it passes/blocks at a glance. A compact inline SVG or a clean nested/ASCII layout (no heavy dependency); reuse the existing topological ordering (walkthrough.js:~5690).
  - **A one-paragraph roll-up** — the investigation's `computed_acceptance` verdict + which criteria pass/block (from A1), connecting the studies into the investigation's story.
- [ ] **Step 2: Structural test** — `walkthrough.js` renders a study-DAG with verdict badges + the acceptance roll-up paragraph in the report. **Step 3: Commit** — `feat(spine-present): investigation verdict DAG + acceptance narrative in the report`

## Task 5 — Golden + manual verify

- [ ] **Step 1 (structural/golden, skipif v2e-invest absent, READ-ONLY):** the data the panels need is present on a real study/investigation spec (computed_gate_verdict, a finding, computed_acceptance, the lint findings); no writes to v2e-invest.
- [ ] **Step 2:** structural tests green; `node -c` clean on the touched JS; existing server tests no new failures (pre-existing environmental verified via base).
- [ ] **MANUAL VERIFY (pending — no JS harness):** serve v2e-invest; on a study: the Spine-at-a-glance panel shows verdict/why/acceptance/readiness/next, each linking to detail; follow-ups + outcomes appear ONCE; purpose/tests/status are visible (not collapsed); discovery implications elevated. In the report: the verdict DAG + acceptance roll-up render.
- [ ] **Step 3: Commit** — `test(spine-present): thread-C golden + suite`

---

## Self-Review
- Coverage: spine summary (T1), de-dup (T2), un-bury (T3), report DAG+narrative (T4), golden+manual (T5). Matches the chosen C scope.
- Non-destructive: adds a summary + de-dups + un-buries; keeps the tabs for detail. Reuses A+B's computed data — no recompute, AI-free.
- No placeholders: grounded anchors. Rendering structural-tested + manual (no JS harness) — flagged.

## Notes for the executor
- `.venv/bin/python -m pytest`; `node -c` the touched JS. Structural tests assert the markup/data references (no JS harness).
- REUSE the computed data already produced by A+B (`computed_gate_verdict`, `computed_acceptance`, findings.evidence, `/api/report-lint`) — the summary panel is a re-presentation, not a recompute. AI-free.
- Be conservative: keep the existing tabs + their content working; this thread re-presents and de-dups, it does not rewrite the page. Don't break the existing renderers.
- Don't modify the real v2e-invest; goldens are read-only.
