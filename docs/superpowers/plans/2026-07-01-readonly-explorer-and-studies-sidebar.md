# Read-only Composite Explorer tabs + Studies sidebar scoping — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the read-only (snapshot) Composite Explorer show the full Setup & Run / Results / Visualizations / Wiring / Document tabs (read-only), and scope the Studies sidebar to a single chosen investigation with a picker (no "Ungrouped" bucket).

**Architecture:** Two independent workstreams in two repos. WS-A is bigraph-loom (React/TS): un-gate the tab set in `?static=1`, teach the static loader to read the config-form fields the published JSON already carries, and add a `readOnly` posture to the run/results panels. WS-B is vivarium-dashboard (vanilla JS): rewrite the STUDIES rail render to show only the current investigation or a chooser, add a picker, and persist the selection.

**Tech Stack:** bigraph-loom — React 18 + TypeScript + Vite + Vitest (`npm run build` = `tsc -b && vite build`; `npm test` = `vitest run`); committed `_dist`. vivarium-dashboard — stdlib HTTP server + vanilla JS in `static/walkthrough.js` (no JS test runner).

## Global Constraints

- WS-A must not change live (non-static) behavior: same five tabs, same default tab (`setup`), same `composite:load` postMessage seeding.
- WS-A requires **no** change to `publish.py` or the `api/composite-state/<id>.json` contract — the fields it consumes (`parameters`, `default_n_steps`, `name`, `id`) already exist in the published resolve dict.
- WS-B is **display-only**: no `.yaml` edits, no backend payload changes, no new endpoints.
- Read-only Run/Preview affordances must be visibly **disabled** (not hidden) with a one-line reason.
- The STUDIES rail in read-only/snapshot mode and live mode share the same render path — both must work.

**Repos / branches:**
- WS-A: `/Users/eranagmon/code/bigraph-loom`. Create branch `feat/static-explorer-tabs` off `origin/main`.
- WS-B: `/Users/eranagmon/code/vivarium-dashboard`, branch `feat/readonly-explorer-studies-sidebar` (already exists, spec committed).

---

## File Structure

**WS-A (bigraph-loom):**
- `src/panels/SetupRunPanel.tsx` — add `readOnly` prop; disable Run + Preview; read-only banner.
- `src/panels/ResultsPanel.tsx` — add `readOnly` prop; read-only empty message.
- `src/panels/VisualizationsPanel.tsx` — add `readOnly` prop; read-only empty message.
- `src/App.tsx` — un-gate tabs + tab strip in static; default `setup`; static loader parses config fields; pass `readOnly={STATIC}` to the three panels.
- `src/__tests__/App.test.tsx` — update the existing static-mode test (now expects `setup` default + visible tabs) and add a static-loader test.
- `src/__tests__/SetupRunPanel.test.tsx`, `ResultsPanel.test.tsx` — add read-only assertions.
- `bigraph_loom/_dist/**` — rebuilt by `npm run build` (committed).

**WS-B (vivarium-dashboard):**
- `static/walkthrough.js` — rewrite the tail of `_renderRailInvestigationGroups()` (lines ~11826–11890); add `_railInvestigationPicker()`, `window._railSelectInvestigation()`, `_railIsetKey()`; set `_currentIsetSlug` on initial iset load (~line 3400).

---

# WS-A — Read-only Composite Explorer tabs (bigraph-loom)

Work in `/Users/eranagmon/code/bigraph-loom`. Setup once:

```bash
cd /Users/eranagmon/code/bigraph-loom
git fetch origin && git checkout -b feat/static-explorer-tabs origin/main
npm ci   # if node_modules missing; otherwise skip
```

Reference types (already in `src/api.ts`, do not redefine):
```ts
export interface ParameterDecl {
  type: 'string' | 'int' | 'float' | 'bool' | 'list[string]' | string;
  default?: unknown; description?: string; choices?: string[];
}
```

---

### Task A1: SetupRunPanel read-only posture

**Files:**
- Modify: `src/panels/SetupRunPanel.tsx` (props interface ~61-85; render ~294-382, 442-465)
- Test: `src/__tests__/SetupRunPanel.test.tsx`

**Interfaces:**
- Produces: `SetupRunPanelProps.readOnly?: boolean`. When true, the panel renders the parameter form but disables the **Run** and **Preview wiring** buttons and shows a one-line note; it makes **no** network calls.

- [ ] **Step 1: Write the failing test**

Add to `src/__tests__/SetupRunPanel.test.tsx` (inside the top-level `describe('SetupRunPanel', ...)`):

```tsx
  it('readOnly disables Run and Preview and makes no fetch calls', () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy as any);
    render(
      <SetupRunPanel
        {...BASE_PROPS}
        compositeId="some.composite.id"
        parameters={PARAMS}
        overrides={{}}
        readOnly
      />
    );
    // Form still renders (parameter label present)…
    expect(screen.getByText((t) => t.includes('biomodel_ids'))).toBeTruthy();
    // …but Run and Preview are disabled.
    expect((screen.getByRole('button', { name: /^Run$/i }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: /Preview wiring/i }) as HTMLButtonElement).disabled).toBe(true);
    // A read-only note is shown.
    expect(screen.getByText(/read-only|live dashboard/i)).toBeTruthy();
    // No network calls happened on render.
    expect(fetchSpy).not.toHaveBeenCalled();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- SetupRunPanel`
Expected: FAIL — the new test errors (`readOnly` not accepted / Run not disabled / note missing).

- [ ] **Step 3: Add the `readOnly` prop to the interface**

In `src/panels/SetupRunPanel.tsx`, add to `SetupRunPanelProps` (after `onRunState?`, keeping the closing brace):

```ts
  /** Read-only posture (static/snapshot mode): render the parameter form but
   *  disable Run + Preview wiring, since no live dashboard backend exists. */
  readOnly?: boolean;
```

- [ ] **Step 4: Gate `canRun` and the Preview button on `readOnly`**

Change the `canRun` line (~129) from:
```ts
  const canRun = !!props.compositeId && !inInvestigation;
```
to:
```ts
  const canRun = !!props.compositeId && !inInvestigation && !props.readOnly;
```

Change the Preview button's `disabled` (~368) from:
```tsx
              disabled={previewBusy || !props.compositeId}
```
to:
```tsx
              disabled={previewBusy || !props.compositeId || !!props.readOnly}
```

- [ ] **Step 5: Add the read-only note**

Immediately after the opening `<div className="sr-panel">` of the main (non-investigation) return (~295), insert:

```tsx
      {props.readOnly && (
        <p style={{
          margin: '0 0 12px', padding: '8px 10px', fontSize: 13,
          background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 6,
          color: '#475569',
        }}>
          Read-only preview — running requires a live dashboard.
        </p>
      )}
```

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `npm test -- SetupRunPanel`
Expected: PASS (new test + all existing SetupRunPanel tests).

- [ ] **Step 7: Commit**

```bash
git add src/panels/SetupRunPanel.tsx src/__tests__/SetupRunPanel.test.tsx
git commit -m "loom: SetupRunPanel readOnly posture (disable Run + Preview)"
```

---

### Task A2: Results & Visualizations read-only empty states

**Files:**
- Modify: `src/panels/ResultsPanel.tsx` (props ~11-16; empty branch ~121-130)
- Modify: `src/panels/VisualizationsPanel.tsx` (props ~7-10; empty branch ~19-27)
- Test: `src/__tests__/ResultsPanel.test.tsx`

**Interfaces:**
- Produces: `ResultsPanelProps.readOnly?: boolean` and `VisualizationsPanelProps.readOnly?: boolean`. When true and there is no run data, the empty message states results are live-only.

- [ ] **Step 1: Write the failing test**

Add to `src/__tests__/ResultsPanel.test.tsx` (mirror the file's existing import of `ResultsPanel`; if it lacks imports, use the header `// @vitest-environment jsdom` and `import { render, screen, cleanup } from '@testing-library/react';`, `import { describe, it, expect, afterEach } from 'vitest';`, `import { ResultsPanel } from '../panels/ResultsPanel';`):

```tsx
  it('readOnly + no trajectory shows the live-only message', () => {
    render(<ResultsPanel trajectory={null} hasRun={false} readOnly />);
    expect(screen.getByText(/read-only mirror|live dashboard/i)).toBeTruthy();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- ResultsPanel`
Expected: FAIL — `readOnly` not accepted / message text absent.

- [ ] **Step 3: Add `readOnly` to ResultsPanel**

In `src/panels/ResultsPanel.tsx`, add to `ResultsPanelProps`:
```ts
  readOnly?: boolean;
```
Update the destructure (~104):
```ts
export function ResultsPanel({ trajectory, hasRun, runId, downloadable, readOnly }: ResultsPanelProps) {
```
Replace the empty-branch `<p>` (~126-128):
```tsx
        <p style={{ color: '#6b7280' }}>
          {hasRun ? 'Loading trajectory…' : 'No run yet. Go to the Run tab to start one.'}
        </p>
```
with:
```tsx
        <p style={{ color: '#6b7280' }}>
          {readOnly
            ? 'The read-only mirror does not include run data — run this composite in a live dashboard to see results.'
            : hasRun ? 'Loading trajectory…' : 'No run yet. Go to the Run tab to start one.'}
        </p>
```

- [ ] **Step 4: Add `readOnly` to VisualizationsPanel**

In `src/panels/VisualizationsPanel.tsx`, add to `VisualizationsPanelProps`:
```ts
  readOnly?: boolean;
```
Update the signature (~16):
```ts
export function VisualizationsPanel({ vizHtml, hasRun, readOnly }: VisualizationsPanelProps) {
```
Replace the empty-branch `<p>` (~23-25):
```tsx
        <p style={{ color: '#6b7280' }}>
          {hasRun ? 'Loading visualizations…' : 'No run yet. Go to the Run tab to start one.'}
        </p>
```
with:
```tsx
        <p style={{ color: '#6b7280' }}>
          {readOnly
            ? 'The read-only mirror does not include run data — run this composite in a live dashboard to see visualizations.'
            : hasRun ? 'Loading visualizations…' : 'No run yet. Go to the Run tab to start one.'}
        </p>
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `npm test -- ResultsPanel`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/panels/ResultsPanel.tsx src/panels/VisualizationsPanel.tsx src/__tests__/ResultsPanel.test.tsx
git commit -m "loom: Results/Visualizations read-only empty states"
```

---

### Task A3: App static mode — full tabs, default setup, config-field loader, readOnly wiring

**Files:**
- Modify: `src/App.tsx` (default tab ~82-86; static loader ~165-191; tabs ~556-558; nav display ~589-591; panel props ~725-753)
- Test: `src/__tests__/App.test.tsx` (rewrite the existing static-mode test ~25-59; add a loader test)

**Interfaces:**
- Consumes: `SetupRunPanelProps.readOnly` (A1), `ResultsPanelProps.readOnly` / `VisualizationsPanelProps.readOnly` (A2).

- [ ] **Step 1: Update the existing static-mode test + add a loader test**

In `src/__tests__/App.test.tsx`, **replace** the test body of `it('defaults to wiring canvas (not setup panel) when ?static=1', ...)` (the whole `it(...)`, ~34-58) with the two tests below (rename the first):

```tsx
  it('static mode shows all tabs and defaults to Setup & Run', () => {
    window.history.pushState({}, '', '?static=1');
    render(<App />);
    postCompositeLoad({ id: 'test.composites.demo', name: 'demo' });

    // The tab strip is visible in static mode and includes every tab.
    expect(screen.getByRole('button', { name: /Setup & Run/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Results$/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Visualizations$/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Wiring$/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Document$/i })).toBeTruthy();
    // Default tab is Setup & Run → its read-only note renders.
    expect(screen.getByText(/read-only preview|live dashboard/i)).toBeTruthy();
  });

  it('static loader seeds parameters + steps from a resolve-dict stateUrl', async () => {
    const resolveDict = {
      id: 'test.composites.demo', name: 'demo',
      state: { top: {} },
      parameters: { seed: { type: 'int', default: 7, description: 'RNG seed' } },
      default_n_steps: 42,
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => resolveDict,
    }) as any);
    window.history.pushState({}, '', '?static=1&stateUrl=/x.json');
    render(<App />);
    // The Setup & Run form should show the published parameter + its default.
    expect(await screen.findByText((t) => t.includes('seed'))).toBeTruthy();
    const input = await screen.findByLabelText(/seed/i) as HTMLInputElement;
    expect(input.value).toBe('7');
  });
```

Add `vi` to the vitest import at the top of the file if not present:
```tsx
import { describe, it, expect, beforeAll, afterEach, vi } from 'vitest';
```
And add `afterEach(() => { vi.unstubAllGlobals(); });` alongside the existing `afterEach` cleanup (or extend it).

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test -- App`
Expected: FAIL — static mode currently defaults to `wiring`, hides the tab strip, and the loader drops parameters.

- [ ] **Step 3: Default tab → `setup` in all modes**

Replace the `useState<TabId>` initializer (~82-86):
```tsx
  const [tab, setTab] = useState<TabId>(
    () => new URLSearchParams(window.location.search).get('static') === '1'
      ? 'wiring'
      : 'setup',
  );
```
with:
```tsx
  const [tab, setTab] = useState<TabId>('setup');
```

- [ ] **Step 4: Un-gate the tab set and show the tab strip in static mode**

Replace (~555-558):
```tsx
  // Static / view-only mode exposes only the Wiring tab (the others need /api/*).
  const tabs: TabId[] = STATIC
    ? ['wiring']
    : ['setup', 'results', 'visualizations', 'wiring', 'document'];
```
with:
```tsx
  // All tabs are available in static mode too. Setup & Run renders read-only
  // (form visible, Run/Preview disabled); Results/Visualizations show a
  // read-only empty state (no run data in the snapshot).
  const tabs: TabId[] = ['setup', 'results', 'visualizations', 'wiring', 'document'];
```

Replace the `<nav>` display style (~589-591):
```tsx
        <nav style={{
          // In static mode only the View tab exists — drop the tab strip entirely.
          display: STATIC ? 'none' : 'flex', gap: 24, alignItems: 'center',
```
with:
```tsx
        <nav style={{
          display: 'flex', gap: 24, alignItems: 'center',
```

- [ ] **Step 5: Teach the static loader to seed the config-form fields**

Replace the `.then((data) => { ... })` body of the static/popout loader (~178-188):
```tsx
      .then((data) => {
        if (cancelled) return;
        // Accept either an /api/composite-state response ({state: ...}) or a
        // bare state object (a committed snapshot may be either shape).
        const st = (data && typeof data === 'object' && 'state' in data) ? data.state : data;
        if (st && !state) {
          setState(st);
          setEmitSet(new Set(topLevelStorePaths(st)));
          setCollapsed(defaultCollapsedIds(st));
        }
      })
```
with:
```tsx
      .then((data) => {
        if (cancelled) return;
        // Accept either an /api/composite-state response ({state: ...}) or a
        // bare state object (a committed snapshot may be either shape).
        const st = (data && typeof data === 'object' && 'state' in data) ? data.state : data;
        if (st && !state) {
          setState(st);
          setEmitSet(new Set(topLevelStorePaths(st)));
          setCollapsed(defaultCollapsedIds(st));
          // The published composite-state JSON is the full resolve dict — it also
          // carries the configure-form inputs. Seed them so Setup & Run renders
          // (read-only) in static mode. Absent on bare-state snapshots — tolerate.
          if (data && typeof data === 'object' && data !== st) {
            if (data.parameters) setParameters(data.parameters);
            if (data.overrides) setOverrides(data.overrides);
            if (data.default_n_steps != null) setDefaultSteps(data.default_n_steps);
            if (data.name) setName(data.name);
            if (data.library) setLibrary(data.library);
            if (data.id) setCompositeId(data.id);
          }
        }
      })
```

- [ ] **Step 6: Pass `readOnly={STATIC}` to the three panels**

In the panel render block (~725-753), add `readOnly={STATIC}` to each:
```tsx
          {tab === 'setup' && (
            <SetupRunPanel
              compositeId={compositeId}
              parameters={parameters}
              overrides={overrides}
              emitSet={emitSet}
              runContext={runContext}
              defaultSteps={defaultSteps}
              onApplied={handleApplied}
              onTrajectory={setTrajectory}
              onVizHtml={setVizHtml}
              onCompleted={() => setTab('results')}
              onRunState={(s) => { setActiveRunId(s.runId); setDownloadable(s.downloadable); }}
              readOnly={STATIC}
            />
          )}
          {tab === 'results' && (
            <ResultsPanel
              trajectory={trajectory}
              hasRun={trajectory !== null || vizHtml !== null}
              runId={activeRunId}
              downloadable={downloadable}
              readOnly={STATIC}
            />
          )}
          {tab === 'visualizations' && (
            <VisualizationsPanel
              vizHtml={vizHtml}
              hasRun={trajectory !== null || vizHtml !== null}
              readOnly={STATIC}
            />
          )}
```

- [ ] **Step 7: Run the tests and make sure they pass**

Run: `npm test -- App`
Expected: PASS (both new App tests).

- [ ] **Step 8: Commit**

```bash
git add src/App.tsx src/__tests__/App.test.tsx
git commit -m "loom: show all Explorer tabs in static mode (read-only), default Setup & Run"
```

---

### Task A4: Full suite, typecheck, rebuild committed `_dist`

**Files:**
- Modify: `bigraph_loom/_dist/**` (build output; committed)

- [ ] **Step 1: Run the full test suite**

Run: `npm test`
Expected: PASS — all suites (App, SetupRunPanel, ResultsPanel, sidebar, etc.).

- [ ] **Step 2: Typecheck + build (rebuilds `_dist`)**

Run: `npm run build`
Expected: `tsc -b` reports no errors; `vite build` writes to `bigraph_loom/_dist/`.

- [ ] **Step 3: Sanity-check the built bundle carries the new behavior**

Run: `grep -rl "Read-only preview" bigraph_loom/_dist/assets/*.js`
Expected: at least one match (the new note string is in the bundle).

- [ ] **Step 4: Commit the rebuilt bundle**

```bash
git add bigraph_loom/_dist
git commit -m "loom: rebuild _dist for static Explorer tabs"
```

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin feat/static-explorer-tabs
gh pr create --repo vivarium-collective/bigraph-loom --base main \
  --title "Static Composite Explorer: full read-only tabs (Setup & Run / Results / Visualizations)" \
  --body "Static mode (?static=1) now shows all Explorer tabs read-only instead of wiring only. Setup & Run renders the config form (Run/Preview disabled); the static loader reads parameters/default_n_steps from the published composite-state JSON; Results/Visualizations show a read-only empty state. No publish-side change — the snapshot already ships these fields.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

**Delivery note (post-merge, not a task step):** the published v2ecoli read-only dashboard picks this up automatically via the `bigraph-loom@main` force-reinstall in `publish-dashboard.yml`. Live dashboards need a bigraph-loom pin bump.

---

# WS-B — Studies sidebar scoping (vivarium-dashboard)

Work in `/Users/eranagmon/code/vivarium-dashboard` on the existing branch
`feat/readonly-explorer-studies-sidebar`. This repo has **no JS test runner**;
verification is a scripted manual DOM check (Task B2, Step 4).

Current behavior (`static/walkthrough.js:11788-11891`): `_renderRailInvestigationGroups()`
builds per-iset groups plus an `__ungrouped__` bucket, filters to
`window._currentIsetSlug` **only if it matches a group**, else shows either a
"Select an investigation →" hint (no-current + many groups) or the full
all-groups list. `_currentIsetSlug` is `''` on load (line 4720), set by
`_openInvestigationDetail` (5214) and `investigation-switcher.js` (201). There is
no in-rail picker.

Target behavior: the rail shows **only** the current investigation's studies (flat
list) or, when there's no valid current, a **picker + "Choose an investigation"**
placeholder. The `__ungrouped__` bucket and the all-groups list are never
rendered. Selection persists per workspace.

---

### Task B1: Rewrite the rail render — current-only or chooser, with a picker

**Files:**
- Modify: `static/walkthrough.js` — `_renderRailInvestigationGroups()` (~11826-11891); add helpers before `window._renderRailInvestigationGroups = ...` (~11892).

**Interfaces:**
- Produces: `_railInvestigationPicker(currentSlug) -> string` (HTML `<select>`); `window._railSelectInvestigation(name)`; `_railIsetKey() -> string` (localStorage key). Consumes existing `window._isetIndex` (each `{name, title, studies[], current}`), `window._investigations`, `_railStudyItem`, `_esc`.

- [ ] **Step 1: Replace the render tail (grouping → current-only + chooser)**

In `static/walkthrough.js`, replace the block from the `var ungrouped = ...` line (~11826) through the end of the all-groups `host.innerHTML = groups.map(...)` and its closing (`}).join('');` at ~11890) — i.e. everything **after** the `groups.push({name: iset.name, ...})` loop (keep that loop, ~11810-11825) and **before** `}` that closes the function — with:

```javascript
    // Scope the rail to a SINGLE investigation. We never render the
    // "Ungrouped" bucket or an all-investigations list here: the rail shows
    // either the current investigation's studies or a chooser. (Orphan studies
    // that belong to no investigation are intentionally not surfaced here.)
    var currentSlug = window._currentIsetSlug || '';
    var currentGroup = currentSlug
      ? groups.filter(function(g) { return g.name === currentSlug; })[0] || null
      : null;

    var picker = _railInvestigationPicker(currentSlug);

    if (!currentGroup) {
      // No valid current investigation → picker + placeholder, no study rows.
      host.innerHTML = picker
        + '<div class="viv-rail-empty" style="font-size:0.85em;color:#94a3b8;'
        + 'padding:6px 14px;font-style:italic">Choose an investigation to see its studies.</div>';
      return;
    }

    // Current investigation → its studies as a flat list under the picker.
    host.innerHTML = picker
      + '<div class="rail-iset-name" title="' + _esc(currentGroup.title || currentGroup.name) + '"'
      + ' onclick="window._railOpenInvestigationDetail(\'' + _esc(currentGroup.name) + '\');"'
      + ' style="cursor:pointer;">' + _esc(currentGroup.title || currentGroup.name) + '</div>'
      + currentGroup.studies.map(function(s) { return _railStudyItem(s); }).join('');
  }

  // Per-workspace localStorage key for the remembered investigation. The URL
  // path differs per hosted workspace (base-path), so it namespaces cleanly.
  function _railIsetKey() {
    return 'viv:rail-iset:' + (window.location.pathname || '/');
  }

  // Build the investigation <select> shown at the top of the STUDIES rail.
  function _railInvestigationPicker(currentSlug) {
    var isets = (window._isetIndex || []).slice().sort(function(a, b) {
      return String(a.title || a.name).localeCompare(String(b.title || b.name));
    });
    var opts = ['<option value="">Choose an investigation…</option>'];
    isets.forEach(function(i) {
      var sel = i.name === currentSlug ? ' selected' : '';
      opts.push('<option value="' + _esc(i.name) + '"' + sel + '>'
        + _esc(i.title || i.name) + '</option>');
    });
    return '<select class="rail-iset-picker" style="width:calc(100% - 24px);'
      + 'margin:2px 12px 6px;padding:3px 6px;font-size:0.82em;color:#374151;'
      + 'border:1px solid #e5e7eb;border-radius:4px;background:#fff;cursor:pointer;"'
      + ' onchange="window._railSelectInvestigation(this.value)">'
      + opts.join('') + '</select>';
  }

  // Picker onchange: set the current investigation, persist it, re-render.
  window._railSelectInvestigation = function(name) {
    window._currentIsetSlug = name || '';
    try { window.localStorage.setItem(_railIsetKey(), name || ''); } catch (_) { /* ignore */ }
    _renderRailInvestigationGroups();
  };
```

> Note: `window._railOpenInvestigationDetail` is the same helper the existing
> flat-list branch used (see original line 11863); it remains defined elsewhere
> in the file. Do not redefine it.

- [ ] **Step 2: Verify the file still parses**

Run: `node --check static/walkthrough.js`
Expected: no output (exit 0). If it errors, fix the brace/paren balance around the replaced block.

- [ ] **Step 3: Commit**

```bash
git add static/walkthrough.js
git commit -m "dashboard: STUDIES rail shows only current investigation + picker (no Ungrouped)"
```

---

### Task B2: Resolve the current investigation on load (persisted → registry current)

**Files:**
- Modify: `static/walkthrough.js` — the sidebar data-load `Promise.all` that populates `window._isetIndex` then calls `_renderRailInvestigationGroups()` (~3395-3410).

**Interfaces:**
- Consumes: `_railIsetKey()` (B1), `window._isetIndex` entries' `current` flag.

- [ ] **Step 1: Locate the loader**

Run: `grep -n "_isetIndex = " static/walkthrough.js`
Expected: a line inside a `Promise.all([...]).then(...)` that sets
`window._isetIndex = arr[1].investigations || [];` immediately before a call to
`_renderRailInvestigationGroups()`.

- [ ] **Step 2: Seed `_currentIsetSlug` before the render call**

Immediately **before** that `_renderRailInvestigationGroups()` call (and after
`window._isetIndex` is assigned), insert:

```javascript
      // Resolve the current investigation once, if nothing set it yet:
      // remembered selection (validated against known isets) → the server's
      // `current` flag (branch/running/first) → none (rail shows the chooser).
      if (!window._currentIsetSlug) {
        var _isets = window._isetIndex || [];
        var _persisted = '';
        try { _persisted = window.localStorage.getItem(_railIsetKey()) || ''; } catch (_) { /* ignore */ }
        var _valid = _persisted && _isets.some(function(i) { return i.name === _persisted; });
        var _cur = (_isets.filter(function(i) { return i.current; })[0] || {}).name || '';
        window._currentIsetSlug = _valid ? _persisted : _cur;
      }
```

- [ ] **Step 3: Verify the file still parses**

Run: `node --check static/walkthrough.js`
Expected: exit 0.

- [ ] **Step 4: Manual DOM verification against the v2ecoli workspace**

The dashboard has no JS unit harness; verify in a browser. Start a live dashboard
against the v2ecoli workspace (which has `pdmp`, `scaling`, and orphan studies):

```bash
cd /Users/eranagmon/code/v2ecoli
.venv/bin/vivarium-dashboard serve --workspace . --port 8799
```

Open http://127.0.0.1:8799 and confirm, in the STUDIES rail:
1. A **"Choose an investigation…"** `<select>` is present at the top of STUDIES.
2. With nothing selected: the placeholder text "Choose an investigation to see its studies." shows; **no** "Ungrouped" row; **no** pdmp/scaling group headers listed together.
3. Selecting **pdmp** in the picker: only pdmp's studies list; the count matches; no other groups.
4. Reload the page: the picker still shows **pdmp** (persisted) and its studies (unless the server marks a different investigation `current`, which also counts as pass).
5. Browser console: `document.querySelectorAll('#viv-rail-investigations .rail-iset-name').length` is `0` (chooser) or `1` (one investigation) — never many.

Record the observed results in the task report. If the editable install isn't on
this branch, run `pip install -e /Users/eranagmon/code/vivarium-dashboard` first
(from the up-to-date branch checkout).

- [ ] **Step 5: Commit**

```bash
git add static/walkthrough.js
git commit -m "dashboard: seed current investigation on load (persisted -> registry current)"
```

---

## Self-Review

**Spec coverage:**
- WS-A full tabs in static + default Setup & Run → A3. ✅
- Static loader reads parameters/overrides/default_n_steps/metadata → A3 Step 5. ✅
- SetupRunPanel read-only (Run/Preview disabled + note) → A1. ✅
- Results/Visualizations read-only empty states → A2. ✅
- No publish.py change → confirmed (no such task). ✅
- WS-B scope to current investigation + picker + placeholder → B1. ✅
- No Ungrouped bucket (display-only, no yaml) → B1 (bucket never rendered). ✅
- On-load current resolution: persisted → `current` flag → none → B2. ✅
- Snapshot + live share the render path → B1/B2 touch the shared function; picker/persistence are DOM/localStorage only (work in both). ✅

**Placeholder scan:** no TBD/TODO; every code step shows complete code. ✅

**Type/name consistency:** `readOnly` prop name identical across A1/A2/A3 and both panels + App wiring. `_railInvestigationPicker` / `window._railSelectInvestigation` / `_railIsetKey` used consistently between B1 and B2. `window._railOpenInvestigationDetail` reused, not redefined. ✅

**Known coupling:** A3 consumes A1/A2's props → sequence A1 → A2 → A3 → A4. B2 consumes B1's helpers → sequence B1 → B2. WS-A and WS-B are independent and may run in parallel (different repos).
