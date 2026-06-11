# Read-only viewer — full surface (folds into #2) — Implementation Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Complete the read-only ("snapshot") viewer so EVERY data view is browsable read-only: add read-only Composite **Explore** (via bigraph-loom's `?static=1` mode), **Simulations DB**, **Visualizations/Analyses**; add a **read-only banner** linking to the interactive version; replace the **repo switcher** with a static "repo loaded" label. Branches stays hidden; all authoring/run/git stays stripped. Local mode IDENTICAL.

**Tech:** Python stdlib + Jinja2 + vanilla JS; pytest. Branch `feat/narrative-export`. Builds on the existing snapshot machinery (`publish.build_bundle`, `data-source.js` snapshot mode, `body.snapshot` + `snapshot-readonly.css`). All file:lines below are grounded.

---

## Task 1: Read-only Composite Explore (bigraph-loom `?static=1`)

**Files:** `publish.py`, `static/data-source.js`, `static/walkthrough.js`, `templates/index.html.j2`, `static/snapshot-readonly.css`; Test `tests/test_publish.py`.

bigraph-loom serves at `/bigraph-loom/*` from `bigraph_loom.asset_dir()` (server.py:6489) and supports **`?static=1&stateUrl=<json>`** → View-only tab, loads the composite state from a JSON snapshot, no `/api/*` (App.tsx:89,157-183,547). `_get_composite_resolve` (server.py:12724) returns `{id,name,description,parameters,state,svg,kind,module,default_n_steps}` — `state` is what loom renders.

- [ ] **Step 1: Failing test** — after build, `api/composite-state/<id>.json` exists for each composite (== the resolve payload), and `bundle/bigraph-loom/index.html` exists:
```python
def test_bundle_exports_composite_state_and_loom(tmp_workspace, tmp_path):
    out = tmp_path/"bundle"; publish.build_bundle(server.WORKSPACE, out)
    comps = json.loads((out/"api"/"composites.json").read_text())["composites"]
    if comps:
        cid = comps[0]["id"]
        assert (out/"api"/"composite-state"/f"{cid}.json").is_file()
    assert (out/"bigraph-loom"/"index.html").is_file()
```
- [ ] **Step 2: fail. Step 3: implement.**
  - `publish.py`: after `composites.json`, loop the composite ids and write `api/composite-state/<id>.json` ← the `_get_composite_resolve` data builder (factor `_composite_resolve_data(ws_root, cid, overrides={})` out of `_get_composite_resolve` if needed; tolerate per-composite failure → skip + log). Copy `bigraph_loom.asset_dir()` tree → `bundle/bigraph-loom/` (use `shutil.copytree`).
  - `data-source.js`: `_compositeResolveUrl(id)` → snapshot `/api/composite-state/<id>.json`, live `/api/composite-resolve?id=<id>`; `DataSource.loadCompositeResolve(id)`.
  - `walkthrough.js`: `_ceFetch` (~3719) + the `_loadCompositeExplorer` fetch (~3854) route through `DataSource.loadCompositeResolve(id)` (raw-fetch fallback). In snapshot mode, set the loom iframe `src` to `/bigraph-loom/index.html?static=1&stateUrl=/api/composite-state/<id>.json` (both `#composite-explore-frame` index.html.j2:1192 and the JS-built `inv-composite-explore-frame` ~9827); in local mode keep `/bigraph-loom/index.html` + postMessage.
  - REMOVE the snapshot redirect for `composite-explore` (`walkthrough.js` ~436) and the Explore-hide CSS (`snapshot-readonly.css` last block `button[onclick*="_openCompositeExplorer"]`). Keep `#ce-begin-study-bar`/`#ce-post-run-bar` hidden.
- [ ] **Step 4: pass. Step 5: commit** — `feat(viewer): read-only Composite Explore via bigraph-loom ?static=1 + composite-state export`

## Task 2: Simulations DB tab (read-only)

**Files:** `publish.py`, `data-source.js`, `walkthrough.js`, `snapshot-readonly.css`; Test.

`GET /api/simulations` (server.py:8901) → `{simulations:[...], current}`; loader `_initSimulations` (walkthrough.js:11904, fetch :11912); Delete `walkthrough.js:11837` → `DELETE /api/simulation-run`; rail link `index.html.j2:398`.

- [ ] **Step 1: Failing test** — `api/simulations.json` exported; `data-source.js` has `loadSimulations`.
- [ ] **Step 2-3:** `publish.py` writes `api/simulations.json` ← the `_get_simulations` data builder (factor if needed). `data-source.js` `loadSimulations()` (snapshot `/api/simulations.json`, live `/api/simulations`). `_initSimulations` routes through it (raw-fetch fallback). Tag the Delete button (`11837`) + the Open-in-explorer run button (`11829`, seeds `?run_id=` live polling) with `js-authoring` (so they're hidden); the Refresh is harmless (routes to the json). In `snapshot-readonly.css`, REMOVE `a.menu-link[data-page="simulations"]` from the hidden-rail block; remove `'simulations'` from the `walkthrough.js:433` snapshot redirect.
- [ ] **Step 4: pass. Step 5: commit** — `feat(viewer): read-only Simulations DB tab (pre-run simulations)`

## Task 3: Visualizations/Analyses tab (read-only)

**Files:** `publish.py`, `data-source.js`, `walkthrough.js`, `snapshot-readonly.css`; Test.

`GET /api/visualization-classes` (server.py:10306, needs build_core → export at publish) → `{classes:[{address,name,doc,kind}]}`; loader `_loadAnalysesPage` (walkthrough.js:1143, fetch :1147); Preview `1098`→POST /api/visualization-preview, Use `1109`→modal-visualization; rail link `index.html.j2:413`.

- [ ] **Step 1: Failing test** — `api/visualization-classes.json` exported; `loadVisualizationClasses` in data-source.js.
- [ ] **Step 2-3:** `publish.py` writes `api/visualization-classes.json` ← the builder (tolerate build_core failure → empty). `data-source.js` `loadVisualizationClasses()`. `_loadAnalysesPage` routes through it. Tag Preview/Use buttons (`1098/1109`, and the picker `_renderKindPicker` `1187`) with `js-authoring`; `#modal-viz-preview` (index.html.j2:582) hidden in css (modal-visualization already hidden). REMOVE `a.menu-link[data-page="visualizations"]` from the hidden-rail block; remove `'visualizations'` from the `walkthrough.js:433` redirect.
- [ ] **Step 4: pass. Step 5: commit** — `feat(viewer): read-only Visualizations/Analyses tab (available classes)`

## Task 4: Read-only banner + interactive-version link

**Files:** `templates/index.html.j2`, `static/snapshot-readonly.css`, `static/walkthrough.js`, `publish.py`; Test.

- [ ] **Step 1-3:** In `index.html.j2` between the rail and `<main>` (~511-515, where the removed-banner comment is), add `<div id="snapshot-banner"><span>Read-only view — a full interactive version is available.</span> <a id="snapshot-interactive-link" target="_blank" rel="noopener">Open interactive version</a></div>`. In `snapshot-readonly.css`: `#snapshot-banner{display:none}` baseline + `body.snapshot #snapshot-banner{display:block}` (the inverse rule) + styling. `publish.py` `_set_snapshot_config` (~99): inject `interactiveUrl` into the swapped `__DASH_CONFIG__` literal (a configurable `--interactive-url` CLI arg, default ""). In `walkthrough.js` DOMContentLoaded (~2895): if snapshot, set `#snapshot-interactive-link` href from `window.__DASH_CONFIG__.interactiveUrl`; hide the link if absent.
- [ ] **Step 4: pass. Step 5: commit** — `feat(viewer): read-only banner + interactive-version link`

## Task 5: Repo switcher → static "repo loaded" label

**Files:** `static/investigation-switcher.js` (or wherever the repo/workspace dropdown is), `snapshot-readonly.css`, `templates/index.html.j2`; Test.

The workspace/repo switcher dropdown (`investigation-switcher.js`, fetches `/api/workspaces` ~:117) should be removed in snapshot; show a static label of the loaded repo (from `__DASH_CONFIG__.repo` / the workspace name).

- [ ] **Step 1-3:** In snapshot mode, hide the repo-switcher control (add its selector to `snapshot-readonly.css` — find the switcher root element, e.g. `.viv-workspace-switcher`/`#workspace-switcher`), and render a static `<span class="viv-repo-label">` showing the repo name (from `__DASH_CONFIG__.repo` injected by `_set_snapshot_config`, or the rail branding). Confirm `investigation-switcher.js`'s `/api/workspaces` fetch is gated/no-op in snapshot (it already `.catch`es; ensure it doesn't show an error). 
- [ ] **Step 4: pass. Step 5: commit** — `feat(viewer): replace repo switcher with static repo label in snapshot`

## Task 6: Rebuild golden + full verify

- [ ] **Step 1:** extend the v2e-invest golden: bundle has `api/{simulations,visualization-classes}.json`, `api/composite-state/<id>.json` (≥1), `bundle/bigraph-loom/index.html`, `#snapshot-banner` in index.html, repo label. v2e-invest untouched.
- [ ] **Step 2:** `tests/test_publish.py tests/test_data_endpoints.py` green. Local mode unchanged.
- [ ] **MANUAL (pending):** rebuild from the v2ecoli venv (`PYTHONPATH=...vivarium-dashboard ...v2ecoli/.venv/bin/python -m vivarium_dashboard.publish --workspace ...v2e-invest --out /tmp/ro`), serve, confirm: all six tabs load read-only; Composite Explore shows the loom View-only wiring (no Run); Simulations DB lists pre-run sims (no Delete/Open-run); read-only banner shows; repo label not a switcher; no authoring controls anywhere.
- [ ] **Step 3: commit** — `test(publish): full read-only surface golden`

---

## Self-Review
- Coverage: read-only Explore (T1, loom ?static), Simulations (T2), Visualizations (T3), banner (T4), repo label (T5) — all user decisions. Branches stays hidden; authoring/run/git stripped.
- Local mode IDENTICAL: every snapshot branch gated by `mode==="snapshot"`/`body.snapshot`; raw-fetch fallbacks; CSS only under `body.snapshot`.
- No placeholders: grounded builders + file:lines; loom `?static=1&stateUrl=` is the confirmed read-only hook.

## Notes for the executor
- `.venv/bin/python -m pytest`. The endpoint/control/loom map (file:lines) is in the grounding above — follow it.
- Loom dist MUST come from `bigraph_loom.asset_dir()` (has `?static`), NOT the stale `static/loom-explore/` copy.
- Composite-state/visualization-classes export needs `build_core` — publish from a sim-stack venv (v2ecoli/.venv + this branch on PYTHONPATH) to capture real data; without it they export empty/error (graceful, like the live endpoints).
- Don't modify real v2e-invest; goldens write to tmp.
