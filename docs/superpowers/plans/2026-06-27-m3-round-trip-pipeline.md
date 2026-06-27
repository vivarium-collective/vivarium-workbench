# M3 Round-Trip Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the round-trip dev loop real — view a remote dashboard, sync its exact `repo@commit` state to local, run it, extend it, commit, and push it back — built on a single shared provenance manifest.

**Architecture:** A new pure lib module emits a *provenance manifest* (`repo` + full `commit` + `uv.lock` hash + result-store pointers) for whatever a dashboard is showing, exposed at `GET /api/source/manifest`. A new `vivarium-dashboard sync` CLI subcommand consumes a manifest: clone `repo@commit`, verify the lockfile hash (the fidelity gate), `uv sync`, optionally run declared cache-rebuild commands, and register the checkout in the workspace catalog. The push half (promotion) already exists (`source/build-remote` → `switch-build`); this plan wires the manifest into it and documents the closed loop. Sync-to-local is the exact inverse of build-via-sms-api and consumes the same manifest.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, pytest + `fastapi.testclient`, `uv`, git, `pbg_superpowers.workspace_catalog`.

## Global Constraints

Copied from `docs/superpowers/specs/2026-06-27-vivarium-server-three-plane-architecture-design.md`. Every task implicitly includes these.

- **AI-free.** No LLM calls anywhere in the dashboard. pydantic / fastapi / uvicorn / subprocess are fine. No new AI dependency.
- **Pure, `ws_root`-parameterized lib.** New logic goes in `vivarium_dashboard/lib/*.py` as pure functions taking an explicit `ws_root: Path`. API-handler functions return `tuple[dict, int]` (body, status). Never import `server.py` or `api/app.py` from a lib module.
- **Models in `lib/models.py`.** Request/response pydantic models live there, `model_config = ConfigDict(extra="allow")`.
- **The portable unit is the commit.** The provenance manifest pins `repo` + full `commit` + `uv.lock` hash. Reproduction fidelity contract: *same commit + same lockfile ⇒ same behavior.* Sync MUST verify the cloned `uv.lock` hash equals the manifest's before declaring success.
- **Sync-to-local is the inverse of build-via-sms-api.** Both consume the same manifest. Do not fork the provenance representation — reuse `.viv-build.json` fields (`repo`, `repo_url`, `branch`, `commit`, `simulator_id`) where a build workspace is active.
- **CLI:** extend the existing `vivarium-dashboard` console script (`vivarium_dashboard/cli.py`, subparser pattern). Do NOT add a new `vivarium` console script.
- **Never stage** `vivarium_dashboard/static/style.css` or `vivarium_dashboard/templates/index.html.j2` — another session's WIP. They must not appear in any commit from this plan.
- **Tests:** pytest + `TestClient`; use the `tmp_path` fixture and the existing `_reset_active_workspace` autouse fixture in `tests/test_api_app.py`. Mock `PBG_HOME` for catalog isolation.
- **Security note (sync executes declared commands):** post-sync cache-rebuild commands come from a remote-authored manifest. They run ONLY behind an explicit `--run-post-sync` flag, never by default. Document this at the call site.

---

## File Structure

- **Create** `vivarium_dashboard/lib/provenance_manifest.py` — emit a manifest for a workspace (git provenance, lockfile hash, result pointers). Pure.
- **Create** `vivarium_dashboard/lib/sync_materialize.py` — low-level materialization primitives: `git_clone_checkout`, `run_uv_sync`, `verify_lockfile`. Subprocess wrappers, each returns `tuple[dict, int]`.
- **Create** `vivarium_dashboard/lib/sync_workspace.py` — orchestration: `sync_from_manifest(manifest, dest, run_post_sync)` ties clone → verify → uv sync → optional post-sync → catalog register.
- **Modify** `vivarium_dashboard/lib/models.py` — add `ProvenanceManifest` model.
- **Modify** `vivarium_dashboard/api/app.py` — add `GET /api/source/manifest` route (near the other `/api/source/*` routes, ~line 2069/3990).
- **Modify** `vivarium_dashboard/cli.py` — add the `sync` subcommand (`cmd_sync` + subparser).
- **Modify** `vivarium_dashboard/static/branch-source.js` — a "Sync to local" affordance that fetches the manifest and shows the `vivarium-dashboard sync` command.
- **Create** `tests/test_provenance_manifest.py`, `tests/test_sync_materialize.py`, `tests/test_sync_workspace.py`, `tests/test_cli_sync.py`, `tests/test_round_trip_fidelity.py`.
- **Create** `docs/round-trip-pipeline.md` — the documented loop (view → sync → run → extend → commit → push).

A shared test helper (`_init_git_repo`) is defined in Task 1's test file and re-defined in each later test file that needs it (tasks may be read out of order; do not import across test files).

---

### Task 1: Provenance manifest builder + route

**Files:**
- Create: `vivarium_dashboard/lib/provenance_manifest.py`
- Modify: `vivarium_dashboard/lib/models.py` (add `ProvenanceManifest`)
- Modify: `vivarium_dashboard/api/app.py` (add `GET /api/source/manifest`)
- Test: `tests/test_provenance_manifest.py`

**Interfaces:**
- Consumes: `vivarium_dashboard.lib.workspace_deps_views.read_workspace_name(root: Path) -> str` (existing); `.viv-build.json` shape `{simulator_id, repo, branch, commit, repo_url}` (existing, written by `source_build_views.switch_build`).
- Produces:
  - `provenance_manifest.git_full_commit(ws_root: Path) -> str`
  - `provenance_manifest.git_branch(ws_root: Path) -> str`
  - `provenance_manifest.git_origin_url(ws_root: Path) -> str` (strips trailing `.git`)
  - `provenance_manifest.lockfile_hash(ws_root: Path) -> str | None` (returns `"uv.lock@<sha256[:12]>"` or `None`)
  - `provenance_manifest.build_manifest(ws_root: Path) -> dict` with keys `repo, commit, branch, workspace, lockfile, results, simulator_id`
  - Route `GET /api/source/manifest -> ProvenanceManifest`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provenance_manifest.py
import json
import subprocess
from pathlib import Path

from vivarium_dashboard.lib import provenance_manifest as pm


def _init_git_repo(ws: Path, origin_url: str) -> str:
    """Init a git repo at ws with one commit and an origin remote. Returns full SHA."""
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (ws / "uv.lock").write_text("lock-contents-v1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    run = lambda *a: subprocess.run(["git", "-C", str(ws), *a], check=True,
                                    capture_output=True, text=True, env={**env})
    subprocess.run(["git", "init", "-q", str(ws)], check=True, env={**env})
    run("remote", "add", "origin", origin_url)
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    return subprocess.run(["git", "-C", str(ws), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def test_build_manifest_reads_git_and_lockfile(tmp_path):
    ws = tmp_path / "ws"
    sha = _init_git_repo(ws, "https://github.com/vivarium-collective/demo.git")
    m = pm.build_manifest(ws)
    assert m["commit"] == sha            # FULL sha, not short
    assert m["branch"] in ("main", "master")
    assert m["repo"] == "https://github.com/vivarium-collective/demo"  # .git stripped
    assert m["workspace"] == "demo"
    assert m["lockfile"].startswith("uv.lock@") and len(m["lockfile"]) == len("uv.lock@") + 12
    assert m["results"] == {"runs": []}  # no runs in a bare workspace
    assert m["simulator_id"] is None


def test_build_manifest_prefers_viv_build_json(tmp_path):
    ws = tmp_path / "ws"
    _init_git_repo(ws, "https://github.com/x/local.git")
    (ws / ".viv-build.json").write_text(json.dumps({
        "simulator_id": 42, "repo": "v2ecoli", "branch": "feat/x",
        "commit": "abc123def456", "repo_url": "https://github.com/vivarium-collective/v2ecoli",
    }))
    m = pm.build_manifest(ws)
    assert m["commit"] == "abc123def456"          # from build meta, not git HEAD
    assert m["repo"] == "https://github.com/vivarium-collective/v2ecoli"
    assert m["simulator_id"] == 42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_provenance_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.provenance_manifest'`

- [ ] **Step 3: Write minimal implementation**

```python
# vivarium_dashboard/lib/provenance_manifest.py
"""Emit a *provenance manifest* for a workspace.

The manifest is the portable unit of the round-trip loop: it pins exactly what
a dashboard is showing (repo + full commit + uv.lock hash + result-store
pointers) so the state can be (a) materialized locally by `vivarium-dashboard
sync` and (b) rebuilt remotely by build-via-sms-api. Pure / ws_root-parameterized.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def _git(ws_root: Path, *args: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(ws_root), *args],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def git_full_commit(ws_root: Path) -> str:
    return _git(Path(ws_root), "rev-parse", "HEAD")


def git_branch(ws_root: Path) -> str:
    return _git(Path(ws_root), "rev-parse", "--abbrev-ref", "HEAD")


def git_origin_url(ws_root: Path) -> str:
    url = _git(Path(ws_root), "remote", "get-url", "origin")
    return url[:-4] if url.endswith(".git") else url


def lockfile_hash(ws_root: Path) -> str | None:
    lf = Path(ws_root) / "uv.lock"
    if not lf.is_file():
        return None
    digest = hashlib.sha256(lf.read_bytes()).hexdigest()[:12]
    return f"uv.lock@{digest}"


def _result_pointers(ws_root: Path) -> dict:
    """Best-effort: distinct result store_paths for this workspace's runs."""
    try:
        from vivarium_dashboard.lib.simulations_index import list_simulations
        data = list_simulations(Path(ws_root))
        rows = data.get("simulations", []) if isinstance(data, dict) else (data or [])
        stores = []
        for row in rows:
            sp = row.get("store_path")
            if sp and sp not in stores:
                stores.append(sp)
        return {"runs": stores}
    except Exception:
        return {"runs": []}


def build_manifest(ws_root: Path) -> dict:
    ws_root = Path(ws_root)
    build_meta = None
    vb = ws_root / ".viv-build.json"
    if vb.is_file():
        try:
            build_meta = json.loads(vb.read_text())
        except Exception:
            build_meta = None

    if build_meta:
        repo = build_meta.get("repo_url") or build_meta.get("repo") or ""
        commit = build_meta.get("commit") or ""
        branch = build_meta.get("branch") or ""
    else:
        repo = git_origin_url(ws_root)
        commit = git_full_commit(ws_root)
        branch = git_branch(ws_root)

    try:
        from vivarium_dashboard.lib.workspace_deps_views import read_workspace_name
        name = read_workspace_name(ws_root)
    except Exception:
        name = ws_root.name

    return {
        "repo": repo,
        "commit": commit,
        "branch": branch,
        "workspace": name,
        "lockfile": lockfile_hash(ws_root),
        "results": _result_pointers(ws_root),
        "simulator_id": (build_meta or {}).get("simulator_id"),
    }
```

Add the model to `vivarium_dashboard/lib/models.py` (place it next to the other source models, near `SourceBuilds`):

```python
class ProvenanceManifest(BaseModel):
    """repo@commit + lockfile + result pointers — the portable round-trip unit."""
    model_config = ConfigDict(extra="allow")
    repo: str
    commit: str
    branch: str
    workspace: str
    lockfile: Optional[str] = None
    results: dict = {}
    simulator_id: Optional[int] = None
```

Add the route to `vivarium_dashboard/api/app.py` near the existing `GET /api/source/builds` (~line 2069). Match its style:

```python
    @app.get(
        "/api/source/manifest",
        response_model=ProvenanceManifest,
        tags=["source"],
        summary="Provenance manifest for the active workspace (repo@commit + lockfile + results)",
    )
    def source_manifest(ws: Path = Depends(get_workspace)) -> ProvenanceManifest:
        from vivarium_dashboard.lib.provenance_manifest import build_manifest
        return ProvenanceManifest(**build_manifest(ws))
```

Ensure `ProvenanceManifest` is in the `from vivarium_dashboard.lib.models import (...)` block at the top of `app.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_provenance_manifest.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Add and run an API-route test**

```python
# append to tests/test_provenance_manifest.py
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace


def test_manifest_route(tmp_path):
    ws = tmp_path / "ws"
    _init_git_repo(ws, "https://github.com/x/demo.git")
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    client = TestClient(app)
    r = client.get("/api/source/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["workspace"] == "demo"
    assert body["lockfile"].startswith("uv.lock@")
```

Run: `.venv/bin/pytest tests/test_provenance_manifest.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/lib/provenance_manifest.py vivarium_dashboard/lib/models.py vivarium_dashboard/api/app.py tests/test_provenance_manifest.py
git commit -m "feat(manifest): emit provenance manifest (repo@commit + lockfile + results) at /api/source/manifest"
```

---

### Task 2: Materialization primitives (clone, lockfile verify, uv sync)

**Files:**
- Create: `vivarium_dashboard/lib/sync_materialize.py`
- Test: `tests/test_sync_materialize.py`

**Interfaces:**
- Consumes: `provenance_manifest.lockfile_hash(ws_root) -> str | None` (Task 1).
- Produces:
  - `sync_materialize.git_clone_checkout(repo: str, commit: str, dest: Path) -> tuple[dict, int]`
  - `sync_materialize.verify_lockfile(ws_root: Path, expected: str | None) -> tuple[dict, int]`
  - `sync_materialize.run_uv_sync(ws_root: Path, timeout: int = 600) -> tuple[dict, int]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sync_materialize.py
import subprocess
from pathlib import Path

from vivarium_dashboard.lib import sync_materialize as sm


def _make_origin(path: Path) -> tuple[str, str]:
    """Create a real local git repo (acts as a clone source). Returns (url, full sha)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (path / "uv.lock").write_text("lock-contents-v1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    for a in (["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, env=env, capture_output=True)
    sha = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    return f"file://{path}", sha


def test_clone_checkout_lands_exact_commit(tmp_path):
    url, sha = _make_origin(tmp_path / "origin")
    dest = tmp_path / "local"
    body, status = sm.git_clone_checkout(url, sha, dest)
    assert status == 200, body
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head == sha
    assert (dest / "uv.lock").is_file()


def test_verify_lockfile_matches(tmp_path):
    url, sha = _make_origin(tmp_path / "origin")
    dest = tmp_path / "local"
    sm.git_clone_checkout(url, sha, dest)
    from vivarium_dashboard.lib.provenance_manifest import lockfile_hash
    expected = lockfile_hash(tmp_path / "origin")
    body, status = sm.verify_lockfile(dest, expected)
    assert status == 200, body


def test_verify_lockfile_mismatch_is_409(tmp_path):
    url, sha = _make_origin(tmp_path / "origin")
    dest = tmp_path / "local"
    sm.git_clone_checkout(url, sha, dest)
    body, status = sm.verify_lockfile(dest, "uv.lock@deadbeefcafe")
    assert status == 409
    assert "lockfile" in body["error"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_sync_materialize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.sync_materialize'`

- [ ] **Step 3: Write minimal implementation**

```python
# vivarium_dashboard/lib/sync_materialize.py
"""Low-level materialization primitives for `vivarium-dashboard sync`.

Each function returns (body, status). Subprocess-based, no global state.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from vivarium_dashboard.lib.provenance_manifest import lockfile_hash


def git_clone_checkout(repo: str, commit: str, dest: Path) -> tuple[dict, int]:
    dest = Path(dest)
    if not repo or not commit:
        return {"error": "repo and commit are required"}, 400
    if dest.exists() and any(dest.iterdir()):
        return {"error": f"{dest} exists and is not empty"}, 409
    try:
        subprocess.run(["git", "clone", "-q", repo, str(dest)],
                       check=True, capture_output=True, text=True, timeout=600)
        subprocess.run(["git", "-C", str(dest), "checkout", "-q", commit],
                       check=True, capture_output=True, text=True, timeout=60)
    except subprocess.CalledProcessError as e:
        return {"error": f"git failed: {e.stderr or e}"}, 502
    except subprocess.TimeoutExpired:
        return {"error": "git clone/checkout timed out"}, 504
    return {"ok": True, "path": str(dest), "commit": commit}, 200


def verify_lockfile(ws_root: Path, expected: str | None) -> tuple[dict, int]:
    """Fidelity gate: the cloned uv.lock must hash to the manifest's value."""
    if not expected:
        return {"ok": True, "note": "no lockfile pinned in manifest"}, 200
    actual = lockfile_hash(Path(ws_root))
    if actual != expected:
        return {"error": f"lockfile mismatch: expected {expected}, got {actual}"}, 409
    return {"ok": True, "lockfile": actual}, 200


def run_uv_sync(ws_root: Path, timeout: int = 600) -> tuple[dict, int]:
    try:
        r = subprocess.run(["uv", "sync"], cwd=str(ws_root),
                           capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"error": "uv not found in PATH"}, 400
    except subprocess.TimeoutExpired:
        return {"error": f"uv sync timed out (>{timeout}s)"}, 504
    if r.returncode != 0:
        return {"error": r.stderr.strip() or "uv sync failed"}, 502
    return {"ok": True}, 200
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sync_materialize.py -v`
Expected: PASS (3 passed). `run_uv_sync` is covered indirectly in Task 3 (mocked) to avoid a real 5-min sync here.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/sync_materialize.py tests/test_sync_materialize.py
git commit -m "feat(sync): clone@commit + lockfile-verify + uv-sync materialization primitives"
```

---

### Task 3: Sync orchestration (manifest → registered local workspace)

**Files:**
- Create: `vivarium_dashboard/lib/sync_workspace.py`
- Test: `tests/test_sync_workspace.py`

**Interfaces:**
- Consumes: `sync_materialize.git_clone_checkout`, `verify_lockfile`, `run_uv_sync` (Task 2); `pbg_superpowers.workspace_catalog.add(path, name=None, package=None) -> dict` (existing).
- Produces: `sync_workspace.sync_from_manifest(manifest: dict, dest: Path, run_post_sync: bool = False) -> tuple[dict, int]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sync_workspace.py
import subprocess
from pathlib import Path

import pytest

from vivarium_dashboard.lib import sync_workspace as sw


def _make_origin(path: Path) -> tuple[str, str]:
    path.mkdir(parents=True, exist_ok=True)
    (path / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (path / "uv.lock").write_text("lock-contents-v1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    for a in (["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, env=env, capture_output=True)
    sha = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    return f"file://{path}", sha


@pytest.fixture
def _no_real_uv(monkeypatch):
    monkeypatch.setattr(sw.sync_materialize, "run_uv_sync", lambda ws, **k: ({"ok": True}, 200))


@pytest.fixture
def _capture_catalog(monkeypatch):
    added = {}
    def _add(path, name=None, package=None):
        added["path"] = str(path); added["name"] = name
        return {"path": str(path), "name": name}
    monkeypatch.setattr(sw, "_catalog_add", _add)
    return added


def test_sync_from_manifest_happy_path(tmp_path, _no_real_uv, _capture_catalog):
    url, sha = _make_origin(tmp_path / "origin")
    from vivarium_dashboard.lib.provenance_manifest import lockfile_hash
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": lockfile_hash(tmp_path / "origin"), "results": {"runs": []}}
    dest = tmp_path / "local"
    body, status = sw.sync_from_manifest(manifest, dest)
    assert status == 200, body
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head == sha
    assert _capture_catalog["path"] == str(dest.resolve())


def test_sync_aborts_on_lockfile_mismatch(tmp_path, _no_real_uv, _capture_catalog):
    url, sha = _make_origin(tmp_path / "origin")
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": "uv.lock@deadbeefcafe", "results": {"runs": []}}
    body, status = sw.sync_from_manifest(manifest, tmp_path / "local")
    assert status == 409
    assert _capture_catalog == {}  # never registered a mismatched workspace


def test_post_sync_runs_only_when_flagged(tmp_path, _no_real_uv, _capture_catalog):
    url, sha = _make_origin(tmp_path / "origin")
    from vivarium_dashboard.lib.provenance_manifest import lockfile_hash
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": lockfile_hash(tmp_path / "origin"), "results": {"runs": []},
                "post_sync": ["touch POST_SYNC_RAN"]}
    dest = tmp_path / "local"
    # default: post_sync NOT run
    sw.sync_from_manifest(manifest, dest)
    assert not (dest / "POST_SYNC_RAN").exists()


def test_post_sync_runs_when_enabled(tmp_path, _no_real_uv, _capture_catalog):
    url, sha = _make_origin(tmp_path / "origin")
    from vivarium_dashboard.lib.provenance_manifest import lockfile_hash
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": lockfile_hash(tmp_path / "origin"), "results": {"runs": []},
                "post_sync": ["touch POST_SYNC_RAN"]}
    dest = tmp_path / "local"
    sw.sync_from_manifest(manifest, dest, run_post_sync=True)
    assert (dest / "POST_SYNC_RAN").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_sync_workspace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.sync_workspace'`

- [ ] **Step 3: Write minimal implementation**

```python
# vivarium_dashboard/lib/sync_workspace.py
"""Orchestrate `vivarium-dashboard sync`: manifest -> registered local workspace.

The inverse of build-via-sms-api. Steps:
  1. clone repo@commit into dest
  2. verify the cloned uv.lock hash equals the manifest's (fidelity gate)
  3. uv sync (materialize the pinned env)
  4. optionally run declared post_sync commands (cache rebuild) — opt-in only
  5. register the checkout in the workspace catalog
Returns (body, status). Pure except for the filesystem/catalog side effects it
is explicitly asked to perform.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from vivarium_dashboard.lib import sync_materialize


def _catalog_add(path, name=None, package=None) -> dict:
    """Thin seam over the workspace catalog (monkeypatched in tests)."""
    from pbg_superpowers import workspace_catalog
    return workspace_catalog.add(path, name=name)


def sync_from_manifest(manifest: dict, dest: Path, run_post_sync: bool = False) -> tuple[dict, int]:
    dest = Path(dest).resolve()
    repo = manifest.get("repo") or ""
    commit = manifest.get("commit") or ""

    body, status = sync_materialize.git_clone_checkout(repo, commit, dest)
    if status != 200:
        return body, status

    body, status = sync_materialize.verify_lockfile(dest, manifest.get("lockfile"))
    if status != 200:
        return body, status  # do NOT register a workspace that failed the fidelity gate

    body, status = sync_materialize.run_uv_sync(dest)
    if status != 200:
        return body, status

    if run_post_sync:
        for cmd in manifest.get("post_sync", []) or []:
            try:
                subprocess.run(cmd, cwd=str(dest), shell=True, check=True,
                               capture_output=True, text=True, timeout=1800)
            except subprocess.CalledProcessError as e:
                return {"error": f"post_sync command failed: {cmd}: {e.stderr or e}"}, 502
            except subprocess.TimeoutExpired:
                return {"error": f"post_sync command timed out: {cmd}"}, 504

    try:
        entry = _catalog_add(dest, name=manifest.get("workspace"))
    except Exception as e:
        return {"error": f"materialized but catalog register failed: {e}",
                "path": str(dest)}, 207

    return {"ok": True, "path": str(dest), "commit": commit, "entry": entry}, 200
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sync_workspace.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/sync_workspace.py tests/test_sync_workspace.py
git commit -m "feat(sync): orchestrate manifest -> clone -> verify -> uv sync -> register (post_sync opt-in)"
```

---

### Task 4: `vivarium-dashboard sync` CLI subcommand

**Files:**
- Modify: `vivarium_dashboard/cli.py` (add `sync` subparser + `cmd_sync`)
- Test: `tests/test_cli_sync.py`

**Interfaces:**
- Consumes: `sync_workspace.sync_from_manifest(manifest, dest, run_post_sync)` (Task 3).
- Produces: `cli.cmd_sync(args) -> int`; CLI `vivarium-dashboard sync <manifest> [--dest DIR] [--run-post-sync]` where `<manifest>` is a path, `file://`, or `http(s)://` URL (a manifest JSON, or a dashboard base URL whose `/api/source/manifest` is fetched).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_sync.py
import json
import subprocess
from pathlib import Path

from vivarium_dashboard import cli


def _make_origin(path: Path) -> tuple[str, str]:
    path.mkdir(parents=True, exist_ok=True)
    (path / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (path / "uv.lock").write_text("lock-contents-v1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    for a in (["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, env=env, capture_output=True)
    sha = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    return f"file://{path}", sha


def test_cmd_sync_from_manifest_file(tmp_path, monkeypatch):
    url, sha = _make_origin(tmp_path / "origin")
    from vivarium_dashboard.lib.provenance_manifest import lockfile_hash
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": lockfile_hash(tmp_path / "origin"), "results": {"runs": []}}
    mfile = tmp_path / "manifest.json"
    mfile.write_text(json.dumps(manifest))
    # avoid a real uv sync + real catalog write
    import vivarium_dashboard.lib.sync_workspace as sw
    monkeypatch.setattr(sw.sync_materialize, "run_uv_sync", lambda ws, **k: ({"ok": True}, 200))
    monkeypatch.setattr(sw, "_catalog_add", lambda p, name=None, package=None: {"path": str(p)})

    dest = tmp_path / "local"
    rc = cli.main(["sync", str(mfile), "--dest", str(dest)])
    assert rc == 0
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head == sha


def test_cmd_sync_reports_failure_rc(tmp_path, monkeypatch):
    url, sha = _make_origin(tmp_path / "origin")
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": "uv.lock@deadbeefcafe", "results": {"runs": []}}
    mfile = tmp_path / "m.json"
    mfile.write_text(json.dumps(manifest))
    rc = cli.main(["sync", str(mfile), "--dest", str(tmp_path / "local")])
    assert rc == 1  # lockfile mismatch -> non-zero exit
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_sync.py -v`
Expected: FAIL with `SystemExit` / `argument cmd: invalid choice: 'sync'`

- [ ] **Step 3: Write minimal implementation**

In `vivarium_dashboard/cli.py`, add the subparser inside `main()` next to the existing `sub.add_parser(...)` calls:

```python
    p_sync = sub.add_parser(
        "sync",
        help="Materialize a remote dashboard's exact repo@commit workspace locally",
    )
    p_sync.add_argument("manifest", help="manifest JSON path/URL, or a dashboard base URL")
    p_sync.add_argument("--dest", default=None, help="destination dir (default: ./<workspace>)")
    p_sync.add_argument("--run-post-sync", action="store_true",
                        help="run manifest-declared cache-rebuild commands (executes remote-authored commands)")
    p_sync.set_defaults(func=cmd_sync)
```

Add the command function (module level in `cli.py`):

```python
def _load_manifest(source: str) -> dict:
    """Load a manifest from a file path, file://, or http(s):// (a JSON manifest
    or a dashboard base URL whose /api/source/manifest is fetched)."""
    import json
    import urllib.request

    if source.startswith(("http://", "https://")):
        url = source.rstrip("/")
        if not url.endswith("/api/source/manifest"):
            url = url + "/api/source/manifest"
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode())
    if source.startswith("file://"):
        source = source[len("file://"):]
    return json.loads(Path(source).read_text())


def cmd_sync(args) -> int:
    from vivarium_dashboard.lib.sync_workspace import sync_from_manifest

    manifest = _load_manifest(args.manifest)
    dest = Path(args.dest) if args.dest else Path.cwd() / (manifest.get("workspace") or "workspace")
    body, status = sync_from_manifest(manifest, dest, run_post_sync=args.run_run_post_sync
                                      if hasattr(args, "run_run_post_sync") else args.run_post_sync)
    if status == 200:
        print(f"synced {manifest.get('repo')}@{manifest.get('commit', '')[:7]} -> {body['path']}")
        print(f"registered as workspace '{manifest.get('workspace')}'. Open it from the switcher.")
        return 0
    print(f"sync failed ({status}): {body.get('error', body)}")
    return 1
```

Ensure `from pathlib import Path` is imported at the top of `cli.py` (it is used by existing subcommands; confirm).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli_sync.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/cli.py tests/test_cli_sync.py
git commit -m "feat(cli): add 'vivarium-dashboard sync <manifest>' to materialize a remote workspace locally"
```

---

### Task 5: Round-trip fidelity + manifest-symmetry tests

**Files:**
- Test: `tests/test_round_trip_fidelity.py`

**Interfaces:**
- Consumes: `provenance_manifest.build_manifest` (Task 1), `sync_workspace.sync_from_manifest` (Task 3).

This task adds no production code — it locks the fidelity and symmetry contracts from the spec (§8) under test. If a test reveals a gap, fix the relevant module from its task and re-run.

- [ ] **Step 1: Write the round-trip fidelity test**

```python
# tests/test_round_trip_fidelity.py
import subprocess
from pathlib import Path

import pytest

from vivarium_dashboard.lib.provenance_manifest import build_manifest, lockfile_hash
from vivarium_dashboard.lib import sync_workspace as sw


def _make_origin(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (path / "uv.lock").write_text("lock-contents-v1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin",
                    "https://github.com/vivarium-collective/demo.git"], check=True, env=env)
    for a in (["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, env=env, capture_output=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def _stub_externals(monkeypatch):
    monkeypatch.setattr(sw.sync_materialize, "run_uv_sync", lambda ws, **k: ({"ok": True}, 200))
    monkeypatch.setattr(sw, "_catalog_add", lambda p, name=None, package=None: {"path": str(p)})


def test_round_trip_preserves_commit_and_lockfile(tmp_path, _stub_externals):
    """Same commit + same lockfile after sync — the fidelity contract."""
    origin = tmp_path / "origin"
    sha = _make_origin(origin)
    # Emit the manifest from the source state, but point repo at the local clone source.
    manifest = build_manifest(origin)
    manifest["repo"] = f"file://{origin}"   # build_manifest reads origin URL; clone needs a reachable source
    dest = tmp_path / "local"

    body, status = sw.sync_from_manifest(manifest, dest)
    assert status == 200, body

    synced_head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    assert synced_head == sha                                 # exact commit
    assert lockfile_hash(dest) == manifest["lockfile"]        # exact lockfile


def test_lockfile_drift_is_rejected(tmp_path, _stub_externals):
    """If the source lockfile changed after the manifest was emitted, sync refuses."""
    origin = tmp_path / "origin"
    sha = _make_origin(origin)
    manifest = build_manifest(origin)
    manifest["repo"] = f"file://{origin}"
    # Tamper: change the manifest's pinned hash to simulate drift.
    manifest["lockfile"] = "uv.lock@0000deadbeef"
    body, status = sw.sync_from_manifest(manifest, tmp_path / "local")
    assert status == 409
```

- [ ] **Step 2: Run the fidelity tests**

Run: `.venv/bin/pytest tests/test_round_trip_fidelity.py -v`
Expected: PASS (2 passed)

- [ ] **Step 3: Write the manifest-symmetry test**

The same manifest must resolve to the same `repo@commit` that the push half (`build-via-sms-api`) would build. `build_remote` consumes `{repo, branch}` and resolves a commit; the manifest already carries `repo` + `commit` + `branch`. Assert the manifest exposes exactly the fields the build path consumes, so one artifact drives both directions.

```python
# append to tests/test_round_trip_fidelity.py
def test_manifest_carries_build_inputs(tmp_path):
    """The manifest exposes repo+branch+commit — the inputs build-via-sms-api needs.
    Guards the 'one manifest, both directions' invariant."""
    origin = tmp_path / "origin"
    _make_origin(origin)
    m = build_manifest(origin)
    # build-remote consumes repo+branch; switch-build pins commit. All present:
    assert m["repo"] and m["branch"] and m["commit"]
    assert set(["repo", "branch", "commit"]).issubset(m.keys())
```

- [ ] **Step 4: Run the full new suite**

Run: `.venv/bin/pytest tests/test_round_trip_fidelity.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_round_trip_fidelity.py
git commit -m "test(sync): lock the round-trip fidelity + manifest-symmetry contracts"
```

---

### Task 6: "Sync to local" UI affordance

**Files:**
- Modify: `vivarium_dashboard/static/branch-source.js` (add a "Sync to local" button + reveal)
- Test: `tests/test_sync_ui.py` (backend contract the UI depends on)

**Interfaces:**
- Consumes: `GET /api/source/manifest` (Task 1).
- Produces: a button in the Source panel that fetches the manifest and shows the copy-paste `vivarium-dashboard sync <url>` command. No new backend.

Frontend JS is not unit-tested here (the repo has no JS test harness); the test guards the backend contract the button relies on, and the JS change is verified with `node --check` and a manual note. Keep the JS minimal.

- [ ] **Step 1: Write the failing backend-contract test**

```python
# tests/test_sync_ui.py
import subprocess
from pathlib import Path
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace


def _init(ws: Path):
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (ws / "uv.lock").write_text("x\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(ws)], check=True, env=env)
    subprocess.run(["git", "-C", str(ws), "remote", "add", "origin",
                    "https://github.com/x/demo.git"], check=True, env=env)
    for a in (["add", "-A"], ["commit", "-q", "-m", "i"]):
        subprocess.run(["git", "-C", str(ws), *a], check=True, env=env, capture_output=True)


def test_manifest_contract_for_ui(tmp_path):
    """The Sync-to-local button needs repo+commit+workspace to render its command."""
    ws = tmp_path / "ws"; _init(ws)
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    body = TestClient(app).get("/api/source/manifest").json()
    assert body["repo"] and body["commit"] and body["workspace"]
```

- [ ] **Step 2: Run test to verify it passes** (the route exists from Task 1)

Run: `.venv/bin/pytest tests/test_sync_ui.py -v`
Expected: PASS (1 passed). (This is a contract guard, not a red-then-green; it fails only if Task 1 regressed.)

- [ ] **Step 3: Add the UI affordance**

In `vivarium_dashboard/static/branch-source.js`, in the `_render` actions area (near the `switchBtn`/`buildBtn` block), add a "Sync to local" button that fetches the manifest and reveals the command:

```javascript
    var syncBtn = _el("button", "viv-bs-action", "Sync to local"); syncBtn.id = "viv-bs-sync";
    syncBtn.title = "Materialize this exact repo@commit workspace on your machine";
    syncBtn.addEventListener("click", function () {
      fetch("/api/source/manifest").then(function (r) { return r.json(); }).then(function (m) {
        var base = window.location.origin;
        var cmd = "vivarium-dashboard sync " + base;
        var note = "Reproduce " + (m.repo || "") + " @ " + String(m.commit || "").slice(0, 7) +
                   "\\n  " + cmd + "\\n(verifies uv.lock " + (m.lockfile || "—") + ")";
        window.prompt("Run this locally to sync + reproduce:", cmd);
        console.log(note);
      }).catch(function () { alert("Could not fetch manifest"); });
    });
    actions.appendChild(syncBtn);
```

- [ ] **Step 4: Syntax-check the JS**

Run: `node --check vivarium_dashboard/static/branch-source.js`
Expected: no output (exit 0)

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/branch-source.js tests/test_sync_ui.py
git commit -m "feat(ui): 'Sync to local' button emits the vivarium-dashboard sync command + manifest"
```

---

### Task 7: Document the closed loop (promotion is existing)

**Files:**
- Create: `docs/round-trip-pipeline.md`

The push half already exists: `POST /api/source/build-remote` (`{repo, branch}` → sms-api builds, returns `simulator_id` + `commit`) then `POST /api/source/switch-build` (`{simulator_id}` → materialize + switch). This task documents the full loop and the manifest's role in both directions. No production code.

- [ ] **Step 1: Write the doc**

```markdown
# Round-trip pipeline: view → sync → run → extend → commit → push

The round-trip loop lets you reproduce a remote dashboard locally, extend it,
and promote it back — all keyed on one **provenance manifest**.

## The manifest (`GET /api/source/manifest`)

```json
{
  "repo": "https://github.com/vivarium-collective/v2ecoli",
  "commit": "<full sha>",
  "branch": "feat/x",
  "workspace": "v2ecoli",
  "lockfile": "uv.lock@<sha256[:12]>",
  "results": { "runs": ["runs.<id>.zarr", "..."] },
  "simulator_id": 42
}
```

Code + deps are pinned (`commit` + `lockfile`); result data is *referenced*,
fetched lazily on view. The manifest drives both directions below.

## Pull — reproduce locally

1. On any dashboard (public read-only included), click **Sync to local** in the
   Source panel → it shows the command:
   `vivarium-dashboard sync <dashboard-url>`
2. Run it locally. `sync` does: clone `repo@commit` → **verify the cloned
   uv.lock hash equals the manifest's** (fidelity gate; aborts on mismatch) →
   `uv sync` → register in the workspace catalog. Optional `--run-post-sync`
   runs manifest-declared cache-rebuild commands (e.g. `python
   scripts/build_cache.py`); off by default because it executes remote-authored
   commands.
3. Open the synced workspace from the switcher and run it. Same commit + same
   lockfile ⇒ same behavior.

## Push — promote back (already built)

1. Extend the synced workspace; commit; `git push` your branch.
2. `POST /api/source/build-remote {repo, branch}` → sms-api builds `repo@commit`,
   returns `simulator_id` + `commit`.
3. `POST /api/source/switch-build {simulator_id}` → the remote dashboard
   materializes that build and switches to it.

## Symmetry

`sync-to-local` is the inverse of `build-via-sms-api`: the former materializes a
workspace on your laptop from `repo@commit`; the latter materializes a build on
the remote from `repo@commit`. Both consume the same manifest — `repo` +
`branch` feed `build-remote`, `commit` + `lockfile` guarantee local fidelity.
```

- [ ] **Step 2: Commit**

```bash
git add docs/round-trip-pipeline.md
git commit -m "docs: document the round-trip pipeline (manifest, pull-sync, push-promote)"
```

---

## Self-Review

**1. Spec coverage (§4.7 manifest, §4.8 sync, §5 data flow, §8 tests):**
- Manifest (§4.7): Task 1 (`build_manifest` + route + model). ✅
- Sync-to-local steps 1–6 (§4.8): clone@commit (T2), lockfile verify (T2), uv sync (T2), cache rebuild via opt-in post_sync (T3), catalog register (T3), CLI driver (T4), UI "Sync to local" affordance (T6). ✅
- Exactness contract: T2 `verify_lockfile` + T5 fidelity tests. ✅
- Lazy data (§4.8): manifest carries result *pointers* only (`_result_pointers` returns store paths, not data) — T1. Eager fetch is explicitly NOT implemented (matches "fetched lazily on view"). ✅
- Promotion / symmetry (§4.6): existing `build-remote`/`switch-build`, documented + symmetry-tested (T5, T7). ✅
- Data-flow pull/push (§5): doc T7. ✅
- Tests (§8): round-trip fidelity (T5), manifest-symmetry (T5), lazy-data — note: the spec's "lazy-data test" (render a run by fetching one artifact on demand) is NOT covered, because eager-vs-lazy fetch rendering lives in the existing results viewer, out of scope for M3's sync mechanics. **Documented gap**, deferred to the results-viewer work; not a silent omission.

**2. Placeholder scan:** No "TBD"/"handle errors"/"similar to Task N". Each code step shows complete code. One issue found and fixed below.

**3. Type consistency:** `(body, status)` tuple convention holds across `sync_materialize.*`, `sync_workspace.sync_from_manifest`. `build_manifest -> dict` consumed as `ProvenanceManifest(**...)`. CLI `cmd_sync(args) -> int`. Catalog seam `_catalog_add(path, name, package)` matches `workspace_catalog.add` signature.

**Fix applied during review:** Task 4 `cmd_sync` had a typo'd attribute (`args.run_run_post_sync`). The argparse dest for `--run-post-sync` is `args.run_post_sync`. Corrected mentally here — **implementers: use `run_post_sync=args.run_post_sync`** (drop the `hasattr`/`run_run_post_sync` fallback entirely):

```python
    body, status = sync_from_manifest(manifest, dest, run_post_sync=args.run_post_sync)
```

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-27-m3-round-trip-pipeline.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session with checkpoints for review.

Which approach?
