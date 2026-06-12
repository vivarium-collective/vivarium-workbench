# Spine Presentation — Program Design

> **Status:** approved program design (2026-06-11). Companion to the Active Investigation Framework (`pbg-superpowers/docs/specs/2026-06-11-active-investigation-framework-design.md`): that program makes the spine *compute* content; this one makes the computed content *visible, connected, and trustworthy* in the study UI and the report. Decomposed into 3 threads (A→B→C); each gets its own plan.

## Motivation

A three-agent review of the study detail UI (`templates/study-detail.html` + `static/study-detail.js`), the generated report (`static/walkthrough.js` report-builder + `pbg-superpowers/report.py`), and the spine's computed outputs found that **the presentation layer has not caught up to the spine.** The framework lift made the spine compute verdicts, divergences, validation statuses, and acceptance roll-ups — but the UI and report largely hide that content, disconnect it from its source, or blend it indistinguishably with human-authored values. Confirmed gaps:

- **Investigation acceptance roll-up is invisible.** `executive.computed_acceptance` is computed *and returned by the investigation API* (`server.py:13075`), but no view renders it; the authored acceptance list was removed (`walkthrough.js:7741-7749`). `computed_verdict_status` + `diverges_from_authored` are written to disk and never read.
- **Study verdict divergence is invisible.** `gate_evaluator.diverges_from_authored` is written and never read; `server.py:1715` recomputes a `computed_gate_verdict` via `roll_up_verdict` (render-only, dropping `diverges_from_authored`) that the frontend never renders. The UI shows only the *authored* gate pill.
- **The finding's headline computed number — `divergence_factor` — is dropped.** `observed` and `expected.range` are both shown, but the computed distance between them (plus `calibration_anchor`, `provenance.run_ids`) is not.
- **SP2b-i readout validation is unwired.** The new `/api/observables` + `/api/study-observable-check` have no frontend consumer; the readouts table shows the *authored* status, masking whether each readout resolves against the composite.
- **`computed_outcomes` is crude + blended.** The dashboard shows only an aggregate "N divergent" tally (you cannot find *which* tests); the report dumps raw `k:v` and *merges* authored+computed so they cannot be compared.
- **Linter content never reaches the dashboard.** SP2b-ii migration findings + SP2c citation-gap warnings exist only as `report_linter` text, and the dashboard never runs the linter (no `lint_workspace_report` call in `server.py`).

**The one thing done right** is the param-enforcement banner (`walkthrough.js:7126-7143`): the only computed artifact that is prominent, **connected to its run**, and **clearly labeled code-computed**. It is the template the rest should follow.

## The principle

Every computed/code-owned artifact the spine produces should be presented by three rules:
1. **Surfaced** — visible, not buried, collapsed-by-default, or aggregated into a count that hides the individual signal.
2. **Connected** — linked to the source that produced it (the run, the test, the band, the member study), so a reader can trace "this number → this run → this band" without hunting.
3. **Distinguished** — visually unmistakable as *code-computed* vs *human-authored*, with divergence between the two flagged (`diverges_from_authored`, `reconcile: divergent`).

Constraints: the dashboard stays **AI-free** (it renders deterministic data the spine already computes); the **report and the UI render the same data** (no second source of truth); changes are additive and preserve existing layouts where possible.

## Threads (sub-projects)

### Thread A — Surface the computed verdicts (cheapest; data mostly already exists)
- **A1 — Investigation acceptance roll-up.** Render `executive.computed_acceptance` (already returned by `/api/iset/<inv>`) in the investigation executive fold: per-criterion `study → behavior → result`, beside the authored `verdict_status`, with a divergence badge when `diverges_from_authored`.
- **A2 — Study gate divergence.** Read the persisted `gate_evaluator` (incl. `diverges_from_authored`) instead of recomputing without it (`server.py:1715`); render "code says X · authored says Y" beside the gate pill on the study page and the per-study report verdict pill.
- **A3 — Lint / readiness panel.** Add `GET /api/report-lint` (runs `report_linter.lint_workspace_report`); render a per-study readiness panel surfacing the SP2b-ii readout-migration findings (info/warning) and SP2c band-citation-gap warnings — three computed artifacts wired at once.

### Thread B — Connect the evidence
- **B1 — Finding ↔ test ↔ run ↔ band traceability.** Make `evidence.from_test`/`from_run` clickable (jump/popover to the test card + run); inline the `pass_if` band on a cited finding; surface `divergence_factor` + `provenance.run_ids`.
- **B2 — Readout validation status.** Fetch `/api/study-observable-check`; badge the readouts table with the computed status (`ok/unresolved/not_in_structure/aspirational`) beside the authored status.
- **B3 — Per-test computed outcomes.** Render `measured_value`/`evaluated_by`/`reconcile` per-test (not aggregate), authored-vs-computed separated, the value linked to its run + the band that judged it. Add an outcomes column to the Runs table.

### Thread C — Restructure the study page + report narrative
- **C1 — Coherent spine flow on the study page.** Collapse the spine into one readable inputs→design→runs→outcomes→verdicts→findings flow; de-duplicate (follow-ups appear in 3 places; outcomes in 2–3); fix collapsed-by-default / buried spine-critical content (purpose, behavioral tests, multi-axis status, discovery implications).
- **C2 — Investigation narrative + verdict DAG** in the report: the story of what each study unblocks, the roll-up to acceptance, a verdict-annotated dependency graph.

## Order & rationale
`A → B → C`. A is the cheapest and most visible (the acceptance/verdict data is already produced and even reaches the client); B connects the evidence following the param-enforcement pattern; C is the larger restructure that benefits from A and B being in place. Each thread ships independently and improves the product on its own.

## Non-goals
- No new computed content — this program only *presents* what the spine already produces.
- No AI in the dashboard; no second source of truth (render the spine's data).
- No frontend framework rewrite; changes are additive and reuse the existing renderers/templates.
- Results/charts from sms-api are out of scope (that is read-only-dashboard #3).

## Success criteria
- A reader can answer "did this study pass, why, and what's the evidence" — and "does this investigation pass" — without leaving the page or cross-referencing tabs by hand.
- Every computed artifact in the gap list is surfaced, connected to its source, and visibly distinguished from authored content, with divergence flagged — following the param-enforcement-banner template.
- The dashboard imports no AI dependency; the report and UI render the same spine data.
