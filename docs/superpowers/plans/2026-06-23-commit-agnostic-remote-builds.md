# Commit-agnostic Dashboard — Remote sms-api Build Source (SP3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add remote sms-api simulator builds to the dashboard's source dropdown — select a build → download its `repo@commit` workspace (once, cached) → re-point to it via SP2, browsing it as a full local workspace.

**Architecture:** Extend `SmsApiClient` to list builds + download SP1's `workspace.tar.gz`; a new `lib/remote_build_source.py` materializes a build into a per-commit local cache (extract + strip GitHub's top dir + reuse); two server endpoints (`GET /api/source/builds`, `POST /api/source/switch-build`) list builds and switch to a materialized build via SP2's `_switch_active_workspace`; the SP2 dropdown gains a "Builds" `<optgroup>`.

**Tech Stack:** Python stdlib (`urllib`, `tarfile`, `shutil`, `os`), the existing `Handler` (stdlib `http.server`), SP2's re-pointing machinery, vanilla JS, pytest.

## Global Constraints

- **Lazy materialize** — list builds from cheap metadata (`/core/v1/simulator/versions`); download a build's tarball ONLY when that build is selected. (Spec §Decision 1.)
- **Cache by commit, reuse immutably** — extract to `<build_cache_root>/sim<id>-<commit>/`; reuse the dir if present (a `repo@commit` is immutable). (Spec §Decision 2.)
- **Strip GitHub's single top-level `<org>-<repo>-<sha>/` dir** so the cache dir is the workspace root. (Spec §Decision 3.)
- **Degrade gracefully** — sms-api unreachable → `/api/source/builds` returns `{"builds": [], "error": <str>}`; never crash. (Spec §Decision 4.)
- **Reuse SP2 for the switch** — after materializing, call `_switch_active_workspace(cache_dir)` directly; the cache dir is server-created/trusted, so it bypasses the user-path catalog allow-list. (Spec §Decision 5.)
- **Failure leaves the active workspace unchanged** — materialize fully (download+extract) BEFORE `_switch_active_workspace`; on failure return 502 and do not switch. (Spec §Error handling.)
- **Build cache root is env-overridable** (`VIVARIUM_DASHBOARD_BUILD_CACHE`, default `~/.pbg/build-cache`) so tests never touch real `$HOME`.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `vivarium_dashboard/lib/sms_api_client.py` | modify | `list_simulators()` + `download_workspace()` |
| `vivarium_dashboard/lib/remote_build_source.py` | create | cache root, `materialize_build`, `list_build_sources` |
| `vivarium_dashboard/server.py` | modify | `GET /api/source/builds` + `POST /api/source/switch-build` handlers + routing |
| `vivarium_dashboard/static/source-switch.js` | modify | Builds `<optgroup>` + `_switchBuild` |
| `tests/test_remote_build_source.py` | create | client, materialize/cache, endpoints, dropdown |

---

## Task 1: `SmsApiClient` — `list_simulators` + `download_workspace`

**Files:**
- Modify: `vivarium_dashboard/lib/sms_api_client.py`
- Test: `tests/test_remote_build_source.py`

**Interfaces:**
- Consumes: existing `SmsApiClient._get(path, params)`, the `Request`/`urlopen`/`shutil`/`SmsApiError` already imported in the module.
- Produces: `list_simulators() -> dict` (`{"versions": [...]}`); `download_workspace(simulator_id: int, dest_dir: Path) -> Path` (writes `dest_dir/workspace.tar.gz`, returns it).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_remote_build_source.py
import io
import json
from pathlib import Path

import pytest

from vivarium_dashboard.lib import sms_api_client as sac


class _Resp:
    """Minimal urlopen() context-manager response."""
    def __init__(self, body: bytes):
        self._body = body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self):
        return self._body


def test_list_simulators_hits_versions_endpoint(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Resp(json.dumps({"versions": [{"database_id": 1}]}).encode())

    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    out = sac.SmsApiClient("http://x").list_simulators()
    assert out == {"versions": [{"database_id": 1}]}
    assert seen["url"] == "http://x/core/v1/simulator/versions"


def test_download_workspace_streams_to_file(monkeypatch, tmp_path):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Resp(b"TARBALLBYTES")

    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    out = sac.SmsApiClient("http://x").download_workspace(45, tmp_path)
    assert out == tmp_path / "workspace.tar.gz"
    assert out.read_bytes() == b"TARBALLBYTES"
    assert seen["url"] == "http://x/api/v1/simulations/workspace?simulator_id=45"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/eranagmon/code/vdash-sp3 && unset VIRTUAL_ENV && .venv/bin/python -m pytest tests/test_remote_build_source.py -q`
Expected: FAIL — `AttributeError: 'SmsApiClient' object has no attribute 'list_simulators'`.

- [ ] **Step 3: Add the two methods to `SmsApiClient`**

Add inside the `SmsApiClient` class (near `simulator_status`):

```python
    def list_simulators(self) -> dict:
        """GET /core/v1/simulator/versions — all registered simulator builds."""
        return self._get("/core/v1/simulator/versions")

    def download_workspace(self, simulator_id: int, dest_dir: Path) -> Path:
        """Stream a build's repo@commit workspace tarball (SP1's endpoint) to
        dest_dir/workspace.tar.gz."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / "workspace.tar.gz"
        url = f"{self.base_url}/api/v1/simulations/workspace?simulator_id={simulator_id}"
        req = Request(url, method="GET", headers={"Accept": "application/gzip"})
        try:
            with urlopen(req, timeout=self.timeout) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}") from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        return out_path
```

(`Request`, `urlopen`, `HTTPError`, `URLError`, `shutil`, `Path` are already imported at the top of the file — verify with `grep -nE "^from urllib|^import shutil|^from pathlib" vivarium_dashboard/lib/sms_api_client.py` and only add an import if one is genuinely missing.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remote_build_source.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/sms_api_client.py tests/test_remote_build_source.py
git commit -m "feat(remote-build): SmsApiClient list_simulators + download_workspace"
```

---

## Task 2: `lib/remote_build_source.py` — materialize, cache, list

**Files:**
- Create: `vivarium_dashboard/lib/remote_build_source.py`
- Test: `tests/test_remote_build_source.py`

**Interfaces:**
- Consumes: a client with `list_simulators()` + `download_workspace(sim_id, dest)` (Task 1 — but injectable for tests).
- Produces:
  - `build_cache_root() -> Path`
  - `cache_dir_for(simulator_id: int, commit: str) -> Path`
  - `materialize_build(client, simulator_id: int, commit: str, *, force: bool = False) -> Path`
  - `list_build_sources(client) -> dict` (`{"builds": [...], "error": str|None}`, each build `{simulator_id, repo, commit, branch, label}`)

- [ ] **Step 1: Write the failing tests**

```python
import os
import tarfile

from vivarium_dashboard.lib import remote_build_source as rbs


def _make_tarball(path, top="org-repo-abc1234"):
    """A GitHub-style tarball: one top-level dir containing workspace.yaml."""
    import io
    with tarfile.open(path, "w:gz") as tar:
        data = b"name: built-ws\n"
        info = tarfile.TarInfo(f"{top}/workspace.yaml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


class _FakeClient:
    def __init__(self, tarball_src):
        self._src = tarball_src
        self.downloads = 0

    def download_workspace(self, simulator_id, dest_dir):
        from pathlib import Path
        import shutil
        self.downloads += 1
        dest = Path(dest_dir) / "workspace.tar.gz"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self._src, dest)
        return dest

    def list_simulators(self):
        return {"versions": [
            {"database_id": 45, "git_repo_url": "https://github.com/org/v2ecoli",
             "git_commit_hash": "32b901", "git_branch": "main"},
        ]}


@pytest.fixture
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("VIVARIUM_DASHBOARD_BUILD_CACHE", str(tmp_path / "bc"))
    return tmp_path


def test_materialize_extracts_and_strips_top_dir(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)
    cache = rbs.materialize_build(client, 45, "32b901")
    assert cache == rbs.cache_dir_for(45, "32b901")
    assert (cache / "workspace.yaml").read_text() == "name: built-ws\n"   # top dir stripped


def test_materialize_reuses_cache(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)
    rbs.materialize_build(client, 45, "32b901")
    rbs.materialize_build(client, 45, "32b901")   # second call
    assert client.downloads == 1                  # reused, not re-downloaded


def test_list_build_sources_maps_and_labels():
    client = _FakeClient(None)
    out = rbs.list_build_sources(client)
    assert out["error"] is None
    b = out["builds"][0]
    assert b["simulator_id"] == 45 and b["commit"] == "32b901"
    assert b["label"] == "v2ecoli @ 32b901 (build #45)"


def test_list_build_sources_degrades_on_error():
    class _Boom:
        def list_simulators(self):
            from vivarium_dashboard.lib.sms_api_client import SmsApiError
            raise SmsApiError("tunnel down")
    out = rbs.list_build_sources(_Boom())
    assert out["builds"] == [] and "tunnel down" in out["error"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_remote_build_source.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.remote_build_source'`.

- [ ] **Step 3: Create `lib/remote_build_source.py`**

```python
"""Materialize a remote sms-api simulator build into a local workspace cache.

A build is a repo@commit; SP1's GET /api/v1/simulations/workspace streams it as
a gzipped tarball (GitHub's repo tarball). We download it once, extract it,
strip GitHub's single top-level `<org>-<repo>-<sha>/` dir, and cache it by
commit (immutable → reusable). The dashboard then re-points (SP2) to the cache
dir and serves the build as a full local workspace.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from vivarium_dashboard.lib.sms_api_client import SmsApiError


def build_cache_root() -> Path:
    """Root dir for materialized build workspaces (env-overridable for tests)."""
    env = os.environ.get("VIVARIUM_DASHBOARD_BUILD_CACHE")
    return Path(env) if env else Path.home() / ".pbg" / "build-cache"


def cache_dir_for(simulator_id: int, commit: str) -> Path:
    return build_cache_root() / f"sim{simulator_id}-{commit}"


def materialize_build(client: Any, simulator_id: int, commit: str, *, force: bool = False) -> Path:
    """Return a local workspace dir for the build, downloading+extracting once.

    Reuses the per-commit cache dir if present (immutable repo@commit). Extracts
    under the cache root (same filesystem) then os.replace()s into place, so a
    partial download never leaves a half-written cache.
    """
    cache = cache_dir_for(simulator_id, commit)
    if cache.exists() and not force:
        return cache

    root = build_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".staging-sim{simulator_id}-", dir=root))
    try:
        tar_path = client.download_workspace(simulator_id, staging)
        extract_root = staging / "extract"
        extract_root.mkdir()
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(extract_root, filter="data")  # noqa: S202 — trusted internal artifact

        # GitHub wraps everything in one top-level dir; lift it so the cache dir
        # is the workspace root. Fall back to the extract root if the shape is
        # unexpected (not exactly one top-level dir).
        entries = [p for p in extract_root.iterdir() if not p.name.startswith(".")]
        src = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_root

        if cache.exists():
            shutil.rmtree(cache)
        os.replace(str(src), str(cache))  # same-filesystem atomic move
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return cache


def list_build_sources(client: Any) -> dict:
    """Map sms-api's simulator versions to dropdown build entries.

    Best-effort: returns {"builds": [], "error": <str>} when sms-api is
    unreachable so the dropdown degrades to Local-only.
    """
    try:
        data = client.list_simulators()
    except SmsApiError as e:
        return {"builds": [], "error": str(e)}
    builds = []
    for v in data.get("versions", []) or []:
        sim_id = v.get("database_id")
        commit = v.get("git_commit_hash", "")
        repo = (v.get("git_repo_url", "") or "").rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        builds.append({
            "simulator_id": sim_id,
            "repo": repo,
            "commit": commit,
            "branch": v.get("git_branch", ""),
            "label": f"{repo} @ {commit} (build #{sim_id})",
        })
    return {"builds": builds, "error": None}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remote_build_source.py -q`
Expected: PASS (6 tests total now).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/remote_build_source.py tests/test_remote_build_source.py
git commit -m "feat(remote-build): materialize build to per-commit local cache + list sources"
```

---

## Task 3: server endpoints — list builds + switch to a build

**Files:**
- Modify: `vivarium_dashboard/server.py` (do_GET dispatch near `~8322`; `_POST_ROUTE_MAP` near `~342`; handler methods near `_post_source_switch`)
- Test: `tests/test_remote_build_source.py`

**Interfaces:**
- Consumes: `_sms_api_base()`, `SmsApiClient`, `_switch_active_workspace` (SP2), `lib.remote_build_source.{list_build_sources, materialize_build}`, `Handler._json`.
- Produces: `GET /api/source/builds -> {"builds", "error"}`; `POST /api/source/switch-build {"simulator_id"} -> {"ok","source"}` (404 unknown id; 502 materialize failure).

- [ ] **Step 1: Write the failing tests**

```python
def test_source_builds_route_in_do_get(monkeypatch):
    from vivarium_dashboard import server
    from vivarium_dashboard.lib import remote_build_source
    monkeypatch.setattr(
        remote_build_source, "list_build_sources",
        lambda client: {"builds": [{"simulator_id": 7, "label": "x"}], "error": None},
    )
    captured = {}

    class H:
        path = "/api/source/builds"
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._get_source_builds(H())
    assert captured["code"] == 200
    assert captured["obj"]["builds"][0]["simulator_id"] == 7


def test_switch_build_unknown_id_404(monkeypatch):
    from vivarium_dashboard import server
    from vivarium_dashboard.lib import remote_build_source
    monkeypatch.setattr(remote_build_source, "list_build_sources",
                        lambda client: {"builds": [], "error": None})
    captured = {}

    class H:
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._post_source_switch_build(H(), {"simulator_id": 999})
    assert captured["code"] == 404


def test_switch_build_materializes_and_switches(monkeypatch, tmp_path):
    from vivarium_dashboard import server
    from vivarium_dashboard.lib import remote_build_source
    cache = tmp_path / "sim45-32b901"; cache.mkdir()
    (cache / "workspace.yaml").write_text("name: built\n")
    monkeypatch.setattr(remote_build_source, "list_build_sources",
                        lambda client: {"builds": [{"simulator_id": 45, "commit": "32b901",
                                                    "label": "v2ecoli @ 32b901 (build #45)"}], "error": None})
    monkeypatch.setattr(remote_build_source, "materialize_build",
                        lambda client, sim_id, commit, **k: cache)
    switched = {}
    monkeypatch.setattr(server, "_switch_active_workspace", lambda root: switched.update(root=root))
    captured = {}

    class H:
        def _json(self, obj, code):
            captured.update(obj=obj, code=code)

    server.Handler._post_source_switch_build(H(), {"simulator_id": 45})
    assert captured["code"] == 200 and captured["obj"]["ok"] is True
    assert switched["root"] == cache
    assert server._POST_ROUTE_MAP.get("/api/source/switch-build") == "_post_source_switch_build"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_remote_build_source.py -q`
Expected: FAIL — `_get_source_builds` / `_post_source_switch_build` not defined.

- [ ] **Step 3: Add the route + two handlers to `server.py`**

In `do_GET` (near the other `if self.path.startswith("/api/...")` checks, ~line 8322), add:

```python
        if self.path.startswith("/api/source/builds"):
            return self._get_source_builds()
```

In `_POST_ROUTE_MAP` (the dict, ~line 342), add:

```python
    "/api/source/switch-build":      "_post_source_switch_build",
```

Add the two handler methods to the `Handler` class (near `_post_source_switch`):

```python
    def _get_source_builds(self):
        """GET /api/source/builds — remote sms-api simulator builds for the
        source dropdown. Best-effort; empty list + reason if sms-api is down."""
        from vivarium_dashboard.lib import remote_build_source
        from vivarium_dashboard.lib.sms_api_client import SmsApiClient
        payload = remote_build_source.list_build_sources(SmsApiClient(_sms_api_base()))
        return self._json(payload, 200)

    def _post_source_switch_build(self, body: dict):
        """POST /api/source/switch-build — materialize a build's workspace (once,
        cached) and re-point to it in-process (SP2). Body: {simulator_id}."""
        from vivarium_dashboard.lib import remote_build_source
        from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError
        sim_id = body.get("simulator_id")
        if sim_id is None:
            return self._json({"error": "missing 'simulator_id'"}, 400)
        client = SmsApiClient(_sms_api_base())
        listing = remote_build_source.list_build_sources(client)
        entry = next((b for b in listing["builds"] if b["simulator_id"] == sim_id), None)
        if entry is None:
            return self._json({"error": f"build {sim_id} not found"}, 404)
        try:
            cache_dir = remote_build_source.materialize_build(client, sim_id, entry["commit"])
        except SmsApiError as e:
            return self._json({"error": f"materialize failed: {e}"}, 502)
        _switch_active_workspace(cache_dir)
        return self._json({"ok": True, "source": {"path": str(cache_dir), "name": entry["label"]}}, 200)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remote_build_source.py -q`
Expected: PASS (9 tests total).

- [ ] **Step 5: Run a routing regression**

Run: `.venv/bin/python -m pytest tests/test_data_endpoints.py tests/test_source_switch.py -q`
Expected: PASS (no regression in existing GET/POST routing or SP2).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_remote_build_source.py
git commit -m "feat(remote-build): /api/source/builds + /api/source/switch-build endpoints"
```

---

## Task 4: dropdown — Builds optgroup + `_switchBuild`

**Files:**
- Modify: `vivarium_dashboard/static/source-switch.js`
- Test: `tests/test_remote_build_source.py` (string-presence, the repo convention for JS)

**Interfaces:**
- Consumes: `GET /api/source/builds` + `POST /api/source/switch-build` (Task 3); SP2's `_switch(path)` (unchanged, for Local options).
- Produces: a two-`<optgroup>` dropdown (Local + Builds); a `_switchBuild(simulatorId)` POST+reload.

- [ ] **Step 1: Write the failing test (string-presence)**

```python
def test_source_switch_js_has_builds_section():
    from pathlib import Path
    from vivarium_dashboard import server
    js = (Path(server.__file__).parent / "static" / "source-switch.js").read_text()
    assert "/api/source/builds" in js
    assert "/api/source/switch-build" in js
    assert "optgroup" in js
    assert "simulator_id" in js
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_remote_build_source.py::test_source_switch_js_has_builds_section -q`
Expected: FAIL — the builds wiring isn't in the JS yet.

- [ ] **Step 3: Extend `source-switch.js`**

Replace `_populate` with a version that renders two `<optgroup>`s and tags each option with its kind, and add `_switchBuild`. Replace the existing `_populate` function and the `sel.addEventListener("change", ...)` line:

```javascript
  function _localOption(ws) {
    const opt = document.createElement("option");
    opt.value = "local:" + ws.path;
    opt.textContent = ws.name || ws.path;
    if (ws.status === "current") opt.selected = true;
    return opt;
  }

  async function _populate(sel) {
    sel.innerHTML = "";
    // Local workspaces (existing catalog).
    try {
      const r = await fetch("/api/workspaces");
      if (r.ok) {
        const data = await r.json();
        const items = (data && data.workspaces) || data || [];
        if (items.length) {
          const g = document.createElement("optgroup");
          g.label = "Local";
          items.forEach(function (ws) { g.appendChild(_localOption(ws)); });
          sel.appendChild(g);
        }
      }
    } catch (e) { /* offline */ }
    // Remote sms-api builds (best-effort).
    try {
      const r = await fetch("/api/source/builds");
      if (r.ok) {
        const data = await r.json();
        const builds = (data && data.builds) || [];
        if (builds.length) {
          const g = document.createElement("optgroup");
          g.label = "Builds";
          builds.forEach(function (b) {
            const opt = document.createElement("option");
            opt.value = "build:" + b.simulator_id;
            opt.textContent = b.label;
            g.appendChild(opt);
          });
          sel.appendChild(g);
        }
      }
    } catch (e) { /* sms-api down — Local only */ }
  }

  async function _switchBuild(simulatorId, sel) {
    if (sel) { sel.disabled = true; }   // "Loading build…" — first select downloads
    const r = await fetch("/api/source/switch-build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ simulator_id: Number(simulatorId) }),
    });
    if (r.ok) {
      try { sessionStorage.setItem("viv-source-switched", "1"); } catch (e) {}
      window.location.reload();
    } else {
      if (sel) { sel.disabled = false; }
      const d = await r.json().catch(function () { return {}; });
      alert("Switch failed: " + (d.error || r.status));
    }
  }

  function _onChange(sel) {
    const v = sel.value || "";
    if (v.indexOf("build:") === 0) { _switchBuild(v.slice(6), sel); }
    else if (v.indexOf("local:") === 0) { _switch(v.slice(6)); }
  }
```

And in `_mount`, change the listener wiring from `sel.addEventListener("change", function () { _switch(sel.value); });` to:

```javascript
    sel.addEventListener("change", function () { _onChange(sel); });
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remote_build_source.py::test_source_switch_js_has_builds_section -q`
Expected: PASS.

- [ ] **Step 5: Run the full SP3 test file**

Run: `.venv/bin/python -m pytest tests/test_remote_build_source.py -q`
Expected: PASS (10 tests).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/static/source-switch.js tests/test_remote_build_source.py
git commit -m "feat(remote-build): Builds optgroup + build switch in source dropdown"
```

---

## Self-Review

**1. Spec coverage:**
- §Component 1 (client `list_simulators` + `download_workspace`) → Task 1. ✓
- §Component 2 (`build_cache_root`/`cache_dir_for`/`materialize_build`/`list_build_sources`, strip top dir, reuse, atomic) → Task 2. ✓
- §Component 3 (`GET /api/source/builds` + `POST /api/source/switch-build`, 404/502, switch via SP2) → Task 3. ✓
- §Component 4 (two-optgroup dropdown + `_switchBuild`) → Task 4. ✓
- §Decisions: lazy (Task 3 materializes only on switch-build) ✓; cache+reuse (Task 2 test) ✓; strip top dir (Task 2) ✓; degrade (Task 2 `list_build_sources` + Task 3 GET) ✓; reuse SP2 switch / trusted cache dir (Task 3) ✓; failure-leaves-unchanged (Task 3 materialize-before-switch, 502) ✓; env-overridable cache (Task 2 `build_cache_root` + `_cache` fixture) ✓.
- §Data flow + §Error handling → Tasks 2–3 (404 unknown id, 502 download failure, degrade). ✓

**2. Placeholder scan:** No "TBD"/"handle errors". The one verify-and-match note (sms_api_client's existing imports, Task 1 step 3) is concrete (names the exact grep). No vague steps.

**3. Type consistency:** `list_simulators()`, `download_workspace(simulator_id, dest_dir) -> Path`, `build_cache_root()`, `cache_dir_for(simulator_id, commit)`, `materialize_build(client, simulator_id, commit, *, force=False)`, `list_build_sources(client) -> {"builds","error"}`, build entry keys `{simulator_id, repo, commit, branch, label}`, routes `/api/source/builds` → `_get_source_builds`, `/api/source/switch-build` → `_post_source_switch_build`, JS option prefixes `local:`/`build:` — all used identically across Tasks 1–4.
