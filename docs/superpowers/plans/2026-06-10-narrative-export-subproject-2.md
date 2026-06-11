# Narrative export / "publish" (read-only dashboard sub-project #2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** A `publish` CLI that exports a workspace's investigations/studies into a **self-contained static bundle** (per-resource JSON mirroring the API + per-study shells + assets + a snapshot-mode config), plus a `SnapshotSource` in the frontend — so you can open the bundle and browse the full narrative statically (results are placeholders until sub-project #3 wires sms-api).

**Architecture:** Factor pure data builders (`_iset_detail_data`, `_workspace_home_data`) the export + the live API share. The CLI walks `WorkspacePaths` (investigations + `iter_study_dirs`), writes `api/{workspace,iset/<id>,study/<slug>}.json`, copies `static/` → `bundle/assets/`, renders the home + per-study shells with asset/API URLs normalized to **root-absolute** (`/assets/…`, `/api/…json`), and writes `config.json` (`{mode:"snapshot", smsApiBase, repo, commit}`). `data-source.js` gains a `snapshot` mode that fetches the static JSON.

**Tech Stack:** Python 3.11 stdlib + Jinja2 + vanilla JS; pytest. Spec: `docs/superpowers/specs/2026-06-10-read-only-online-dashboard-design.md` (§6 #2). Builds on #1's `DataSource` seam (merged).

**Decisions (settled):** per-resource JSON files mirroring the API; per-study `studies/<slug>/index.html` shells; #2 delivers a working static viewer (export + SnapshotSource + bundle).

**Bundle layout (target):**
```
bundle/
├── index.html                  (home SPA shell)
├── studies/<slug>/index.html   (study shell, one per study)
├── assets/  (data-source.js, study-detail.js, walkthrough.js, client.js, *.css, ...)
├── api/
│   ├── workspace.json
│   ├── iset/<id>.json
│   └── study/<slug>.json
└── config.json
```

**Boundary (YAGNI):** narrative only. NO sms-api/results fetching (that's #3 — results render as a "loading from sms-api" placeholder), NO CI/deploy or CORS (that's #4), NO auth.

---

## File structure
- `vivarium_dashboard/server.py` — factor `_iset_detail_data(name)->dict|None` out of `_get_iset_detail` (~9311); factor `_workspace_home_data(ws_root)->dict` out of `render_workspace_report`/the home context; add `GET /api/workspace` (the #1 home follow-on, needed here). Keep the existing handlers calling the new pure builders.
- `vivarium_dashboard/publish.py` — NEW. The export logic + CLI (`python -m vivarium_dashboard.publish`). Pure bundle-builder functions for testability.
- `vivarium_dashboard/static/data-source.js` — add `snapshot` mode (static-JSON URLs).
- `pyproject.toml` — add the `vivarium-dashboard-publish` console script.
- Tests: `tests/test_publish.py` (NEW).

---

## Task 1: Pure data builders + `GET /api/workspace`

**Files:** Modify `server.py`; Test `tests/test_data_endpoints.py`.

- [ ] **Step 1: Failing test.** Reuse the tmp-workspace fixture from `tests/test_study_detail_page.py` (it has ≥1 investigation + study). Assert the pure builders return dicts and the new endpoint matches:
```python
def test_iset_detail_data_and_workspace_home_data(tmp_workspace):
    inv = server._first_investigation_name()          # helper or read from the fixture
    iset = server.Handler._iset_detail_data(inv)
    assert isinstance(iset, dict) and "studies" in iset
    home = server._workspace_home_data(server.WORKSPACE)
    assert isinstance(home, dict)
    body, code = server.Handler._build_api_workspace_response()
    assert code == 200 and json.loads(body) == json.loads(json.dumps(home, default=server._json_default))
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.** Extract the dict-building body of `_get_iset_detail` into a module/staticmethod `_iset_detail_data(name)` that returns the dict (or `None` on missing) — and have `_get_iset_detail` call it + `self._json(...)`. Extract the home context dict assembled in `render_workspace_report` into `_workspace_home_data(ws_root)` (the investigations list + branding + imports) — and have the renderer pass `_workspace_home_data(ws_root)` to the template. Add `_build_api_workspace_response()` (returns `_json_body(_workspace_home_data(WORKSPACE)),200`) + a `do_GET` branch `if path == "/api/workspace": ...` (mirror #1's `/api/config` branch, delegating to the builder).
- [ ] **Step 4: Run → pass.** **Step 5: Commit** — `feat(server): pure _iset_detail_data/_workspace_home_data builders + GET /api/workspace`

---

## Task 2: `data-source.js` snapshot mode

**Files:** Modify `vivarium_dashboard/static/data-source.js`; Test: structural.

- [ ] **Step 1: Implement** snapshot mode — when `cfg().mode === "snapshot"`, fetch the static JSON files; else same-origin `/api/*` (today's behavior):
```javascript
function _studyUrl(slug){ return cfg().mode === "snapshot"
  ? "/api/study/" + encodeURIComponent(slug) + ".json"
  : "/api/study/" + encodeURIComponent(slug); }
function _isetUrl(id){ return cfg().mode === "snapshot"
  ? "/api/iset/" + encodeURIComponent(id) + ".json"
  : "/api/iset/" + encodeURIComponent(id); }
function _workspaceUrl(){ return cfg().mode === "snapshot" ? "/api/workspace.json" : "/api/workspace"; }
// loadStudy/loadInvestigation/loadWorkspace call _get(_studyUrl(slug)) etc.
```
(Keep `loadStudy`/`loadInvestigation`/`loadWorkspace` names — #1 locked them.)
- [ ] **Step 2: Structural test** in `tests/test_data_endpoints.py`:
```python
def test_data_source_has_snapshot_mode():
    text = (server.STATIC_DIR / "data-source.js").read_text()
    for token in ['mode === "snapshot"', ".json", "_studyUrl", "_isetUrl", "_workspaceUrl"]:
        assert token in text
```
- [ ] **Step 3: Run → pass.** **Step 4: Commit** — `feat(static): data-source.js snapshot mode (static JSON URLs)`

---

## Task 3: The `publish` export (pure builder funcs + CLI)

**Files:** Create `vivarium_dashboard/publish.py`; modify `pyproject.toml`; Test `tests/test_publish.py`.

- [ ] **Step 1: Failing test** — `build_bundle(ws_root, out_dir)` produces the bundle and the JSON has parity with the builders:
```python
# tests/test_publish.py
import json
from pathlib import Path
from vivarium_dashboard import publish, server

def test_build_bundle_structure_and_parity(tmp_workspace, tmp_path):
    out = tmp_path / "bundle"
    summary = publish.build_bundle(server.WORKSPACE, out)
    assert (out / "index.html").is_file()
    assert (out / "config.json").is_file()
    assert (out / "assets" / "data-source.js").is_file()
    assert (out / "api" / "workspace.json").is_file()
    # at least one study + investigation exported, with parity + a per-study shell:
    slug = summary["studies"][0]
    assert (out / "api" / "study" / f"{slug}.json").is_file()
    assert (out / "studies" / slug / "index.html").is_file()
    assert json.loads((out / "api" / "study" / f"{slug}.json").read_text()) == \
           json.loads(json.dumps(server._study_detail_spec(slug), default=server._json_default))
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["mode"] == "snapshot" and "commit" in cfg
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `vivarium_dashboard/publish.py`:
  - `build_bundle(ws_root, out_dir) -> summary` that:
    1. enumerates investigations (`WorkspacePaths.load(ws_root).investigations` dirs with `investigation.yaml`) + studies (`iter_study_dirs`).
    2. writes `api/workspace.json` (`_workspace_home_data`), `api/iset/<id>.json` (`_iset_detail_data`), `api/study/<slug>.json` (`_study_detail_spec`) — JSON via `server._json_body`/`_json_default` (the same serializer as the API, so parity holds).
    3. copies `server.STATIC_DIR/*` → `out_dir/assets/`.
    4. renders the home shell → `out_dir/index.html` and a per-study shell → `out_dir/studies/<slug>/index.html`, each with `__DASH_CONFIG__ = {mode:"snapshot", ...}` and asset/API URLs **normalized to root-absolute** (`/assets/<name>`, `/api/...json`). Reuse the existing templates but rewrite `src=`/`href=` asset refs to `/assets/<basename>` and drop the embed; set the slug per study shell.
    5. writes `config.json` `{mode:"snapshot", smsApiBase:"", repo:<origin url or name>, commit:<git rev-parse HEAD>, generated_from_ref:<branch>}` (use `subprocess git rev-parse HEAD` / `git config --get remote.origin.url`; tolerate non-git → commit=null).
    6. returns `{investigations:[...], studies:[...], out:str}`.
  - `main(argv=None)` CLI: `--workspace` (default `.`), `--out` (required), prints the summary.
  - Add console script `vivarium-dashboard-publish = "vivarium_dashboard.publish:main"` to `pyproject.toml`.
- [ ] **Step 4: Run → pass.** **Step 5: Commit** — `feat(publish): build_bundle export (per-resource JSON + per-study shells + assets + config) + CLI`

---

## Task 4: Asset-URL normalization is correct (per-study shells resolve)

**Files:** Test `tests/test_publish.py`.

- [ ] **Step 1: Failing test** — every asset URL in EVERY rendered shell resolves to a file that exists in the bundle (the #1-style URL-resolution check, applied to the static bundle so a per-study `studies/<slug>/index.html` doesn't reference a broken relative path):
```python
import re
def test_bundle_shell_asset_urls_resolve(tmp_workspace, tmp_path):
    out = tmp_path / "bundle"; publish.build_bundle(server.WORKSPACE, out)
    shells = [out/"index.html"] + list(out.glob("studies/*/index.html"))
    for shell in shells:
        html = shell.read_text()
        for m in re.finditer(r'(?:src|href)="(/[^"]+\.(?:js|css))"', html):
            url = m.group(1).lstrip("/")
            assert (out / url).is_file(), f"{shell.name}: {m.group(1)} -> missing in bundle"
        # snapshot config present, no live /api embed:
        assert 'mode: "snapshot"' in html or '"mode":"snapshot"' in html
```
- [ ] **Step 2: Run → fail** (if any shell references a non-bundled or relative-broken asset). **Step 3: Fix** the URL normalization in `build_bundle` until green. **Step 4: Commit** — `test(publish): bundle shell asset URLs all resolve`

---

## Task 5: Golden on a real workspace + manual open

**Files:** Test `tests/test_publish.py` (skipif absent).

- [ ] **Step 1 (skipif `/Users/eranagmon/code/v2e-invest` absent):** `build_bundle("/Users/eranagmon/code/v2e-invest", tmp_out)`; assert it exports its real investigations + studies (≥1 of each), the JSON parity holds for one real study, all shell asset URLs resolve, `config.json.commit` is a real sha. READ-ONLY: write only to `tmp_out`; never modify v2e-invest.
- [ ] **Step 2: Full suite** `.venv/bin/python -m pytest tests/test_publish.py tests/test_data_endpoints.py -q` green. **Step 3: Commit** — `test(publish): real v2e-invest golden bundle`
- [ ] **MANUAL VERIFY (report as pending):** `python -m vivarium_dashboard.publish --workspace /Users/eranagmon/code/v2e-invest --out /tmp/ro-bundle`, then `cd /tmp/ro-bundle && python -m http.server 8123`, open `http://localhost:8123/` and a `studies/<slug>/` — confirm the narrative renders from the static JSON (Network shows `/api/study/<slug>.json` 200), results show the sms-api placeholder.

---

## Self-Review
- **Spec coverage (§6 #2):** per-resource JSON mirroring the API → Task 3; per-study shells → Task 3 (+ Task 4 resolves them); SnapshotSource → Task 2; the bundle (assets+shells+config) → Task 3; the working static viewer → Task 5 manual; `(repo, ref)` stamp → Task 3 config.json; the pure builders the export shares with the API → Task 1.
- **Placeholders:** none — real test code + concrete build steps. The sms-api results placeholder is an explicit #3 boundary, not a gap. `_iset_detail_data`/`_workspace_home_data` are factored in Task 1 before Task 3 uses them.
- **Type/name consistency:** `build_bundle(ws_root,out)->summary`; `_iset_detail_data(name)`, `_workspace_home_data(ws_root)`, `_build_api_workspace_response()`; `data-source.js` `_studyUrl/_isetUrl/_workspaceUrl` + the locked `loadStudy/loadInvestigation/loadWorkspace`; `config.json {mode,smsApiBase,repo,commit}`.

## Notes for the executor
- `.venv/bin/python -m pytest`. Reuse the `tests/test_study_detail_page.py` tmp-workspace fixture (sets `server.WORKSPACE`); add a small helper if you need the first investigation/study name from it.
- Ground the EXACT bodies of `_get_iset_detail` (~9311) and `render_workspace_report` (`lib/report.py` ~415) before factoring — extract the dict assembly, leave the I/O (`self._json`, `tpl.render`) in the callers.
- Asset normalization: study-detail.html uses root `/data-source.js`,`/style.css`,`/study-detail.js`; index.html.j2 uses relative `assets/...`. In the bundle, rewrite ALL to `/assets/<basename>` and put the files at `bundle/assets/`. The plotly CDN `<script>` stays as-is (external).
- The result-rendering placeholder is #3's concern — for #2, if a renderer tries to load results and there's no source, it should show a neutral "results available via sms-api (coming soon)" rather than error. Add that guard minimally where the chart/results renderers run, only if they currently error on missing data.
- Don't modify real v2e-invest; the golden writes to a tmp dir only.
