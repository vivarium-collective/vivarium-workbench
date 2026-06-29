# Investigation Detail Page Declutter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the single investigation detail page into a clean, uniform top-to-bottom overview — consolidate the four intro collapsibles into one "About" disclosure, elevate "Needs attention" to a banner below the header, condense the graph-lead prose into a caption + tooltip, and tighten the header actions.

**Architecture:** A DOM-skeleton restructure in `templates/index.html.j2` (the `#investigation-detail-view` block, ~lines 876–933) that **preserves every JS-addressed element id**, so the existing `static/walkthrough.js` render functions keep populating them. The only JS change is removing the now-dead `#investigation-at-a-glance` cleanup lines.

**Tech Stack:** Jinja template (`index.html.j2`) + vanilla JS (`walkthrough.js`) + pytest source-assertions + Jinja-parse + `node --check`.

## Global Constraints

- **Preserve these JS-addressed ids verbatim** (render functions target them): `investigation-detail-description`, `investigation-how-to-read` (+ its inner `<ol>`), `investigation-glossary` (+ its inner `<dl>`), `investigation-biology-story`, `investigation-biology-story-text`, `investigation-needs-attention`, `investigation-dag-lead`, `investigation-detail-refresh`, `investigation-intro`, `investigation-intro-details`.
- **No data/API change.** Only the skeleton's structure/order and the header chrome change. What gets fetched and shown is identical.
- `_renderInvHowToRead`/`_renderInvGlossary` toggle `host.style.display` and write into `host.querySelector('ol'|'dl')` — they do NOT use `<details>.open`. So demoting those hosts from `<details>` to `<div>` (with the same id + inner `<ol>`/`<dl>`) is safe and needs no JS change.
- Tests assert on `index.html.j2` (and `walkthrough.js`) **source**, scoped to the `#investigation-detail-view` block. Template must Jinja-parse; JS must pass `node --check`.
- Run python via `/Users/eranagmon/code/venv/bin/python`.
- Co-author commits: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

**Shared test helper** (put at the top of `tests/test_investigation_page_declutter.py`, used by all tasks):

```python
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_dashboard/templates/index.html.j2").read_text()
JS = (ROOT / "vivarium_dashboard/static/walkthrough.js").read_text()


def detail_view():
    """The #investigation-detail-view block, up to the next page section."""
    i = HTML.index('id="investigation-detail-view"')
    j = HTML.index('id="page-github"', i)
    return HTML[i:j]
```

---

### Task 1: Tighten the header actions

**Files:**
- Modify: `vivarium_dashboard/templates/index.html.j2` (the header flex `<div>`, ~lines 880–888)
- Test: `tests/test_investigation_page_declutter.py`

**Interfaces:**
- Produces: a `<span class="inv-export-actions">` wrapping the report + notebook buttons; an icon-only `#investigation-detail-refresh` button. No new JS symbols.

- [ ] **Step 1: Write the failing test** (add the shared helper above first if not present)

```python
def test_header_export_cluster_and_icon_refresh():
    dv = detail_view()
    assert 'inv-export-actions' in dv, "export cluster wrapper missing"
    cluster_start = dv.index('inv-export-actions')
    cluster = dv[cluster_start:cluster_start + 600]
    assert '_generateInvestigationReport()' in cluster, "report button not in export cluster"
    assert '_downloadInvestigationNotebook()' in cluster, "notebook button not in export cluster"
    # Refresh is icon-only now (no text label on the button itself)
    assert '↻ Refresh</button>' not in HTML, "Refresh button still has a text label"
    assert 'id="investigation-detail-refresh"' in dv, "refresh button id lost"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_page_declutter.py::test_header_export_cluster_and_icon_refresh -q`
Expected: FAIL (no `inv-export-actions`; `↻ Refresh` still present).

- [ ] **Step 3: Restructure the header row**

Find this block (~lines 882–888):

```html
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:8px;">
      <h3 id="investigation-detail-title" style="margin:0; flex:1"></h3>
      <span id="investigation-detail-status" class="status-pill planned" style="font-size:0.85em"></span>
      <span class="viv-info-chip" data-tooltip="Refresh &mdash; re-fetch the investigation from disk. Use after editing YAML files directly.&#10;&#10;Generate report &mdash; self-contained HTML report you can email.&#10;&#10;Download notebook &mdash; a self-contained Jupyter notebook (+ matching .py) that re-runs the studies and renders their figures.">?</span>
      <button class="btn-mini" id="investigation-detail-refresh" onclick="_refreshInvestigationDetail()" title="Re-fetch the investigation + its studies from disk. Use after editing YAML files directly (which the dashboard can't see otherwise).">↻ Refresh</button>
      <button class="btn-mini" onclick="_generateInvestigationReport()" title="Generate a shareable HTML report (self-contained, attach to email)">Generate report 📄</button>
      <button class="btn-mini" onclick="_downloadInvestigationNotebook()" title="Download a self-contained Jupyter notebook (+ matching .py) that re-runs this investigation's studies via the process-bigraph protocol and renders their figures. The coder-facing complement to the HTML report.">Download notebook 📓</button>
    </div>
```

Replace it with (title · status · export cluster · icon-Refresh · info-chip):

```html
    <div style="display:flex; align-items:center; gap:10px; margin-bottom:8px;">
      <h3 id="investigation-detail-title" style="margin:0; flex:1"></h3>
      <span id="investigation-detail-status" class="status-pill planned" style="font-size:0.85em"></span>
      <span class="inv-export-actions" style="display:inline-flex; gap:6px">
        <button class="btn-mini" onclick="_generateInvestigationReport()" title="Generate a shareable HTML report (self-contained, attach to email)">Report 📄</button>
        <button class="btn-mini" onclick="_downloadInvestigationNotebook()" title="Download a self-contained Jupyter notebook (+ matching .py) that re-runs this investigation's studies via the process-bigraph protocol and renders their figures. The coder-facing complement to the HTML report.">Notebook 📓</button>
      </span>
      <button class="btn-mini" id="investigation-detail-refresh" onclick="_refreshInvestigationDetail()" title="Re-fetch the investigation + its studies from disk. Use after editing YAML files directly (which the dashboard can't see otherwise).">↻</button>
      <span class="viv-info-chip" data-tooltip="Report &mdash; self-contained HTML report you can email.&#10;&#10;Notebook &mdash; a self-contained Jupyter notebook (+ matching .py) that re-runs the studies and renders their figures.&#10;&#10;Refresh (↻) &mdash; re-fetch the investigation from disk after editing YAML directly.">?</span>
    </div>
```

- [ ] **Step 4: Run test + Jinja parse**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_page_declutter.py::test_header_export_cluster_and_icon_refresh -q && /Users/eranagmon/code/venv/bin/python -c "from jinja2 import Environment; Environment().parse(open('vivarium_dashboard/templates/index.html.j2').read()); print('PARSE_OK')"`
Expected: PASS + PARSE_OK.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/templates/index.html.j2 tests/test_investigation_page_declutter.py
git commit -m "feat(inv-page): tighten header — icon-only Refresh + grouped export actions"
```

---

### Task 2: Consolidate intro + elevate Needs-attention + drop at-a-glance

**Files:**
- Modify: `vivarium_dashboard/templates/index.html.j2` (the `#investigation-intro` block, ~lines 891–916)
- Modify: `vivarium_dashboard/static/walkthrough.js` (the at-a-glance cleanup block in `_renderInvestigationDetail`, ~lines 5256–5258)
- Test: `tests/test_investigation_page_declutter.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: one `<details id="investigation-intro-details" ... open>` with summary "About this investigation" containing the lead + three demoted `<div>` sub-blocks; `#investigation-needs-attention` relocated above `#investigation-intro`; `#investigation-at-a-glance` removed (DOM + JS).

- [ ] **Step 1: Write the failing tests**

```python
def test_one_about_disclosure_with_demoted_subblocks():
    dv = detail_view()
    # Exactly one <summary> remains in the detail view: the About disclosure.
    assert dv.count('<summary>') == 1, f"expected 1 <summary>, got {dv.count('<summary>')}"
    assert '<summary>About this investigation</summary>' in dv
    # Standalone collapsibles gone; ids preserved as plain blocks.
    assert '<summary>How to read this</summary>' not in dv
    assert '<summary>Glossary</summary>' not in dv
    for id_ in ['investigation-detail-description', 'investigation-how-to-read',
                'investigation-glossary', 'investigation-biology-story',
                'investigation-biology-story-text']:
        assert f'id="{id_}"' in dv, f"lost id {id_}"
    # About open by default.
    about_start = dv.index('id="investigation-intro-details"')
    assert ' open' in dv[about_start:about_start + 120], "About disclosure not open by default"


def test_needs_attention_elevated_above_intro():
    dv = detail_view()
    na = dv.index('id="investigation-needs-attention"')
    intro = dv.index('id="investigation-intro"')
    assert na < intro, "needs-attention should appear before the intro block"


def test_at_a_glance_removed():
    assert 'id="investigation-at-a-glance"' not in HTML, "dead at-a-glance node still present"
    assert "getElementById('investigation-at-a-glance')" not in JS, "dead at-a-glance JS still present"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_page_declutter.py -k "about_disclosure or needs_attention_elevated or at_a_glance" -q`
Expected: FAIL (4 sibling summaries; needs-attention inside intro; at-a-glance present).

- [ ] **Step 3: Restructure the intro block (index.html.j2)**

Find this block (~lines 891–916):

```html
    <div id="investigation-intro" class="inv-intro" style="margin: 0 0 16px 0;">
      <!-- Lead paragraph (the abstract). Auto-styled as serif body type for readability. -->
      <details id="investigation-intro-details" class="inv-lead-details">
        <summary>About this investigation</summary>
        <div id="investigation-detail-description" class="inv-lead" style="margin-top:6px"></div>
      </details>
      <!-- At-a-glance grid: one tile per study with a one-line role. Populated by JS. -->
      <div id="investigation-at-a-glance" class="inv-at-a-glance" style="display:none"></div>
      <!-- How to read this: ordered list of usage tips for the evaluator. -->
      <details id="investigation-how-to-read" class="inv-how-to-read" style="display:none">
        <summary>How to read this</summary>
        <ol></ol>
      </details>
      <!-- Glossary: collapsible. -->
      <details id="investigation-glossary" class="inv-glossary" style="display:none">
        <summary>Glossary</summary>
        <dl></dl>
      </details>
      <!-- Biology story (free-form prose). Hidden if the yaml omits it. -->
      <details id="investigation-biology-story" class="inv-bio-story" style="display:none">
        <summary class="inv-bio-story-label">Biology — the mechanism this investigation models</summary>
        <div id="investigation-biology-story-text" class="inv-bio-story-text"></div>
      </details>
      <!-- SP5: Needs-attention panel. Populated by JS from /api/needs-attention. -->
      <div id="investigation-needs-attention"></div>
    </div>
```

Replace it with (needs-attention pulled OUT and ABOVE; one About disclosure; sub-blocks demoted to `<div>`+`<h4>`; at-a-glance removed):

```html
    <!-- Needs-attention elevated: a distinct, actionable banner directly below the
         header. Populated by JS from /api/needs-attention; renders nothing when empty. -->
    <div id="investigation-needs-attention"></div>

    <div id="investigation-intro" class="inv-intro" style="margin: 0 0 16px 0;">
      <!-- Single "About" disclosure (open by default): the lead/abstract, then any
           supplementary context as labeled sub-blocks. Each sub-block keeps its id +
           inner <ol>/<dl> so the existing render functions populate it unchanged; JS
           toggles their display, so they stay hidden when the yaml omits them. -->
      <details id="investigation-intro-details" class="inv-lead-details" open>
        <summary>About this investigation</summary>
        <div id="investigation-detail-description" class="inv-lead" style="margin-top:6px"></div>
        <div id="investigation-how-to-read" class="inv-how-to-read" style="display:none; margin-top:12px">
          <h4 class="inv-subhead">How to read this</h4>
          <ol></ol>
        </div>
        <div id="investigation-glossary" class="inv-glossary" style="display:none; margin-top:12px">
          <h4 class="inv-subhead">Glossary</h4>
          <dl></dl>
        </div>
        <div id="investigation-biology-story" class="inv-bio-story" style="display:none; margin-top:12px">
          <h4 class="inv-subhead inv-bio-story-label">Biology — the mechanism this investigation models</h4>
          <div id="investigation-biology-story-text" class="inv-bio-story-text"></div>
        </div>
      </details>
    </div>
```

- [ ] **Step 4: Remove the dead at-a-glance JS (walkthrough.js)**

Find this block in `_renderInvestigationDetail` (~lines 5255–5258):

```javascript
        // At-a-glance study-card row removed (user request 2026-06-07): the
        // dependency DAG below shows the same studies, so the top row was
        // redundant. Clear + hide the host so no empty band remains.
        var _aagHost = document.getElementById('investigation-at-a-glance');
        if (_aagHost) { _aagHost.innerHTML = ''; _aagHost.style.display = 'none'; }
```

Delete it entirely (the node no longer exists, so the cleanup is dead).

- [ ] **Step 5: Run tests + Jinja parse + node check**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_page_declutter.py -k "about_disclosure or needs_attention_elevated or at_a_glance" -q && /Users/eranagmon/code/venv/bin/python -c "from jinja2 import Environment; Environment().parse(open('vivarium_dashboard/templates/index.html.j2').read()); print('PARSE_OK')" && node --check vivarium_dashboard/static/walkthrough.js`
Expected: PASS (3 tests) + PARSE_OK + node exit 0.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/index.html.j2 vivarium_dashboard/static/walkthrough.js tests/test_investigation_page_declutter.py
git commit -m "feat(inv-page): one About disclosure, elevate Needs-attention, drop dead at-a-glance"
```

---

### Task 3: Condense the graph-lead into a caption + tooltip

**Files:**
- Modify: `vivarium_dashboard/templates/index.html.j2` (the `#investigation-dag-lead` block, ~lines 918–923)
- Test: `tests/test_investigation_page_declutter.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–2.
- Produces: a short `#investigation-dag-lead` caption with a `viv-info-chip` carrying the full explanation in `data-tooltip`.

- [ ] **Step 1: Write the failing test**

```python
import re

def test_dag_lead_condensed_with_tooltip():
    dv = detail_view()
    i = dv.index('id="investigation-dag-lead"')
    j = dv.index('</div>', i)
    block = dv[i:j]
    # Chip carries the full explanation.
    assert 'viv-info-chip' in block and 'data-tooltip=' in block, "info chip missing from dag-lead"
    assert 'knowledge-producing' in block, "full explanation lost"
    # The verbose explanation is no longer in the *visible* caption (only in the tooltip attr).
    visible = re.sub(r'<[^>]+>', '', block)
    assert 'knowledge-producing' not in visible, "verbose phrase still in visible caption"
    assert 'builds understanding of the mechanism' not in visible, "verbose phrase still visible"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_page_declutter.py::test_dag_lead_condensed_with_tooltip -q`
Expected: FAIL (no chip; verbose phrase visible).

- [ ] **Step 3: Condense the dag-lead**

Find this block (~lines 918–923):

```html
    <div id="investigation-dag-lead" style="margin: 4px 0 8px; font-size: 0.88em; color: #475569; line-height: 1.4;">
      <strong>Investigation graph</strong> — each study is a knowledge-producing operation that
      builds understanding of the mechanism. Each node shows the <em>question</em> it asks and
      the <em>evidence</em> it produced; edges show what a result leads to.
    </div>
```

Replace it with (short caption; full text moved into the chip tooltip — the visible caption must NOT contain "knowledge-producing" or "builds understanding"):

```html
    <div id="investigation-dag-lead" style="margin: 4px 0 8px; font-size: 0.88em; color: #475569; line-height: 1.4;">
      <strong>Investigation graph</strong>
      <span class="viv-info-chip" data-tooltip="Each study is a knowledge-producing operation that builds understanding of the mechanism. Each node shows the question it asks and the evidence it produced; edges show what a result leads to.">?</span>
      — nodes are studies; edges show what each result leads to.
    </div>
```

- [ ] **Step 4: Run test + Jinja parse**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_page_declutter.py::test_dag_lead_condensed_with_tooltip -q && /Users/eranagmon/code/venv/bin/python -c "from jinja2 import Environment; Environment().parse(open('vivarium_dashboard/templates/index.html.j2').read()); print('PARSE_OK')"`
Expected: PASS + PARSE_OK.

- [ ] **Step 5: Run the full new test file + commit**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_page_declutter.py -q`
Expected: all pass (6 tests).

```bash
git add vivarium_dashboard/templates/index.html.j2 tests/test_investigation_page_declutter.py
git commit -m "feat(inv-page): condense graph-lead into a caption + tooltip"
```

---

## Final verification (controller, after all tasks)

- `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_page_declutter.py -q` → 6 passed.
- Jinja parse OK; `node --check vivarium_dashboard/static/walkthrough.js` exit 0.
- Serve the SPA and confirm 200 on `/` (the shell) using the working recipe:
  `PYTHONPATH=<worktree>:/Users/eranagmon/code/process-bigraph /Users/eranagmon/code/vivarium-dashboard/.venv/bin/python -m vivarium_dashboard.cli serve --workspace /Users/eranagmon/code/v2e-main --port <P>` then `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:<P>/`.
- Note: full visual confirmation of the JS-rendered page needs a browser; the preserved-id property (Global Constraints) is what guarantees the render functions still populate `#investigation-detail-description`, the how-to-read `<ol>`, the glossary `<dl>`, `#investigation-biology-story-text`, and `#investigation-needs-attention` after the restructure.

## Self-Review

**Spec coverage:**
- ① Consolidate 4 intro collapsibles → one About disclosure (open) with demoted sub-blocks → Task 2 Step 3 + `test_one_about_disclosure_with_demoted_subblocks`. ✓
- ② Elevate Needs-attention above the intro → Task 2 Step 3 + `test_needs_attention_elevated_above_intro`. ✓
- ③ Condense graph-lead → caption + tooltip → Task 3 + `test_dag_lead_condensed_with_tooltip`. ✓
- ④ Tighten header (icon Refresh + export cluster) → Task 1 + `test_header_export_cluster_and_icon_refresh`. ✓
- Remove dead at-a-glance (DOM + JS) → Task 2 Steps 3–4 + `test_at_a_glance_removed`. ✓
- Preserve JS-addressed ids → Global Constraints + assertions in Task 2 + final-verification note. ✓
- Out of scope (landing list, drawer/embed internals, DAG renderer, report/notebook behavior) → not touched. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". Every code step shows the exact before + after markup and complete test code with exact commands. ✓

**Type/name consistency:** ids (`investigation-intro-details`, `investigation-needs-attention`, `investigation-dag-lead`, `investigation-detail-refresh`, etc.), the class `inv-export-actions`, the helper `detail_view()`, and the substring markers (`knowledge-producing`, `↻ Refresh`, `<summary>About this investigation</summary>`) are identical across the markup, the tests, and the constraints. The `viv-info-chip` + `data-tooltip` pattern matches the existing header chip. ✓
