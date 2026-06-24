# FastAPI builder-backed routes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Migrate 6 more read routes from the stdlib `server.py` handler to the typed FastAPI seam by extracting their data builders (+ server-local helpers) into `lib/` and adding pydantic-typed routes.

**Architecture:** Each route's builder (`_<name>_data` in `server.py`) plus the server-local helpers it calls are MOVED into a focused `lib/` module, made `ws_root`-parameterized (no `WORKSPACE` global). `server.py` re-imports them so its existing handlers/call-sites are unchanged (one implementation). A pydantic model is added to `lib/models.py`; a `@app.get(..., response_model=...)` route is added to `api/app.py`. Composites is special-cased: its discovery must run in a subprocess (generator discovery is broken in the long-running server — SP2b), so the route calls a stdlib-only subprocess launcher in `lib/`.

**Tech Stack:** Python stdlib `http.server` (server.py), FastAPI + pydantic v2, pytest.

## Global Constraints

- Worktree `/Users/eranagmon/code/vdash-fastapi-routes`, branch `feat/fastapi-builder-routes` (stacked on `feat/fastapi-more-routes` / PR #305).
- Run tests: `cd /Users/eranagmon/code/vdash-fastapi-routes && unset VIRTUAL_ENV && uv run pytest tests/test_api_app.py tests/test_payload_models.py -q` (this worktree's `.venv` has the FastAPI deps; if `uv run` is hijacked by a stray `VIRTUAL_ENV`, use the worktree's `.venv/bin/python` directly).
- The FastAPI app (`api/app.py`) must import from `lib/` ONLY — never `vivarium_dashboard.server` (it would couple the typed app to the legacy module).
- Extracted lib functions are `ws_root`-parameterized: take `ws_root: Path`, never read the `WORKSPACE` global.
- `server.py` keeps its existing public function names by re-importing the moved functions (e.g. `from vivarium_dashboard.lib.X import _name_data` as an alias) so all stdlib call-sites keep working — verify with `python -m py_compile` and a parity check.
- Pydantic models use `model_config = ConfigDict(extra="allow")` for any payload whose item fields vary (composites/catalog/registry entries, investigation rows).
- mypy stays scoped: add each new `lib/` module to `[tool.mypy] files` only if it type-checks cleanly; otherwise leave it out (the app.py route still gets typed via the model).
- After each route: `GET /openapi.json` must list the new path + model; `GET /docs` stays 200.

---

### Task 1: `/api/composite-resolve` → `lib/composite_resolve.py`

**Files:**
- Create: `vivarium_dashboard/lib/composite_resolve.py`
- Modify: `vivarium_dashboard/server.py` (move `_composite_resolve_data` + helpers `_derive_module_from_spec_id`, `_emitter_tag`; re-import), `vivarium_dashboard/lib/models.py`, `vivarium_dashboard/api/app.py`
- Test: `tests/test_api_app.py`

**Extract:** `_composite_resolve_data(spec_id)` and its server-local helpers `_derive_module_from_spec_id`, `_emitter_tag` into `lib/composite_resolve.py` as `resolve_composite(ws_root: Path, spec_id: str) -> dict | None`. `_ws_add_to_sys_path()` stays in server.py — replicate the minimal sys.path setup the builder needs inside the lib fn (add `ws_root` + its package to `sys.path`), or accept a small `ws_add_to_sys_path(ws_root)` helper moved alongside. server.py re-imports `resolve_composite` and keeps `_composite_resolve_data = lambda sid: resolve_composite(WORKSPACE, sid)`.

**Model (`lib/models.py`):** `CompositeResolvePayload(BaseModel)` with `model_config = ConfigDict(extra="allow")` and the stable keys the builder returns (inspect `_composite_resolve_data`'s return dict: typically `id`/`document`/`module`/`emitter`/`svg` — model the ones always present, `extra="allow"` for the rest).

**Route (`api/app.py`):** `@app.get("/api/composite-resolve", response_model=Optional[CompositeResolvePayload])` taking `ref: str` query param + `ws: Path = Depends(get_workspace)`; returns `resolve_composite(ws, ref)` (null on miss → 200 with null body, mirroring the stdlib route).

- [ ] **Step 1: Write the failing test** — `tests/test_api_app.py`: `/api/composite-resolve?ref=missing` on an empty workspace returns 200 with `null` (or `{}`); the route + model appear in `/openapi.json`.
- [ ] **Step 2: Run → fails** (route undefined). `uv run pytest tests/test_api_app.py -q`
- [ ] **Step 3: Extract the builder + helpers to `lib/composite_resolve.py`; re-import in server.py.**
- [ ] **Step 4: Add the model + route.**
- [ ] **Step 5: Run tests → pass; `py_compile server.py`; parity-check `_composite_resolve_data` still resolves.**
- [ ] **Step 6: Commit** `feat(api): typed /api/composite-resolve (extract resolver to lib)`

---

### Task 2: `/api/visualization-classes` → `lib/visualization_classes.py`

**Files:** Create `lib/visualization_classes.py`; modify `server.py` (move `_visualization_classes_data` + helpers `_is_viz`, `_list_visualization_classes`; `_normalize_requirements` is shared — see Task 5, for now COPY a private copy into this module or move to a shared `lib/_spec_norm.py`), `lib/models.py`, `api/app.py`; test `tests/test_api_app.py`.

**Extract:** `_visualization_classes_data(ws_root)` + `_is_viz`, `_list_visualization_classes` into `lib/visualization_classes.py` as `list_visualization_classes(ws_root: Path) -> dict`. `_normalize_requirements` and `_ws_add_to_sys_path`: move `_normalize_requirements` to a new shared `lib/spec_norm.py` (Task 5 will reuse it) and import it; replicate the sys.path setup as in Task 1.

**Model:** `VisualizationClassesPayload(BaseModel)` (`extra="allow"`) with the wrapper key the builder returns (likely `{"classes": [...]}`); item model `VizClass(BaseModel, extra="allow")` with stable keys.

**Route:** `@app.get("/api/visualization-classes", response_model=VisualizationClassesPayload)`.

- [ ] Steps 1–6 mirror Task 1 (failing test → extract → model+route → green + py_compile + commit). Commit: `feat(api): typed /api/visualization-classes`.

---

### Task 3: `/api/registry` → `lib/registry.py`

**Files:** Create `lib/registry.py`; modify `server.py` (move `_get_registry_data` + helpers `_apply_registry_include_filter`, `_classify_source`, `_mark_default_emitter`, `_registry_imports_meta` + the module-level `_REGISTRY_CACHE` it uses), `lib/models.py`, `api/app.py`; test.

**Extract:** `_get_registry_data(bypass_cache=False)` reads the `WORKSPACE` global + `_REGISTRY_CACHE`. Move it as `build_registry(ws_root: Path, *, bypass_cache: bool = False) -> dict` into `lib/registry.py`, moving the 4 helpers + a module-level cache. server.py re-imports and keeps `_get_registry_data` delegating to `build_registry(WORKSPACE, ...)`; the cache-invalidation in `_invalidate_workspace_caches` must clear the lib cache (import + clear it there).

**Model:** `RegistryPayload(BaseModel, extra="allow")` with the wrapper key (likely `{"modules": [...], "types": [...]}` or similar — inspect the return); item models `extra="allow"`.

**Route:** `@app.get("/api/registry", response_model=RegistryPayload)`.

- [ ] Steps 1–6 mirror Task 1. Watch the cache: the lib cache must be cleared on workspace switch. Commit: `feat(api): typed /api/registry`.

---

### Task 4: `/api/composites` (subprocess) → `lib/composites_query.py`

**Files:** Create `lib/composites_query.py`; modify `server.py` (move the stdlib-only subprocess launcher `_composites_data_subprocess` to lib; `_get_composites` + `_composites_data` stay in server.py), `lib/models.py`, `api/app.py`; test.

**Extract:** Move `_composites_data_subprocess(ws_root) -> dict | None` (the start/end-fenced subprocess launcher — stdlib `subprocess`/`json`/`sys` only; the child script imports `vivarium_dashboard.server`) into `lib/composites_query.py` as `composites_via_subprocess(ws_root: Path) -> dict | None`. server.py's `_get_composites` re-imports it. The FastAPI route calls it; falls back to `{"composites": [], "error": "discovery unavailable"}` if the subprocess returns None (the app must NOT import server, so no in-process fallback in app.py).

**Model:** `CompositesPayload(BaseModel)` with `composites: list[CompositeRecord]`, `workspace_package: Optional[str] = None`, `error: Optional[str] = None`; `CompositeRecord(BaseModel, extra="allow")` with stable keys `id`/`name`/`kind`/`module` (+ `extra="allow"` for the rest — generators carry varied fields).

**Route:** `@app.get("/api/composites", response_model=CompositesPayload)` → `composites_via_subprocess(ws)` (or the empty+error payload).

- [ ] **Step 1: failing test** — mock `composites_via_subprocess` to return a 2-composite payload (1 spec + 1 generator); assert the route validates it through `CompositesPayload` and both kinds survive (extra fields preserved). Also: subprocess returns None → route returns `{composites: [], error: ...}`.
- [ ] Steps 2–6 mirror Task 1. Commit: `feat(api): typed /api/composites (subprocess-isolated discovery)`.

---

### Task 5: `/api/investigations` → `lib/investigations_index.py`

**Files:** Create `lib/investigations_index.py`; modify `server.py` (move `_investigations_data` + the 8 helpers: `_conclusions_excerpt`, `_condition_satisfied`, `_count_runs_for_study`, `_format_baseline_source`, `_http_get_json`, `_iter_study_dirs`, `_normalize_parents`, `_normalize_requirements`), `lib/models.py`, `api/app.py`; test.

**Extract:** Move `_investigations_data(ws_root) -> dict` + its 8 server-local helpers into `lib/investigations_index.py`, `ws_root`-parameterized. `_normalize_requirements` is shared with Task 2 — put it in `lib/spec_norm.py` (created in Task 2) and import from there in both. Some helpers may themselves call `load_spec`/`normalize_dag_edges` (already in `lib/investigations.py`) — import those. `_http_get_json` (used for cross-worktree registry enrichment) — keep its network call best-effort (swallow errors) so the route never hangs/500s. server.py re-imports `_investigations_data` (delegating to the lib fn with WORKSPACE).

**Model:** `InvestigationRow(BaseModel, extra="allow")` (the row has ~26 keys — model the stable scalar/count ones: `name`/`status`/`phase`/`n_studies`/`n_simulations`/etc., `extra="allow"` for the rest, incl. the invalid-row shape `{name,status:'invalid',error}`); `InvestigationsPayload(BaseModel)` with `investigations: list[InvestigationRow]`.

**Route:** `@app.get("/api/investigations", response_model=InvestigationsPayload)`.

- [ ] **Step 1: failing test** — empty workspace → `{"investigations": []}`, 200; route + model in `/openapi.json`. Plus a test that a mocked `_investigations_data` row (incl. an `invalid` row) validates through the model.
- [ ] Steps 2–6 mirror Task 1, but EXPECT a larger extraction; run a parity check (`_investigations_data(WORKSPACE)` identical before/after on a real workspace). Commit: `feat(api): typed /api/investigations (extract index builder to lib)`.

---

### Task 6: `/api/catalog` → `lib/catalog.py`

**Files:** Create `lib/catalog.py`; modify `server.py` (move `_catalog_data` + the 11 helpers: `_build_override_catalog`, `_build_reexport_origin_modules`, `_check_installed_module_sync`, `_dedupe_alias_composites` (shared — see note), `_detect_workspace_venv_distributions`, `_filter_catalog_modules`, `_lr`, `_name_variants`, `_read_workspace_pyproject_deps`, `_registry_modules_override`), `lib/models.py`, `api/app.py`; test.

**Extract:** Move `_catalog_data(ws_root) -> dict` + its helpers into `lib/catalog.py`, `ws_root`-parameterized. `_dedupe_alias_composites` is also used by `_composites_data` (server.py) — move it to `lib/composite_lookup.py` (where `discover_all_composites` lives) and import from there in both server.py and `lib/catalog.py` to avoid duplication. Some helpers inspect the venv / pyproject — keep them best-effort (swallow errors → empty result), never 500.

**Model:** `CatalogPayload(BaseModel)` with `modules: list[CatalogModule]`; `CatalogModule(BaseModel, extra="allow")` with stable keys (`name`/`installed`/`install_source`/`module`/`description`, `extra="allow"` for the rest).

**Route:** `@app.get("/api/catalog", response_model=CatalogPayload)`.

- [ ] **Step 1: failing test** — empty workspace → `{"modules": []}` or the workspace's own package only, 200; route + model in `/openapi.json`.
- [ ] Steps 2–6 mirror Task 1; largest extraction — parity check on a real workspace. Commit: `feat(api): typed /api/catalog (extract catalog builder to lib)`.

---

## Notes for the executor
- Order is easy→hard (composite-resolve, viz-classes, registry, composites, investigations, catalog) so the pattern + the shared `lib/spec_norm.py` / `lib/composite_lookup` dedupe-move are established before the heavy tasks.
- Each task's reviewer must confirm: (a) `api/app.py` imports `lib` only (no `server`); (b) the lib fn is `ws_root`-parameterized; (c) server.py still compiles + the old builder name still works (parity); (d) the new route + model are in `/openapi.json` and `/docs` is 200.
- After all 6: a final whole-branch review, then open the PR stacked on #305.
