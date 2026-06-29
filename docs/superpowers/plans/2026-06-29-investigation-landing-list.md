# All-Investigations Landing List Declutter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the all-investigations landing list scannable and uniform — group cards into Active/Closed sections, declutter each card, condense the header, and add a client-side filter — with no backend change.

**Architecture:** Two files: rewrite `_renderInvestigationSets()` in `static/walkthrough.js` (grouped sections + decluttered cards + filter data-attrs + a new `_filterInvestigations()`), and restructure the `#page-investigations` header skeleton in `templates/index.html.j2` (filter input, one-line lead, plain list container). All client-side; `window._isetIndex` data is unchanged.

**Tech Stack:** Jinja template + vanilla JS + pytest source-assertions + Jinja-parse + `node --check`.

## Global Constraints

- No API/data change. `window._isetIndex[]` items still carry `name` (= slug), `title`, `description`, `status`, `effective_status`, `current`, `n_studies`.
- Preserve behavior: click → `_openInvestigationDetail(name)`; the ↓report/↓notebook links (`_vivReportFromCard`/`_vivNotebookFromCard`); the existing sort (archived/closed → bottom, baseline → top, else declaration order); the empty-index message; the New/Clone modals (untouched).
- No information removed, only relocated: slug → card `title=` tooltip; intent divergence → status-pill `title=` tooltip.
- Tests assert on source. Template must Jinja-parse; JS must pass `node --check`.
- Run python via `/Users/eranagmon/code/venv/bin/python`. Co-author commits: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

**Shared test file header** (`tests/test_investigation_landing_list.py`):

```python
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vivarium_dashboard/templates/index.html.j2").read_text()
JS = (ROOT / "vivarium_dashboard/static/walkthrough.js").read_text()


def page_investigations():
    """The #page-investigations header region, up to the first modal."""
    i = HTML.index('id="page-investigations"')
    j = HTML.index('id="new-iset-modal"', i)
    return HTML[i:j]
```

---

### Task 1: Header skeleton — filter input, condensed lead, plain list container

**Files:**
- Modify: `vivarium_dashboard/templates/index.html.j2` (`#page-investigations` header, ~lines 787–799)
- Test: `tests/test_investigation_landing_list.py`

**Interfaces:**
- Produces: `#investigations-filter` (a `<input>` calling `_filterInvestigations()` on input); a one-line `#investigation-page-lead`; `#investigations-list` as a plain block (grid moved to JS-built `.investigations-grid` wrappers in Task 2).

- [ ] **Step 1: Write the failing tests** (create the file with the shared header above, then add:)

```python
def test_filter_input_present_and_dead_div_gone():
    p = page_investigations()
    assert 'id="investigations-filter"' in p
    assert 'oninput="_filterInvestigations()"' in p
    assert 'actions now live in' not in p  # dead actions comment/div removed


def test_lead_condensed():
    p = page_investigations()
    assert 'preserved as artifacts' not in p          # verbose lead gone
    assert 'open its study graph' in p                # condensed lead


def test_list_container_not_inline_grid():
    p = page_investigations()
    i = p.index('id="investigations-list"')
    assert 'grid-template-columns' not in p[i:i + 200]  # grid moved to .investigations-grid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_landing_list.py -k "filter_input or lead_condensed or list_container" -q`
Expected: FAIL (no filter input; verbose lead; inline grid present).

- [ ] **Step 3: Restructure the header**

Find (~lines 787–799):

```html
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
    <h2 class="page-title" style="margin:0">Investigations</h2>
    <div style="display:flex;gap:8px;align-items:center">
      <!-- "Switch investigation" + "+ New Investigation" actions now live in
           the left-rail investigation switcher dropdown (top of the rail). -->
    </div>
  </div>
  <p class="page-lead" id="investigation-page-lead">All investigations in this repo. Select one to open it — its studies appear in the left rail. Merged investigations are preserved as artifacts; in-progress ones live on their branch/PR.</p>
  <!-- List view: investigation cards. -->
  <div id="investigations-list" class="investigations-list-grid" style="display:grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap:12px;">
    <p class="empty-state">Loading…</p>
  </div>
```

Replace with:

```html
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
    <h2 class="page-title" style="margin:0">Investigations</h2>
    <div style="display:flex;gap:8px;align-items:center">
      <input id="investigations-filter" type="search" placeholder="Filter investigations…"
             aria-label="Filter investigations" oninput="_filterInvestigations()"
             style="padding:5px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:0.9em;min-width:220px">
    </div>
  </div>
  <p class="page-lead" id="investigation-page-lead">Select an investigation to open its study graph.</p>
  <!-- List view: grouped investigation cards (Active / Closed). The grid lives on
       the per-group .investigations-grid wrappers built by _renderInvestigationSets. -->
  <div id="investigations-list">
    <p class="empty-state">Loading…</p>
  </div>
```

- [ ] **Step 4: Run tests + Jinja parse**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_landing_list.py -k "filter_input or lead_condensed or list_container" -q && /Users/eranagmon/code/venv/bin/python -c "from jinja2 import Environment; Environment().parse(open('vivarium_dashboard/templates/index.html.j2').read()); print('PARSE_OK')"`
Expected: PASS (3) + PARSE_OK.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/templates/index.html.j2 tests/test_investigation_landing_list.py
git commit -m "feat(inv-list): filter input + condensed header; list container is a plain block"
```

---

### Task 2: Grouped, decluttered cards + the filter function

**Files:**
- Modify: `vivarium_dashboard/static/walkthrough.js` (`_renderInvestigationSets`, ~lines 4724–4795; add `_filterInvestigations` right after it)
- Test: `tests/test_investigation_landing_list.py`

**Interfaces:**
- Consumes: the `#investigations-filter` input + `#investigations-list` block from Task 1.
- Produces: `_renderInvestigationSets()` emits `.iset-group` blocks (header `iset-group-head` + count `iset-group-count` + `.investigations-grid` of cards) for Active then Closed; each card has `data-iset-title/-slug/-status` (lowercased) and a `title=` slug tooltip; `_filterInvestigations()` (on `window`) filters cards by title+slug+status, updates per-group counts, hides empty groups, toggles `#investigations-empty`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_render_groups_and_filter_function():
    assert 'function _filterInvestigations' in JS
    assert 'window._filterInvestigations' in JS
    assert 'iset-group-head' in JS
    assert "_groupHtml('Active'" in JS and "_groupHtml('Closed'" in JS
    assert 'investigations-grid' in JS and 'grid-template-columns' in JS
    assert 'data-iset-status' in JS


def test_card_decluttered():
    i = JS.index('function _renderInvestigationSets')
    block = JS[i:i + 4200]
    assert 'click to open DAG' not in block      # filler removed
    assert 'font-family:monospace' not in block  # standalone slug row removed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_landing_list.py -k "render_groups or card_decluttered" -q`
Expected: FAIL.

- [ ] **Step 3: Replace `_renderInvestigationSets` (full function, lines 4724 through its closing `}` near 4795)**

Replace the entire existing function body with:

```javascript
  function _renderInvestigationSets() {
    var list = document.getElementById('investigations-list');
    if (!list) return;
    if (!window._isetIndex.length) {
      list.innerHTML = '<p class="empty-state">No investigations declared. Author one at <code>investigations/&lt;name&gt;/investigation.yaml</code>.</p>';
      return;
    }
    // Closed/archived sink to the bottom; baseline floats to the top; else
    // declaration order. The Active/Closed grouping below makes the split visual.
    var ordered = (window._isetIndex || []).map(function(it, idx) { return [it, idx]; });
    ordered.sort(function(a, b) {
      var ac = (a[0].status === 'archived' || a[0].status === 'closed') ? 1 : 0;
      var bc = (b[0].status === 'archived' || b[0].status === 'closed') ? 1 : 0;
      if (ac !== bc) return ac - bc;
      var ab = /baseline/i.test(a[0].name || '') ? 0 : 1;
      var bb = /baseline/i.test(b[0].name || '') ? 0 : 1;
      if (ab !== bb) return ab - bb;
      return a[1] - b[1];
    });

    function _isetCardHtml(iset) {
      var closed = (iset.status === 'archived' || iset.status === 'closed');
      var desc = (iset.description || '').split('\n')[0].slice(0, 240);
      // Prefer server effective_status; fall back to author status. Intent
      // divergence goes into the status-pill tooltip (not a separate line).
      var effStatus  = iset.effective_status || iset.status || 'planning';
      var authStatus = iset.status || 'planning';
      var pillClass  = effStatus.replace(/[^a-z_]/g, '_');
      var pillTip = (authStatus && authStatus !== effStatus)
        ? 'effective: ' + effStatus + '  ·  intent: ' + authStatus
        : 'status: ' + effStatus;
      var currentPill = iset.current
        ? '<span class="status-pill" style="font-size:0.72em;background:#dcfce7;color:#166534;border:1px solid #86efac">● current branch</span>'
        : '';
      var statusPill = closed
        ? '<span class="status-pill" style="font-size:0.78em;background:#e5e7eb;color:#4b5563;border:1px solid #d1d5db">Closed</span>'
        : '<span class="status-pill ' + pillClass + '" style="font-size:0.78em" title="' + _esc(pillTip) + '">' + _esc(effStatus) + '</span>';
      var cardStyle = 'background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px 16px;cursor:pointer;transition:box-shadow 0.1s,border-color 0.1s;' +
        (closed ? 'opacity:0.6;' : '');
      var filterStatus = (closed ? 'closed' : effStatus);
      return '<div class="investigation-set-card" onclick="_openInvestigationDetail(\'' + _esc(iset.name) + '\')" ' +
             'title="' + _esc(iset.name) + '" ' +
             'data-iset-title="' + _esc(String(iset.title || iset.name).toLowerCase()) + '" ' +
             'data-iset-slug="' + _esc(String(iset.name).toLowerCase()) + '" ' +
             'data-iset-status="' + _esc(String(filterStatus).toLowerCase()) + '" ' +
             'style="' + cardStyle + '">' +
        '<div style="display:flex;align-items:baseline;gap:10px;margin-bottom:6px;">' +
          '<strong style="font-size:1.05em;flex:1">' + _esc(iset.title || iset.name) + '</strong>' +
          currentPill +
          statusPill +
        '</div>' +
        (desc ? '<p style="margin:0 0 8px 0;font-size:0.9em;color:#475569">' + _esc(desc) + (iset.description.length > 240 ? '…' : '') + '</p>' : '') +
        '<div style="display:flex;align-items:center;gap:12px;font-size:0.85em;color:#64748b">' +
          '<span style="flex:1"><strong>' + iset.n_studies + '</strong> stud' + (iset.n_studies === 1 ? 'y' : 'ies') + '</span>' +
          '<a href="#" title="Download the rendered HTML report for this investigation" ' +
            'onclick="window._vivReportFromCard(event,\'' + _esc(iset.name) + '\');return false;" ' +
            'style="color:#3b82f6;text-decoration:none;white-space:nowrap">↓ report</a>' +
          '<a href="#" title="Download the runnable notebook for this investigation" ' +
            'onclick="window._vivNotebookFromCard(event,\'' + _esc(iset.name) + '\');return false;" ' +
            'style="color:#3b82f6;text-decoration:none;white-space:nowrap">↓ notebook</a>' +
        '</div>' +
      '</div>';
    }

    var GRID = 'display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:12px;margin:6px 0 14px';
    function _groupHtml(label, items) {
      if (!items.length) return '';
      return '<div class="iset-group" data-group-label="' + label + '">' +
        '<h3 class="iset-group-head" style="font-size:0.9em;color:#475569;font-weight:700;margin:10px 0 2px;text-transform:uppercase;letter-spacing:0.04em">' +
          label + ' <span class="iset-group-count" style="color:#94a3b8;font-weight:600">(' + items.length + ')</span></h3>' +
        '<div class="investigations-grid" style="' + GRID + '">' +
          items.map(_isetCardHtml).join('') +
        '</div>' +
      '</div>';
    }

    var active = [], closedItems = [];
    ordered.forEach(function(pair) {
      var iset = pair[0];
      if (iset.status === 'archived' || iset.status === 'closed') closedItems.push(iset);
      else active.push(iset);
    });

    list.innerHTML =
      _groupHtml('Active', active) +
      _groupHtml('Closed', closedItems) +
      '<p id="investigations-empty" class="empty-state" style="display:none">No investigations match the filter.</p>';

    _filterInvestigations();
  }

  // Client-side filter for the landing list: matches the query against each
  // card's title + slug + status (data-attrs), updates per-group counts, hides
  // empty groups, and toggles the "no matches" line. No re-fetch, no re-render.
  function _filterInvestigations() {
    var input = document.getElementById('investigations-filter');
    var q = ((input && input.value) || '').trim().toLowerCase();
    var anyVisible = false;
    document.querySelectorAll('#investigations-list .investigation-set-card').forEach(function(card) {
      var hay = (card.getAttribute('data-iset-title') || '') + ' ' +
                (card.getAttribute('data-iset-slug') || '') + ' ' +
                (card.getAttribute('data-iset-status') || '');
      var show = !q || hay.indexOf(q) !== -1;
      card.style.display = show ? '' : 'none';
      if (show) anyVisible = true;
    });
    document.querySelectorAll('#investigations-list .iset-group').forEach(function(group) {
      var n = 0;
      group.querySelectorAll('.investigation-set-card').forEach(function(c) {
        if (c.style.display !== 'none') n++;
      });
      var countEl = group.querySelector('.iset-group-count');
      if (countEl) countEl.textContent = '(' + n + ')';
      group.style.display = n ? '' : 'none';
    });
    var empty = document.getElementById('investigations-empty');
    if (empty) empty.style.display = anyVisible ? 'none' : '';
  }
  window._filterInvestigations = _filterInvestigations;
```

(If the old `_setInvestigationStatus` function followed `_renderInvestigationSets`, leave it in place — insert `_filterInvestigations` between them.)

- [ ] **Step 4: Run tests + node check**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_landing_list.py -q && node --check vivarium_dashboard/static/walkthrough.js`
Expected: PASS (5 tests) + node exit 0.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/walkthrough.js tests/test_investigation_landing_list.py
git commit -m "feat(inv-list): group cards into Active/Closed, declutter cards, add client filter"
```

---

## Final verification (controller, after both tasks)

- `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_landing_list.py -q` → 5 passed.
- Jinja parse OK; `node --check vivarium_dashboard/static/walkthrough.js` exit 0.
- Serve the SPA (recipe: `PYTHONPATH=<worktree>:/Users/eranagmon/code/process-bigraph /Users/eranagmon/code/vivarium-dashboard/.venv/bin/python -m vivarium_dashboard.cli serve --workspace /Users/eranagmon/code/v2e-main --port <P>`) and open the Investigations tab: Active/Closed sections with counts, decluttered cards (no slug row / "click to open DAG"), one-line lead; typing in the filter narrows cards live and updates the counts; clearing restores all; a card click still opens its DAG. (Full visual needs the browser — SPA renders client-side.)

## Self-Review

**Spec coverage:**
- ① Group into Active/Closed sections (header + count, baseline-top within Active, empty groups omitted) → Task 2 `_groupHtml` + `test_render_groups_and_filter_function`. ✓
- ② Declutter cards (drop "click to open DAG", slug row, intent line; slug+intent → tooltips; add filter data-attrs) → Task 2 `_isetCardHtml` + `test_card_decluttered`. ✓
- ③ Condense header + lead; remove dead actions div; grid CSS off `#investigations-list` → Task 1 + its 3 tests. ✓
- ④ Client-side filter (title+slug+status, per-group counts, empty groups hidden, no-match line) → Task 2 `_filterInvestigations` + `test_render_groups_and_filter_function`. ✓
- Preserve click-through / report / notebook / sort / modals / empty-index → Global Constraints + unchanged code in `_isetCardHtml` and the early return. ✓
- Out of scope (rail switcher, modals internals, detail page, server-side filter) → not touched. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step shows exact before+after and complete test code with exact commands. ✓

**Type/name consistency:** ids/classes (`investigations-filter`, `investigations-list`, `iset-group`, `iset-group-head`, `iset-group-count`, `investigations-grid`, `investigations-empty`, `investigation-set-card`), data-attrs (`data-iset-title/-slug/-status`), and function names (`_renderInvestigationSets`, `_filterInvestigations`, `_groupHtml`, `_isetCardHtml`) are identical across Task 1, Task 2, the tests, and the filter logic. The filter reads the same three data-attrs the card builder emits. ✓
