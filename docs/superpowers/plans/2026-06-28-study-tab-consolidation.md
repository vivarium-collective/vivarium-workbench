# Study Tab Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group the study-detail page's 12 tab buttons into 5 pillar tabs (Understand/Inquire/Compose/Simulate/Visualize) with a secondary sub-nav, leaving all 11 panels and their JS untouched.

**Architecture:** Two-level nav. Each existing (Jinja-conditional) member button gains `data-pillar`; a new row of 5 pillar buttons sits above. `_setStudyTab(kind)` is enhanced to derive the pillar from the clicked kind's `data-pillar` and reveal only that pillar's member buttons — so every existing deep link keeps working with no caller change. CSS hides non-active pillars' member buttons.

**Tech Stack:** Jinja template + vanilla browser JS (`study-detail.js`) + CSS; pytest source-assertions + `node --check`.

## Global Constraints

- **Panels untouched:** the 11 `<section class="study-tab-panel" data-kind id="panel-…">` keep their id/data-kind/content/JS. Only the nav (`study-detail.html` tab bar) + `_setStudyTab`/`_setStudyPillar` (study-detail.js) + CSS change.
- **Deep links preserved:** `_setStudyTab(kind)` keeps its signature + panel-toggle + kind-specific loaders (`tests`→`loadTestsTab`, `visualizations`→`_loadCharts`, `observables`→`_loadReadouts`); the ~8 existing `_setStudyTab('…')` callers are NOT edited.
- **Conditional-safe:** member buttons stay Jinja `{% if _is_v3 %}`/`{% endif %}`-conditional; the pillar is read from the DOM (`data-pillar`), never a hardcoded list.
- **Pillar mapping (binding):** overview→understand; build/baseline/variants/interventions→compose; simulations/runs→simulate; observables/visualizations→visualize; tests/conclusions→inquire. Pillar order: understand, inquire, compose, simulate, visualize. Default active: understand.
- The Readouts(v3)/Observables(v4) button label is unified to **"Readouts"**.
- Escape nothing new (static labels). Run JS via `node --check`; tests via `/Users/eranagmon/code/venv/bin/python -m pytest`.

---

### Task 1: Pillar + sub-nav markup (template)

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html` (the tab `<nav>`, ~lines 138–156)
- Test: `tests/test_study_tabs_structure.py`

**Interfaces:**
- Produces: a `.study-pillars` row of 5 `<button class="study-pillar" data-pillar="…" onclick="_setStudyPillar('…')">`; a `#study-subnav` row holding the existing member buttons, each with `data-pillar`. Consumed by Task 2's JS.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_study_tabs_structure.py
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_dashboard/templates/study-detail.html").read_text()


def test_five_pillar_buttons_present():
    for p in ["understand", "inquire", "compose", "simulate", "visualize"]:
        assert f'data-pillar="{p}"' in HTML and f"_setStudyPillar('{p}')" in HTML


def test_subnav_container_present():
    assert 'id="study-subnav"' in HTML


def test_every_study_tab_button_has_a_pillar():
    # no member button left without data-pillar (rough check: count study-tab buttons
    # with onclick=_setStudyTab vs those carrying data-pillar)
    import re
    btns = re.findall(r'<button class="study-tab"[^>]*onclick="_setStudyTab\([^>]*</button>', HTML)
    assert btns, "expected member buttons"
    for b in btns:
        assert "data-pillar=" in b, f"member button missing data-pillar: {b[:80]}"


def test_panels_unchanged_all_eleven_present():
    for kind in ["overview", "build", "simulations", "baseline", "observables",
                 "variants", "interventions", "runs", "tests", "visualizations", "conclusions"]:
        assert f'data-kind="{kind}"' in HTML and f'id="panel-{kind}"' in HTML


def test_deep_link_onclicks_preserved():
    assert "_setStudyTab('tests')" in HTML or "_setStudyTab(\\'tests\\'" in HTML
    assert "_setStudyTab('conclusions')" in HTML or "_setStudyTab(\\'conclusions\\'" in HTML
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_study_tabs_structure.py -q`
Expected: FAIL — no `data-pillar`/`study-pillar`/`#study-subnav`.

- [ ] **Step 3: Restructure the tab `<nav>`**

In `vivarium_dashboard/templates/study-detail.html`, find the tab `<nav>` (the block containing the 12 `<button class="study-tab" …>` ~lines 138–157, ending `</nav>`). Replace its INNER content (keep the outer `<nav …>`/`</nav>`; if the `<nav>` has an id/class, keep it) with:

```html
  <div class="study-pillars">
    <button class="study-pillar active" data-pillar="understand" onclick="_setStudyPillar('understand')">Understand</button>
    <button class="study-pillar"        data-pillar="inquire"    onclick="_setStudyPillar('inquire')">Inquire</button>
    <button class="study-pillar"        data-pillar="compose"    onclick="_setStudyPillar('compose')">Compose</button>
    <button class="study-pillar"        data-pillar="simulate"   onclick="_setStudyPillar('simulate')">Simulate</button>
    <button class="study-pillar"        data-pillar="visualize"  onclick="_setStudyPillar('visualize')">Visualize</button>
  </div>
  <div class="study-subnav" id="study-subnav">
    <button class="study-tab active" data-kind="overview" data-pillar="understand" onclick="_setStudyTab('overview')">Overview</button>
    <button class="study-tab"        data-kind="tests"        data-pillar="inquire"  onclick="_setStudyTab('tests')">Tests</button>
    <button class="study-tab"        data-kind="conclusions"  data-pillar="inquire"  onclick="_setStudyTab('conclusions')">{{ "Decide" if _is_v3 else "Conclusions" }}</button>
    {% if _is_v3 or study.model_change or study.implementation_requirements %}
    <button class="study-tab"        data-kind="build"        data-pillar="compose"  onclick="_setStudyTab('build')">Build</button>
    {% endif %}
    {% if not _is_v3 %}
    <button class="study-tab"        data-kind="baseline"      data-pillar="compose"  onclick="_setStudyTab('baseline')">Baseline</button>
    <button class="study-tab"        data-kind="variants"      data-pillar="compose"  onclick="_setStudyTab('variants')">Variants</button>
    <button class="study-tab"        data-kind="interventions" data-pillar="compose"  onclick="_setStudyTab('interventions')">Interventions</button>
    {% endif %}
    {% if _is_v3 %}
    <button class="study-tab"        data-kind="simulations"   data-pillar="simulate" onclick="_setStudyTab('simulations')">Simulations</button>
    {% endif %}
    <button class="study-tab"        data-kind="runs"          data-pillar="simulate" onclick="_setStudyTab('runs')">Runs</button>
    <button class="study-tab"        data-kind="observables"   data-pillar="visualize" onclick="_setStudyTab('observables')">Readouts</button>
    <button class="study-tab"        data-kind="visualizations" data-pillar="visualize" onclick="_setStudyTab('visualizations')">Visualizations</button>
  </div>
```

(This preserves every member button's `data-kind`/`onclick` and the v3/v4 conditionals, regroups them by pillar, unifies the observables label to "Readouts", and adds the 5-pillar row.)

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_study_tabs_structure.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html tests/test_study_tabs_structure.py
git commit -m "feat(tabs): 5-pillar tab row + data-pillar-tagged sub-nav"
```

---

### Task 2: Pillar-aware switcher (study-detail.js)

**Files:**
- Modify: `vivarium_dashboard/static/study-detail.js` (`_setStudyTab` ~lines 14–32; add `_setStudyPillar`)
- Test: `tests/test_study_tabs_structure.py` (extend)

**Interfaces:**
- Consumes: the `data-pillar` member buttons + `.study-pillar` buttons + `#study-subnav` (Task 1).
- Produces: `window._setStudyTab(kind)` (enhanced, same signature) + `window._setStudyPillar(pillar)`.

- [ ] **Step 1: Write the failing test**

```python
def test_js_has_pillar_switcher():
    js = (ROOT / "vivarium_dashboard/static/study-detail.js").read_text()
    assert "function _setStudyPillar" in js
    assert "window._setStudyPillar" in js
    assert "dataset.pillar" in js or "data-pillar" in js
    assert "study-pillar" in js          # toggles the pillar buttons
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_study_tabs_structure.py::test_js_has_pillar_switcher -q`
Expected: FAIL — no `_setStudyPillar`.

- [ ] **Step 3: Enhance `_setStudyTab` + add `_setStudyPillar`**

In `vivarium_dashboard/static/study-detail.js`, replace the existing `_setStudyTab` function (the block from `function _setStudyTab(kind) {` through its closing `}` and the `window._setStudyTab = _setStudyTab;` line, ~14–33) with:

```javascript
  // Map a panel kind -> its pillar by reading the member button's data-pillar
  // (DOM is the source of truth, so v3/v4 conditional tab sets are always correct).
  function _pillarForKind(kind) {
    var btn = document.querySelector('.study-tab[data-kind="' + kind + '"]');
    return btn ? (btn.dataset.pillar || '') : '';
  }

  function _showPillarSubnav(pillar) {
    // pillar buttons
    document.querySelectorAll('.study-pillar').forEach(function (b) {
      b.classList.toggle('active', b.dataset.pillar === pillar);
    });
    // member buttons: only the active pillar's are visible
    document.querySelectorAll('#study-subnav .study-tab').forEach(function (b) {
      b.style.display = (b.dataset.pillar === pillar) ? '' : 'none';
    });
  }

  function _setStudyTab(kind) {
    var pillar = _pillarForKind(kind);
    if (pillar) _showPillarSubnav(pillar);
    document.querySelectorAll('.study-tab').forEach(function (b) {
      b.classList.toggle('active', b.dataset.kind === kind);
    });
    document.querySelectorAll('.study-tab-panel').forEach(function (p) {
      p.classList.toggle('active', p.dataset.kind === kind);
    });
    if (kind === 'tests') { loadTestsTab(window._study); }
    if (kind === 'visualizations') { _loadCharts('viz-charts-panel'); }
    if (kind === 'observables') { _loadReadouts(); }
  }
  window._setStudyTab = _setStudyTab;

  // Click a pillar -> reveal its member sub-nav and open its first member panel.
  function _setStudyPillar(pillar) {
    _showPillarSubnav(pillar);
    var first = document.querySelector('#study-subnav .study-tab[data-pillar="' + pillar + '"]');
    if (first) _setStudyTab(first.dataset.kind);
  }
  window._setStudyPillar = _setStudyPillar;
```

- [ ] **Step 4: Run test + syntax check**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_study_tabs_structure.py -q && node --check vivarium_dashboard/static/study-detail.js`
Expected: PASS (all structure tests); `node --check` exit 0.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/study-detail.js tests/test_study_tabs_structure.py
git commit -m "feat(tabs): pillar-aware _setStudyTab + _setStudyPillar (deep links preserved)"
```

---

### Task 3: Two-level nav styling + initial sub-nav

**Files:**
- Modify: `vivarium_dashboard/static/style.css` (study-tab nav styles; append a pillar/sub-nav block)
- Modify: `vivarium_dashboard/static/study-detail.js` (one line — initialize the sub-nav for the default pillar on load)
- Test: extend `tests/test_study_tabs_structure.py`

**Interfaces:**
- Consumes: `.study-pillars`/`.study-pillar`/`.study-subnav` + `_showPillarSubnav` (Tasks 1–2).

- [ ] **Step 1: Write the failing test**

```python
def test_css_styles_pillars():
    css = (ROOT / "vivarium_dashboard/static/style.css").read_text()
    assert ".study-pillar" in css and ".study-subnav" in css
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_study_tabs_structure.py::test_css_styles_pillars -q`
Expected: FAIL — no `.study-pillar` rule.

- [ ] **Step 3: Add the CSS**

Append to `vivarium_dashboard/static/style.css`:

```css
/* Two-level study nav: 5 pillars over a per-pillar sub-nav (tab consolidation). */
.study-pillars { display: flex; gap: 4px; flex-wrap: wrap; margin: 0 0 2px; }
.study-pillar {
  border: 0; background: none; cursor: pointer; padding: 8px 14px;
  font-weight: 700; font-size: 0.95rem; color: #64748b; border-bottom: 3px solid transparent;
}
.study-pillar:hover { color: #334155; }
.study-pillar.active { color: #1e293b; border-bottom-color: #2563eb; }
.study-subnav { display: flex; gap: 2px; flex-wrap: wrap; margin: 0 0 10px; padding: 4px 0 0; }
.study-subnav .study-tab {
  border: 0; background: none; cursor: pointer; padding: 4px 11px; border-radius: 9999px;
  font-size: 0.85rem; color: #64748b;
}
.study-subnav .study-tab:hover { background: #f1f5f9; color: #334155; }
.study-subnav .study-tab.active { background: #e0e7ff; color: #1e40af; font-weight: 600; }
/* When a pillar has a single visible member, the pill row reads as a label, not a choice. */
```

(If the existing `.study-tab` rules in style.css conflict with the new pill look, the more specific `.study-subnav .study-tab` selectors win — leave the old `.study-tab` rules in place for any other usage.)

- [ ] **Step 4: Initialize the sub-nav on load**

In `vivarium_dashboard/static/study-detail.js`, find where the study page initializes the default tab (search for `_setStudyTab('overview')` or the init in `_runStudyInit`/`_bootstrapStudy`). Ensure the default sub-nav is shown by calling `_showPillarSubnav('understand')` once at init (or simply rely on the initial `_setStudyTab('overview')` call, which now calls `_showPillarSubnav` itself). If there is NO explicit initial `_setStudyTab` call, add `_setStudyTab('overview');` to the init path so the Understand sub-nav + Overview panel are shown on load. Confirm by reading the init: the page must open on Understand/Overview with only Understand's sub-nav visible.

- [ ] **Step 5: Run tests + syntax check**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_study_tabs_structure.py -q && node --check vivarium_dashboard/static/study-detail.js`
Expected: PASS; `node --check` exit 0.

- [ ] **Step 6: Manual browser verification (documented)**

Serve against `v2e-readouts` (`/Users/eranagmon/code/venv/bin/python -m vivarium_dashboard.cli serve --workspace /Users/eranagmon/code/v2e-readouts --port 8810`) and open a study (e.g. `/studies/param-uq-01-elongation`):
- Top nav shows 5 pillars; **Understand** active, Overview panel shown, only Overview in the sub-nav.
- Click **Compose** → sub-nav shows Build/Baseline/Variants/Interventions (v4) or Build (v3); first panel opens.
- Click **Inquire** → Tests + Decide/Conclusions sub-nav.
- From a finding row, a `decide →` / Tests deep link lands on the right pillar with its sub-nav shown.
- **Visualize** → Readouts + Visualizations; the Readouts (`_loadReadouts`) and Visualizations (`_loadCharts`) loaders still fire.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/static/style.css vivarium_dashboard/static/study-detail.js tests/test_study_tabs_structure.py
git commit -m "feat(tabs): two-level pillar/sub-nav styling + default Understand sub-nav"
```

---

## Self-Review

**Spec coverage:**
- 5 pillar buttons + `data-pillar`-tagged member sub-nav, conditional-safe, observables→"Readouts" → Task 1. ✓
- `_setStudyTab` pillar-aware (DOM-derived) + `_setStudyPillar`, deep links + kind-loaders preserved → Task 2. ✓
- Two-level nav styling + default Understand/Overview on load → Task 3. ✓
- Panels untouched (asserted) → Task 1 test `test_panels_unchanged_all_eleven_present`. ✓
- Out of scope (panel-content declutter, investigation nav, persistence) → not touched. ✓

**Placeholder scan:** No TBD/TODO; complete markup/JS/CSS. Task 3 Step 4 is a locate-and-confirm of the init path (concrete: ensure `_setStudyTab('overview')` runs at init, which now drives the sub-nav) — not a placeholder. ✓

**Type consistency:** `data-pillar` values (understand/inquire/compose/simulate/visualize) are identical across Task 1 markup, Task 2's `_pillarForKind`/`_showPillarSubnav`/`_setStudyPillar` (read `dataset.pillar`, query `[data-pillar=…]`), and Task 3 CSS (`.study-pillar`, `.study-subnav`). `_setStudyTab(kind)` keeps its exact signature so the unedited deep-link callers still resolve. The member `data-kind` values match the unchanged panel `data-kind`s. ✓
