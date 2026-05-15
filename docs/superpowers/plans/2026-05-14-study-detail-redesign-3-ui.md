# Study Detail Redesign — Plan 3 (UI)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the Study Detail page (`/studies/<name>`) from a 6-card stack to the spec-defined 5-tab layout (Overview · Baseline · Variants · Interventions · Runs), wiring it end-to-end to Plan 2's v3 endpoints; remove the dead v2 handler `_post_study_set_baseline_params` and its JS caller along the way.

**Architecture:** Inline `<script>window._study={{study|tojson}}` keeps the spec available to JS without per-tab AJAX. Tab-switching uses a new `_setStudyTab(kind)` parallel to the existing `_setRegistryTab` pattern (selectors `.study-tab` / `.study-tab-panel` with `data-kind` + `.active`). The Variants tab gets a small parameter-form helper that calls `GET /api/composite-resolve` for the per-baseline param schema. Tests assert rendered-HTML structure via substring checks (the codebase's established UI-test pattern — no BeautifulSoup, no headless browser).

**Tech Stack:** Jinja2 templates, plain ES5 JS in IIFEs (no bundler), shared `style.css`, Python `unittest`/pytest for HTML-shape assertions.

---

## File Structure

**Modified files:**

- `vivarium_dashboard/templates/study-detail.html` — full rewrite: 5-tab header + 5 panels, reads v3 shape (`baseline[]`, flat variants, `interventions[]`). The current 6-card layout disappears entirely.
- `vivarium_dashboard/static/study-detail.js` — full rewrite: new `_setStudyTab`, click handlers wired to Plan 2 endpoints, param-form helper, intervention CRUD UI, restored add-viz integration.
- `vivarium_dashboard/static/style.css` — append `.study-tab` / `.study-tab-panel` rules (or alias `.registry-tab*` — Task 8 decides), plus `.study-overview`, `.study-counts-strip`, `.baseline-entry`, `.variant-row`, `.intervention-row`.
- `vivarium_dashboard/server.py` — delete the dead `_post_study_set_baseline_params_for_test`, its `_post_study_set_baseline_params` Handler wrapper, and the route `/api/study-set-baseline-params`.
- `tests/test_study_detail_page.py` — extend with HTML-shape assertions for the 5 tabs.
- `tests/test_study_handlers.py` — remove the `_post_study_set_baseline_params*` references if any remain (Task 0 will surface them).

**No new files.** The plan strictly modifies existing files.

---

## Conventions

**Test command:** No `.venv` — `python3 -m pytest …`.

**JS style:** plain ES5 inside an IIFE, matching the existing `study-detail.js`. No `let`/`const` arrow functions in the global scope; `var` + `function`. (Inside the IIFE, modern ES6 is fine — the existing file already uses `=>` and `const` inside its IIFE.)

**`window._study` is canonical client-side spec state.** After any mutating POST that succeeds, the page does a full `location.reload()`. **Do NOT** add client-side spec mutation — keep the simple reload-after-write pattern; it matches the rest of the dashboard and avoids state divergence.

**Tab selector convention:** Use `.study-tab` (buttons) and `.study-tab-panel` (panels) with `data-kind="<tab>"`. Active state: `.active` class on the matching button + panel. `_setStudyTab(kind)` is the toggle function. This parallels the registry-tabs pattern without coupling to its DOM.

**Endpoint body conventions:** all POSTs use `study: <slug>` (not `investigation`, not `name`). Plan 2 helpers accept both via `_study_name_from_body`, but we standardize on `study:` for new code.

**Commit messages:** Prefix `feat(ui):` for new tab functionality, `fix(ui):` for shape-fixes against v3 data, `refactor(ui):` for restructuring without behavior change, `chore:` for Task 0 deletion, `test(ui):` for new assertion tests.

**`git add`:** always list specific files. No `git add -A` / `git add .`.

---

## Endpoint cheat-sheet (Plan 2 surface that Plan 3 calls)

| UI affordance | Endpoint | Body |
|---|---|---|
| Overview · Set objective | `POST /api/study-set-objective` | `{study, text}` |
| Overview · Set conclusion | `POST /api/study-set-conclusion` | `{study, text}` |
| Baseline · Add composite | `POST /api/study-baseline-add` | `{study, name, composite, params?}` |
| Baseline · Remove composite | `POST /api/study-baseline-remove` | `{study, name}` |
| Baseline · Run a composite | `POST /api/study-run-baseline` | `{study, composite?, steps?}` |
| Baseline · Read composites (for variants picker) | `GET /api/investigation-composites?investigation=<study>` | — |
| Variants · Resolve a composite's params for the form | `GET /api/composite-resolve?id=<spec_id>&overrides=<json>` | — |
| Variants · Add | `POST /api/study-variant-add` | `{study, name, base_composite, parameter_overrides?}` |
| Variants · Edit params | `POST /api/study-variant-set-params` | `{study, variant, parameter_overrides}` |
| Variants · Delete | `POST /api/study-variant-delete` | `{study, variant}` |
| Variants · Run | `POST /api/study-run-variant` | `{study, variant, steps?}` |
| Interventions · Add | `POST /api/study-intervention-add` | `{study, name, description?}` |
| Interventions · Update | `POST /api/study-intervention-update` | `{study, name, description}` |
| Interventions · Delete | `POST /api/study-intervention-delete` | `{study, name}` |
| Runs · Delete one | `POST /api/study-run-delete` | `{study, run_id}` |
| Runs · Clear all | `POST /api/study-runs-clear` | `{study}` |
| Runs · Compare selected | `POST /api/study-comparison-add` | `{study, run_ids}` |
| Runs · Add viz | `POST /api/study-viz-add` | `{study, name, address, config}` (see Task 7) |

---

## Task 0: Delete the dead `_post_study_set_baseline_params` handler

This handler reads `spec.setdefault("baseline", {})["params"]` — a dict-shape op on the v3 list — and is wired to the old `.btn-edit-baseline-params` button. Plan 2's final review flagged it; the v2 UI is being replaced anyway. Delete it cleanly so it can't 500 silently.

**Files:**
- Modify: `vivarium_dashboard/server.py` (remove helper, wrapper, route)

- [ ] **Step 1: Find the affected lines**

Run: `grep -n "study_set_baseline_params\|study-set-baseline-params" vivarium_dashboard/server.py`

You should see:
- The route entry around `:218-235` in the dispatch table (something like `"/api/study-set-baseline-params": "_post_study_set_baseline_params",`).
- The Handler wrapper `_post_study_set_baseline_params(self, body)` around `:5430-5440`.
- The module-level helper `_post_study_set_baseline_params_for_test(ws_root, body)` around `:491-509`.

Capture each exact line range before editing.

- [ ] **Step 2: Delete the route entry**

In the dispatch table, delete the line:

```python
    "/api/study-set-baseline-params":   "_post_study_set_baseline_params",
```

- [ ] **Step 3: Delete the Handler wrapper**

Delete the `_post_study_set_baseline_params` method (the 3-4 line `def`+body+return inside the Handler class). Approximate signature:

```python
    def _post_study_set_baseline_params(self, body: dict):
        response, code = _post_study_set_baseline_params_for_test(WORKSPACE, body)
        return self._json(response, code)
```

- [ ] **Step 4: Delete the module-level helper**

Delete the `_post_study_set_baseline_params_for_test` function. Approximate body:

```python
def _post_study_set_baseline_params_for_test(ws_root: Path, body: dict):
    """Set study.yaml baseline.params field. Returns (response_dict, status_code)."""
    name = (body.get("study") or "").strip()
    params = body.get("params") or {}
    if not name:
        return {"error": "missing study"}, 400
    sf = ws_root / "studies" / name / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404
    if not isinstance(params, dict):
        return {"error": "params must be an object"}, 400
    spec = yaml.safe_load(sf.read_text()) or {}
    spec.setdefault("baseline", {})["params"] = params
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200
```

- [ ] **Step 5: Confirm no references remain**

Run: `grep -rn "study_set_baseline_params\|study-set-baseline-params" vivarium_dashboard/ tests/`

Expected: zero matches. If any test still imports it, that test was already failing on the v3 fixture change (Plan 2 Task 7 deleted `test_set_baseline_params_updates_yaml`) — but double-check.

- [ ] **Step 6: Run the suite**

```bash
python3 -m pytest -q 2>&1 | tail -5
```

Expected: same 6 pre-existing failures, no new failures.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/server.py
git commit -m "chore: remove dead _post_study_set_baseline_params (latent broken under v3)"
```

---

## Task 1: Tab scaffold

Replace the 6-card stack with a 5-tab header and 5 empty panels. Wire `_setStudyTab(kind)` so clicking a tab swaps the `.active` class. The panels stay empty in this task — content moves in over Tasks 2-7. Tests assert the structural skeleton is present.

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html`
- Modify: `vivarium_dashboard/static/study-detail.js`
- Modify: `tests/test_study_detail_page.py`

- [ ] **Step 1: Write the failing tests first**

Append to `tests/test_study_detail_page.py`:

```python
def test_study_detail_page_has_five_tabs(_ws):
    """The 5-tab scaffold is present: Overview · Baseline · Variants · Interventions · Runs."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    # Five buttons
    for kind in ("overview", "baseline", "variants", "interventions", "runs"):
        assert f'class="study-tab' in html
        assert f'data-kind="{kind}"' in html
    # Five panels
    panels = html.count('class="study-tab-panel')
    assert panels == 5, f"expected 5 panel elements, got {panels}"
    # The Overview tab is active by default
    assert 'class="study-tab active" data-kind="overview"' in html or \
           'data-kind="overview" class="study-tab active"' in html or \
           '"study-tab active"' in html and 'data-kind="overview"' in html


def test_study_detail_page_loads_set_tab_helper(_ws):
    """The page ships the _setStudyTab helper inline or via study-detail.js."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    # The page must reference _setStudyTab somewhere (in the script tag or via onclick)
    assert "_setStudyTab" in html
```

The `_ws` fixture already exists at the top of the file (a tmp_path workspace with the legacy study under `investigations/`); reuse it.

- [ ] **Step 2: Run tests; expect FAIL**

```bash
python3 -m pytest tests/test_study_detail_page.py -v
```

Expected: 2 new tests FAIL (existing slug-regex and spec-resolver tests still pass).

- [ ] **Step 3: Rewrite the template skeleton**

Replace the **entire** `vivarium_dashboard/templates/study-detail.html` body (between `<body>` and `</body>`) with the new 5-tab skeleton. Keep the `<head>`, the stylesheet link, the inline `window._study` script, and the `<script src="/study-detail.js">` block.

New body content (everything between `<body>` and `</body>`):

```html
<header class="study-header">
  <h1 class="study-title">
    <span class="study-name" id="study-name">{{ name }}</span>
    <span class="status-pill status-{{ study.status or 'planned' }}">{{ study.status or 'planned' }}</span>
  </h1>
</header>

<nav class="study-tabs" aria-label="Study sections">
  <button class="study-tab active" data-kind="overview"      onclick="_setStudyTab('overview')">Overview</button>
  <button class="study-tab"        data-kind="baseline"      onclick="_setStudyTab('baseline')">Baseline</button>
  <button class="study-tab"        data-kind="variants"      onclick="_setStudyTab('variants')">Variants</button>
  <button class="study-tab"        data-kind="interventions" onclick="_setStudyTab('interventions')">Interventions</button>
  <button class="study-tab"        data-kind="runs"          onclick="_setStudyTab('runs')">Runs</button>
</nav>

<section class="study-tab-panel active" data-kind="overview" id="panel-overview">
  <!-- Task 2 fills this -->
</section>

<section class="study-tab-panel" data-kind="baseline" id="panel-baseline">
  <!-- Task 3 fills this -->
</section>

<section class="study-tab-panel" data-kind="variants" id="panel-variants">
  <!-- Task 5 fills this -->
</section>

<section class="study-tab-panel" data-kind="interventions" id="panel-interventions">
  <!-- Task 6 fills this -->
</section>

<section class="study-tab-panel" data-kind="runs" id="panel-runs">
  <!-- Task 7 fills this -->
</section>
```

The previous `<section class="card" id="card-*">` blocks are gone. Their content will be reborn inside the panels in later tasks. The inline `<script>window._study=...</script>` and `<script src="/study-detail.js"></script>` at the bottom of the file are preserved as-is.

- [ ] **Step 4: Add `_setStudyTab` to `study-detail.js`**

Open `vivarium_dashboard/static/study-detail.js`. At the top of the IIFE (after the existing `api` helper, before the existing `makeEditable` helper), add:

```js
  function _setStudyTab(kind) {
    document.querySelectorAll('.study-tab').forEach(function(b) {
      b.classList.toggle('active', b.dataset.kind === kind);
    });
    document.querySelectorAll('.study-tab-panel').forEach(function(p) {
      p.classList.toggle('active', p.dataset.kind === kind);
    });
  }
  window._setStudyTab = _setStudyTab;
```

(The `window._setStudyTab` line at the end makes it callable from the inline `onclick=` attributes.)

- [ ] **Step 5: Run the tests; expect PASS**

```bash
python3 -m pytest tests/test_study_detail_page.py -v
```

Expected: all tests in the file pass, including the 2 new ones.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html vivarium_dashboard/static/study-detail.js tests/test_study_detail_page.py
git commit -m "refactor(ui): replace card stack with 5-tab scaffold (Overview/Baseline/Variants/Interventions/Runs)"
```

---

## Task 2: Overview tab — Layout A (Notebook)

Vertical, narrative: name+status header (already in Task 1) → Objective (inline-editable) → counts strip → Conclusion (inline-editable). The two text fields use the existing `makeEditable` helper.

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html`
- Modify: `vivarium_dashboard/static/study-detail.js`
- Modify: `tests/test_study_detail_page.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_study_detail_page.py`:

```python
def test_overview_panel_has_objective_and_conclusion_editables(_ws):
    """Overview tab includes inline-editable objective and conclusion fields."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'id="objective-text"' in html
    assert 'id="conclusion-text"' in html
    assert 'data-editable="true"' in html


def test_overview_panel_has_counts_strip(_ws):
    """Overview tab shows a counts strip: variants · runs · interventions."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'study-counts-strip' in html or 'class="counts-strip"' in html
    # Each label appears
    for label in ('variants', 'runs', 'interventions'):
        assert label in html.lower()
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m pytest tests/test_study_detail_page.py::test_overview_panel_has_objective_and_conclusion_editables tests/test_study_detail_page.py::test_overview_panel_has_counts_strip -v
```

Expected: 2 tests FAIL.

- [ ] **Step 3: Fill the Overview panel**

In `vivarium_dashboard/templates/study-detail.html`, replace the placeholder comment inside `<section class="study-tab-panel active" data-kind="overview" id="panel-overview">` with:

```html
  <div class="study-overview">
    <div class="overview-section">
      <h2 class="overview-label">Objective</h2>
      <div id="objective-text" class="overview-prose" data-editable="true"
           data-placeholder="(set an objective for this study)">{{ study.objective or '' }}</div>
    </div>

    <div class="study-counts-strip">
      <div class="count-cell">
        <strong>{{ (study.variants or [])|length }}</strong>
        <span class="count-label">variants</span>
      </div>
      <div class="count-cell">
        <strong>{{ (study.runs or [])|length }}</strong>
        <span class="count-label">runs</span>
      </div>
      <div class="count-cell">
        <strong>{{ (study.interventions or [])|length }}</strong>
        <span class="count-label">interventions</span>
      </div>
    </div>

    <div class="overview-section">
      <h2 class="overview-label">Conclusion</h2>
      <div id="conclusion-text" class="overview-prose" data-editable="true"
           data-placeholder="(fill in when the study wraps)">{{ study.conclusion or '' }}</div>
    </div>
  </div>
```

The existing `study.conclusion` may be a dict `{text: ...}` (legacy) or a string. The template uses `study.conclusion or ''` which renders a dict as `{'text': '...'}` if present — for now that's acceptable; the inline editor (Task 2 step 4) saves a flat string back, so the dict shape gets overwritten on first edit. If you find this surfaces a real bug in your testing, replace with `{{ (study.conclusion.text if study.conclusion.__class__.__name__ == 'dict' else study.conclusion) or '' }}` — but most v3 specs already store conclusion as a string.

- [ ] **Step 4: Update `makeEditable` wiring in `study-detail.js`**

The current `study-detail.js` already wires `#objective-text` and `#conclusion-text` to the `makeEditable` helper. Verify that wiring still runs (it should — those IDs are unchanged). Specifically check that the two calls near the top of the IIFE (look for `makeEditable(document.getElementById("objective-text")` and similar for conclusion) use the right endpoints and field names:
- `#objective-text` → `POST /api/study-set-objective` with `{study, text}`.
- `#conclusion-text` → `POST /api/study-set-conclusion` with `{study, text}` (the current code may send `{study, conclusion}` — change to `text` to match Plan 2 / server.py conventions).

If the conclusion wiring currently sends `conclusion: t.value`, change the field name to `text`:

```js
  makeEditable(
    document.getElementById('conclusion-text'),
    '/api/study-set-conclusion',
    'text',
    '(fill in when the study wraps)'
  );
```

- [ ] **Step 5: Run tests; expect PASS**

```bash
python3 -m pytest tests/test_study_detail_page.py -v
```

Expected: all Overview tests pass.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html vivarium_dashboard/static/study-detail.js tests/test_study_detail_page.py
git commit -m "feat(ui): Overview tab — notebook layout with inline-edit objective + conclusion + counts strip"
```

---

## Task 3: Baseline tab

Lists each entry in `study.baseline[]`. For each: name (prominent), composite id (FQN, muted), params (read-only table), and three buttons: `Run`, `Remove`. A footer `+ Add composite` button opens a small inline form (composite-id input + name input + optional params textarea — submit to `study-baseline-add`).

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html`
- Modify: `vivarium_dashboard/static/study-detail.js`
- Modify: `tests/test_study_detail_page.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_study_detail_page.py`:

```python
def test_baseline_panel_lists_entries(_ws):
    """Baseline panel renders one .baseline-entry per baseline[] entry."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    # The legacy fixture has one variants-as-composites that migrates to one baseline entry
    # named "monod_kinetics" (per Plan 1 migration rules).
    assert 'class="baseline-entry"' in html
    assert 'data-baseline-name="monod_kinetics"' in html


def test_baseline_panel_has_add_button(_ws):
    """Baseline panel has a '+ Add composite' button."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'btn-baseline-add' in html


def test_baseline_panel_per_entry_buttons(_ws):
    """Each baseline entry has Run + Remove buttons carrying its name."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'btn-run-baseline' in html
    assert 'btn-baseline-remove' in html
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m pytest tests/test_study_detail_page.py -k baseline_panel -v
```

Expected: 3 tests FAIL.

- [ ] **Step 3: Fill the Baseline panel**

In `vivarium_dashboard/templates/study-detail.html`, replace the placeholder comment inside `<section class="study-tab-panel" data-kind="baseline" id="panel-baseline">` with:

```html
  <div class="baseline-list">
    {% for b in (study.baseline or []) %}
    <article class="baseline-entry" data-baseline-name="{{ b.name }}">
      <header class="baseline-entry-header">
        <h3 class="baseline-entry-name">{{ b.name }}</h3>
        <code class="baseline-entry-fqn">{{ b.composite }}</code>
      </header>
      {% if b.params %}
      <table class="baseline-params">
        <thead><tr><th>Parameter</th><th>Value</th></tr></thead>
        <tbody>
          {% for k, v in b.params.items() %}
          <tr><td><code>{{ k }}</code></td><td><code>{{ v }}</code></td></tr>
          {% endfor %}
        </tbody>
      </table>
      {% endif %}
      <div class="baseline-entry-actions">
        <button class="btn-run-baseline" data-baseline-name="{{ b.name }}">Run</button>
        <button class="btn-baseline-remove" data-baseline-name="{{ b.name }}">Remove</button>
      </div>
    </article>
    {% else %}
    <p class="empty-message">No baseline composites yet. Add one to begin.</p>
    {% endfor %}
  </div>

  <details class="baseline-add-form">
    <summary class="btn-baseline-add">+ Add composite</summary>
    <form id="baseline-add-form" onsubmit="return _submitBaselineAdd(event)">
      <label>Name (short, unique): <input name="name" required pattern="[a-zA-Z0-9_-]+"></label>
      <label>Composite ID: <input name="composite" required placeholder="pkg.composites.foo"></label>
      <label>Params (JSON, optional): <textarea name="params" placeholder="{}"></textarea></label>
      <button type="submit">Add</button>
    </form>
  </details>
```

- [ ] **Step 4: Add JS handlers in `study-detail.js`**

Inside the IIFE in `vivarium_dashboard/static/study-detail.js`, replace any existing `.btn-run-baseline` handler with this one (reads the baseline entry name from `data-baseline-name`):

```js
  bindAll('.btn-run-baseline', function() {
    var entryName = this.dataset.baselineName;
    api('POST', '/api/study-run-baseline', {
      study: studyName(), composite: entryName
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Run failed: ' + (r.body && r.body.error || r.status));
    });
  });

  bindAll('.btn-baseline-remove', function() {
    var entryName = this.dataset.baselineName;
    if (!confirm('Remove baseline composite "' + entryName + '"?')) return;
    api('POST', '/api/study-baseline-remove', {
      study: studyName(), name: entryName
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else if (r.status === 409 && r.body.dependents) {
        alert('Cannot remove: variants depend on this composite (' +
              r.body.dependents.join(', ') + '). Delete those variants first.');
      } else {
        alert('Remove failed: ' + (r.body && r.body.error || r.status));
      }
    });
  });

  function _submitBaselineAdd(ev) {
    ev.preventDefault();
    var form = ev.target;
    var params = {};
    var raw = form.params.value.trim();
    if (raw) {
      try { params = JSON.parse(raw); }
      catch (e) { alert('Params must be valid JSON.'); return false; }
    }
    api('POST', '/api/study-baseline-add', {
      study: studyName(),
      name: form.name.value.trim(),
      composite: form.composite.value.trim(),
      params: params
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Add failed: ' + (r.body && r.body.error || r.status));
    });
    return false;
  }
  window._submitBaselineAdd = _submitBaselineAdd;
```

- [ ] **Step 5: Run tests; expect PASS**

```bash
python3 -m pytest tests/test_study_detail_page.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html vivarium_dashboard/static/study-detail.js tests/test_study_detail_page.py
git commit -m "feat(ui): Baseline tab — list composites + Add/Remove/Run actions wired to v3 endpoints"
```

---

## Task 4: Parameter-form helper

The Variants tab needs to render a parameter form pre-filled with the base composite's defaults. Build a small JS helper `renderParamForm(containerEl, specId, currentOverrides) → Promise<collectFn>` that:

1. fetches `GET /api/composite-resolve?id=<specId>&overrides=<json>`,
2. renders `<input>` elements into `containerEl`, one per declared parameter, pre-filled with `currentOverrides[k] ?? params[k].default`,
3. returns a `collect()` function that reads back the inputs and returns the override dict (with the same type coercion the Composite Explorer uses).

This task ships only the helper, no UI yet. Task 5 wires it into the Variants tab.

**Files:**
- Modify: `vivarium_dashboard/static/study-detail.js`

- [ ] **Step 1: Add the helper**

In `study-detail.js`, inside the IIFE, after `_setStudyTab` and before the existing click handlers, add:

```js
  // Fetch the param schema for a composite and render an input form.
  // currentOverrides: {} or existing overrides (for edit flow).
  // Returns a Promise<{collect, ok}>: collect() reads back the current input
  // values and returns an overrides dict; ok=false if fetch failed (containerEl
  // shows the error message in that case).
  function renderParamForm(containerEl, specId, currentOverrides) {
    var overridesJson = encodeURIComponent(JSON.stringify(currentOverrides || {}));
    return fetch('/api/composite-resolve?id=' +
                 encodeURIComponent(specId) + '&overrides=' + overridesJson)
      .then(function(r) { return r.json().then(function(b) { return {status: r.status, body: b}; }); })
      .then(function(r) {
        if (r.status !== 200) {
          containerEl.innerHTML = '<p class="error">Could not resolve composite: ' +
            (r.body && r.body.error || r.status) + '</p>';
          return {collect: function() { return {}; }, ok: false};
        }
        var params = r.body.parameters || {};
        containerEl.innerHTML = '';
        var inputs = {};
        Object.keys(params).forEach(function(k) {
          var def = params[k] || {};
          var type = def.type || 'string';
          var current = (currentOverrides && k in currentOverrides) ? currentOverrides[k] : def.default;
          var row = document.createElement('div');
          row.className = 'param-row';
          var label = document.createElement('label');
          label.className = 'param-label';
          var nameSpan = document.createElement('span');
          nameSpan.innerHTML = '<code>' + k + '</code> <span class="muted">(' + type + ')</span>';
          var input = document.createElement('input');
          input.className = 'param-input';
          input.dataset.paramKey = k;
          input.dataset.paramType = type;
          if (type === 'integer' || type === 'number' || type === 'float') {
            input.type = 'number';
            input.step = (type === 'integer') ? '1' : 'any';
          } else if (type === 'boolean') {
            input.type = 'checkbox';
            if (current === true) input.checked = true;
          } else {
            input.type = 'text';
          }
          if (input.type !== 'checkbox' && current !== undefined && current !== null) {
            input.value = current;
          }
          label.appendChild(nameSpan);
          label.appendChild(input);
          row.appendChild(label);
          if (def.description) {
            var desc = document.createElement('div');
            desc.className = 'param-desc muted';
            desc.textContent = def.description;
            row.appendChild(desc);
          }
          containerEl.appendChild(row);
          inputs[k] = input;
        });
        var collect = function() {
          var out = {};
          Object.keys(inputs).forEach(function(k) {
            var el = inputs[k];
            var t = el.dataset.paramType;
            if (t === 'boolean') out[k] = !!el.checked;
            else if (t === 'integer') out[k] = el.value === '' ? null : parseInt(el.value, 10);
            else if (t === 'number' || t === 'float') out[k] = el.value === '' ? null : parseFloat(el.value);
            else out[k] = el.value;
            // Drop unset / equal-to-default values to keep the override set minimal.
          });
          // Remove null/empty entries (don't send them as overrides).
          Object.keys(out).forEach(function(k) {
            if (out[k] === null || out[k] === '' || out[k] === undefined) delete out[k];
          });
          return out;
        };
        return {collect: collect, ok: true};
      });
  }
  // Not exposed on window; consumed internally by the Variants tab.
```

- [ ] **Step 2: Smoke-test syntax**

There are no JS unit tests in this repo (per the survey). Verify the file parses by loading it in a browser console or running:

```bash
node -e "var fs = require('fs'); var src = fs.readFileSync('vivarium_dashboard/static/study-detail.js', 'utf8'); new Function(src);"
```

Expected: no exception (silent success).

If node isn't available, manually open `study-detail.js` and visually scan for matched braces / parens.

- [ ] **Step 3: Commit**

```bash
git add vivarium_dashboard/static/study-detail.js
git commit -m "feat(ui): add renderParamForm helper that pre-fills inputs from /api/composite-resolve"
```

---

## Task 5: Variants tab

Lists each entry in `study.variants[]`. Per row: name · base composite · overrides count (with toggle to show full overrides dict). Three actions: Edit params, Delete, Run. Footer `+ New variant` opens an inline form: pick a base composite (dropdown of `study.baseline[].name`) → params form (via Task 4 helper) pre-filled with the chosen baseline's defaults → name input → Save.

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html`
- Modify: `vivarium_dashboard/static/study-detail.js`
- Modify: `tests/test_study_detail_page.py`

- [ ] **Step 1: Failing tests**

Append to `tests/test_study_detail_page.py`:

```python
def test_variants_panel_lists_entries(_ws):
    """Variants panel renders one .variant-row per variants[] entry, with name + base_composite + params count."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    # The legacy fixture has no variants[] (the only variant has `source:` so it migrated
    # to baseline). So expect the empty-message instead of a row.
    assert 'variant-row' in html or 'No variants yet' in html


def test_variants_panel_has_new_variant_button(_ws):
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'btn-variant-new' in html
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m pytest tests/test_study_detail_page.py -k variants_panel -v
```

Expected: 2 tests FAIL.

- [ ] **Step 3: Fill the Variants panel**

Replace the placeholder inside `<section class="study-tab-panel" data-kind="variants" id="panel-variants">`:

```html
  <div class="variants-list">
    {% for v in (study.variants or []) %}
    <article class="variant-row" data-variant-name="{{ v.name }}"
             data-base-composite="{{ v.base_composite or '' }}">
      <header class="variant-row-header">
        <h3 class="variant-name">{{ v.name }}</h3>
        <span class="variant-base">based on <code>{{ v.base_composite or '(none)' }}</code></span>
        <span class="variant-overrides-count muted">{{ (v.parameter_overrides or {})|length }} override(s)</span>
      </header>
      {% if v.parameter_overrides %}
      <details class="variant-overrides-detail">
        <summary>Parameter overrides</summary>
        <table class="variant-overrides-table">
          <tbody>
          {% for k, val in v.parameter_overrides.items() %}
          <tr><td><code>{{ k }}</code></td><td><code>{{ val }}</code></td></tr>
          {% endfor %}
          </tbody>
        </table>
      </details>
      {% endif %}
      <div class="variant-row-actions">
        <button class="btn-variant-edit" data-variant-name="{{ v.name }}">Edit params</button>
        <button class="btn-variant-delete" data-variant-name="{{ v.name }}">Delete</button>
        <button class="btn-variant-run" data-variant-name="{{ v.name }}">Run</button>
      </div>
    </article>
    {% else %}
    <p class="empty-message">No variants yet. Add one to perturb a baseline composite's parameters.</p>
    {% endfor %}
  </div>

  <details class="variant-add-form-wrapper">
    <summary class="btn-variant-new">+ New variant</summary>
    <form id="variant-new-form" onsubmit="return _submitVariantAdd(event)">
      <label>Name: <input name="name" required pattern="[a-zA-Z0-9_-]+"></label>
      <label>Base composite:
        <select name="base_composite" required onchange="_onBaseCompositeChange(this)">
          <option value="">— pick a baseline composite —</option>
          {% for b in (study.baseline or []) %}
          <option value="{{ b.name }}" data-composite-id="{{ b.composite }}">{{ b.name }} ({{ b.composite }})</option>
          {% endfor %}
        </select>
      </label>
      <div id="variant-new-params" class="param-form">
        <p class="muted">Pick a base composite to see its parameters.</p>
      </div>
      <button type="submit">Save variant</button>
    </form>
  </details>

  <!-- Hidden edit form, populated dynamically when "Edit params" is clicked -->
  <dialog id="variant-edit-dialog">
    <form id="variant-edit-form" onsubmit="return _submitVariantEdit(event)" method="dialog">
      <h3>Edit parameter overrides for <code id="variant-edit-name"></code></h3>
      <div id="variant-edit-params" class="param-form"></div>
      <menu>
        <button type="button" onclick="document.getElementById('variant-edit-dialog').close()">Cancel</button>
        <button type="submit">Save</button>
      </menu>
    </form>
  </dialog>
```

- [ ] **Step 4: Add JS handlers**

In `study-detail.js`, inside the IIFE, add (after the renderParamForm helper from Task 4):

```js
  // Variant add — base-composite dropdown changes trigger param-form render.
  var _currentVariantAddCollect = null;
  function _onBaseCompositeChange(selectEl) {
    var opt = selectEl.options[selectEl.selectedIndex];
    var specId = opt.dataset.compositeId || '';
    var container = document.getElementById('variant-new-params');
    if (!specId) {
      container.innerHTML = '<p class="muted">Pick a base composite to see its parameters.</p>';
      _currentVariantAddCollect = null;
      return;
    }
    container.innerHTML = '<p class="muted">Loading parameters…</p>';
    renderParamForm(container, specId, {}).then(function(result) {
      _currentVariantAddCollect = result.collect;
    });
  }
  window._onBaseCompositeChange = _onBaseCompositeChange;

  function _submitVariantAdd(ev) {
    ev.preventDefault();
    var form = ev.target;
    var name = form.name.value.trim();
    var baseComposite = form.base_composite.value;
    if (!name || !baseComposite) { alert('Name and base composite are required.'); return false; }
    var overrides = _currentVariantAddCollect ? _currentVariantAddCollect() : {};
    api('POST', '/api/study-variant-add', {
      study: studyName(), name: name, base_composite: baseComposite,
      parameter_overrides: overrides
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Add variant failed: ' + (r.body && r.body.error || r.status));
    });
    return false;
  }
  window._submitVariantAdd = _submitVariantAdd;

  // Variant edit — populate dialog, render param form with current overrides, then save.
  var _currentVariantEditCollect = null;
  bindAll('.btn-variant-edit', function() {
    var variantName = this.dataset.variantName;
    var variant = (window._study.variants || []).filter(function(v) { return v.name === variantName; })[0];
    if (!variant) { alert('Variant not found in local spec.'); return; }
    var baseEntry = (window._study.baseline || []).filter(function(b) { return b.name === variant.base_composite; })[0];
    if (!baseEntry) { alert('Variant references a base composite that no longer exists.'); return; }
    document.getElementById('variant-edit-name').textContent = variantName;
    var container = document.getElementById('variant-edit-params');
    container.innerHTML = '<p class="muted">Loading parameters…</p>';
    renderParamForm(container, baseEntry.composite, variant.parameter_overrides || {}).then(function(result) {
      _currentVariantEditCollect = result.collect;
      document.getElementById('variant-edit-dialog').dataset.variantName = variantName;
      document.getElementById('variant-edit-dialog').showModal();
    });
  });

  function _submitVariantEdit(ev) {
    ev.preventDefault();
    var dialog = document.getElementById('variant-edit-dialog');
    var variantName = dialog.dataset.variantName;
    var overrides = _currentVariantEditCollect ? _currentVariantEditCollect() : {};
    api('POST', '/api/study-variant-set-params', {
      study: studyName(), variant: variantName, parameter_overrides: overrides
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Save failed: ' + (r.body && r.body.error || r.status));
    });
    return false;
  }
  window._submitVariantEdit = _submitVariantEdit;

  bindAll('.btn-variant-delete', function() {
    var variantName = this.dataset.variantName;
    if (!confirm('Delete variant "' + variantName + '"?')) return;
    api('POST', '/api/study-variant-delete', {
      study: studyName(), variant: variantName
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Delete failed: ' + (r.body && r.body.error || r.status));
    });
  });

  bindAll('.btn-variant-run', function() {
    var variantName = this.dataset.variantName;
    api('POST', '/api/study-run-variant', {
      study: studyName(), variant: variantName
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Run failed: ' + (r.body && r.body.error || r.status));
    });
  });
```

The OLD `.btn-add-variant`, `.btn-edit-variant`, `.btn-delete-variant`, `.btn-run-variant` handlers in `study-detail.js` are now superseded — find them in the existing file and DELETE them (they sent `extends:` + `description:` which Plan 2's helper rejects with 400).

- [ ] **Step 5: Run tests; expect PASS**

```bash
python3 -m pytest tests/test_study_detail_page.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html vivarium_dashboard/static/study-detail.js tests/test_study_detail_page.py
git commit -m "feat(ui): Variants tab — add/edit/delete/run with base_composite picker + param form"
```

---

## Task 6: Interventions tab

CRUD list of `{name, description}`. Inline form per row for edit, footer form for add.

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html`
- Modify: `vivarium_dashboard/static/study-detail.js`
- Modify: `tests/test_study_detail_page.py`

- [ ] **Step 1: Failing tests**

Append:

```python
def test_interventions_panel_lists_entries(_ws):
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'intervention-row' in html or 'No interventions yet' in html


def test_interventions_panel_has_new_button(_ws):
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'btn-intervention-new' in html
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_study_detail_page.py -k interventions_panel -v
```

Expected: 2 fail.

- [ ] **Step 3: Fill the Interventions panel**

Replace the placeholder inside `<section class="study-tab-panel" data-kind="interventions" id="panel-interventions">`:

```html
  <div class="interventions-list">
    {% for i in (study.interventions or []) %}
    <article class="intervention-row" data-intervention-name="{{ i.name }}">
      <header class="intervention-row-header">
        <h3 class="intervention-name">{{ i.name }}</h3>
      </header>
      <p class="intervention-description"
         id="intervention-description-{{ i.name }}"
         data-editable-intervention="{{ i.name }}">{{ i.description or '' }}</p>
      <div class="intervention-row-actions">
        <button class="btn-intervention-delete" data-intervention-name="{{ i.name }}">Delete</button>
      </div>
    </article>
    {% else %}
    <p class="empty-message">No interventions yet. Add one to record an experimental condition.</p>
    {% endfor %}
  </div>

  <details class="intervention-add-form-wrapper">
    <summary class="btn-intervention-new">+ New intervention</summary>
    <form id="intervention-new-form" onsubmit="return _submitInterventionAdd(event)">
      <label>Name: <input name="name" required pattern="[a-zA-Z0-9_-]+"></label>
      <label>Description: <textarea name="description" placeholder="Describe the experimental condition"></textarea></label>
      <button type="submit">Add</button>
    </form>
  </details>
```

- [ ] **Step 4: Add JS handlers**

Append to `study-detail.js` (inside the IIFE, after the variant handlers):

```js
  function _submitInterventionAdd(ev) {
    ev.preventDefault();
    var form = ev.target;
    api('POST', '/api/study-intervention-add', {
      study: studyName(),
      name: form.name.value.trim(),
      description: form.description.value
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Add failed: ' + (r.body && r.body.error || r.status));
    });
    return false;
  }
  window._submitInterventionAdd = _submitInterventionAdd;

  bindAll('.btn-intervention-delete', function() {
    var name = this.dataset.interventionName;
    if (!confirm('Delete intervention "' + name + '"?')) return;
    api('POST', '/api/study-intervention-delete', {
      study: studyName(), name: name
    }).then(function(r) {
      if (r.status === 200) location.reload();
      else alert('Delete failed: ' + (r.body && r.body.error || r.status));
    });
  });

  // Inline-edit intervention descriptions. Uses a click-to-textarea pattern
  // parallel to makeEditable but POSTs to the intervention-update endpoint.
  document.querySelectorAll('[data-editable-intervention]').forEach(function(el) {
    el.addEventListener('click', function() {
      var name = el.dataset.editableIntervention;
      var current = el.textContent;
      var t = document.createElement('textarea');
      t.value = current;
      t.style.width = '100%';
      t.rows = 3;
      el.replaceWith(t);
      t.focus();
      t.addEventListener('blur', function() {
        api('POST', '/api/study-intervention-update', {
          study: studyName(), name: name, description: t.value
        }).then(function(r) {
          if (r.status === 200) location.reload();
          else { alert('Update failed: ' + (r.body && r.body.error || r.status)); }
        });
      });
    });
  });
```

- [ ] **Step 5: Run tests; expect PASS**

```bash
python3 -m pytest tests/test_study_detail_page.py -v
```

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html vivarium_dashboard/static/study-detail.js tests/test_study_detail_page.py
git commit -m "feat(ui): Interventions tab — text CRUD with inline-edit description"
```

---

## Task 7: Runs tab

Move the existing runs table and visualizations section into the Runs panel. The runs table is largely unchanged (columns, checkbox-compare, view/delete actions); update only the per-row `variant` column to display either the variant name OR the baseline name (since baseline runs now record `composite: <name>` in addition to `variant: null`).

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html`
- Modify: `vivarium_dashboard/static/study-detail.js`
- Modify: `tests/test_study_detail_page.py`

- [ ] **Step 1: Failing tests**

Append:

```python
def test_runs_panel_has_runs_table(_ws):
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'id="runs-table"' in html


def test_runs_panel_includes_visualizations(_ws):
    """Runs panel folds in the visualizations section."""
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("study-monod_kinetics-096184")
    html = _render_study_detail_html("study-monod_kinetics-096184", spec)
    assert 'id="viz-list"' in html
    assert 'btn-add-viz' in html
```

- [ ] **Step 2: Verify fail**

```bash
python3 -m pytest tests/test_study_detail_page.py -k runs_panel -v
```

Expected: 2 fail.

- [ ] **Step 3: Fill the Runs panel**

Replace the placeholder inside `<section class="study-tab-panel" data-kind="runs" id="panel-runs">`:

```html
  <div class="runs-section">
    <h3 class="section-title">Runs</h3>
    <table id="runs-table">
      <thead>
        <tr>
          <th></th>
          <th>Variant / Baseline</th>
          <th>Label</th>
          <th>Steps</th>
          <th>Status</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {% for r in (study.runs or []) %}
        <tr data-run-id="{{ r.run_id }}">
          <td><input type="checkbox" class="run-compare-checkbox" value="{{ r.run_id }}"></td>
          <td>{{ r.variant or r.composite or 'baseline' }}</td>
          <td>{{ r.label or '' }}</td>
          <td>{{ r.n_steps or '' }}</td>
          <td>{{ r.status }}</td>
          <td>
            <button class="btn-view-run" data-run-id="{{ r.run_id }}">View</button>
            <button class="btn-delete-run" data-run-id="{{ r.run_id }}">Delete</button>
          </td>
        </tr>
        {% else %}
        <tr><td colspan="6" class="empty-message">No runs yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
    <div class="runs-actions">
      <button class="btn-compare-selected">Compare selected</button>
      <button class="btn-clear-runs danger">Clear all runs</button>
    </div>
  </div>

  <div class="viz-section">
    <h3 class="section-title">Visualizations</h3>
    <div id="viz-list">
      {% for v in (study.visualizations or []) %}
      <div class="viz-config">{{ v.name }} <code class="muted">{{ v.address or '' }}</code></div>
      {% endfor %}
    </div>
    <button class="btn-add-viz">+ Add visualization</button>
  </div>
```

(Removed the `r.viz` icon column since `viz` is never written; replaced `v.kind` with `v.address` since that's the actually-stored field.)

- [ ] **Step 4: Re-wire the existing run handlers**

The existing handlers (`.btn-view-run`, `.btn-delete-run`, `.btn-clear-runs`, `.btn-compare-selected`) in `study-detail.js` are already correct for these selectors — verify they still pick up the new markup. If `.btn-view-run` currently links to `/composite-explorer?run_id=<id>` (a broken URL per the survey), keep that for now — fixing the composite-explorer entry path is OUT OF SCOPE for Plan 3 (it depends on how the Composite Explorer page is invoked, which is a separate concern). Just note the broken link as a follow-up.

For `.btn-add-viz`: currently it alerts "not implemented in Phase 1". Replace with a real flow by linking to the existing modal in `walkthrough.js`. However, `walkthrough.js` is not loaded on the study-detail page, so the modal isn't available here. **Simplest fix:** open the Investigations page anchor that hosts the modal, with the study pre-selected. Replace the existing alert handler with:

```js
  bindAll('.btn-add-viz', function() {
    // The add-viz modal lives on the main dashboard page. Take the user there.
    location.href = '/#composite-explore?study=' + encodeURIComponent(studyName());
  });
```

(Out of scope for Plan 3: building a standalone add-viz modal on this page. Tracked as a follow-up.)

- [ ] **Step 5: Run tests; expect PASS**

```bash
python3 -m pytest tests/test_study_detail_page.py -v
```

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/templates/study-detail.html vivarium_dashboard/static/study-detail.js tests/test_study_detail_page.py
git commit -m "feat(ui): Runs tab — fold runs + visualizations into one panel; show baseline name for baseline runs"
```

---

## Task 8: CSS

Add styling for the new 5-tab UI. Reuse the existing `.registry-tab` color palette (blue accent on active) and the `.status-pill` family. Add new selectors for the per-tab content (`.baseline-entry`, `.variant-row`, `.intervention-row`, `.param-row`, `.study-counts-strip`, etc.).

**Files:**
- Modify: `vivarium_dashboard/static/style.css`
- Modify: `tests/test_study_detail_page.py` (smoke assertion that the CSS file references the new classes — optional)

- [ ] **Step 1: Append CSS rules**

Append to `vivarium_dashboard/static/style.css`:

```css
/* ===== Study Detail — 5-tab redesign (Plan 3) ===== */

/* Tab bar — visually identical to .registry-tabs but scoped */
.study-tabs {
  display: flex;
  gap: 24px;
  align-items: center;
  border-bottom: 1px solid #e5e7eb;
  margin: 16px 0 20px;
}
.study-tab {
  background: transparent;
  border: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  margin: 0;
  font: inherit;
  font-size: 0.95em;
  color: #6b7280;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: color 0.12s, border-color 0.12s;
}
.study-tab:hover { color: #1f2937; }
.study-tab.active {
  color: #2563eb;
  border-bottom-color: #2563eb;
  font-weight: 600;
}
.study-tab-panel { display: none; padding: 8px 0; }
.study-tab-panel.active { display: block; }

/* Study header — name + status pill */
.study-header { margin: 0 0 8px; }
.study-title { display: flex; align-items: center; gap: 12px; margin: 0; font-size: 1.6em; }
.study-title .study-name { font-weight: 600; }

/* Overview tab — notebook layout */
.study-overview { max-width: 760px; }
.overview-section { margin-bottom: 24px; }
.overview-label { font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.06em; color: var(--gray); margin: 0 0 6px; }
.overview-prose {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px 14px;
  min-height: 40px;
  white-space: pre-wrap;
  cursor: text;
}
.overview-prose:empty::before {
  content: attr(data-placeholder);
  color: #9ca3af;
  font-style: italic;
}

.study-counts-strip {
  display: flex;
  gap: 24px;
  padding: 12px 0;
  border-top: 1px solid #eee;
  border-bottom: 1px solid #eee;
  margin: 18px 0;
}
.count-cell { display: flex; flex-direction: column; align-items: flex-start; min-width: 60px; }
.count-cell strong { font-size: 1.2em; }
.count-cell .count-label { color: #888; font-size: 0.78em; }

/* Baseline tab */
.baseline-list { display: flex; flex-direction: column; gap: 12px; }
.baseline-entry {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px 16px;
}
.baseline-entry-header { display: flex; align-items: baseline; gap: 12px; }
.baseline-entry-name { margin: 0; font-size: 1.05em; }
.baseline-entry-fqn { color: var(--gray); font-size: 0.85em; }
.baseline-params { width: 100%; margin-top: 8px; font-size: 0.85em; border-collapse: collapse; }
.baseline-params th, .baseline-params td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #f3f4f6; }
.baseline-entry-actions { display: flex; gap: 8px; margin-top: 10px; }

.baseline-add-form, .variant-add-form-wrapper, .intervention-add-form-wrapper {
  margin-top: 16px;
}
.baseline-add-form summary, .variant-add-form-wrapper summary, .intervention-add-form-wrapper summary {
  display: inline-block;
  cursor: pointer;
  color: #2563eb;
  font-weight: 600;
}
.baseline-add-form form, .variant-add-form-wrapper form, .intervention-add-form-wrapper form {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 500px;
}
.baseline-add-form label, .variant-add-form-wrapper label, .intervention-add-form-wrapper label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.9em;
}

/* Variants tab */
.variants-list { display: flex; flex-direction: column; gap: 12px; }
.variant-row {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px 16px;
}
.variant-row-header { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.variant-name { margin: 0; font-size: 1.05em; }
.variant-base { color: var(--gray); font-size: 0.9em; }
.variant-overrides-count { font-size: 0.85em; }
.variant-overrides-detail { margin-top: 8px; }
.variant-overrides-table { width: 100%; font-size: 0.85em; border-collapse: collapse; }
.variant-overrides-table td { padding: 3px 8px; border-bottom: 1px solid #f3f4f6; }
.variant-row-actions { display: flex; gap: 8px; margin-top: 10px; }

.param-form { display: flex; flex-direction: column; gap: 8px; margin: 12px 0; }
.param-row { display: flex; flex-direction: column; gap: 2px; }
.param-label { display: flex; align-items: center; gap: 10px; }
.param-input { padding: 4px 8px; border: 1px solid var(--border); border-radius: 4px; min-width: 200px; }
.param-desc { font-size: 0.8em; }

/* Interventions tab */
.interventions-list { display: flex; flex-direction: column; gap: 12px; }
.intervention-row {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px 16px;
}
.intervention-row-header { display: flex; align-items: baseline; gap: 12px; }
.intervention-name { margin: 0; font-size: 1.05em; }
.intervention-description { margin: 8px 0; cursor: text; min-height: 1.5em; white-space: pre-wrap; }
.intervention-description:empty::before {
  content: "(click to add a description)";
  color: #9ca3af;
  font-style: italic;
}
.intervention-row-actions { display: flex; gap: 8px; margin-top: 4px; }

/* Empty state */
.empty-message { color: var(--gray); font-style: italic; padding: 12px 0; }

/* Run-related shared utilities */
.section-title { font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.06em; color: var(--gray); margin: 16px 0 8px; }
.muted { color: var(--gray); }
.danger { background: #fee2e2; color: #991b1b; border-color: #fca5a5; }
```

- [ ] **Step 2: Visual smoke test**

Start the dashboard and load a study page in the browser:

```bash
cd /Users/eranagmon/code/vivarium-dashboard
# Start the server however the project starts it — adapt if needed.
# Then navigate to http://127.0.0.1:<port>/studies/<a-real-study-name>
```

Verify:
- The tab bar shows 5 tabs with the Overview tab active.
- Clicking tabs swaps the panel.
- Objective and Conclusion are click-to-edit.
- The counts strip shows the right numbers.
- The baseline tab lists composites (or shows empty-message).
- The variants tab renders.
- The interventions tab renders.
- The runs tab renders.

If any panel looks wrong, fix the CSS inline before committing.

- [ ] **Step 3: Commit**

```bash
git add vivarium_dashboard/static/style.css
git commit -m "feat(ui): style the new 5-tab Study Detail layout"
```

---

## Task 9: End-to-end smoke test

A single Python test that renders the full page against a comprehensive v3 fixture (multi-entry baseline, multiple variants, multiple interventions, multiple runs) and asserts all 5 tabs render together correctly. This is the regression guard for the whole UI rewrite.

**Files:**
- Modify: `tests/test_study_detail_page.py`

- [ ] **Step 1: Add the fixture + test**

Append to `tests/test_study_detail_page.py`:

```python
@pytest.fixture
def _rich_ws(tmp_path, monkeypatch):
    """Workspace with a richly-populated v3 study to exercise every tab."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    sd = ws / "studies" / "rich"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "rich",
        "objective": "Compare growth kinetics across substrate-affinity variants.",
        "status": "in_progress",
        "baseline": [
            {"name": "core", "composite": "pkg.composites.core", "params": {"k": 1}},
            {"name": "alt",  "composite": "pkg.composites.alt",  "params": {}},
        ],
        "variants": [
            {"name": "hi", "base_composite": "core", "parameter_overrides": {"k": 2}},
            {"name": "lo", "base_composite": "core", "parameter_overrides": {"k": 0.5}},
        ],
        "interventions": [
            {"name": "heat-shock", "description": "+10C for 5 min at t=10"},
        ],
        "runs": [
            {"run_id": "r1", "variant": None, "composite": "core", "label": "core",
             "n_steps": 5, "status": "completed"},
            {"run_id": "r2", "variant": "hi",  "composite": "core", "label": "hi",
             "n_steps": 5, "status": "completed"},
        ],
        "visualizations": [
            {"name": "growth-curve", "address": "viv.metric.growth", "config": {}},
        ],
        "conclusion": "Variant `hi` showed faster early growth but plateaued sooner.",
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_full_study_renders_all_tabs(_rich_ws):
    from vivarium_dashboard.server import _render_study_detail_html, _study_detail_spec
    spec = _study_detail_spec("rich")
    html = _render_study_detail_html("rich", spec)

    # 5 tabs scaffolded
    for kind in ("overview", "baseline", "variants", "interventions", "runs"):
        assert f'data-kind="{kind}"' in html

    # Overview: objective text + counts
    assert "Compare growth kinetics" in html
    assert "2</strong>" in html  # 2 variants OR 2 runs OR 2 baseline entries — at least one matches

    # Baseline: both entries + their FQNs
    assert 'data-baseline-name="core"' in html
    assert 'data-baseline-name="alt"' in html
    assert "pkg.composites.core" in html
    assert "pkg.composites.alt" in html

    # Variants: both + base_composite references
    assert 'data-variant-name="hi"' in html
    assert 'data-variant-name="lo"' in html
    assert "based on" in html

    # Interventions: the one entry + its description
    assert 'data-intervention-name="heat-shock"' in html
    assert "+10C for 5 min" in html

    # Runs: both runs + viz section
    assert 'data-run-id="r1"' in html
    assert 'data-run-id="r2"' in html
    assert "growth-curve" in html

    # Conclusion text rendered
    assert "Variant `hi` showed faster early growth" in html
```

- [ ] **Step 2: Run the test**

```bash
python3 -m pytest tests/test_study_detail_page.py::test_full_study_renders_all_tabs -v
```

Expected: PASS.

- [ ] **Step 3: Run the full test suite**

```bash
python3 -m pytest -q 2>&1 | tail -10
```

Expected: 6 failed total — all 6 still the known pre-existing list (LAMMPS, scripts._lib, multi_cell registry, v2-spec-create endpoint). No new failures.

- [ ] **Step 4: Commit**

```bash
git add tests/test_study_detail_page.py
git commit -m "test(ui): end-to-end smoke test for full Study Detail page render"
```

---

## Final Verification

After Task 9:

```bash
python3 -m pytest -q 2>&1 | tail -10
```

Expected:
- **6 failed** (all pre-existing, unchanged from start of Plan 3): test_investigation_run_e2e (2), test_investigations (1), test_study_runs (2), test_visualization_endpoints (1).
- All Plan 3 tests pass.
- No new regressions.

Visual verification (manual):
- Load any real study at `/studies/<name>`.
- Click through all 5 tabs.
- Add a baseline composite; verify it appears.
- Add a variant with parameter overrides via the picker + form.
- Add an intervention; edit its description inline; delete it.
- Run a baseline; verify a run record appears in the Runs tab.

---

## Notes for future cleanup (post-Plan 3)

- **The `_study_dir` writers still using WORKSPACE global** — `_post_study_variant_delete`, `_post_study_run_delete`, `_post_study_runs_clear`, `_post_study_comparison_add` — should migrate to inline `ws_root`-based path resolution for testability uniformity (matches the pattern Plan 2 established).
- **`.btn-view-run` opens `/composite-explorer?run_id=<id>`** — there is no such route in `server.py`; this has been broken since before Plan 3. Likely should open `/#composite-explore` with appropriate state. Defer to a separate fix.
- **Add-viz on the Study Detail page links to `/#composite-explore`** — the actual add-viz modal lives in `walkthrough.js` on the dashboard page. A self-contained add-viz on the Study Detail page is a Plan-4 candidate.
- **Conclusion shape** — if any v2 spec stored `conclusion` as `{"text": "..."}` and got migrated incompletely, the template's `study.conclusion or ''` may render `{'text': '...'}` literally. Confirm what `migrate_v2_to_v3` does to a dict conclusion; fix the template if needed.
- **Inline-edit visual cue** — `[data-editable]` has no hover/focus border. Plan 3 ships without it for simplicity; a `:hover { border-color: #d1d5db }` rule on `.overview-prose` is a small follow-up.
