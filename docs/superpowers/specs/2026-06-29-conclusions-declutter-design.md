# Conclusions/Decide Tab Declutter — Design

**Date:** 2026-06-29
**Status:** Design — approved, pending spec review
**Repo:** `vivarium-dashboard` (branch `feat/conclusions-declutter`, off `main`)
**Part of:** the "streamline around the 5 pillars" effort. Prior slices merged: Overview declutter, derivation consolidation, tab consolidation (5 pillars), `types` CI fix. This declutters the **Inquire** pillar's Conclusions/Decide panel — the last noisy panel (~14 stacked sections, like the old Overview).

## Goal

The study-detail **Conclusions/Decide** tab (`#panel-conclusions`, ~359 lines) stacks ~14 headed sections in a flat list — Discovery implications, the 3-track Verdicts, a Decide block (run outcomes / gate decision / conclusion / follow-up seeding), and a findings-synthesis block (Claims/Evidence/Limitations/Next steps). It's the same "noisy, hard to make sense of" problem the Overview had. Reorganize into **4 clear groups** that preserve every editable field + JS hook, collapsing the secondary tail.

## Decisions (locked with the user)

| Question | Decision |
|---|---|
| Target shape | **4 groups**: Verdict & conclusion · Evidence · Follow-ups & decisions · Limitations & provenance (collapsed). |
| Editable fields / JS hooks | **Preserve all** (regroup, don't delete) — pure template restructure. |

## Current state (grounded)

`templates/study-detail.html`, `#panel-conclusions` (lines ~1569–1928). Sections in order:
1. **Discovery implications** (`#discovery-implications-section`) — `<h3>` sub-sections: Alternate hypotheses, Mechanism update proposals, Follow-up study proposals. *(read-only, from `discovery_implications`)*
2. **⚖️ Verdicts — three-track outcome** — editable basis inputs `data-narrative-path="conclusion_verdicts.{regression_compatibility,biological_validation,explanatory_gain}.basis"` + the computed track results. *(authoring surface; the dict-form `conclusion_verdicts` basis editor)*
3. **Decide** (`<h2>`) — `<h3>` sub-sections: Latest run outcomes; Conclusion logic — gate decision; Conclusion; **Follow-up studies — pick one to seed** (`#followups-authored`, the seeding UI). *(mixed read-only + the followup-seed controls + JS)*
4. **Synthesis** — `<h3>`: Claims (from findings), Evidence (from findings), Limitations (from limitations), Next steps (from discovery implications). *(read-only, computed)*

JS hooks to keep working: `#discovery-implications-section`, the `[data-narrative-path]` basis inputs (the same generic `[data-narrative-path]` save handler used elsewhere), the `#followups-authored` seeding controls + their `_setStudyTab('conclusions')`/seed handlers. (study-detail.js wires `[data-narrative-path]` generically, so regrouping is safe.)

## Architecture

Pure template restructure of `#panel-conclusions` (+ small CSS reuse of the Overview group styling). No backend/JS-handler change. The 4 groups, each introduced by an `<h2 class="overview-label">` group header:

### Group 1 — Verdict & conclusion *(visible)*
- **⚖️ Verdicts** (the three-track block, with its editable `conclusion_verdicts.*.basis` inputs + computed results) — unchanged content.
- **Conclusion logic — gate decision** (from Decide).
- **Conclusion** (the conclusion text, from Decide).
The headline outcome + its reasoning, first.

### Group 2 — Evidence *(visible)*
- **Latest run outcomes** (from Decide).
- The **synthesis Claims** + **Evidence** sub-sections (computed from findings).
What the conclusion rests on.

### Group 3 — Follow-ups & decisions *(visible)*
- **Discovery implications** (`#discovery-implications-section`, with its Alternate hypotheses / Mechanism update proposals / Follow-up study proposals sub-sections) — unchanged.
- **Follow-up studies — pick one to seed** (`#followups-authored` seeding UI) — unchanged.
The forward decisions, grouped together (currently split between the top Discovery block and the Decide block).

### Group 4 — Limitations & provenance *(one collapsed `<details>`)*
- The synthesis **Limitations** + **Next steps** sub-sections (computed). The secondary tail, tucked away.

## Data flow

Open a study → Inquire pillar → Conclusions/Decide tab renders the 4 groups. Editing a verdict `basis` (`[data-narrative-path]`) saves to `study.yaml` exactly as today (handler unchanged, ids preserved). Seeding a follow-up via `#followups-authored` works unchanged. Group 4 is collapsed by default.

## Error handling / compatibility

- Pure regroup: every section's inner markup, `{% if %}` guards, ids, and editable inputs are preserved verbatim — only their grouping/order and the new group headers change. The generic `[data-narrative-path]` / seeding JS keeps working (fewer/no element changes).
- Conditional sections (e.g. discovery implications only when `discovery_implications` present) keep their guards → absent data renders nothing, as today.
- No `study.yaml` change; tolerant of both schema versions (the panel already handles them).

## Testing

- **`tests/test_conclusions_structure.py`** (assert on `study-detail.html` source):
  - The four group headers present once each: "Verdict & conclusion", "Evidence", "Follow-ups & decisions", "Limitations & provenance".
  - Editable + JS hooks survive: `data-narrative-path="conclusion_verdicts.regression_compatibility.basis"` (and the other two tracks), `id="discovery-implications-section"`, `id="followups-authored"` each present.
  - The panel still has `data-kind="conclusions" id="panel-conclusions"` (one).
  - Group 4 is a collapsed `<details>` (no `open`) wrapping Limitations/Next steps.
- **Jinja parses:** `Environment().parse(...)` OK.
- **Live render (the strong gate):** serve against `v2e-readouts`, open a study's Conclusions/Decide tab (e.g. a showcase study) → HTTP 200, the 4 groups present, the verdict-basis inputs still editable + persist, the follow-up-seed UI works, group 4 collapsed.

## Out of scope (later slices)

- Compose-pillar unification (4 sub-panels → one view).
- B2b schema-debt collapse (the `conclusion_verdicts` list/dict + v2/v3/v4 duality).
- Investigation-level page declutter beyond the graph readability already shipped.
- Cutting any computed synthesis content (this slice regroups; it does not remove sections).
