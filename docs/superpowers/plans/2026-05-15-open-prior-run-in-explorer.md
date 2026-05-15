# Open a Prior Run in the Composite Explorer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Click a Simulations-tab row's composite cell → opens the Composite Explorer with that run's results + viz pre-loaded. Folds in a fix for the Explorer's broken `_ceTestRun()` so the fresh-Run flow and the prior-run-load flow share one render path.

**Architecture:** Pure frontend. A new helper `_ceLoadRunFromId(run_id)` fetches `/api/composite-run/<id>/status` + `/api/composite-run/<id>`, transforms the trajectory into observable-keyed arrays, and renders into the existing `#ce-test-results` container via a new `_ceRenderRunResults(...)`. Both URL-load (`?run_id=`) and fresh-Run (POST + poll) go through it.

**Tech Stack:** Plain ES5-ish JavaScript in `vivarium_dashboard/static/walkthrough.js`. Python integration smoke against `tests/_fixtures/ws_increase_demo`.

**Spec:** `docs/superpowers/specs/2026-05-15-open-prior-run-in-explorer-design.md`

**Branch:** Work on `open-prior-run-in-explorer` (already created off `origin/main`). Worktree at `/Users/eranagmon/code/vivarium-dashboard-explore-run`. Verify with `git branch --show-current` before each commit. Stage scoped — do not `git add -A`.

## Current file landmarks (verified before plan)

In `vivarium_dashboard/static/walkthrough.js`:
- `function _switchPage(pageId)` at line 377 — the page-init dispatch block. The `composite-explore` branch sits around line 419-421.
- `function _openCompositeExplorer(id)` at line 2437-2447 — the URL-build pattern we mirror.
- `function _initCompositeExplorer()` at line 2449-2470 — currently reads only `?id=`.
- `function _ceFetch()` at line 2876 — populates wiring/Document.
- `function _ceTestRun()` at line 3078-3187 — the OLD synchronous handler we replace.
- `function _renderSimRow(sim)` at line 5713 — the Simulations row template.

## File Structure

| File | Responsibility |
|---|---|
| `vivarium_dashboard/static/walkthrough.js` *(modify)* | New helpers (`_trajectoryToObservables`, `_ceRenderRunResults`, `_ceLoadRunFromId`, `_ceStopRunPoll`, `_openSimulationInExplorer`); new module-scope `_cePollIntervalId`; `_switchPage` cleanup hook; `_initCompositeExplorer` extension; `_renderSimRow` link; `_ceTestRun` rewrite |
| `tests/test_open_run_in_explorer.py` *(create)* | Integration smoke: served `/walkthrough.js` has new symbols; end-to-end POST→poll→fetch produces a complete canonical input |

---

## Task 1: Core helpers + `_switchPage` cleanup hook

**Files:**
- Modify: `vivarium_dashboard/static/walkthrough.js` (append helpers to the end-of-file Simulations block; add hook inside `_switchPage`)

This task introduces the render machinery (`_trajectoryToObservables`, `_ceRenderRunResults`, `_ceLoadRunFromId`, `_ceStopRunPoll`) and a module-scope `_cePollIntervalId`. Nothing calls these yet — Task 2 wires them in. We do them first because they're the most logic-heavy part; pure functions, no DOM dependencies beyond `#ce-test-results` writes.

- [ ] **Step 1: Add the helpers at the end of the file** (just before the closing IIFE `})();`)

Append:

```javascript
  // ===========================================================================
  // Composite Explorer — load a prior run into the Run tab
  // ===========================================================================

  // Module-scope interval id for the running-state poll. Owned by
  // _ceLoadRunFromId; cleared by _ceStopRunPoll (called from _switchPage on
  // navigation away, and on terminal status transitions).
  window._cePollIntervalId = null;

  function _ceStopRunPoll() {
    if (window._cePollIntervalId != null) {
      clearInterval(window._cePollIntervalId);
      window._cePollIntervalId = null;
    }
  }
  window._ceStopRunPoll = _ceStopRunPoll;

  /** Transform a per-step trajectory list into the observable-keyed shape the
   *  Run-tab table renderer wants. Skips rows without step or state. */
  function _trajectoryToObservables(trajectory) {
    var out = {};
    if (!trajectory || !trajectory.length) return out;
    for (var i = 0; i < trajectory.length; i++) {
      var row = trajectory[i];
      if (!row || row.step == null || !row.state) continue;
      var state = row.state;
      for (var k in state) {
        if (!Object.prototype.hasOwnProperty.call(state, k)) continue;
        if (!out[k]) out[k] = [];
        out[k].push(state[k]);
      }
    }
    return out;
  }
  window._trajectoryToObservables = _trajectoryToObservables;

  /** Render the Run-tab results panel from a canonical input.
   *
   *  Single writer of #ce-test-results. The same input shape is produced by
   *  both _ceLoadRunFromId (URL/prior-run flow) and the rewritten _ceTestRun
   *  (fresh in-Explorer Run flow), so the rendered DOM only depends on this
   *  data, not on which flow produced it.
   *
   *  Input fields:
   *    status        — 'running' | 'completed' | 'failed' | 'orphaned' | 'gone'
   *                    (the special value 'gone' is used when the run no
   *                    longer exists in the DB; renders the deleted banner)
   *    results       — {key: [entries, ...]}  (observable-keyed)
   *    viz_html      — {path: {html}}  (may be undefined / empty)
   *    n_steps       — int | null
   *    progress_step — int | null
   *    log_path      — workspace-relative string | undefined
   *    error         — string | undefined  (log excerpt for failed/orphaned)
   */
  function _ceRenderRunResults(input) {
    var el = document.getElementById('ce-test-results');
    if (!el) return;
    var status = (input && input.status) || 'unknown';
    var n = (input && input.n_steps != null) ? input.n_steps : '?';
    var prog = (input && input.progress_step != null) ? input.progress_step : 0;
    var results = (input && input.results) || {};
    var viz = (input && input.viz_html) || {};

    if (status === 'gone') {
      el.innerHTML =
        '<div style="background:#fef3c7; border:1px solid #fde68a; ' +
        'padding:10px 14px; border-radius:4px;">' +
        '<strong>This run no longer exists.</strong> It may have been deleted ' +
        'from the <a href="#simulations">Simulations tab</a>. Click <strong>' +
        'Run</strong> above to start a new one.</div>';
      return;
    }

    var bannerHtml = '';
    if (status === 'running') {
      var pct = (typeof n === 'number' && n > 0)
        ? Math.round((prog / n) * 100) : 0;
      bannerHtml =
        '<div style="margin:0 0 12px;">' +
        '<div style="background:#e5e7eb; border-radius:4px; height:10px; overflow:hidden;">' +
        '<div style="width:' + pct + '%; background:#3b82f6; height:100%;"></div>' +
        '</div>' +
        '<small style="color:#6b7280;">Running detached — step ' + _esc(String(prog)) +
        ' of ' + _esc(String(n)) + ' — safe to leave this tab.</small></div>';
    } else if (status === 'failed' || status === 'orphaned') {
      var logTxt = input && input.log_path
        ? ' See log: <code>' + _esc(input.log_path) + '</code>'
        : '';
      var errBlock = '';
      if (input && input.error) {
        errBlock =
          '<details style="margin-top:6px;"><summary style="cursor:pointer; color:#7f1d1d;">' +
          'Show log excerpt</summary><pre style="background:#fef2f2; border:1px solid #fecaca; ' +
          'padding:10px; font-size:11px; line-height:1.4; overflow:auto; max-height:320px; ' +
          'margin-top:6px; white-space:pre-wrap;">' + _esc(String(input.error).trim()) +
          '</pre></details>';
      }
      bannerHtml =
        '<div style="color:#c00; margin:0 0 12px;"><p style="margin:0;"><strong>Run ' +
        _esc(status) + '.</strong>' + logTxt + '</p>' + errBlock + '</div>';
    } else if (status === 'completed') {
      bannerHtml =
        '<p style="color:#6b7280; font-size:13px; margin:0 0 10px;">Run complete — ' +
        '<strong>' + _esc(String(n)) + '</strong> steps. ' +
        Object.keys(results).length + ' observables.</p>';
    }

    var tableHtml = '';
    var keys = Object.keys(results).sort();
    if (!keys.length) {
      if (status === 'running') {
        tableHtml = '<p class="muted">No trajectory data yet.</p>';
      } else if (status === 'completed') {
        tableHtml = '<p class="muted">No observables in this run.</p>';
      }
    } else {
      tableHtml = '<table style="font-size:0.86em; width:100%;">' +
        '<thead><tr><th style="text-align:left;">Observable</th>' +
        '<th style="text-align:left; width:80px;">Steps</th>' +
        '<th style="text-align:left;">Final value</th></tr></thead><tbody>';
      keys.forEach(function(k) {
        var entries = results[k] || [];
        var last = entries[entries.length - 1];
        var preview;
        if (last == null || typeof last !== 'object') {
          preview = String(last);
        } else if (Array.isArray(last)) {
          preview = 'list[' + last.length + ']';
        } else {
          preview = '{' + Object.keys(last).length + ' keys}';
        }
        tableHtml += '<tr><td><code>' + _esc(k) + '</code></td>' +
          '<td>' + entries.length + '</td>' +
          '<td style="font-family:monospace; font-size:12px; color:#4b5563;">' +
          _esc(preview) + '</td></tr>';
      });
      tableHtml += '</tbody></table>';
    }

    var vizHtml = '';
    var vizKeys = Object.keys(viz);
    if (vizKeys.length) {
      vizHtml = '<div style="margin-top:20px;"><h4>Visualizations</h4>';
      vizKeys.forEach(function(path) {
        var payload = viz[path] || {};
        var html = payload.html || '<p>No HTML</p>';
        vizHtml +=
          '<div style="margin-bottom:12px; border:1px solid #e5e7eb; border-radius:4px;">' +
          '<div style="padding:6px 10px; background:#f3f4f6; font-family:monospace; ' +
          'font-size:12px;">' + _esc(path) + '</div>' +
          '<iframe srcdoc="' + _esc(html).replace(/&quot;/g, '&#34;') +
          '" style="width:100%; height:320px; border:0;" sandbox="allow-scripts"></iframe>' +
          '</div>';
      });
      vizHtml += '</div>';
    }

    el.innerHTML = bannerHtml + tableHtml + vizHtml;
  }
  window._ceRenderRunResults = _ceRenderRunResults;

  /** Load a prior run (or follow a live one) into the Run tab.
   *
   *  Fetches /api/composite-run/<id>/status and /api/composite-run/<id>,
   *  transforms the trajectory, renders. If status is 'running', starts a
   *  1.5s setInterval that re-fetches + re-renders until terminal.
   */
  function _ceLoadRunFromId(run_id) {
    if (!run_id) return;
    _ceStopRunPoll();  // clear any prior interval

    function tick() {
      Promise.all([
        fetch('/api/composite-run/' + encodeURIComponent(run_id) + '/status')
          .then(function(r) {
            if (r.status === 404) return { _gone: true };
            return r.json();
          }),
        fetch('/api/composite-run/' + encodeURIComponent(run_id))
          .then(function(r) { return r.ok ? r.json() : { trajectory: [] }; })
          .catch(function() { return { trajectory: [] }; }),
      ]).then(function(parts) {
        var statusBody = parts[0] || {};
        var trajBody = parts[1] || {};
        if (statusBody._gone || statusBody.error === 'run not found') {
          _ceStopRunPoll();
          _ceRenderRunResults({ status: 'gone' });
          return;
        }
        var results = _trajectoryToObservables(trajBody.trajectory || []);
        _ceRenderRunResults({
          status: statusBody.status,
          results: results,
          viz_html: statusBody.viz_html,
          n_steps: statusBody.n_steps,
          progress_step: statusBody.progress_step,
          log_path: statusBody.log_path,
          error: statusBody.error,
        });
        var terminal = statusBody.status === 'completed'
                    || statusBody.status === 'failed'
                    || statusBody.status === 'orphaned';
        if (terminal) _ceStopRunPoll();
      }).catch(function() { /* transient — next tick retries */ });
    }
    tick();
    window._cePollIntervalId = setInterval(tick, 1500);
  }
  window._ceLoadRunFromId = _ceLoadRunFromId;
```

- [ ] **Step 2: Hook `_ceStopRunPoll()` into `_switchPage`**

At line 377-422-ish, `_switchPage(pageId)` already dispatches per-page inits. The cleanup should fire when the user navigates AWAY from `composite-explore` (so a poll started by the Explorer doesn't keep firing on other pages).

Find this block (around line 419-421):

```javascript
    // Initialize composite explorer when switching to that page.
    if (pageId === 'composite-explore') {
      _initCompositeExplorer();
    }
```

Insert a stop call IMMEDIATELY ABOVE it (so navigating to ANY page — including the explorer itself — clears any prior poll before the new page's init runs):

```javascript
    // Stop any running poll-loop started by the Composite Explorer's Run tab
    // before activating a new page. _ceLoadRunFromId will restart polling if
    // the next page is the explorer with a still-running run.
    if (typeof _ceStopRunPoll === 'function') _ceStopRunPoll();

    // Initialize composite explorer when switching to that page.
    if (pageId === 'composite-explore') {
      _initCompositeExplorer();
    }
```

The `typeof === 'function'` guard tolerates a transient state where `_switchPage` runs before the end-of-file helpers have been evaluated (unlikely with the script tag's position but cheap insurance).

- [ ] **Step 3: Smoke-load the dashboard to confirm no JS syntax error**

Quick check that the file still parses and serves:

```bash
cd /Users/eranagmon/code/vivarium-dashboard-explore-run
python -c "
import sys, json, subprocess, time, shutil, tempfile, socket, http.client
from pathlib import Path
sys.path.insert(0, '.')
from vivarium_dashboard.lib.report import render_dashboard
fixture = Path('tests/_fixtures/ws_increase_demo')
tmp = Path(tempfile.mkdtemp()) / 'ws'
shutil.copytree(fixture, tmp)
render_dashboard(tmp, write_all=True)
s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
proc = subprocess.Popen([sys.executable, '-m', 'vivarium_dashboard.server',
                         '--workspace', str(tmp), '--port', str(port)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(3)
conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
conn.request('GET', '/walkthrough.js')
js = conn.getresponse().read().decode()
proc.terminate(); proc.wait(timeout=3)
for needle in ['_ceLoadRunFromId', '_ceRenderRunResults',
               '_trajectoryToObservables', '_ceStopRunPoll']:
    assert needle in js, f'missing {needle}'
print('symbols OK')
"
```
Expected: prints `symbols OK`.

- [ ] **Step 4: Commit**

```bash
git add vivarium_dashboard/static/walkthrough.js
git status --short  # only walkthrough.js
git commit -m "feat(explorer): core helpers — load + render a prior run + poll cleanup hook"
```

---

## Task 2: `_initCompositeExplorer` extension — read `?run_id=`

**Files:**
- Modify: `vivarium_dashboard/static/walkthrough.js:2449-2470` (`_initCompositeExplorer`)

After Task 1, the Run-tab can be populated from a run-id, but nothing reads `?run_id=` from the URL yet. Extending the init function makes the deep link work.

- [ ] **Step 1: Replace the body of `_initCompositeExplorer`**

The current body (around line 2449-2470) reads `?id=` and calls `_ceFetch()`. The new body adds: also read `?run_id=`, store it on `_ceCurrent`, and after `_ceFetch()` resolves call `_ceLoadRunFromId(run_id)`.

Find the function and replace it with:

```javascript
  function _initCompositeExplorer() {
    // Called when the explorer page is activated. Parses ?id=<spec_id> from
    // the URL, fetches the resolved composite, populates the page. Also
    // parses ?run_id=<run_id> — when present, loads that run's results and
    // viz into the Run tab (a Simulations-row deep link or a refresh of a
    // URL captured after kicking off a run).
    var params = new URLSearchParams(window.location.search);
    var id = params.get('id');
    var run_id = params.get('run_id');
    if (!id) {
      document.getElementById('ce-loading').textContent =
        'No composite id specified. Open via the Use button on a composite card.';
      return;
    }
    window._ceCurrent = {id: id, overrides: {}, run_id: run_id || null};
    window._ceLastRunId = run_id || null;
    // Hide the post-run bar when loading a fresh composite (it's set by the
    // explore:run-complete postMessage path).
    var bar = document.getElementById('ce-post-run-bar');
    if (bar) bar.style.display = 'none';
    // Eagerly populate the composite card cache so "Create simulation" can
    // open the Configure modal even when the user lands here directly
    // (deep-link / Use button) without ever visiting Simulation Setup.
    if (!window._compositesById || !window._compositesById[id]) {
      _loadComposites();
    }
    _ceFetch();
    if (run_id) {
      // Run tab loads in parallel with _ceFetch's wiring fetch; no need to
      // await, the two writes target different DOM containers.
      _ceLoadRunFromId(run_id);
    }
  }
  window._initCompositeExplorer = _initCompositeExplorer;
```

The two changes from the old body:
- `var run_id = params.get('run_id');` (new line)
- `window._ceCurrent = {id: id, overrides: {}, run_id: run_id || null};` (added `run_id` field)
- `window._ceLastRunId = run_id || null;` (was `null`, now seeds with the URL run_id so the post-run bar's "view results" link points correctly)
- The trailing `if (run_id) { _ceLoadRunFromId(run_id); }` block (new)

- [ ] **Step 2: Smoke-check the served JS**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-explore-run
grep -A2 "var run_id = params.get" vivarium_dashboard/static/walkthrough.js | head -10
```
Expected: shows the `run_id` parse + the `_ceLoadRunFromId(run_id)` call inside `_initCompositeExplorer`.

- [ ] **Step 3: Commit**

```bash
git add vivarium_dashboard/static/walkthrough.js
git commit -m "feat(explorer): read ?run_id= URL param + auto-load prior run into Run tab"
```

---

## Task 3: Simulations row composite link + `_openSimulationInExplorer`

**Files:**
- Modify: `vivarium_dashboard/static/walkthrough.js` (`_renderSimRow` around line 5713; add new helper near it)

Make the composite cell a clickable link that navigates to the explorer with both `id` and `run_id` in the URL.

- [ ] **Step 1: Add the `_openSimulationInExplorer` helper**

Right BEFORE `_renderSimRow` (around line 5712), insert:

```javascript
  /** Open the Composite Explorer for a specific past simulation.
   *
   *  Mirrors _openCompositeExplorer (line 2437) but also seeds ?run_id=, so
   *  _initCompositeExplorer picks it up and renders the run's results +
   *  viz_html in the Run tab.
   */
  function _openSimulationInExplorer(run_id, spec_id) {
    var url = new URL(window.location.href);
    url.searchParams.set('id', spec_id);
    url.searchParams.set('run_id', run_id);
    url.hash = '#composite-explore';
    window.history.pushState({}, '', url.toString());
    _switchPage('composite-explore');
  }
  window._openSimulationInExplorer = _openSimulationInExplorer;
```

- [ ] **Step 2: Modify the composite cell in `_renderSimRow`**

The current composite-cell `<td>` (around line 5733) is:

```javascript
      '<td style="padding:6px 8px;"><code>' + composite + '</code></td>' +
```

Replace it with an anchor wrapping the `<code>`:

```javascript
      '<td style="padding:6px 8px;">' +
        '<a href="?id=' + encodeURIComponent(sim.spec_id) +
        '&run_id=' + encodeURIComponent(sim.run_id) + '#composite-explore" ' +
        'class="sim-composite-link" ' +
        'style="text-decoration:none; color:inherit;" ' +
        'onclick="event.preventDefault(); _openSimulationInExplorer(\'' +
          _escSim(sim.run_id) + '\', \'' + _escSim(sim.spec_id) + '\');" ' +
        'onmouseover="this.style.textDecoration=\'underline\';" ' +
        'onmouseout="this.style.textDecoration=\'none\';">' +
        '<code>' + composite + '</code></a>' +
      '</td>' +
```

The `href` provides a real-URL affordance (right-click → open in new tab, share-the-link). `event.preventDefault()` keeps the in-page click on the SPA path (no full-page navigation). Hover underlines so the cell signals interactivity. The visible string inside `<code>` is unchanged.

- [ ] **Step 3: Smoke-check the served JS**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-explore-run
grep -n "_openSimulationInExplorer\|sim-composite-link" vivarium_dashboard/static/walkthrough.js | head
```
Expected: shows the new helper definition + its onclick usage in `_renderSimRow`.

- [ ] **Step 4: Commit**

```bash
git add vivarium_dashboard/static/walkthrough.js
git commit -m "feat(simulations-ui): composite cell links to Explorer with run_id"
```

---

## Task 4: Rewrite `_ceTestRun` to the detached pipeline

**Files:**
- Modify: `vivarium_dashboard/static/walkthrough.js:3078-3187` (`_ceTestRun`)

Today's `_ceTestRun` expects the OLD synchronous response shape `{results, viz_html, steps}`. The backend now returns `202 {run_id, status:"running"}`. This task replaces the body so it kicks off + hands control to `_ceLoadRunFromId` — same code path as the URL load.

- [ ] **Step 1: Replace the body of `_ceTestRun` (lines 3078-3187 inclusive of the closing brace)**

Find the function (starts at line 3078 with `function _ceTestRun() {`) and replace the entire function body with:

```javascript
  function _ceTestRun() {
    var steps = parseInt(document.getElementById('ce-steps').value, 10) || 5;
    var overrides = _ceCollectOverrides();
    var resultsEl = document.getElementById('ce-test-results');
    resultsEl.innerHTML = '<p class="empty-state">Starting run…</p>';
    fetch('/api/composite-test-run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        id: window._ceCurrent.id,
        overrides: overrides,
        steps: steps,
        emit_paths: window._explorerEmitPaths || [],
      }),
    })
      .then(function(r) { return r.json().then(function(j) { return [r.status, j]; }); })
      .then(function(parts) {
        var code = parts[0], body = parts[1];
        if (code !== 202) {
          var errMsg = body && body.error
            ? body.error
            : ('HTTP ' + code);
          resultsEl.innerHTML =
            '<div style="color:#c00;"><strong>Could not start run:</strong> ' +
            _esc(errMsg) + '</div>';
          return;
        }
        // Successful 202 — server accepted the run, returned a run_id.
        var run_id = body.run_id;
        window._ceLastRunId = run_id;
        // Bookmark the new run in the URL so refresh / share works.
        try {
          var url = new URL(window.location.href);
          url.searchParams.set('run_id', run_id);
          window.history.replaceState({}, '', url.toString());
          if (window._ceCurrent) window._ceCurrent.run_id = run_id;
        } catch (e) { /* non-critical */ }
        // Hand off to the shared loader — same render path as URL deep-link.
        _ceLoadRunFromId(run_id);
      })
      .catch(function(err) {
        resultsEl.innerHTML =
          '<div style="color:#c00;"><strong>Network error:</strong> ' +
          _esc(String(err)) + '</div>';
      });
  }
  window._ceTestRun = _ceTestRun;
```

This is a complete replacement of the function body. The function name and `window._ceTestRun = _ceTestRun` assignment are preserved. The behavior change:
- POST → expect 202 (not 200); body has `run_id`, not `simulation_id`/`results`.
- On success, hand off to `_ceLoadRunFromId(run_id)` which does the polling + rendering.
- Bookmark `run_id` in the URL via `history.replaceState` (not `pushState` — replacing the current history entry, not adding to it) so refresh restores the same run.

- [ ] **Step 2: Verify the diff is scoped to that one function**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-explore-run
git diff vivarium_dashboard/static/walkthrough.js | head -60
```
The diff should show the old `_ceTestRun` body removed and the new one in its place; no other function should be affected.

- [ ] **Step 3: Smoke-check the served JS contains the new shape**

```bash
grep -n "_ceTestRun\|window._ceLastRunId = run_id" vivarium_dashboard/static/walkthrough.js | head
```
Expected: shows `window._ceLastRunId = run_id` (the new assignment from a 202 response). The OLD code wrote `window._ceLastRunId = ev.data.simulation_id` only in the postMessage handler at line 52; that line stays for backward compatibility with the loom-explore iframe's `explore:run-complete` message, no change needed.

- [ ] **Step 4: Commit**

```bash
git add vivarium_dashboard/static/walkthrough.js
git commit -m "fix(explorer): rewrite _ceTestRun to detached-runs pipeline (POST 202 → _ceLoadRunFromId)"
```

---

## Task 5: Integration smoke test

**Files:**
- Create: `tests/test_open_run_in_explorer.py`

The dashboard repo has no JS test infra; this is a Python smoke that (a) confirms the served JS contains the new symbols, (b) exercises the underlying endpoints end-to-end.

- [ ] **Step 1: Create the test file**

Create `tests/test_open_run_in_explorer.py`:

```python
"""Integration smoke for the 'open prior run in Composite Explorer' feature.

The actual rendering is browser-side JS; this test verifies (a) the served
walkthrough.js exposes the new symbols, (b) the underlying backend
endpoints produce a complete canonical input the JS would render from.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WORKSPACE = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server(tmp_path):
    if not FIXTURE_WORKSPACE.is_dir():
        pytest.skip(f"Fixture workspace not present at {FIXTURE_WORKSPACE}")
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WORKSPACE, ws)
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = (str(_REPO_ROOT) + os.pathsep + str(ws)
                         + os.pathsep + env.get("PYTHONPATH", ""))
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_dashboard.server",
         "--workspace", str(ws), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    info_path = ws / ".pbg" / "server" / "server-info"
    for _ in range(40):
        if info_path.exists():
            break
        time.sleep(0.1)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=2)
        pytest.fail(f"server did not start:\n{out.decode()}\n{err.decode()}")
    yield {"url": f"http://127.0.0.1:{port}", "ws": ws}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _post(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, json.loads(r.read().decode())


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def _get_text(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode()


def _poll_until_terminal(base, run_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, body = _get(f"{base}/api/composite-run/{run_id}/status")
        if body.get("status") in ("completed", "failed", "orphaned"):
            return body
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_walkthroughjs_exports_required_symbols(server):
    base = server["url"]
    status, js = _get_text(f"{base}/walkthrough.js")
    assert status == 200
    for needle in (
        "_ceLoadRunFromId",
        "_ceRenderRunResults",
        "_trajectoryToObservables",
        "_ceStopRunPoll",
        "_openSimulationInExplorer",
    ):
        assert needle in js, f"missing {needle}"
    # _ceTestRun must read run_id from a 202 response, not the old fields.
    assert "window._ceLastRunId = run_id" in js, \
        "rewritten _ceTestRun should assign run_id from the 202 body"


def test_simulations_row_template_links_to_explorer_with_run_id(server):
    base = server["url"]
    _, js = _get_text(f"{base}/walkthrough.js")
    # The composite cell in _renderSimRow wraps <code> in an anchor whose
    # onclick calls _openSimulationInExplorer.
    assert "_openSimulationInExplorer(" in js
    assert "sim-composite-link" in js


def test_explorer_loads_with_run_id_then_endpoints_serve(server):
    """End-to-end: POST a run → poll terminal → both endpoints return the
    canonical input shape the JS would render from."""
    base = server["url"]
    spec_id = "pbg_ws_increase_demo.composites.increase-demo"
    _, body = _post(f"{base}/api/composite-test-run",
                    {"id": spec_id, "steps": 3})
    run_id = body["run_id"]
    final = _poll_until_terminal(base, run_id)

    # Status endpoint: terminal state, full shape.
    assert final["status"] == "completed"
    assert final["n_steps"] == 3
    assert "viz_html" in final  # may be empty dict, must be present

    # Trajectory endpoint: rows with the (step, time, state) shape.
    _, traj = _get(f"{base}/api/composite-run/{run_id}")
    assert "trajectory" in traj
    assert isinstance(traj["trajectory"], list)
    if traj["trajectory"]:
        first = traj["trajectory"][0]
        assert "step" in first
        assert "state" in first
```

- [ ] **Step 2: Run the tests**

```bash
cd /Users/eranagmon/code/vivarium-dashboard-explore-run
python -m pytest tests/test_open_run_in_explorer.py -v
```
Expected: PASS — all 3 tests.

- [ ] **Step 3: Regression — run the surrounding tests too**

```bash
python -m pytest tests/test_open_run_in_explorer.py tests/test_simulations_api.py \
                  tests/test_simulations_index.py tests/test_composite_explorer_api.py -q
```
Expected: all green.

- [ ] **Step 4: Manual browser verification (note these in PR test plan, don't automate)**

With the dashboard serving the updated walkthrough.js (the running preview server picks it up after `cp -R` or a fresh start with `PYTHONPATH` pointing at this worktree):

1. Click **Run** inside the Composite Explorer for any composite — confirm the new flow: results panel shows "Starting run…", progress bar appears, terminal state renders the table + viz iframes. URL gains `?run_id=<id>` after the 202. Refresh — same view restores.
2. Open the **Simulations** tab, click a row's composite cell — Explorer opens with the prior run's results pre-loaded. URL is `?id=...&run_id=...#composite-explore`.
3. Click a `running` run from Simulations — progress bar advances, terminal state renders.
4. Click a `failed`/`orphaned` run — error banner + log_path.
5. Delete a run from Simulations, then revisit its Explorer URL — "this run no longer exists" banner appears.
6. Navigate away from the Explorer mid-poll — DevTools Network tab confirms no further `/api/composite-run/.../status` requests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_open_run_in_explorer.py
git commit -m "test(explorer): integration smoke for prior-run load + new symbols in walkthrough.js"
```

---

## Self-Review

**1. Spec coverage:**
- `_trajectoryToObservables` (spec §Components #1) → Task 1 ✓
- `_ceRenderRunResults` single writer of `#ce-test-results` (spec §Components #2, invariant) → Task 1 ✓
- `_ceLoadRunFromId` + 1.5s poll loop, terminal-stop (spec §Components #3, §Data Flow §`_ceLoadRunFromId`) → Task 1 ✓
- `_ceStopRunPoll` + `_switchPage` cleanup hook (spec §Components #4, §Error Handling "Page navigation away") → Task 1 ✓
- `_ceTestRun` rewrite — POST → `202 {run_id}` → `_ceLoadRunFromId` (spec §Components #5, §Data Flow §Fresh run) → Task 4 ✓
- `_initCompositeExplorer` extension for `?run_id=` (spec §Components #6, §Data Flow §Loading a prior run via URL) → Task 2 ✓
- `_renderSimRow` link + `_openSimulationInExplorer` helper (spec §Components #7, §Data Flow §Simulations row click) → Task 3 ✓
- Test file with the three integration smokes (spec §Testing) → Task 5 ✓
- Error states (gone / running / failed/orphaned / no trajectory yet / no observables) (spec §Error Handling) → covered in Task 1's `_ceRenderRunResults` branches ✓
- "Bookmark run_id in URL on fresh run" — covered in Task 4 via `history.replaceState`. (Spec called this out-of-scope, but the cost is one line; it directly enables manual check #1 "URL gains `?run_id=` after the 202". Folded in.)

**2. Placeholder scan:** No TBD/TODO/"add appropriate error handling"/"similar to". Each step shows the exact code. Smoke-check commands include their full Python snippets.

**3. Type consistency:**
- `run_id` (string) used consistently across all tasks.
- `_ceRenderRunResults` input shape (`status`, `results`, `viz_html`, `n_steps`, `progress_step`, `log_path`, `error`) is the same in Task 1 (producer + consumer), Task 4 (producer via `_ceLoadRunFromId`), Task 5 (the test asserts the same field set on the backend response that feeds it).
- `_openSimulationInExplorer(run_id, spec_id)` two-arg signature used consistently in Task 3's helper definition and its `_renderSimRow` onclick.
- `window._cePollIntervalId` (module-scope) named consistently in `_ceStopRunPoll` and `_ceLoadRunFromId`.
- `window._ceCurrent.run_id` field set in `_initCompositeExplorer` (Task 2) and `_ceTestRun` (Task 4) — same name in both.
- Status enum (`running`/`completed`/`failed`/`orphaned`/`gone`) used consistently — backend returns the first four; `'gone'` is internal to `_ceRenderRunResults` to render the 404 banner.

No gaps found.
