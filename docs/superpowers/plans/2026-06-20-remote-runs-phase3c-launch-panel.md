# Remote Runs — Phase 3c: launch-panel UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Run on remote (smsvpctest)" panel in a study's Runs tab: a small form (generations, seeds, run-parca) + button that POSTs `/api/remote-run-start`, then a 6-stage progress strip that polls `/api/remote-run-status`. Login is enforced by the server (401) and surfaced gracefully. This makes the Phase 3a/3b backend usable from the dashboard.

**Architecture:** Static HTML form in `study-detail.html` (inside `#panel-runs`) + vanilla JS handlers in `study-detail.js` mirroring the existing `investigation-run-unblocked` start→poll→render pattern (`walkthrough.js`). No framework, no new deps. Tests are string-presence assertions on the served `.js`/`.html` (repo convention) + a manual browser verify.

**Tech Stack:** Vanilla JS, Jinja2 HTML template, pytest (string-presence).

**Repo:** `/Users/eranagmon/code/vivarium-dashboard` (branch `feat/dashboard-remote-runs`).

## Global Constraints
- No new deps; vanilla JS only; match existing SPA style (`api(method,path,body)` wrapper, `escapeHtmlForTests()` HTML-escape, window-scoped handlers).
- The panel calls the LIVE `/api/remote-run-start` / `/api/remote-run-status` (mutation endpoints; no snapshot/DataSource equivalent) via raw `fetch` at the origin.
- **Login gate = the server's 401.** The handler must detect a 401 from `/api/remote-run-start` and show "Log in with GitHub to run remotely" rather than failing silently. Do not depend on the login chip existing on the study page.
- Poll interval 2000ms; stop on job `status` of `done` or `failed` (matches the existing pattern).
- Study slug from `studyName()` (`window._studyName`).
- The job shape from the backend (Phase 3b `RemoteRunJob.to_dict`): `{job_id, study, status, steps:[{name,status,message}], run_id, error, started_at, completed_at}`; `STEP_NAMES = push, build, run, poll, download, land`; step/job status values `pending|running|done|failed` (job also `queued`).

## Confirmed anchors (from the codebase)
- Runs-tab panel: `study-detail.html:1622` → `<section class="study-tab-panel" data-kind="runs" id="panel-runs">` (contains `#runs-table`). Add the launch panel at the TOP of this section.
- `study-detail.js:3` `api(method, path, body) -> {status, body}`; `studyName()` (`study-detail.js:411`); `escapeHtmlForTests()` HTML-escape helper exists in study-detail.js.
- Pattern to mirror: `walkthrough.js:5223` (POST→job_id), `:5265` (`setTimeout(tick, 2000)` poll loop), `:5288` (render per-item progress).
- Tests read `(server.STATIC_DIR / "study-detail.js").read_text()` and the `study-detail.html` template text (test_data_endpoints.py:594, :159).

## File Structure
- Modify `vivarium_dashboard/templates/study-detail.html` — the launch panel `<form>` + hidden `#remote-run-progress` div inside `#panel-runs`.
- Modify `vivarium_dashboard/static/study-detail.js` — `_submitRemoteRun`, `_pollRemoteRun`, `_renderRemoteRunProgress` (+ `window.` exposure).
- Test `tests/test_remote_run_panel.py` — assert the template + JS contain the wiring.

---

## Task 1: launch-panel HTML in the Runs tab

**Files:**
- Modify: `vivarium_dashboard/templates/study-detail.html` (inside `#panel-runs`, line 1622)
- Test: `tests/test_remote_run_panel.py`

**Interfaces:**
- Produces (in the served page): a `<form id="remote-run-form" onsubmit="return _submitRemoteRun(event)">` with `num_generations`, `num_seeds`, `run_parca` inputs + a submit button; and a hidden `<div id="remote-run-progress">`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_remote_run_panel.py`:

```python
from vivarium_dashboard import server

TPL = server.TEMPLATES_DIR / "study-detail.html" if hasattr(server, "TEMPLATES_DIR") else None


def _template_text():
    # study-detail.html lives next to the package templates
    from pathlib import Path
    p = Path(server.__file__).parent / "templates" / "study-detail.html"
    return p.read_text(encoding="utf-8")


def test_runs_tab_has_remote_run_form():
    t = _template_text()
    assert 'id="remote-run-form"' in t
    assert 'onsubmit="return _submitRemoteRun(event)"' in t
    assert 'name="num_generations"' in t
    assert 'name="num_seeds"' in t
    assert 'name="run_parca"' in t
    assert 'id="remote-run-progress"' in t
    assert "Run on remote" in t  # the panel heading/button label
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && .venv/bin/python -m pytest tests/test_remote_run_panel.py -v`
Expected: FAIL — `id="remote-run-form"` not in the template.

- [ ] **Step 3: Write minimal implementation**

In `vivarium_dashboard/templates/study-detail.html`, immediately AFTER the opening `<section ... id="panel-runs">` tag (line 1622), insert:

```html
  <div class="panel" style="margin-bottom:18px">
    <h3 style="margin-top:0">Run on remote (smsvpctest)</h3>
    <p class="muted" style="font-size:0.9em">
      Pushes the workspace's current branch, builds a simulator from that commit, and runs
      it on the Ray backend. Results land as a run on this study. Requires GitHub login.
    </p>
    <form id="remote-run-form" onsubmit="return _submitRemoteRun(event)">
      <label>Generations: <input name="num_generations" type="number" min="1" value="1" required></label>
      <label>Seeds: <input name="num_seeds" type="number" min="1" value="1" required></label>
      <label><input type="checkbox" name="run_parca" checked> Run ParCa</label>
      <button type="submit" class="btn-mini" id="remote-run-btn">▶ Run on remote</button>
    </form>
    <div id="remote-run-progress" hidden></div>
  </div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remote_run_panel.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/templates/study-detail.html tests/test_remote_run_panel.py
git commit -m "feat(remote-runs): launch-panel form in the study Runs tab"
```

---

## Task 2: JS handlers (submit + poll + render)

**Files:**
- Modify: `vivarium_dashboard/static/study-detail.js`
- Test: `tests/test_remote_run_panel.py`

**Interfaces:**
- Consumes (served page): `#remote-run-form`, `#remote-run-progress`, `#remote-run-btn`; `api`, `studyName`, `escapeHtmlForTests` (existing in study-detail.js); endpoints `/api/remote-run-start`, `/api/remote-run-status`.
- Produces: `window._submitRemoteRun(ev)`, `_pollRemoteRun(jobId)`, `_renderRemoteRunProgress(job)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_remote_run_panel.py`:

```python
from pathlib import Path


def _js_text():
    return (Path(server.__file__).parent / "static" / "study-detail.js").read_text(encoding="utf-8")


def test_js_has_remote_run_handlers_and_endpoints():
    js = _js_text()
    assert "_submitRemoteRun" in js
    assert "_pollRemoteRun" in js
    assert "_renderRemoteRunProgress" in js
    assert "/api/remote-run-start" in js
    assert "/api/remote-run-status" in js
    assert "window._submitRemoteRun" in js  # exposed for the inline onsubmit
    # login gate: a 401 from start must be handled explicitly
    assert "401" in js
    # poll cadence + terminal stop
    assert "2000" in js
    assert "'done'" in js or '"done"' in js
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_remote_run_panel.py::test_js_has_remote_run_handlers_and_endpoints -v`
Expected: FAIL — `_submitRemoteRun` not in study-detail.js.

- [ ] **Step 3: Write minimal implementation**

Append to `vivarium_dashboard/static/study-detail.js` (before any trailing IIFE close; if the file is a plain script, append at end). Use the existing `escapeHtmlForTests` escape helper:

```javascript
// ---- Remote run (smsvpctest) -------------------------------------------
var _remoteRunTimer = null;

function _submitRemoteRun(ev) {
  ev.preventDefault();
  var form = ev.target;
  var btn = document.getElementById('remote-run-btn');
  var prog = document.getElementById('remote-run-progress');
  var body = {
    study: studyName(),
    num_generations: parseInt(form.num_generations.value, 10) || 1,
    num_seeds: parseInt(form.num_seeds.value, 10) || 1,
    run_parca: !!form.run_parca.checked,
  };
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
  fetch('/api/remote-run-start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  }).then(function(r) {
    return r.json().then(function(j) { return {status: r.status, body: j}; });
  }).then(function(res) {
    if (res.status === 401) {
      if (prog) { prog.hidden = false; prog.innerHTML =
        '<div class="inv-run-err">Log in with GitHub (top-right on the main dashboard) to run remotely.</div>'; }
      if (btn) { btn.disabled = false; btn.textContent = '▶ Run on remote'; }
      return;
    }
    if (res.status !== 202 || !res.body.job_id) {
      if (prog) { prog.hidden = false; prog.innerHTML =
        '<div class="inv-run-err">Could not start: ' + escapeHtmlForTests((res.body && res.body.error) || res.status) + '</div>'; }
      if (btn) { btn.disabled = false; btn.textContent = '▶ Run on remote'; }
      return;
    }
    _pollRemoteRun(res.body.job_id);
  });
  return false;
}

function _pollRemoteRun(jobId) {
  if (_remoteRunTimer) clearTimeout(_remoteRunTimer);
  function tick() {
    fetch('/api/remote-run-status?job_id=' + encodeURIComponent(jobId))
      .then(function(r) { return r.json().then(function(j) { return {status: r.status, body: j}; }); })
      .then(function(res) {
        if (res.status !== 200) return;
        _renderRemoteRunProgress(res.body);
        if (res.body.status === 'done' || res.body.status === 'failed') {
          var btn = document.getElementById('remote-run-btn');
          if (btn) { btn.disabled = false; btn.textContent = '▶ Run on remote'; }
          return;
        }
        _remoteRunTimer = setTimeout(tick, 2000);
      });
  }
  tick();
}

function _renderRemoteRunProgress(job) {
  var prog = document.getElementById('remote-run-progress');
  if (!prog) return;
  prog.hidden = false;
  var icon = {pending: '⋯', running: '▶', done: '✓', failed: '✗'};
  var steps = (job.steps || []).map(function(s) {
    var msg = s.message ? ' <span class="muted">' + escapeHtmlForTests(s.message) + '</span>' : '';
    return '<div class="inv-run-item inv-run-' + (s.status || 'pending') + '">'
      + '<span class="inv-run-icon">' + (icon[s.status] || '?') + '</span> '
      + '<code>' + escapeHtmlForTests(s.name) + '</code>' + msg + '</div>';
  }).join('');
  var head;
  if (job.status === 'done') {
    head = '<strong>✓ Done.</strong> Landed run <code>' + escapeHtmlForTests(job.run_id || '') + '</code> — refresh to see it.';
  } else if (job.status === 'failed') {
    head = '<strong class="inv-run-err">✗ Failed.</strong> ' + escapeHtmlForTests(job.error || '');
  } else {
    head = '<strong>Running…</strong>';
  }
  prog.innerHTML = '<div class="inv-run-progress-banner">' + head + '</div>'
                 + '<div class="inv-run-list">' + steps + '</div>';
}

window._submitRemoteRun = _submitRemoteRun;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remote_run_panel.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/static/study-detail.js tests/test_remote_run_panel.py
git commit -m "feat(remote-runs): study-detail.js submit/poll/render for remote runs"
```

---

## Task 3: served-page integration test + manual verify

**Files:**
- Test: `tests/test_remote_run_panel.py`

**Interfaces:**
- Consumes: `_render_study_detail_html(name, spec)` (server.py:6445) — renders the template for a study.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_remote_run_panel.py`:

```python
def test_rendered_study_detail_includes_remote_run_panel():
    # Render the template with a minimal spec; the panel is static markup so any
    # spec that renders should include it.
    html = server._render_study_detail_html("demo-study", {"name": "demo-study"})
    assert 'id="remote-run-form"' in html
    assert "Run on remote" in html
    assert 'id="remote-run-progress"' in html
```

If `_render_study_detail_html` requires more spec keys to render without error, pass the minimal additional keys its Jinja template references (inspect the traceback and add only what's needed — e.g. `{"name": ..., "title": ...}`); do not stub the renderer.

- [ ] **Step 2: Run test to verify it passes (panel already added in Task 1)**

Run: `.venv/bin/python -m pytest tests/test_remote_run_panel.py -v`
Expected: PASS (3 tests). The panel is static markup, so once Task 1 added it to the template, the rendered HTML contains it. If the render raises on a missing spec key, add the minimal key(s) and re-run.

- [ ] **Step 3: Manual browser verify (documented)**

With the v2ecoli dashboard running (port 8771) and the static files current (restart the server if it caches templates/JS in memory):

1. Open `http://localhost:8771/studies/<a real study slug>` → Runs tab.
2. Confirm the "Run on remote (smsvpctest)" panel renders with the three inputs + button.
3. Click "▶ Run on remote" WITHOUT being logged in → expect the "Log in with GitHub…" message (the server's 401 surfaced).
4. (Full end-to-end — needs the tunnel up + login) clicking when logged in starts a job and the 6-stage strip (push→build→run→poll→download→land) updates every 2s.

- [ ] **Step 4: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add tests/test_remote_run_panel.py
git commit -m "test(remote-runs): rendered study-detail includes the remote-run panel"
```

---

## Self-Review

**Spec coverage:** launch panel in the Runs tab (Task 1) + submit/poll/render JS mirroring the existing run-trigger pattern with server-401 login handling (Task 2) + a rendered-page integration test and manual-verify steps (Task 3). The panel calls the live mutation endpoints; progress shows the six backend steps.

**Placeholder scan:** none — complete HTML + JS; the one "add minimal spec keys if the renderer needs them" note in Task 3 names exactly how to resolve it (inspect traceback, add only what's referenced).

**Type/contract consistency:** the JS reads exactly the `RemoteRunJob.to_dict` shape from Phase 3b (`job_id`, `status`, `steps[].{name,status,message}`, `run_id`, `error`); endpoints + the 202/401 contract match the Phase 3b handlers; `_submitRemoteRun` is exposed on `window` for the inline `onsubmit`.

## Follow-ons
- sms-api: generalize `_build_store_uri` to also locate parquet stores (observables-endpoint parity).
- Polish: record real `n_steps` on the landed run; surface `simulation_id` + the store link in the runs table; a per-study "Remote Runs" history.
- Full live E2E once the tunnel + login are up (the manual verify step 4).
