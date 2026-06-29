# Conclusions/Decide Declutter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the study-detail Conclusions/Decide panel (~14 stacked sections) into 4 clear groups, preserving every editable field + JS hook.

**Architecture:** Pure template restructure of `#panel-conclusions` in `study-detail.html`: regroup the existing sections under 4 `<h2 class="overview-label">` group headers, collapse the secondary tail into one `<details>`. No backend/JS-handler change (the `[data-narrative-path]` save handler + follow-up-seeding JS are generic; only markup grouping/order changes).

**Tech Stack:** Jinja template + small CSS reuse; pytest source-assertions + Jinja parse + a live-render check.

## Global Constraints

- **Regroup, don't delete:** every section's inner markup, `{% if %}` guards, ids, and editable inputs are preserved verbatim. The only changes are grouping/order + 4 new group headers + collapsing group 4. **Cut nothing.**
- **Preserve these hooks exactly** (their JS is generic and keys off them): the editable `data-narrative-path="conclusion_verdicts.regression_compatibility.basis"` / `.biological_validation.basis` / `.explanatory_gain.basis` inputs; `id="discovery-implications-section"`; `id="followups-authored"` (the seeding UI); any `_setStudyTab('conclusions')` references.
- **Panel identity unchanged:** the `<section class="study-tab-panel" data-kind="conclusions" id="panel-conclusions" hidden>` wrapper stays.
- **4-group mapping (binding):**
  1. **Verdict & conclusion** ← ⚖️ Verdicts (3-track, editable basis) + Conclusion logic (gate decision) + Conclusion text.
  2. **Evidence** ← Latest run outcomes + synthesis Claims + Evidence.
  3. **Follow-ups & decisions** ← Discovery implications (`#discovery-implications-section`: alternate hypotheses / mechanism update proposals / follow-up study proposals) + Follow-up studies seeding (`#followups-authored`).
  4. **Limitations & provenance** ← synthesis Limitations + Next steps; this group is ONE collapsed `<details>` (no `open`).
- Run tests via `/Users/eranagmon/code/venv/bin/python -m pytest`; the panel must Jinja-parse.

---

### Task 1: Restructure `#panel-conclusions` into 4 groups

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html` (`#panel-conclusions`, ~lines 1569–1928)
- Test: `tests/test_conclusions_structure.py`

**Interfaces:** none (self-contained template change).

- [ ] **Step 1: Write the failing structure tests**

```python
# tests/test_conclusions_structure.py
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_dashboard/templates/study-detail.html").read_text()


def _panel():
    start = HTML.index('id="panel-conclusions"')
    # the panel ends at the next study-tab-panel section or the template end
    nxt = HTML.find('class="study-tab-panel"', start + 10)
    return HTML[start: nxt if nxt != -1 else len(HTML)]


def test_four_group_headers_present():
    panel = _panel()
    for h in ["Verdict &amp; conclusion", "Evidence", "Follow-ups &amp; decisions", "Limitations &amp; provenance"]:
        assert h in panel or h.replace("&amp;", "&") in panel, f"missing group header: {h}"


def test_editable_verdict_basis_inputs_preserved():
    panel = _panel()
    for track in ["regression_compatibility", "biological_validation", "explanatory_gain"]:
        assert f'data-narrative-path="conclusion_verdicts.{track}.basis"' in panel


def test_js_hooks_preserved():
    panel = _panel()
    assert 'id="discovery-implications-section"' in panel
    assert 'id="followups-authored"' in panel


def test_panel_identity_unchanged():
    assert 'data-kind="conclusions" id="panel-conclusions"' in HTML


def test_group4_is_collapsed_details():
    panel = _panel()
    # the Limitations & provenance group is a <details> with no `open`
    i = panel.find("Limitations &amp; provenance")
    if i == -1:
        i = panel.find("Limitations & provenance")
    assert i != -1
    # the group header sits inside a <details ...><summary> opened just before it
    before = panel[max(0, i - 400):i]
    assert "<details" in before and "open" not in before.split("<details", 1)[1][:120]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_conclusions_structure.py -q`
Expected: FAIL — no group headers yet.

- [ ] **Step 3: Reconstruct the panel into 4 groups**

Read `#panel-conclusions` (`vivarium_dashboard/templates/study-detail.html`, ~1569–1928) in full. Reorder its existing sections into the 4 groups below, **moving each section's markup verbatim** (keep every inner `<h3>`, `{% if %}`, id, `data-narrative-path`, control, and the section's existing classes). Wrap each group in `<div class="overview-section"><h2 class="overview-label">GROUP NAME</h2> … </div>` (reuse the existing `.overview-section`/`.overview-label` styling). Group 4 is `<details class="overview-section"><summary class="overview-label">Limitations &amp; provenance</summary> … </details>` (collapsed, no `open`).

The reconstruction (move the EXISTING blocks; do not rewrite their internals):

```
<section class="study-tab-panel" data-kind="conclusions" id="panel-conclusions" hidden>

  <div class="overview-section"><h2 class="overview-label">Verdict &amp; conclusion</h2>
    {{ the ⚖️ Verdicts three-track block (with conclusion_verdicts.*.basis inputs + computed results) }}
    {{ the "Conclusion logic — gate decision" block (moved out of the old Decide section) }}
    {{ the "Conclusion" block }}
  </div>

  <div class="overview-section"><h2 class="overview-label">Evidence</h2>
    {{ the "Latest run outcomes" block }}
    {{ the synthesis "Claims (from findings)" sub-block }}
    {{ the synthesis "Evidence (from findings)" sub-block }}
  </div>

  <div class="overview-section"><h2 class="overview-label">Follow-ups &amp; decisions</h2>
    {{ the Discovery implications block — id="discovery-implications-section", with its
       Alternate hypotheses / Mechanism update proposals / Follow-up study proposals sub-sections }}
    {{ the "Follow-up studies — pick one to seed" block — id="followups-authored" }}
  </div>

  <details class="overview-section"><summary class="overview-label">Limitations &amp; provenance</summary>
    {{ the synthesis "Limitations (from limitations)" sub-block }}
    {{ the synthesis "Next steps (from discovery implications)" sub-block }}
  </details>

</section>
```

Notes for the implementer:
- The old "Decide" `<h2>` wrapper and the old "Discovery implications" top-level `<h2>` are replaced by the 4 group headers; their *contents* move into the groups above. The old `<h3 class="overview-label">` sub-headers (Latest run outcomes, Conclusion logic — gate decision, Conclusion) stay as `<h3>` inside their new group.
- Demote the existing `<h2 class="overview-label">Discovery implications</h2>` and `<h2 ...>⚖️ Verdicts …</h2>` / `<h2 …>Decide</h2>` to plain content under the new group `<h2>`s (i.e. the old `<h2>` headers become `<h3>` sub-headers or are dropped where the new group header subsumes them — keep a sub-header if it labels content within a group, e.g. keep "⚖️ Verdicts — three-track outcome" as an `<h3>` inside Group 1).
- If a synthesis sub-section can't be cleanly separated (Claims/Evidence vs Limitations/Next steps share one container loop), split the container so Claims+Evidence land in Group 2 and Limitations+Next steps in Group 4 — preserving each sub-section's markup.
- Cut nothing; if unsure where a stray block belongs, put it in Group 4 (provenance) and note it.

- [ ] **Step 4: Run tests + Jinja parse**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_conclusions_structure.py -q && /Users/eranagmon/code/venv/bin/python -c "from jinja2 import Environment; Environment().parse(open('vivarium_dashboard/templates/study-detail.html').read()); print('PARSE_OK')"`
Expected: PASS (5 passed) + PARSE_OK.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html tests/test_conclusions_structure.py
git commit -m "feat(conclusions): restructure Conclusions/Decide into 4 groups"
```

- [ ] **Step 6: Live-render verification (the strong gate — documented; controller runs it)**

Serve against `v2e-readouts` (`/Users/eranagmon/code/venv/bin/python -m vivarium_dashboard.cli serve --workspace /Users/eranagmon/code/v2e-readouts --port 8814`) and open a study with conclusions (e.g. a showcase study) → its Inquire → Conclusions/Decide tab:
- HTTP 200, the 4 groups render; group 4 (Limitations & provenance) collapsed.
- The verdict-basis inputs are still editable and persist (edit one, reload, value saved).
- The follow-up-seeding UI under "Follow-ups & decisions" still works.
- No section content lost vs before (Discovery implications, run outcomes, claims/evidence, limitations, next steps all present).

---

## Self-Review

**Spec coverage:**
- 4-group restructure of `#panel-conclusions` per the binding mapping → Task 1 Step 3. ✓
- Group 4 collapsed `<details>` → mapping + `test_group4_is_collapsed_details`. ✓
- Preserve editable basis inputs + `#discovery-implications-section` + `#followups-authored` → constraints + `test_editable_verdict_basis_inputs_preserved`/`test_js_hooks_preserved`. ✓
- Panel identity unchanged → `test_panel_identity_unchanged`. ✓
- Cut nothing → Step 3 note + live-render "no section content lost". ✓
- Out of scope (Compose unify, B2b, cutting content) → not touched. ✓

**Placeholder scan:** The `{{ ... }}` lines in Step 3 are a structural map of which EXISTING blocks move where (the implementer reads the real panel and moves them) — not code placeholders; paired with the verbatim-move constraint + the must-preserve grep list + the structure tests, the requirement is concrete. Complete test code given; exact commands with expected output.

**Type consistency:** The group header strings ("Verdict & conclusion", "Evidence", "Follow-ups & decisions", "Limitations & provenance") are identical between the binding mapping, Step 3, and the test assertions (with `&amp;` HTML-encoding handled in the test). The preserved hook strings (`conclusion_verdicts.*.basis`, `#discovery-implications-section`, `#followups-authored`) match between constraints, Step 3, and tests.
