# Client-fetch seam (read-only dashboard sub-project #1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dashboard frontend *fetch* its narrative data through a `DataSource` layer instead of consuming Jinja-embedded `window._study`, establishing the seam the hosted/sms-api sub-projects plug into — with local mode behaving identically.

**Architecture:** Add uniform JSON GET endpoints that return exactly the dicts the templates embed today (`_study_detail_spec`, the existing `/api/iset/<id>`). Add a tiny frontend `data-source.js` that reads a source config (default: same-origin local server) and fetches those endpoints. Convert each page's template to a thin shell that loads `data-source.js`, then have the page's existing JS **populate `window._study` from a fetch when the Jinja embed is absent** — so the renderers are unchanged; only data *acquisition* changes.

**Tech Stack:** Python 3.11 stdlib `http.server` (`vivarium_dashboard/server.py`), Jinja2 templates (`vivarium_dashboard/templates/`), vanilla JS (`vivarium_dashboard/static/`), pytest. Spec: `docs/superpowers/specs/2026-06-10-read-only-online-dashboard-design.md` (§7).

**Boundary (YAGNI):** local-mode fetch decoupling only. NO static export, sms-api, auth, Docker, or `server.py` split (those are sub-projects #2–#4). No JS test harness is added (the repo has none); JS/template behavior is verified via Python endpoint/structure tests + explicit manual steps.

---

## File structure

- `vivarium_dashboard/server.py` — add `GET /api/study/<slug>` handler (returns `_study_detail_spec(slug)` as JSON); add `GET /api/config` (the source config). Modify `_render_study_detail_html` / the study-detail template render to stop embedding the full spec.
- `vivarium_dashboard/static/data-source.js` — NEW. The `DataSource` module (`loadStudy`/`loadInvestigation`/`loadWorkspace`) reading `window.__DASH_CONFIG__`.
- `vivarium_dashboard/templates/study-detail.html` — thin shell: drop `window._study = {{ study|tojson }}`, add `__DASH_CONFIG__` + `data-source.js` + a bootstrap that fetches then runs init.
- `vivarium_dashboard/static/study-detail.js` — at init, populate `window._study` via the DataSource when absent (renderers unchanged).
- `vivarium_dashboard/static/investigation-*.js` / the iset page shell — same conversion against the existing `/api/iset/<id>`.
- Tests: `tests/test_data_endpoints.py` (NEW), extend `tests/test_study_detail_page.py`.

**Pages converted this pass:** `study-detail` (worked in full below) + `investigation`/iset. **Home/index** converts identically once a `/api/workspace` data-builder is factored out of `render_workspace_report` — a fast follow, not blocking the seam; tracked at the end.

---

## Task 1: `GET /api/study/<slug>` JSON endpoint (parity with the embed)

**Files:**
- Modify: `vivarium_dashboard/server.py` (add a branch in `do_GET`'s `/api/...` ladder near the other `/api/study-*` branches ~line 6070; the handler method near `_get_study_detail_page` ~12029).
- Test: `tests/test_data_endpoints.py` (create).

- [ ] **Step 1: Write the failing test.** A study fixture on disk (mirror how `tests/test_study_detail_page.py` builds a tmp workspace + a `studies/<slug>/study.yaml`), then assert the new endpoint returns the SAME dict `_study_detail_spec` builds:

```python
# tests/test_data_endpoints.py
import json
from vivarium_dashboard import server

def test_api_study_returns_study_detail_spec(tmp_workspace, monkeypatch):
    # tmp_workspace: a fixture that sets server.WORKSPACE to a tmp dir containing
    # studies/demo/study.yaml (reuse the pattern in tests/test_study_detail_page.py).
    slug = "demo"
    expected = server._study_detail_spec(slug)
    assert expected is not None
    body, code = server.Handler._build_api_study_response(slug)   # pure builder (Step 3)
    assert code == 200
    assert json.loads(body) == json.loads(json.dumps(expected, default=server._json_default))
```

- [ ] **Step 2: Run → fail.** `\.venv/bin/python -m pytest tests/test_data_endpoints.py -q` → FAIL (`_build_api_study_response` not defined). Use the repo's venv: `.venv/bin/python`.

- [ ] **Step 3: Implement.** Add a small pure builder + a `do_GET` branch + a handler. Pure builder so it's testable without a live socket:

```python
# server.py — module-level or staticmethod near _get_study_detail_page
@staticmethod
def _build_api_study_response(slug):
    """Return (json_bytes, status) for GET /api/study/<slug>."""
    spec = _study_detail_spec(slug)
    if spec is None:
        return _json_body({"error": f"study not found: {slug}"}), 404
    return _json_body(spec), 200
```

In `do_GET`, alongside the other `/api/study-*` branches (~6070), BEFORE the `/api/study-charts/`-style prefixes so the exact path wins:

```python
        if self.path.split("?", 1)[0].startswith("/api/study/"):
            slug = self.path.split("?", 1)[0].split("/api/study/", 1)[-1].strip("/")
            if not _SLUG_RE.match(slug):
                return self._json({"error": "invalid slug"}, 400)
            body, code = self._build_api_study_response(slug)
            return self._send_bytes(body, code=code, content_type="application/json")
```

(If `_send_bytes`/an equivalent JSON-from-bytes sender doesn't exist, send via the existing `self._json(...)` path instead: `spec = _study_detail_spec(slug); return self._json(spec, 200)` — keep the pure `_build_api_study_response` for the test, and have the branch call `self._json`.)

- [ ] **Step 4: Run → pass.** `.venv/bin/python -m pytest tests/test_data_endpoints.py -q` → PASS.

- [ ] **Step 5: Commit.**
```bash
git add vivarium_dashboard/server.py tests/test_data_endpoints.py
git commit -m "feat(server): GET /api/study/<slug> returns the study-detail spec as JSON"
```

---

## Task 2: `GET /api/config` (the source config)

**Files:** Modify `vivarium_dashboard/server.py`; Test `tests/test_data_endpoints.py`.

- [ ] **Step 1: Failing test.**
```python
def test_api_config_defaults_to_local_server():
    body, code = server.Handler._build_api_config_response()
    assert code == 200
    assert json.loads(body) == {"mode": "local-server"}
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement.**
```python
@staticmethod
def _build_api_config_response():
    return _json_body({"mode": "local-server"}), 200
```
plus a `do_GET` branch: `if self.path.split("?",1)[0] == "/api/config": body, code = self._build_api_config_response(); return self._send_bytes(body, code=code, content_type="application/json")` (or `self._json({"mode":"local-server"}, 200)`).
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** — `feat(server): GET /api/config (source config; default local-server)`

---

## Task 3: Frontend `data-source.js`

**Files:** Create `vivarium_dashboard/static/data-source.js`; Test: structural (Python) + manual.

- [ ] **Step 1: Implement** the module (vanilla, no build step — matches the existing static JS):
```javascript
// vivarium_dashboard/static/data-source.js
(function (global) {
  function cfg() { return global.__DASH_CONFIG__ || { mode: "local-server" }; }
  async function _get(url) {
    const r = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!r.ok) throw new Error("fetch " + url + " -> " + r.status);
    return r.json();
  }
  const DataSource = {
    config: cfg,
    // local-server mode fetches the same-origin /api endpoints.
    async loadStudy(slug)        { return _get("/api/study/" + encodeURIComponent(slug)); },
    async loadInvestigation(id)  { return _get("/api/iset/" + encodeURIComponent(id)); },
    async loadWorkspace()        { return _get("/api/workspace"); },  // home; see end-of-plan note
  };
  global.DataSource = DataSource;
})(window);
```
- [ ] **Step 2: Structural test** (the file is served + shaped right) in `tests/test_data_endpoints.py`:
```python
def test_data_source_js_is_served_and_defines_loaders():
    text = (server.STATIC_DIR / "data-source.js").read_text()
    for token in ["window.DataSource", "loadStudy", "loadInvestigation", "/api/study/", "/api/iset/", "__DASH_CONFIG__"]:
        assert token in text
```
- [ ] **Step 3: Run → pass.** **Step 4: Commit** — `feat(static): data-source.js DataSource layer (local-server mode)`

---

## Task 4: Convert the study-detail page to fetch

**Files:** Modify `vivarium_dashboard/templates/study-detail.html` (~line 1986 embed) + `vivarium_dashboard/static/study-detail.js` (init ~line 20-25) + `_render_study_detail_html` in `server.py`; Test: extend `tests/test_study_detail_page.py`.

- [ ] **Step 1: Failing test** — the served study page no longer embeds the full spec but DOES carry the slug + config + the data-source bootstrap:
```python
# tests/test_study_detail_page.py (add)
def test_study_page_is_a_fetch_shell_not_an_embed(tmp_workspace):
    html = server._render_study_detail_html("demo", server._study_detail_spec("demo"))
    assert "window.__DASH_CONFIG__" in html
    assert "data-source.js" in html
    assert 'window._studyName = "demo"' in html or "window._studyName='demo'" in html
    # the heavy spec is fetched, not embedded:
    assert "window._study = {" not in html and "window._study={" not in html
```
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3a: Template** — in `templates/study-detail.html`, REPLACE the `window._study = {{ study|tojson }};` line (~1986) with the shell bootstrap (keep `window._studyName`):
```html
<script>window.__DASH_CONFIG__ = { mode: "local-server" };</script>
<script src="/static/data-source.js"></script>
<script>
  window._studyName = "{{ name }}";
  window._study = null;  // populated by the bootstrap below
</script>
```
- [ ] **Step 3b: JS init** — at the top of `static/study-detail.js`'s init (where it currently calls `loadTestsTab(window._study)` ~line 20), wrap init so it fetches first when `window._study` is absent:
```javascript
async function _bootstrapStudy() {
  if (!window._study && window.DataSource && window._studyName) {
    try { window._study = await window.DataSource.loadStudy(window._studyName); }
    catch (e) { _showStudyLoadError(e); return false; }
  }
  return !!window._study;
}
function _showStudyLoadError(e) {
  var el = document.getElementById('study-root') || document.body;
  el.innerHTML = '<div class="error">Could not load study data: ' + String(e && e.message || e) + '</div>';
}
// existing init body (loadTestsTab(window._study), _loadConclusionsTab(window._study), ...)
// is moved into _runStudyInit(); the entry becomes:
document.addEventListener('DOMContentLoaded', async function () {
  if (await _bootstrapStudy()) { _runStudyInit(); }
});
```
(Wrap the current top-level init statements into `function _runStudyInit() { ... }`. The renderers stay byte-identical — they still read `window._study`.)
- [ ] **Step 3c: server.py** — `_render_study_detail_html` must still pass `name` to the template (it already does) and the template no longer needs the full `study` for the embed (it's fetched). Leave `study` available to the template for any server-side bits that still use it, but remove the `window._study` embed line per Step 3a.
- [ ] **Step 4: Run → pass** the Python structural test. **MANUAL VERIFY:** `vivarium-dashboard serve --workspace <a real workspace>`, open `/studies/<slug>`, confirm the page renders identically (tests/charts/conclusions/feedback all populate) and the Network tab shows a `GET /api/study/<slug>` 200.
- [ ] **Step 5: Commit** — `feat(study-detail): fetch study data via DataSource instead of Jinja embed`

---

## Task 5: Convert the investigation (iset) page to fetch

**Files:** the iset page template + its JS (find via `grep -rl "iset" templates/ static/`); reuse the existing `GET /api/iset/<id>`. Test: structural.

- [ ] **Step 1: Failing test** — the iset page shell carries config + data-source.js + the iset id and fetches `/api/iset/<id>` rather than embedding the resolved investigation. (Mirror Task 4's structural assertions for the iset template/JS.)
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — same pattern as Task 4: template becomes a shell (`__DASH_CONFIG__` + `data-source.js` + the iset id); the iset JS bootstraps via `window.DataSource.loadInvestigation(id)` then runs its existing render. `/api/iset/<id>` already returns the right JSON (`_get_iset_detail`), so NO new endpoint.
- [ ] **Step 4: Run → pass** + **MANUAL VERIFY** the investigation page renders identically and fetches `/api/iset/<id>`.
- [ ] **Step 5: Commit** — `feat(iset): fetch investigation data via DataSource`

---

## Task 6: Source-overridable proof (the seam works)

**Files:** Test `tests/test_data_endpoints.py` + a short doc note.

- [ ] **Step 1:** Add a test documenting/asserting the contract the later sub-projects rely on: `/api/study/<slug>`, `/api/iset/<id>`, `/api/workspace`, `/api/config` are the four data seams; `data-source.js` routes through `__DASH_CONFIG__`. Assert all four route-strings appear in `data-source.js` and that the endpoints exist (the builders return 200/404 shapes). This locks the interface so `SnapshotSource`/`SmsApiResultsSource` (sub-projects #2/#3) can swap in.
```python
def test_data_source_interface_is_stable():
    text = (server.STATIC_DIR / "data-source.js").read_text()
    for route in ["/api/study/", "/api/iset/", "/api/workspace", "__DASH_CONFIG__"]:
        assert route in text
    assert server.Handler._build_api_study_response("does-not-exist")[1] == 404
```
- [ ] **Step 2: Run → pass.** **Step 3: Commit** — `test: lock the DataSource interface (study/iset/workspace/config)`

---

## Home/index follow-on (same pattern, after `/api/workspace` is factored)
The home page (`index.html.j2` via `render_workspace_report`) converts identically once its data is exposed as `GET /api/workspace`. That requires factoring the home-data assembly out of `render_workspace_report` into a pure `_workspace_home_data()` builder, then a `_build_api_workspace_response` + a `do_GET` branch + a shell conversion (Task-4 pattern). Kept out of the worked tasks above because the home-data builder isn't cleanly factored today; it's a small, mechanical follow that doesn't block the seam. `loadWorkspace()` already targets `/api/workspace` so the frontend is ready.

---

## Self-Review
- **Spec coverage (§7):** uniform JSON endpoints → Tasks 1,2 (+home note); `data-source.js` frontend layer → Task 3; thin page shells + fetch → Tasks 4,5; source config → Task 2/3; transitional fallback (`if (!window._study) fetch`) → Task 4 Step 3b; error handling (`_showStudyLoadError`) → Task 4; testing (parity Task 1, render-from-fetch manual Tasks 4/5, source-overridable Task 6, no-regression = existing suite) → covered; boundary (no export/sms-api/auth/split) → honored.
- **Placeholders:** none — real test code + real impl shown. The one honestly-deferred item (home `/api/workspace`) is explicitly scoped as a documented follow with the exact remaining steps, not a vague TODO.
- **Type/name consistency:** `_build_api_study_response(slug)->(bytes,int)`, `_build_api_config_response()`, `window.DataSource.{loadStudy,loadInvestigation,loadWorkspace}`, `__DASH_CONFIG__`, `window._studyName`/`window._study` used consistently across tasks.

## Notes for the executor
- Use the repo venv: `.venv/bin/python -m pytest`. If `.venv` is absent, `uv venv .venv && uv pip install -e .`.
- Reuse the tmp-workspace fixture pattern from `tests/test_study_detail_page.py` (sets `server.WORKSPACE`); don't invent a new one.
- The renderers (`study-detail.js` body, walkthrough/iset JS) MUST stay behavior-identical — only data *acquisition* changes. If a renderer is found reading data from somewhere other than `window._study`, surface it rather than rewire it.
- `_SLUG_RE`, `_json_body`, `_json_default`, `self._json`, `STATIC_DIR` already exist in `server.py` — reuse them; don't redefine.
- Manual verification is REQUIRED for the JS/template tasks (no JS harness): serve a real workspace and confirm byte-identical rendering + the expected `/api/*` fetches in the Network tab.
