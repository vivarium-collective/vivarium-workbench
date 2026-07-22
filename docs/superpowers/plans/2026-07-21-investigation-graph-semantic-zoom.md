# Investigation Graph — Semantic Zoom + Status Click-through — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add 3-band semantic zoom (slider + wheel) to the investigation graph and make each node's status badge click through to the study's finding/evidence.

**Architecture:** Pure LOD helper (`_layoutOptsForBand`, exported CommonJS for node tests) drives a refactored `_renderInvestigationDag` that reads band-specific `cardW`/`xGap` and gates the Asks/Finds/chain/follow-ups sections. A module `aigBand` state + `_setAigBand(b)` re-renders from cached args. Slider (in `index.html.j2`) and a `wheel` listener on the graph shell both call `_setAigBand`. The status badge becomes its own click target opening the study at findings/evidence.

**Tech Stack:** Vanilla JS (`vivarium_workbench/static/walkthrough.js`, `aig-graph.js`), Jinja template (`templates/index.html.j2`), `style.css`; tests are `node tests/js/*.js` scripts against exported pure helpers.

## Global Constraints

- All work in the worktree `/code/vdash-graph-zoom` on `feat/investigation-graph-semantic-zoom`. Never touch the shared `/code/vivarium-dashboard` checkout.
- No backend change; no graph-library swap (stays DOM cards + SVG edges).
- Three **discrete** bands only (0=far, 1=mid, 2=near); **no `transform: scale()`** on cards/text — geometry via band-specific `cardW`/`xGap` + section toggling.
- Band → sections: far = title+badge only; mid = +asks+finds; near = +asks+finds+chain(`_chainBlockHtml`)+follow-ups.
- Badge keeps showing the study's `confidence` field (no verdict reconciliation); the click only makes the reason reachable.
- Existing single-click (quick-look) / double-click (open study) node behavior is preserved; the badge handler must `stopPropagation`.
- Tests are pure-function `node` scripts (repo has no jsdom); slider/wheel/badge DOM behavior is verified manually by serving the workbench from this worktree.

## File Structure

- **Modify** `vivarium_workbench/static/aig-graph.js` — add + export the pure `_layoutOptsForBand(band)` helper (this module is already CommonJS-exported and node-tested, so it's the natural home for a pure LOD helper).
- **Modify** `vivarium_workbench/static/walkthrough.js` — `aigBand` state, `_setAigBand`, refactor `_renderInvestigationDag` (~L5643) to consume band opts + cache args + set shell band class + gate sections; make the badge a click target; attach the wheel listener.
- **Modify** `vivarium_workbench/templates/index.html.j2` (~L917 `investigation-dag-lead`) — add the zoom slider control.
- **Modify** `vivarium_workbench/static/style.css` — minimal `.aig-zoom-*` container rules (cursor/spacing) if needed.
- **Create** `tests/js/test_layout_opts_for_band.js` — pure tests for `_layoutOptsForBand`.

---

### Task 1: LOD band model + band-aware render refactor

**Files:**
- Modify: `vivarium_workbench/static/aig-graph.js`
- Modify: `vivarium_workbench/static/walkthrough.js` (`_renderInvestigationDag` ~L5643–5814; constants L5697–5698)
- Test: `tests/js/test_layout_opts_for_band.js`

**Interfaces:**
- Produces: `_layoutOptsForBand(band) -> {band, cls, cardW, xGap, asks, finds, chain, followups}` (exported from aig-graph.js). `band` clamped to 0..2. Consumed by `_renderInvestigationDag` and `_setAigBand` (Task 2).

- [ ] **Step 1: Write the failing test**

```js
// tests/js/test_layout_opts_for_band.js — run with: node tests/js/test_layout_opts_for_band.js
const assert = require('assert');
const { _layoutOptsForBand } = require('../../vivarium_workbench/static/aig-graph.js');

const far = _layoutOptsForBand(0), mid = _layoutOptsForBand(1), near = _layoutOptsForBand(2);
// far: title+badge only
assert.strictEqual(far.cls, 'aig-zoom-far');
assert.strictEqual(far.asks, false); assert.strictEqual(far.finds, false);
assert.strictEqual(far.chain, false); assert.strictEqual(far.followups, false);
// mid: + asks + finds, no chain
assert.strictEqual(mid.cls, 'aig-zoom-mid');
assert.ok(mid.asks && mid.finds); assert.strictEqual(mid.chain, false);
// near: everything
assert.strictEqual(near.cls, 'aig-zoom-near');
assert.ok(near.asks && near.finds && near.chain && near.followups);
// card width grows far < mid < near
assert.ok(far.cardW < mid.cardW && mid.cardW < near.cardW);
// clamp out-of-range to 0..2
assert.strictEqual(_layoutOptsForBand(-5).cls, 'aig-zoom-far');
assert.strictEqual(_layoutOptsForBand(9).cls, 'aig-zoom-near');
console.log('ok test_layout_opts_for_band');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/js/test_layout_opts_for_band.js`
Expected: FAIL — `_layoutOptsForBand is not a function` (not yet exported).

- [ ] **Step 3: Add the pure helper to aig-graph.js**

In `vivarium_workbench/static/aig-graph.js`, add the function and include it in the existing `module.exports` (the file already exports `_chainBlockHtml`, `_groupClaims` via a `typeof module !== 'undefined'` guard — add to that same block):

```js
function _layoutOptsForBand(band) {
  var b = Math.max(0, Math.min(2, band | 0));
  var BANDS = [
    { band: 0, cls: 'aig-zoom-far',  cardW: 150, xGap: 40, asks: false, finds: false, chain: false, followups: false },
    { band: 1, cls: 'aig-zoom-mid',  cardW: 210, xGap: 64, asks: true,  finds: true,  chain: false, followups: true  },
    { band: 2, cls: 'aig-zoom-near', cardW: 320, xGap: 72, asks: true,  finds: true,  chain: true,  followups: true  },
  ];
  return BANDS[b];
}
```
Add `_layoutOptsForBand` to the module.exports object.

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/js/test_layout_opts_for_band.js`
Expected: `ok test_layout_opts_for_band`.

- [ ] **Step 5: Refactor `_renderInvestigationDag` to consume band opts**

In `walkthrough.js`, near the top of the IIFE add module state (once):
```js
  var aigBand = 1;                 // 0=far, 1=mid, 2=near (default = current detail)
  var _lastDagArgs = null;         // [studies, chainsBySlug] for re-render on band change
```
In `_renderInvestigationDag(studies, chainsBySlug)` (L5643): cache args and resolve band opts at the top, replacing the hardcoded constants:
```js
    _lastDagArgs = [studies, chainsBySlug];
    var _opts = (window._layoutOptsForBand || _layoutOptsForBand)(aigBand);
    var shellEl = document.getElementById('investigation-dag-shell');
    if (shellEl) { shellEl.classList.remove('aig-zoom-far','aig-zoom-mid','aig-zoom-near'); shellEl.classList.add(_opts.cls); }
```
Change `var CARD_W = 210;` → `var CARD_W = _opts.cardW;` and `var X_GAP = 64,` → `var X_GAP = _opts.xGap,` (L5697–5698).
Gate the card sections on `_opts` in the `node.innerHTML` build (L5785–5799): wrap the Asks block in `(_opts.asks && asks ? …: '')`, the Finds block in `(_opts.finds ? …: '')`, the `moreN` line in `(_opts.finds && moreN ? …: '')`, `followUpsChip` in `(_opts.followups ? followUpsChip : '')`, and the chain-block append in `(_opts.chain && chainsBySlug && typeof window._chainBlockHtml === 'function' ? window._chainBlockHtml(chainsBySlug[s.name]) : '')`.
Make `_layoutOptsForBand` reachable in the browser: at the end of aig-graph.js's browser path (the non-module branch), also expose it on the global the IIFE receives: `global._layoutOptsForBand = _layoutOptsForBand;` (mirroring `global._chainBlockHtml = _chainBlockHtml;` at aig-graph.js tail), so the browser sees `window._layoutOptsForBand`.

- [ ] **Step 6: Verify pure tests still pass + no syntax break**

Run: `node tests/js/test_layout_opts_for_band.js && node tests/js/test_chain_block.js && node -c vivarium_workbench/static/walkthrough.js`
Expected: both tests print `ok`, and `node -c` (syntax check) exits 0.

- [ ] **Step 7: Commit**

```bash
git add vivarium_workbench/static/aig-graph.js vivarium_workbench/static/walkthrough.js tests/js/test_layout_opts_for_band.js
git commit -m "feat(graph): band-aware LOD layout for the investigation graph"
```

---

### Task 2: Zoom slider + wheel input

**Files:**
- Modify: `vivarium_workbench/templates/index.html.j2` (~L917 `investigation-dag-lead`)
- Modify: `vivarium_workbench/static/walkthrough.js` (add `_setAigBand`; wheel listener)
- Modify: `vivarium_workbench/static/style.css` (slider spacing)

**Interfaces:**
- Consumes: `aigBand`, `_lastDagArgs`, `_renderInvestigationDag` (Task 1).
- Produces: `_setAigBand(b)` (global) that clamps 0..2, sets `aigBand`, re-renders from `_lastDagArgs`, and syncs the slider value.

- [ ] **Step 1: Add the slider to the header**

In `index.html.j2`, inside the `investigation-dag-lead` div (after the `?` chip / lead text, L917–919), add:
```html
      <span class="aig-zoom-ctl" style="float:right;display:inline-flex;align-items:center;gap:6px;font-size:0.9em;color:#64748b">
        <span title="Zoom out — overview">🔍−</span>
        <input id="aig-zoom-slider" type="range" min="0" max="2" step="1" value="1"
               oninput="window._setAigBand(parseInt(this.value,10))" style="width:110px" aria-label="Graph zoom / detail level">
        <span title="Zoom in — full detail">🔍+</span>
      </span>
```

- [ ] **Step 2: Add `_setAigBand` and the wheel listener**

In `walkthrough.js`, add (inside the IIFE, after `_renderInvestigationDag`):
```js
  function _setAigBand(b) {
    var nb = Math.max(0, Math.min(2, b | 0));
    if (nb === aigBand && document.getElementById('investigation-dag-shell').classList.contains('aig-zoom-' + ['far','mid','near'][nb])) {
      // still sync the slider (wheel and slider share state)
    }
    aigBand = nb;
    var sl = document.getElementById('aig-zoom-slider');
    if (sl && String(sl.value) !== String(nb)) sl.value = String(nb);
    if (_lastDagArgs) _renderInvestigationDag(_lastDagArgs[0], _lastDagArgs[1]);
  }
  window._setAigBand = _setAigBand;

  // Wheel over the graph shell zooms bands (one notch per gesture, threshold +
  // cooldown so a single scroll doesn't skip bands). preventDefault so the page
  // doesn't scroll while zooming the graph.
  (function _wireAigWheel() {
    var lastWheel = 0;
    document.addEventListener('wheel', function (ev) {
      var shell = document.getElementById('investigation-dag-shell');
      if (!shell || !shell.contains(ev.target)) return;   // only over the graph
      ev.preventDefault();
      var now = Date.now();
      if (now - lastWheel < 220) return;                  // cooldown between steps
      if (Math.abs(ev.deltaY) < 4) return;
      lastWheel = now;
      _setAigBand(aigBand + (ev.deltaY > 0 ? -1 : 1));    // scroll down = zoom out
    }, { passive: false });
  })();
```

- [ ] **Step 3: Syntax check + pure tests**

Run: `node -c vivarium_workbench/static/walkthrough.js && node tests/js/test_layout_opts_for_band.js`
Expected: exit 0 + `ok test_layout_opts_for_band`.

- [ ] **Step 4: Manual verify (serve from this worktree)**

Serve the workbench from this worktree and open the comparison investigation (see Task 4 for the serve command). Confirm: the slider moves far↔mid↔near and card detail changes; scrolling the wheel over the graph zooms bands (down=out, up=in) without scrolling the page; edges redraw; slider and wheel stay in sync.

- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/templates/index.html.j2 vivarium_workbench/static/walkthrough.js vivarium_workbench/static/style.css
git commit -m "feat(graph): zoom slider + wheel input driving semantic-zoom bands"
```

---

### Task 3: Status badge → finding/evidence click-through

**Files:**
- Modify: `vivarium_workbench/static/walkthrough.js` (badge markup + handler in `_renderInvestigationDag`, ~L5789)

**Interfaces:**
- Consumes: existing `_openInvestigationDrawer` / `_openStudyInsideInvestigation`.
- Produces: badge is a focusable, clickable element that opens the study at its finding/evidence and stops propagation to the node.

- [ ] **Step 1: Make the badge a click target**

In the card `innerHTML` (L5789), change the confidence badge `<span>` to include a class + role and stop-propagation open. Replace:
```js
          '<span style="font-size:0.62em;font-weight:700;color:' + ss.color + ';white-space:nowrap;margin-top:1px">' + _esc(confidence) + '</span>' +
```
with:
```js
          '<span class="aig-status-badge" role="button" tabindex="0" title="Why: open this study’s finding & evidence" ' +
            'style="font-size:0.62em;font-weight:700;color:' + ss.color + ';white-space:nowrap;margin-top:1px;cursor:pointer;text-decoration:underline dotted">' +
            _esc(confidence) + '</span>' +
```

- [ ] **Step 2: Wire the badge handler after the node is built**

Where the node's other listeners are attached (after `nodesHost.appendChild(node);`, near the `.aig-claim-row` wiring ~L5800), add:
```js
      var _badge = node.querySelector('.aig-status-badge');
      if (_badge) {
        var _openReason = function (ev) {
          ev.stopPropagation();
          // Prefer the quick-look drawer opened to the finding/evidence; fall
          // back to opening the full study. _openInvestigationDrawer renders the
          // study's findings/evidence in the drawer body.
          if (window._openInvestigationDrawer) window._openInvestigationDrawer('study', s);
          else _openStudyInsideInvestigation(s.name);
          var body = document.getElementById('investigation-detail-drawer-body');
          var target = body && (body.querySelector('[data-section="findings"]') || body.querySelector('.aig-claim-row'));
          if (target && target.scrollIntoView) target.scrollIntoView({ block: 'nearest' });
        };
        _badge.addEventListener('click', _openReason);
        _badge.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') _openReason(ev); });
      }
```

- [ ] **Step 3: Syntax check**

Run: `node -c vivarium_workbench/static/walkthrough.js`
Expected: exit 0.

- [ ] **Step 4: Manual verify**

In the served workbench: clicking a node's status badge (e.g. acetate "Investigating") opens the study's finding/evidence (drawer scrolled to findings), while clicking the node body still shows the quick-look and double-click still opens the study. Badge is keyboard-focusable (Tab) and activates on Enter/Space.

- [ ] **Step 5: Commit**

```bash
git add vivarium_workbench/static/walkthrough.js
git commit -m "feat(graph): status badge click-through to the study's finding/evidence"
```

---

### Task 4: Serve, end-to-end verify, polish

**Files:** none (verification), or small `style.css` polish if the badge/slider need spacing.

- [ ] **Step 1: Serve the workbench from this worktree**

From the worktree, run the workbench server pointed at the v2ecoli workspace (the investigation with data):
```bash
cd /Users/eranagmon/code/vdash-graph-zoom
PYTHONPATH="$PWD" /Users/eranagmon/code/v2ecoli/.venv/bin/vivarium-workbench serve --workspace /Users/eranagmon/code/v2ecoli --port 8790
```
The `PYTHONPATH="$PWD"` prefix makes THIS worktree's package win over the v2ecoli venv's editable install of the shared checkout (verify with a `vivarium_workbench.__file__` print if unsure). If the console script isn't importable this way, run the pbg-dashboard machinery pointed at this worktree instead.

- [ ] **Step 2: Full end-to-end check**

Open `http://localhost:8790`, go to the `v2ecoli-vecoli-comparison` investigation graph and verify all acceptance criteria:
- Far band: many small title+badge cards, whole DAG visible; Mid: +Asks/Finds; Near: +evidence chain + follow-ups.
- Slider and wheel both change bands and stay in sync; wheel over the graph does not scroll the page.
- Edges redraw correctly at every band.
- Status badge opens the study's finding/evidence; node body quick-look and double-click unchanged.

- [ ] **Step 3: Commit any polish + stop the server**

```bash
git add -A && git commit -m "chore(graph): semantic-zoom polish" || echo "no polish needed"
```

---

## Self-Review

**Spec coverage:** zoom slider + wheel (Task 2) ✓; 3 LOD bands with documented content (Task 1) ✓; re-layout per band, no scale transform (Task 1 — `cardW`/`xGap` from `_layoutOptsForBand`) ✓; status badge → finding/evidence (Task 3) ✓; no backend/graph-lib change (Global Constraints) ✓; worktree isolation (Global Constraints) ✓; badge keeps `confidence` (Task 3) ✓.

**Placeholder scan:** none — every step has concrete code or an exact command. The serve entrypoint has a documented fallback.

**Type/name consistency:** `_layoutOptsForBand(band) -> {band,cls,cardW,xGap,asks,finds,chain,followups}` defined in Task 1 and consumed identically in Tasks 1–2; `aigBand`/`_lastDagArgs`/`_setAigBand` consistent across Tasks 1–2; badge class `aig-status-badge` consistent within Task 3; shell id `investigation-dag-shell` and slider id `aig-zoom-slider` consistent across tasks.
