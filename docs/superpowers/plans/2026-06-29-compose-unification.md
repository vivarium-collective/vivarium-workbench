# Compose Pillar Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the 4 Compose sub-panels (Build/Baseline/Variants/Interventions) into one scrollable `panel-compose`, drop the Compose sub-nav, preserving every inner control + JS hook + schema guard.

**Architecture:** Collect the inner content of the 4 (non-contiguous) Compose panels into one new `<section data-kind="compose" id="panel-compose">`, delete the 4 old wrappers, replace the 4 sub-nav buttons with one "Compose" button, and suppress the single-member sub-nav in `_showPillarSubnav`. The panels' JS keys off inner selectors (verified — no wrapper-id coupling), so the merge is safe.

**Tech Stack:** Jinja template + vanilla JS + pytest source-assertions + Jinja-parse + live render.

## Global Constraints

- **Preserve verbatim** (regroup, don't rewrite): each former panel's inner content, ids, JS-targeted selectors (`.baseline-entry`/`btn-run-baseline`/`[data-baseline-name]`, the `.variant*` override-editor, `[data-editable-intervention]`, the Build Model-settings table), and each section's `{% if %}` guard. **Cut nothing** (only the 4 `<section>` wrappers + the 3 redundant sub-nav buttons are removed; their CONTENT moves).
- **Do NOT touch** any other panel — `panel-simulations` and `panel-observables` sit BETWEEN the compose panels in the DOM and must stay where they are (Simulate/Visualize pillars), along with `panel-runs`/`panel-tests`/`panel-visualizations`/`panel-conclusions`/`panel-overview`.
- **Merged panel structure** (the two existing guards kept around their content, Build block whole):
  ```
  <section class="study-tab-panel" data-kind="compose" id="panel-compose" hidden>
    {% if _is_v3 or study.model_change or study.implementation_requirements %}
      {{ panel-build inner content verbatim }}
    {% endif %}
    {% if not _is_v3 %}
      {{ panel-baseline inner }}{{ panel-variants inner }}{{ panel-interventions inner }}
    {% endif %}
    {# empty-state when neither guard yields content #}
  </section>
  ```
  Order: Build block (Model/Conditions/Model change/Impl reqs) → Baseline → Variants → Interventions. (Build stays whole; Baseline is NOT interleaved mid-Build — different guards.)
- **Tab bar:** the 4 Compose member buttons (`data-kind="build|baseline|variants|interventions" data-pillar="compose"`) → ONE `<button class="study-tab" data-kind="compose" data-pillar="compose" onclick="_setStudyTab('compose')">Compose</button>`.
- Run tests via `/Users/eranagmon/code/venv/bin/python -m pytest`; template must Jinja-parse; JS via `node --check`.

---

### Task 1: Merge the 4 Compose panels into `#panel-compose` + collapse the tab buttons

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html` (the 4 panels at ~690/1140/1200/1265; the 4 sub-nav buttons at ~151–156)
- Test: `tests/test_compose_unification.py`

**Interfaces:** Produces `id="panel-compose"` (`data-kind="compose"`) + one Compose member button (`data-kind="compose" data-pillar="compose"`). Consumed by Task 2's switcher.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compose_unification.py
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_dashboard/templates/study-detail.html").read_text()


def test_panel_compose_exists_and_old_wrappers_gone():
    assert 'data-kind="compose" id="panel-compose"' in HTML
    for old in ['id="panel-build"', 'id="panel-baseline"', 'id="panel-variants"', 'id="panel-interventions"']:
        assert old not in HTML, f"old wrapper still present: {old}"


def test_single_compose_member_button():
    import re
    compose_btns = re.findall(r'<button class="study-tab"[^>]*data-pillar="compose"[^>]*>', HTML)
    assert len(compose_btns) == 1, f"expected 1 compose member button, got {len(compose_btns)}"
    assert 'data-kind="compose" data-pillar="compose"' in HTML
    for old in ["_setStudyTab('build')", "_setStudyTab('baseline')", "_setStudyTab('variants')", "_setStudyTab('interventions')"]:
        assert old not in HTML, f"old compose tab button call still present: {old}"


def _panel_compose():
    i = HTML.index('id="panel-compose"')
    nxt = HTML.find('class="study-tab-panel"', i + 10)
    return HTML[i: nxt if nxt != -1 else len(HTML)]


def test_inner_hooks_preserved_in_compose():
    p = _panel_compose()
    assert "baseline-entry" in p and "btn-run-baseline" in p          # baseline Run/Remove
    assert "data-editable-intervention" in p                          # interventions editor
    assert "data-baseline-name" in p
    # Build block guard + not-v3 guard both present inside the merged panel
    assert "study.model_change or study.implementation_requirements" in p
    assert "not _is_v3" in p


def test_other_panels_untouched():
    for k in ["overview", "simulations", "observables", "runs", "tests", "visualizations", "conclusions"]:
        assert f'id="panel-{k}"' in HTML, f"unrelated panel disturbed: panel-{k}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_compose_unification.py -q`
Expected: FAIL — no `panel-compose` yet.

- [ ] **Step 3: Build `#panel-compose` from the 4 panels' inner content**

Read the 4 Compose panels in full:
- `panel-build` (`<section ... data-kind="build" id="panel-build" hidden>` ~line 690 through its closing `</section>`) — note its outer `{% if _is_v3 or study.model_change or study.implementation_requirements %}…{% endif %}` guard (it wraps the build panel; keep it).
- `panel-baseline` (~1140), `panel-variants` (~1200), `panel-interventions` (~1265) — each wrapped upstream by `{% if not _is_v3 %}` (find the exact guard boundaries around each).

Create ONE new panel at the **former `panel-build` location** (replacing the build `<section>`):

```html
<section class="study-tab-panel" data-kind="compose" id="panel-compose" hidden>
  {% if _is_v3 or study.model_change or study.implementation_requirements %}
    {{ the INNER content of panel-build verbatim — Model (composite + Model-settings table),
       Conditions, Model change, Implementation requirements, with their <h3>s + inner ids }}
  {% endif %}
  {% if not _is_v3 %}
    {{ the INNER content of panel-baseline verbatim (baseline-list, Run/Remove) }}
    {{ the INNER content of panel-variants verbatim (variants + override editor) }}
    {{ the INNER content of panel-interventions verbatim }}
  {% endif %}
  {% if not (_is_v3 or study.model_change or study.implementation_requirements) and _is_v3 %}{% endif %}
  {% if not ((_is_v3 or study.model_change or study.implementation_requirements) or (not _is_v3)) %}
    <p class="empty-message">Nothing to compose yet.</p>
  {% endif %}
</section>
```

Then DELETE the original `panel-baseline`, `panel-variants`, `panel-interventions` `<section>…</section>` blocks (and their surrounding `{% if not _is_v3 %}…{% endif %}` wrappers — fold those guards into the merged panel as shown; don't leave empty `{% if %}` shells). Do NOT touch `panel-simulations`/`panel-observables` (which sit between them) or any other panel.

Add a lead-in header at the top of `panel-compose`: `<h2 class="overview-label" style="margin-bottom:8px">Model composition</h2>` (inside the section, before the first `{% if %}`).

(Simplify the empty-state guard if the doubled condition above is awkward — the requirement is just: render `<p class="empty-message">Nothing to compose yet.</p>` when BOTH guards are false. A clean form: `{% if not (_is_v3 or study.model_change or study.implementation_requirements) and _is_v3 %}` is wrong — use `{% set _has_build = _is_v3 or study.model_change or study.implementation_requirements %}{% if not _has_build and _is_v3 %}…` — actually the correct condition is simply: not _has_build AND _is_v3 means v3-no-build; the not-v3 branch always renders its sections (possibly empty lists). To keep it simple and correct, wrap as: `{% set _has_build = _is_v3 or study.model_change or study.implementation_requirements %}` then `{% if not _has_build and _is_v3 %}<p class="empty-message">Nothing to compose yet.</p>{% endif %}` — i.e. only a v3 study with no build content is truly empty; not-v3 studies always show baseline/variants/interventions sections. Use that.)

- [ ] **Step 4: Replace the 4 Compose sub-nav buttons with one**

In `#study-subnav` (~lines 151–156), replace the four buttons (`data-kind="build|baseline|variants|interventions" data-pillar="compose"`, with their `{% if %}` guards) with a single:
```html
    <button class="study-tab" data-kind="compose" data-pillar="compose" onclick="_setStudyTab('compose')">Compose</button>
```
(Remove the now-unused `{% if _is_v3 or … %}` / `{% if not _is_v3 %}` guards that wrapped those four buttons.)

- [ ] **Step 5: Run tests + Jinja parse**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_compose_unification.py -q && /Users/eranagmon/code/venv/bin/python -c "from jinja2 import Environment; Environment().parse(open('vivarium_dashboard/templates/study-detail.html').read()); print('PARSE_OK')"`
Expected: PASS (4 passed) + PARSE_OK.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html tests/test_compose_unification.py
git commit -m "feat(compose): merge Build/Baseline/Variants/Interventions into one panel-compose"
```

---

### Task 2: Suppress the single-member sub-nav (study-detail.js)

**Files:**
- Modify: `vivarium_dashboard/static/study-detail.js` (`_showPillarSubnav`, ~the function shown below)
- Test: `tests/test_compose_unification.py` (extend)

**Interfaces:**
- Consumes: the one-member Compose pillar (Task 1).
- Produces: `_showPillarSubnav` hides the `#study-subnav` row when the active pillar has ≤1 visible member.

- [ ] **Step 1: Write the failing test**

```python
def test_subnav_hidden_for_single_member_pillar():
    js = (ROOT / "vivarium_dashboard/static/study-detail.js").read_text()
    # _showPillarSubnav hides the sub-nav row when the pillar has <= 1 member
    i = js.index("function _showPillarSubnav")
    block = js[i:i + 700]
    assert "study-subnav" in block
    # a count of the pillar's members + a conditional hide of the container
    assert ("<= 1" in block) or ("< 2" in block) or ("=== 1" in block) or (".length" in block and "display" in block)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_compose_unification.py::test_subnav_hidden_for_single_member_pillar -q`
Expected: FAIL — no single-member hide logic.

- [ ] **Step 3: Add the single-member hide to `_showPillarSubnav`**

Replace the existing `_showPillarSubnav` function:

```javascript
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
```

with:

```javascript
  function _showPillarSubnav(pillar) {
    // pillar buttons
    document.querySelectorAll('.study-pillar').forEach(function (b) {
      b.classList.toggle('active', b.dataset.pillar === pillar);
    });
    // member buttons: only the active pillar's are visible
    var members = 0;
    document.querySelectorAll('#study-subnav .study-tab').forEach(function (b) {
      var mine = b.dataset.pillar === pillar;
      b.style.display = mine ? '' : 'none';
      if (mine) members++;
    });
    // A single-member pillar (e.g. Compose, which is one unified panel) shows no
    // sub-nav pill — the pillar tab itself is the only choice.
    var subnav = document.getElementById('study-subnav');
    if (subnav) subnav.style.display = (members <= 1) ? 'none' : '';
  }
```

- [ ] **Step 4: Run test + syntax check**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_compose_unification.py -q && node --check vivarium_dashboard/static/study-detail.js`
Expected: PASS (all 5 tests); `node --check` exit 0.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/study-detail.js tests/test_compose_unification.py
git commit -m "feat(compose): hide single-member sub-nav (Compose is one unified panel)"
```

- [ ] **Step 6: Live-render verification (the strong gate — controller runs it)**

Serve `v2e-readouts` (`/Users/eranagmon/code/venv/bin/python -m vivarium_dashboard.cli serve --workspace /Users/eranagmon/code/v2e-readouts --port 8815`); open a study → click **Compose**:
- HTTP 200, `panel-compose` renders the merged sections (Model/Conditions/… per the schema version), **no sub-nav pill** under the Compose pillar, no server error.
- For a study with baselines/variants: the baseline **Run/Remove** and the variant **override editor** still work (inner JS intact).
- Other pillars (Inquire/Simulate/Visualize) still show their sub-navs and switch panels correctly (the single-member hide only affects one-member pillars).
- No content lost vs before (Model, Conditions, Model change, Impl reqs, Baseline, Variants, Interventions all present where the schema version includes them).

---

## Self-Review

**Spec coverage:**
- Merge 4 panels → one `panel-compose` (Build block whole, then Baseline/Variants/Interventions, guards preserved) → Task 1 Step 3. ✓
- Delete 4 old wrappers; don't touch simulations/observables/others → Step 3 + `test_other_panels_untouched`. ✓
- 4 sub-nav buttons → 1 Compose button → Step 4 + `test_single_compose_member_button`. ✓
- Empty-state for the no-content edge → Step 3 (`_has_build` form). ✓
- Single-member sub-nav suppressed → Task 2 + `test_subnav_hidden_for_single_member_pillar`. ✓
- Inner hooks/JS preserved → constraints + `test_inner_hooks_preserved_in_compose` + the live-render gate. ✓
- Out of scope (split-Build interleave, B2b, investigation page, Build-internal declutter) → not touched. ✓

**Placeholder scan:** The `{{ … inner content verbatim }}` lines are a move-map (the implementer reads the real panels and moves their inner markup) — concrete given the verbatim-move constraint + the structure tests + the must-preserve hook list. The empty-state guard is given a clean `_has_build` form. Complete test code + exact commands.

**Type consistency:** `data-kind="compose"` + `id="panel-compose"` + `data-pillar="compose"` are identical across Task 1 markup, the tests, and Task 2 (which counts `#study-subnav .study-tab` members — the Compose pillar now has exactly 1). The preserved inner selectors (`btn-run-baseline`, `data-editable-intervention`, `data-baseline-name`, `.variant*`) match between the constraints, Step 3, and the tests.
