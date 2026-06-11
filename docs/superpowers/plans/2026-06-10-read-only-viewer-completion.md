# Read-only viewer completion (folds into sub-project #2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Make the static bundle a *working* read-only viewer: export the full narrative read-surface the home SPA needs (investigations list, inputs/sources, catalog, composites, registry) and add a **snapshot read-only mode** that routes those reads to the static JSON and **hides all authoring** (install/run/edit) + the workspace tabs (GitHub Branches, Simulations DB), while keeping Registry + Composites as read-only "what-was-used" views.

**Architecture:** `publish.build_bundle` writes the new per-resource JSON via the (already-mostly-pure) builders. `data-source.js` gains loaders for the five reads (snapshot → static `.json`). The home SPA's raw `fetch` loaders route through `DataSource`. A `body.snapshot` class (set from `__DASH_CONFIG__.mode`) + CSS hides the enumerated authoring controls + modals; `_switchPage` early-returns for the hidden tabs.

**Tech Stack:** Python stdlib + Jinja2 + vanilla JS; pytest. Builds on #2 (`feat/narrative-export`). Grounding (file:lines): see the read surface + control map below.

**Decisions (user):** keep Investigations + Sources + read-only Registry + read-only Composites; hide all other authoring (Branches, Simulations DB, install/run/edit/PR/commit controls).

---

## File structure
- `vivarium_dashboard/server.py` — factor `_catalog_data(ws_root)->dict` out of `_get_catalog` (~13169); confirm `_build_iset_summary_for_test` (~2577), `_inputs_payload` (~2313), the composites builder (`_get_composites` ~12455), `_get_registry_data` (~320) are callable as data (they are/nearly are).
- `vivarium_dashboard/publish.py` — export the five resources.
- `vivarium_dashboard/static/data-source.js` — five new loaders + snapshot URLs.
- `vivarium_dashboard/static/walkthrough.js` — route the five raw fetches through `DataSource`; set `body.snapshot`; gate `_switchPage` + the valid-page whitelists.
- `vivarium_dashboard/static/snapshot-readonly.css` — NEW; the authoring/tab hides.
- `vivarium_dashboard/templates/index.html.j2` — load `snapshot-readonly.css`.
- Tests: extend `tests/test_publish.py`.

---

## Task 1: Export the five read resources

**Files:** `server.py` (factor `_catalog_data`), `publish.py`; Test `tests/test_publish.py`.

- [ ] **Step 1: Failing test** — after `build_bundle`, these exist + parity:
```python
def test_bundle_exports_full_read_surface(tmp_workspace, tmp_path):
    out = tmp_path/"bundle"; publish.build_bundle(server.WORKSPACE, out)
    assert (out/"api"/"iset-list.json").is_file()
    assert (out/"api"/"catalog.json").is_file()
    assert (out/"api"/"composites.json").is_file()
    assert (out/"api"/"registry.json").is_file()
    # inputs per investigation:
    isets = json.loads((out/"api"/"iset-list.json").read_text())["investigations"]
    if isets:
        inv = isets[0]["name"]
        assert (out/"api"/"inputs"/f"{inv}.json").is_file()
    # parity for iset-list:
    assert json.loads((out/"api"/"iset-list.json").read_text()) == \
        json.loads(json.dumps(server._build_iset_summary_for_test(server.WORKSPACE), default=server._json_default))
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Factor `_catalog_data(ws_root)->dict` out of `_get_catalog` (extract the `{modules:[...]}` assembly; `_get_catalog` then calls it + `self._json`). In `build_bundle`, write:
  - `api/iset-list.json` ← `server._build_iset_summary_for_test(ws_root)`
  - `api/inputs/<inv>.json` ← `server._inputs_payload(ws_root, inv)` for each investigation in the iset-list (so the Sources tab loads per investigation)
  - `api/catalog.json` ← `server._catalog_data(ws_root)`
  - `api/composites.json` ← the composites builder used by `_get_composites` (`composite_lookup.discover_all_composites(ws_root, pkg)` + the handler's filter — factor `_composites_data(ws_root)` if needed)
  - `api/registry.json` ← `server._get_registry_data(bypass_cache=True)` (it never raises; if build_core can't run in the publish venv it returns an error+empty — write whatever it returns; a published-from-sim-venv run captures the real registry)
  All via `server._json_body`/`_json_default` for parity.
- [ ] **Step 4: Run → pass.** **Step 5: Commit** — `feat(publish): export iset-list/inputs/catalog/composites/registry to the bundle`

## Task 2: `data-source.js` loaders + route the SPA reads through them

**Files:** `static/data-source.js`, `static/walkthrough.js`; Test: structural.

- [ ] **Step 1: Implement** five loaders in `data-source.js` (snapshot → static `.json`, else live):
```javascript
function _isetListUrl(){ return cfg().mode==="snapshot" ? "/api/iset-list.json" : "/api/iset-list"; }
function _inputsUrl(slug){ return cfg().mode==="snapshot" ? "/api/inputs/"+encodeURIComponent(slug)+".json"
                                                          : "/api/inputs?investigation="+encodeURIComponent(slug); }
function _catalogUrl(){ return cfg().mode==="snapshot" ? "/api/catalog.json" : "/api/catalog"; }
function _compositesUrl(){ return cfg().mode==="snapshot" ? "/api/composites.json" : "/api/composites"; }
function _registryUrl(){ return cfg().mode==="snapshot" ? "/api/registry.json" : "/api/registry"; }
// DataSource.loadIsetList/loadInputs(slug)/loadCatalog/loadComposites/loadRegistry → _get(...)
```
- [ ] **Step 2: Route** the home SPA's raw fetches through DataSource (preserve behavior in local mode — `DataSource.loadX()` returns the same JSON the raw fetch did):
  - `walkthrough.js:4104` `_loadInvestigationSets` → `DataSource.loadIsetList()`
  - `walkthrough.js:550` `_loadInputs` → `DataSource.loadInputs(window._currentIsetSlug)`
  - `walkthrough.js:2132` `_loadCatalog` → `DataSource.loadCatalog()`
  - `walkthrough.js:1653` `_loadComposites` → `DataSource.loadComposites()`
  - `walkthrough.js:1359` `_loadRegistry` → `DataSource.loadRegistry()`
  Keep a raw-fetch fallback (`if (!window.DataSource) fetch(...)`) like the existing report-builder paths do.
- [ ] **Step 3: Structural test** (`tests/test_publish.py` or test_data_endpoints): `data-source.js` contains `loadIsetList`, `loadInputs`, `loadCatalog`, `loadComposites`, `loadRegistry` + `"snapshot"` + the five `.json` URLs.
- [ ] **Step 4: Run → pass.** **Step 5: Commit** — `feat(static): DataSource loaders for iset-list/inputs/catalog/composites/registry`

## Task 3: Snapshot read-only mode (hide authoring + workspace tabs)

**Files:** `static/walkthrough.js` (set `body.snapshot` + gate `_switchPage`/whitelists), `static/snapshot-readonly.css` (NEW), `templates/index.html.j2` (load the css).

- [ ] **Step 1: Implement.**
  - In `walkthrough.js` `DOMContentLoaded` init (~2866): `if ((window.__DASH_CONFIG__||{}).mode === "snapshot") document.body.classList.add("snapshot");`
  - In `_switchPage` (~427): early-return (no-op or redirect to investigations) for `simulations`, `github`, `studies` when `body.snapshot`. Trim the valid-page whitelists (~492, ~507) to drop those in snapshot mode.
  - `index.html.j2`: add `<link rel="stylesheet" href="/style.css">`-style `<link ... href="snapshot-readonly.css">` (publish normalizes to `/assets/snapshot-readonly.css`).
  - `snapshot-readonly.css` — `body.snapshot` rules hiding (use the grounded selectors):
    - rail links: `body.snapshot a.menu-link[data-page="simulations"], body.snapshot a.menu-link[data-page="github"] { display:none }`
    - the authoring buttons/modals enumerated in the control map (Install/Uninstall on registry cards, the composite Begin-Study/Run/Save-as-Study/Configure, investigation New/Clone/Close/Run-unblocked/New-study, the Sources add-* modals + their open buttons, the github PR/commit/suggest controls, the study-detail run/edit/delete controls). Tag what you can with a shared class (`js-authoring`) where the buttons are JS-rendered (registry/iset cards), and use template selectors for the inline `onclick=` ones. KEEP read-only controls (Generate report, Refresh that's pure-read, Explore on a composite card → but hide Begin-Study/Run/Save-as inside the explorer).
- [ ] **Step 2: Structural test** — render the home shell in snapshot mode (or assert `snapshot-readonly.css` exists in the bundle + `body.classList.add("snapshot")` + the `_switchPage` snapshot guards are present in walkthrough.js). Since the hiding is CSS/JS (no harness), the test asserts the css is bundled + the gating code exists; the real check is manual.
- [ ] **Step 3: Run → pass.** **Step 5: Commit** — `feat(viewer): snapshot read-only mode — hide authoring + Branches/Simulations tabs; read-only Registry/Composites`

## Task 4: Rebuild + verify the full read-only bundle

**Files:** Test `tests/test_publish.py` (extend the golden) + manual.

- [ ] **Step 1:** extend the v2e-invest golden: assert the bundle has `api/{iset-list,catalog,composites,registry}.json` + `api/inputs/<inv>.json` for its real investigations, and `assets/snapshot-readonly.css`. v2e-invest untouched.
- [ ] **Step 2: Full suite** `tests/test_publish.py tests/test_data_endpoints.py` green.
- [ ] **MANUAL VERIFY (pending):** `vivarium-dashboard-publish --workspace /Users/eranagmon/code/v2e-invest --out /tmp/ro-bundle && cd /tmp/ro-bundle && python -m http.server 8124` — open `/`: Investigations list loads (no 404), Sources/Registry/Composites tabs load from the static `.json`, **no Install/Run/New/Edit/PR controls**, **no Branches or Simulations DB tabs**. Network shows only `.json` fetches.
- [ ] **Step 3: Commit** — `test(publish): full read-only bundle golden`

---

## Self-Review
- Coverage: the five missing reads exported (Task 1) + routed (Task 2); authoring + workspace tabs hidden, Registry/Composites read-only (Task 3); verified on real workspace (Task 4). User decision honored (keep Investigations+Sources+read-only Registry/Composites; hide the rest).
- No placeholders: real builders + grounded file:lines. The registry export tolerates a sim-stack-less publish venv (empty, like the live endpoint).
- Names: `loadIsetList/loadInputs/loadCatalog/loadComposites/loadRegistry`; `_catalog_data`; `body.snapshot`; `snapshot-readonly.css`.

## Notes for the executor
- `.venv/bin/python -m pytest`. The control/tab/endpoint map (file:lines) is in the brainstorm grounding — follow it; don't rediscover.
- Local mode MUST stay identical: routing the reads through DataSource and adding `body.snapshot` (only set in snapshot mode) must not change local-server behavior. Test the existing suite stays green.
- Publish the bundle from a venv WITH the sim stack (e.g. v2ecoli/.venv + this branch on PYTHONPATH) to capture a real catalog/composites/registry; without it they export empty (graceful).
- Don't modify real v2e-invest; golden writes to tmp only.
