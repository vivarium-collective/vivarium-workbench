# Study Overview Declutter — Design

**Date:** 2026-06-28
**Status:** Design — approved, pending spec review
**Repo:** `vivarium-dashboard` (branch `feat/study-overview-declutter`, off `main`)
**Part of:** the larger "streamline the investigation structure around the 5 pillars" effort — this is the first slice (the **Understand** pillar). Tab consolidation + the other pillars are later stages.

## Goal

The study-detail **Overview** tab stacks ~16 blocks — a Report panel, Biology summary, a derived **Study card**, **Purpose**, **Findings**, plus duplicate **Question/Hypothesis/Status** sections (v4-vs-legacy fallbacks) and a long tail of planning sections. Much of it restates the same content (the Study card's own comment says its slots are "DERIVED from canonical fields"), and several fields appear two or three times. The tab is noisy and hard to make sense of.

Reorganize it into **4 clear groups** that preserve every editable field and JS hook, cut the one genuinely redundant block (the derived Study card table), and collapse the planning tail — so a reviewer immediately sees the study's summary, question, and findings.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| Target shape | **4 groups**: Summary · Question & approach · Findings & open debts · Plan & provenance (collapsed). |
| The derived **Study card** table | **Cut it** — it's a restatement of canonical fields shown elsewhere. |
| Editable fields / JS hooks | **Preserve all** (regroup, don't delete) except the Study-card inputs being cut. |

## Current state (grounded)

`templates/study-detail.html`, `#panel-overview` (lines ~253–760). Blocks, in order:
1. **Report — executive summary** (`data-narrative-card="report"`): computed Verdict (`computed_gate_verdict`), editable `report.confidence`/`report.evidence_quality`/`report.caveat`/`report.conclusion`, computed Main insight. *(editable narrative inputs)*
2. **Open epistemic debts** (`#epistemic-debts-panel`, `{% if epistemic_debts %}`). *(read-only, server-computed)*
3. **Biology — what this study is about** (`data-narrative-path="biological_summary"` textarea). *(editable)*
4. **Study card** (5 rows `data-narrative-path="study_card.*"`, all derived). → **CUT**
5. **Literature anchors** (`{% if study.literature_anchors %}`, read-only).
6. **Findings** (`{% if study.findings %}`, read-only cards).
7. **Purpose** (`{% if study.purpose %}`: Question/Mechanism/Expected callouts) **else** legacy **Question** (`#question-text` editable) + **Hypothesis** (`#hypothesis-text` editable) + (`{% if study.objective %}`) a *second* **Question** + **Hypothesis** + **Status** (`#status-select`) + **Objective** (`#objective-text` editable). ← the duplicate blocks.
8. **Pipeline gate** (`<details>`). 9. **Key assumptions** (`<details>`). 10. **Behavioral tests** (summary + `_setStudyTab('tests')` link). 11. **Pre-run expert review** (`<details>`). 12. **Limitations**. 13. **Follow-up studies** (link to Decide/Conclusions). 14. **Status** (`#status-select`, editable) + phase. 15. **Counts strip** (sim/readouts/requirements/runs). 16. **Feedback panel** (`#feedback-tracked-panel`, JS-populated).

JS that must keep working (verified): `study-detail.js` wires `document.querySelectorAll('[data-editable="true"]')` (inline-edit) and `document.querySelectorAll('[data-narrative-path]')` (narrative-save) — **generic selectors**, so removing the Study-card inputs is safe; the remaining editable elements keep their ids/`data-field`/`data-narrative-path`. `#epistemic-debts-panel` and `#feedback-tracked-panel` are referenced by id; keep them.

## Architecture

Pure template restructure of `#panel-overview` (+ small CSS for the new group styling). No backend, no JS-handler changes (the generic selectors and preserved ids/attrs keep authoring + populate-by-JS working). The 4 groups:

### Group 1 — Summary *(visible)*
- The **Report — executive summary** card, unchanged in fields, but the long helper paragraph (the "Verdict + main insight are computed…" block) trimmed to one short line.
- Immediately followed by the **Biology — what this study is about** prose (`biological_summary` textarea) — the narrative belongs with the summary.

### Group 2 — Question & approach *(visible)*
One section rendering each of three fields **exactly once**, preferring the v4 `purpose.*` callout and falling back to the editable legacy prose (preserving the editable ids):
- **Question** — `study.purpose.question` callout, else editable `#question-text` (`data-editable`/`data-field="question"`).
- **Hypothesis / expected outcome** — `study.purpose.expected_outcome` callout, else editable `#hypothesis-text` (`data-field="hypothesis"`).
- **Mechanism / model change** — `study.purpose.mechanism` callout, else editable `#objective-text` (`data-field="objective"`).
- The duplicate second Question/Hypothesis blocks are removed; each field appears once.
- The **Study card** table is removed entirely.

### Group 3 — Findings & open debts *(visible)*
- The **Findings** list (unchanged), under the group.
- The **Open epistemic debts** panel (`#epistemic-debts-panel`, unchanged, still `{% if epistemic_debts %}`) — moved to sit directly under Findings ("what we learned / what's still open").

### Group 4 — Plan & provenance *(one collapsed `<details open=false>`)*
A single collapsible wrapper (summary: "Plan & provenance") containing, in order, the secondary blocks unchanged in content: Pipeline gate, Key assumptions, Behavioral-tests summary (+ Tests-tab link), Pre-run expert review, Limitations, Follow-up studies (+ Decide-tab link), Literature anchors, **Status** (`#status-select`, editable — the single surviving status control, with the fuller option list + phase line) + the Counts strip. The **Feedback panel** (`#feedback-tracked-panel`) stays at the very bottom (outside or at the end of the group) so JS still finds it.
- The inner `<details>` (Pipeline gate, Key assumptions, Pre-run expert review) become plain blocks inside the one outer `<details>` (no nested collapsibles), to avoid a collapse-inside-collapse.

## Data flow

Open a study → Overview renders the 4 groups. Editing a narrative field (`[data-narrative-path]`) or an inline-editable prose/status (`[data-editable]`) saves to `study.yaml` exactly as today (handlers unchanged, ids preserved). `epistemic_debts` and the feedback panel populate as before.

## Error handling / compatibility

- Backward-compatible with both schema versions: the v4 `purpose.*` path and the legacy `question`/`hypothesis`/`objective` editable path are both handled in Group 2 (each field once). A study with neither renders an empty (placeholder) editable field, as today.
- No removed JS hook: only the cut Study-card `data-narrative-path="study_card.*"` inputs disappear (the generic save handler simply has fewer targets). All other ids/attrs preserved → no JS breakage.
- Conditional blocks keep their `{% if %}` guards (debts, literature anchors, findings, pipeline gate, etc.) so absent data renders nothing.

## Testing

- **`tests/test_study_overview_structure.py`** (renders the template or asserts on its source — match the repo's existing template-test pattern; if none, assert on the `study-detail.html` source text):
  - The four group headers are present once each: "Summary", "Question & approach", "Findings", "Plan & provenance".
  - `data-narrative-path="study_card.` does **not** appear (Study card cut).
  - The editable hooks survive: `id="question-text"`, `id="hypothesis-text"`, `id="objective-text"`, `id="status-select"`, `data-narrative-path="report.conclusion"`, `data-narrative-path="biological_summary"`, `id="epistemic-debts-panel"`, `id="feedback-tracked-panel"` each appear.
  - No duplicate editable status: `id="status-select"` appears exactly once.
  - The Tests/Decide tab links (`_setStudyTab('tests')`, `_setStudyTab('conclusions')`) survive.
- **Manual:** serve against `v2e-readouts`, open a v4 study (e.g. `param-uq-01-elongation`) and a showcase study → Overview shows the 4 groups, no Study card, no duplicate Question/Hypothesis/Status; editing Confidence/Caveat/Conclusion/Question/Status still persists (reload shows the saved value); Plan & provenance is collapsed by default.

## Out of scope (later stages of the 5-pillar streamline)

- Tab consolidation (~11 tabs → the 5 pillars; remove the literal Readouts/Observables duplicate).
- The other pillars' tabs (Build/Simulations/Readouts/Visualizations) decluttering.
- Schema-debt collapse of the duplicate fields (B2b) — this slice tolerates both schemas in the template; it does not change `study.yaml`.
- Investigation-level (already partly addressed by the readability work).
