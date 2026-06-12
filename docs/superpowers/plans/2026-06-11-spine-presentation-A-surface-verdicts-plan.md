# Spine Presentation — Thread A (Surface the computed verdicts) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Program:** Spine Presentation (spec: `docs/superpowers/specs/2026-06-11-spine-presentation-design.md`), thread A — the cheapest, most visible win: surface the computed verdicts the spine already produces (and that already reach the client).

**Goal:** Render three currently-invisible computed artifacts following the param-enforcement-banner pattern (surfaced · connected · code-computed-vs-authored): the investigation acceptance roll-up, the study gate-verdict divergence, and a lint/readiness panel.

**The pattern to copy:** the param-enforcement banner — `static/walkthrough.js:7126-7143` (`s.param_enforcement` → a styled banner, connected to the run, labeled "code-computed"). Mirror its structure for each new surface.

**Tech:** Python stdlib + Jinja2 + vanilla JS; pytest. Repos: vivarium-dashboard (rendering + endpoints) + pbg-superpowers (the lint function, already exists). `.venv/bin/python`. The Python/data layer is TDD'd; the rendering is structural-tested (markup/data present) + a manual verify step.

**AI-free:** every surface renders deterministic data the spine already computes; no AI in the dashboard.

---

## Task 1 — A2: Study gate-verdict divergence (smallest, server-side first)

**Files:** Modify `vivarium_dashboard/server.py` (~1712-1716); `static/walkthrough.js` + `static/study-detail.js`/`templates/study-detail.html` (gate pill); Test `tests/test_gate_verdict_surface.py`.

- [ ] **Step 1: Failing test (server).** The study/iset detail response must carry the persisted `gate_evaluator` including `diverges_from_authored`, not a recompute that drops it.
```python
def test_detail_surfaces_persisted_gate_evaluator(tmp_study_with_gate_evaluator):
    # study.yaml has pipeline_gate.gate_evaluator = {result, diverges_from_authored: true, evaluated_by: code}
    spec = server._study_detail_spec(slug)
    ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator") or spec.get("computed_gate_verdict")
    assert ge and ge.get("diverges_from_authored") is True
    assert ge.get("evaluated_by") == "code"
```
- [ ] **Step 2: Run → fail** (current `roll_up_verdict` recompute drops `diverges_from_authored`).
- [ ] **Step 3: Implement.** At `server.py:1714-1715`, prefer the PERSISTED `pipeline_gate.gate_evaluator` (written by SP1's `write_gate_evaluator`) when present — it already carries `result`, `evaluated_by`, `diverges_from_authored`; only fall back to `roll_up_verdict` when absent. Expose it on the spec as `computed_gate_verdict` (keep the key for the frontend) with `diverges_from_authored` intact.
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Render (study page + report pill).** Beside the authored gate pill (`templates/study-detail.html:21-22` and the per-study report pill `walkthrough.js:~5965/5998`), when `computed_gate_verdict.diverges_from_authored`, render a small "code: <result> · authored: <gate_status>" divergence chip (mirror the param-enforcement banner's styling/labeling). Add a structural test asserting the JS references `computed_gate_verdict` + `diverges_from_authored`.
- [ ] **Step 6: Commit** — `feat(spine-present): surface persisted gate_evaluator + code-vs-authored divergence chip`

## Task 2 — A1: Investigation acceptance roll-up

**Files:** `static/walkthrough.js` (the investigation executive fold, ~8630-8638; the removed acceptance list ~7741-7749); Test: structural + the API already returns the data.

- [ ] **Step 1: Confirm the data reaches the client.** Assert (a test on the iset response) that `/api/iset/<inv>` / `_get_iset_detail` carries `executive.computed_acceptance` with per-criterion entries (it is computed at `server.py:13075`). If it is NOT in the response, add it (read-only passthrough).
```python
def test_iset_response_carries_computed_acceptance(tmp_inv_with_acceptance):
    data = json.loads(server.Handler._iset_detail_data(inv))   # or the endpoint
    ca = (data.get("executive") or {}).get("computed_acceptance") or data.get("computed_acceptance")
    assert ca and "criteria" in ca   # per-criterion study->behavior->result
```
- [ ] **Step 2: fail/confirm. Step 3: Render.** In the investigation executive fold (`walkthrough.js:~8630`), render `computed_acceptance.criteria` as a per-criterion table: `study → behavior → result`, beside the authored `executive.verdict_status`, with a divergence badge when `computed_acceptance.diverges_from_authored` (or `computed_verdict_status` ≠ authored). This restores the acceptance visibility that was removed at `7741-7749`, now computed + connected to the member studies' verdicts. Each criterion's study links to that study's section.
- [ ] **Step 4: Structural test** — `walkthrough.js` references `computed_acceptance` + renders `criteria` + a divergence badge. **Step 5: Commit** — `feat(spine-present): render investigation computed_acceptance roll-up + divergence`

## Task 3 — A3: Lint / readiness panel

**Files:** `vivarium_dashboard/server.py` (new `GET /api/report-lint`); `static/walkthrough.js` or `study-detail.js` (the readiness panel); Test `tests/test_report_lint_endpoint.py`.

- [ ] **Step 1: Failing test (endpoint).**
```python
def test_report_lint_endpoint_returns_findings(tmp_workspace_with_legacy_readouts):
    body, code = server.Handler._report_lint_test(server.WORKSPACE)
    assert code == 200
    findings = json.loads(body)["findings"]   # per-study, with check + severity + message
    # surfaces SP2b-ii readout-migration + SP2c band-citation-gap findings
    assert any("readout" in f.get("check","") or "needs_human" in f.get("message","").lower() for f in findings) or findings == []
```
- [ ] **Step 2: fail. Step 3: implement** `_report_lint(ws_root) -> (body, code)`: lazy-import `pbg_superpowers.report_linter.lint_workspace_report` (tolerant if absent → empty), return `{findings: [{study, check, severity, message}]}`. Add the `do_GET` branch `/api/report-lint` + route. The findings already include the SP2b-ii readout-migration (migratable=info, needs_human=warning) + SP2c band-citation-gap surfaces — this one endpoint wires three computed artifacts.
- [ ] **Step 4: pass.**
- [ ] **Step 5: Render the readiness panel.** Per study, a small "Readiness" panel/badge: ✓ ready (no findings) · ⚠ N gaps (warnings) — listing the lint findings (readout `needs_human`, uncited bands) with severity colors, mirroring the param-enforcement banner. Fetch `/api/report-lint` once per workspace/investigation; key findings by study. Structural test that the JS fetches `/api/report-lint` + renders findings.
- [ ] **Step 6: Commit** — `feat(spine-present): GET /api/report-lint + per-study readiness panel (migration + citation-gap findings)`

## Task 4 — Manual verify + golden

- [ ] **Step 1 (Python golden, skipif v2e-invest absent, READ-ONLY):** `_report_lint("/Users/eranagmon/code/v2e-invest")` returns findings for its real studies (the readout `needs_human` set from SP2b-ii); `_study_detail_spec` for a real study carries `computed_gate_verdict`; the iset response carries `computed_acceptance`. No writes to v2e-invest.
- [ ] **Step 2:** `tests/test_gate_verdict_surface.py tests/test_report_lint_endpoint.py` + the structural tests green; existing server tests no new failures (pre-existing environmental verified via base).
- [ ] **MANUAL VERIFY (pending — no JS harness):** serve v2e-invest; open a study + its investigation; confirm: the gate divergence chip shows when computed≠authored; the investigation executive shows the acceptance roll-up per criterion; the readiness panel lists the migration/citation findings. Each is visibly labeled code-computed and links to its source.
- [ ] **Step 3: Commit** — `test(spine-present): thread-A golden + suite`

---

## Self-Review
- Coverage: gate divergence (T1/A2), acceptance roll-up (T2/A1), lint/readiness (T3/A3), golden+manual (T4). Matches thread A.
- Pattern: each surface mirrors the param-enforcement banner (surfaced · connected · code-vs-authored).
- AI-free: renders deterministic data; the new endpoint just runs the existing deterministic `lint_workspace_report`.
- No placeholders: grounded anchors (server.py:1715, 13075; walkthrough.js:7126, 8630, 7741). The rendering is structural-tested + manual (no JS harness) — explicitly flagged.
- Deferred: threads B (connect evidence) + C (restructure).

## Notes for the executor
- `.venv/bin/python -m pytest`. The Python/data layer (the gate_evaluator passthrough, the lint endpoint, the acceptance passthrough) is TDD'd; the JS rendering is structural-tested (assert the JS references the data + renders the markup) + the manual step — there is no JS test harness.
- COPY the param-enforcement banner (`walkthrough.js:7126-7143`) structure/labeling for every new surface — that is the approved pattern.
- Render the PERSISTED data the spine computes; do not recompute or add AI. `diverges_from_authored` must be read from the written `gate_evaluator`/`computed_acceptance`, not recomputed without it.
- Don't modify the real v2e-invest; goldens are read-only.
