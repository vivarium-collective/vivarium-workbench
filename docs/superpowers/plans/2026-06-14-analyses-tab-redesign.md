# Analyses Tab Redesign — Implementation Plan

**Goal:** Repurpose the dashboard's **Analyses** tab from a *visualization-class catalog* into a gallery of **special, saved, interactive visualizations** — starting with an embedded **parsimony 3D viewer** (of a saved pack) and a first-class **PTools** card — while the class/registry browsing moves to **Registry → Visualizations**.

**Repo:** `vivarium-dashboard` (shared) — do on a branch → PR, no auto-merge.

**Confirmed decisions:**
- Parsimony viewer: **live embedded iframe** of a saved pack (like `bigraph-loom` in Composites).
- Saved pack: a **workspace artifact** written by the `parsimony-ecoli` composite (e.g. `studies/<name>/viz/3d/<name>.pack.json` + `.meta.json` + `meshes/`).
- PTools: **surface the existing Omics-Viewer launch** (`docs/ptools-launcher.md`) as an Analyses card.

---

## Current state (from exploration)

- Server: `vivarium_dashboard/server.py` (ThreadingHTTPServer, ~727 KB). Template: `templates/index.html.j2`. Frontend: vanilla JS in `static/walkthrough.js` + `static/data-source.js`. No build step.
- Analyses tab = `#page-visualizations` (label "Analyses"), template lines ~629-647. Lists viz **classes** via `GET /api/visualization-classes` → `server.py::_list_visualization_classes()` (~line 11853): pbg `Visualization` subclasses + `v2ecoli ANALYSIS_REGISTRY`.
- Registry tab (template ~672-748) already has Discovered sub-tabs incl. **Visualizations** and **Emitters** (`data-kind="visualization"/"emitter"`), backed by `_get_registry_data()` (~line 328).
- Static serving (`server.py` ~8075-8118): `/bigraph-loom/*` (iframe viewer for Composites, from `bigraph_loom.asset_dir()`), package `static/`, workspace tree, reports. MIME map includes `.tsv` (`text/tab-separated-values`) for PTools.
- PTools: `docs/ptools-launcher.md` — analyses write `studies/<name>/ptools/*.tsv`; `GET /api/ptools-launch/<study>?run=…` builds the Omics-Viewer URL; configured via `workspace.yaml: ui.ptools_server_url / ptools_omics_url_template / dashboard_public_base_url`. Button currently in the Runs table.
- Saved/embedded viz precedent: `studies/<name>/viz/*.html`, `spec["embed_visualizations"]` (~lines 1722-1737), `lib/viz_freshness.py`.

---

## Tasks

### Task 1 — Move the class catalog to Registry → Visualizations
The Registry already discovers + renders `visualization` and `emitter` kinds; ensure the Analyses class-list is fully represented there (incl. `v2ecoli ANALYSIS_REGISTRY` "analysis" kind), then drop the class picker from Analyses.
- `server.py`: make `_get_registry_data()` include analysis classes (kind `analysis`) the way `_list_visualization_classes()` does, OR add an `analysis` registry sub-tab. Reuse `_list_visualization_classes()` as the data source so nothing is lost.
- `templates/index.html.j2`: in the Registry Discovered sub-tabs (~732-738), add a tab `data-kind="analysis"` (label "Analyses") if analysis classes should be browsable there.
- Verify: Registry → Visualizations lists what Analyses used to; Registry → Emitters unchanged.

### Task 2 — Serve the parsimony 3D viewer assets (like bigraph-loom)
Add a static route so the dashboard serves the bundled viewer from `pbg_parsimony`.
- `server.py` (~8075, near the `/bigraph-loom/` handler): add `/parsimony-viewer/<file>` → serve from `pbg_parsimony` package viewer dir. Resolve via:
  ```python
  import importlib.util, pathlib
  spec = importlib.util.find_spec("pbg_parsimony")
  PARSIMONY_VIEWER_DIR = pathlib.Path(spec.origin).parent / "viewer"
  ```
  Serve `index.html`, `viewer.js`, `obj-worker.js` with correct MIME. Guard if `pbg_parsimony` not installed (feature-detect, like other optional integrations).
- The viewer loads its pack via `?file=` (the generic `pbg_parsimony` viewer.js already honours `?file=` and `window.PARSIMONY_PACK`). Saved packs are served from the workspace tree (already handled by the generic static handler).

### Task 3 — Discover saved 3D visualizations in the workspace
A "saved visualization" = a packed scene under `studies/<name>/viz/3d/*.pack.json` (+ `.meta.json` + sibling `meshes/`).
- `server.py`: add `GET /api/saved-visualizations` (or extend an existing study-scan) returning, per saved pack: `{study, name, pack_url, meta_url, n_placed, created}`. Scan `workspace/studies/*/viz/3d/*.pack.json`.
- `data-source.js`: add `loadSavedVisualizations()` mirroring `loadVisualizationClasses()` (snapshot vs live URL).

### Task 4 — Rebuild the Analyses page as a gallery
Replace the class picker in `#page-visualizations` with a gallery of special visualizations.
- `templates/index.html.j2` (~629-647): swap the `viz-picker-container` for a `#analyses-gallery` grid. Each card = title + description + an embedded/openable viewer.
- `walkthrough.js`: on the Analyses page switch, call `loadSavedVisualizations()` and render cards. For each saved 3D pack, embed:
  ```html
  <iframe class="viz-embed" src="/parsimony-viewer/index.html?file=/studies/<study>/viz/3d/<name>.pack.json" loading="lazy"></iframe>
  ```
  (mirror the bigraph-loom iframe pattern used in the Composites Explorer). Add a fullscreen/open-in-tab control.
- Keep a small "browse all visualization classes →" link pointing to Registry → Visualizations.

### Task 5 — PTools card in Analyses
Bring the existing Omics-Viewer launch into Analyses as a card.
- `walkthrough.js`: in the Analyses gallery, add a PTools card listing the study `ptools/*.tsv` files (reuse whatever the Runs table queries) with a "Launch in Pathway Tools Omics Viewer" button hitting the existing `GET /api/ptools-launch/<study>?run=…`.
- Show a configured/not-configured state from `workspace.yaml: ui.ptools_*` (the endpoint already encodes this).

### Task 6 — `parsimony-ecoli` composite writes the saved viz
Make the composite (in v2ecoli, `v2ecoli/structural/composite.py` / `build.py`) write its output where Analyses discovers it.
- `build_model(...)` already produces `pack.json` + `meta.json` + `meshes/`; add an `out_dir` default / option to target `studies/<study>/viz/3d/<name>/` so the dashboard finds it. (Assemble: pack [rounded/compact, ~45 MB], sidecar, meshes.)
- Document in v2ecoli: run the composite → refresh Analyses → the 3D cell appears embedded.

### Task 7 — Tests + docs
- `tests/`: a route test for `/parsimony-viewer/*` (served when pbg_parsimony present) and `/api/saved-visualizations` (scans a fixture study). Mirror `tests/test_ptools_launch.py` style.
- `docs/`: an `analyses-tab.md` describing the redesigned tab (gallery + parsimony 3D + PTools) and how saved visualizations are produced/discovered.

---

## Risks / notes
- `server.py` is huge — make minimal, localized additions near the existing static-route + study-scan code; don't refactor broadly.
- `pbg_parsimony` is an **optional** dep of the dashboard — feature-detect (the parsimony cards/routes appear only when it's importable), like other optional integrations.
- Large packs: serve the **rounded/compact** pack (~45 MB, gzipped by the server) — see the v2ecoli build (float-rounding step). Don't commit packs into the dashboard repo; they live in the workspace.
- Verify renders in a **normal GPU browser**, not headless (software-GL on ~370k instances is pathologically slow).
