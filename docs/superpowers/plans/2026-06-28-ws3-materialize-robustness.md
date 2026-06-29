# WS3 — Materialize robustness + standalone activation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `materialize_build` survive the real (multi-minute) workspace download and stamp `.viv-build.json` so a standalone remote-sourced dashboard activates the (already-working) remote-simulations merge.

**Architecture:** Two focused changes to existing libs. (1) Give the sms-api client's two streaming downloads a per-call `timeout` override (transport stays single-shot). (2) `materialize_build` passes a long download timeout and stamps a minimal `.viv-build.json` marker, idempotently and without clobbering the richer stamp that `switch-build` writes.

**Tech Stack:** Python 3.12, stdlib `urllib`/`tarfile`/`json`, pytest with `monkeypatch`.

## Global Constraints

- **No new dependencies** — stdlib only (`urllib`, `tarfile`, `json`, `shutil`).
- **Python-first, AI-free.** Tests monkeypatch the sms-api client / `urlopen`; never hit a real sms-api.
- **`.viv-build.json` schema** (consumed by `lib/remote_simulations.py:_read_build_meta`): requires `simulator_id`; `commit` is an optional fallback. The richer stamp written by `lib/source_build_views.py:112` is `{simulator_id, repo, branch, commit, repo_url}` — never overwrite it.
- Run tests with the v2ecoli venv: `cd /Users/eranagmon/code/v2ecoli && .venv/bin/python -m pytest <path> -v` (it has the dashboard installed). Work happens in worktree `/Users/eranagmon/code/vdash-remote-dash` (branch `feat/remote-sourced-dashboard`).

---

### Task 1: Per-call timeout on the two streaming downloads

**Files:**
- Modify: `vivarium_dashboard/lib/sms_api_client.py` — `download_workspace` (line 64), `download_data` (line 140)
- Test: `tests/test_remote_build_source.py` (alongside `test_download_workspace_streams_to_file`; uses the existing `sac` import + `_Resp` helper)

**Interfaces:**
- Produces: `SmsApiClient.download_workspace(simulator_id, dest_dir, timeout: float | None = None) -> Path` and `SmsApiClient.download_data(simulation_id, dest_dir, timeout: float | None = None) -> Path`. When `timeout is None`, falls back to `self.timeout`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_remote_build_source.py`:
```python
def test_download_workspace_honors_per_call_timeout(monkeypatch, tmp_path):
    seen = {}
    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _Resp(b"X")
    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    sac.SmsApiClient("http://x", timeout=30).download_workspace(45, tmp_path, timeout=600)
    assert seen["timeout"] == 600


def test_download_workspace_defaults_to_client_timeout(monkeypatch, tmp_path):
    seen = {}
    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _Resp(b"X")
    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    sac.SmsApiClient("http://x", timeout=30).download_workspace(45, tmp_path)
    assert seen["timeout"] == 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/eranagmon/code/v2ecoli && .venv/bin/python -m pytest /Users/eranagmon/code/vdash-remote-dash/tests/test_remote_build_source.py::test_download_workspace_honors_per_call_timeout -v`
Expected: FAIL — `download_workspace()` got an unexpected keyword argument `timeout`.

- [ ] **Step 3: Add the `timeout` param to both downloads**

In `vivarium_dashboard/lib/sms_api_client.py`, change `download_workspace`'s signature and the `urlopen` call:
```python
    def download_workspace(self, simulator_id: int, dest_dir: Path, timeout: float | None = None) -> Path:
        """Stream a build's repo@commit workspace tarball (SP1's endpoint) to
        dest_dir/workspace.tar.gz."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / "workspace.tar.gz"
        url = f"{self.base_url}/api/v1/simulations/workspace?simulator_id={simulator_id}"
        req = Request(url, method="GET", headers={"Accept": "application/gzip"})
        to = timeout if timeout is not None else self.timeout
        try:
            with urlopen(req, timeout=to) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}") from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        return out_path
```
Apply the identical `timeout: float | None = None` param + `to = timeout if timeout is not None else self.timeout` + `urlopen(req, timeout=to)` change to `download_data` (line 140).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/eranagmon/code/v2ecoli && .venv/bin/python -m pytest /Users/eranagmon/code/vdash-remote-dash/tests/test_remote_build_source.py -k "timeout or download" -v`
Expected: PASS (new timeout tests + the pre-existing `test_download_workspace_streams_to_file`).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vdash-remote-dash
git add vivarium_dashboard/lib/sms_api_client.py tests/test_remote_build_source.py
git commit -m "feat(sms-api-client): per-call timeout override on streaming downloads"
```

---

### Task 2: `materialize_build` uses a long download timeout

**Files:**
- Modify: `vivarium_dashboard/lib/remote_build_source.py` — add `_DOWNLOAD_TIMEOUT_S`; pass it in `materialize_build` (line 62)
- Test: `tests/test_remote_build_source.py` — update `_FakeClient.download_workspace` to capture the timeout, add one test

**Interfaces:**
- Consumes: `SmsApiClient.download_workspace(..., timeout=...)` from Task 1.
- Produces: `materialize_build` downloads with `timeout=_DOWNLOAD_TIMEOUT_S` (≥ 300 s).

- [ ] **Step 1: Update the test double + write the failing test**

In `tests/test_remote_build_source.py`, update `_FakeClient` to accept + record the timeout:
```python
class _FakeClient:
    def __init__(self, tarball_src):
        self._src = tarball_src
        self.downloads = 0
        self.timeout_seen = None

    def download_workspace(self, simulator_id, dest_dir, timeout=None):
        import shutil
        self.downloads += 1
        self.timeout_seen = timeout
        dest = Path(dest_dir) / "workspace.tar.gz"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self._src, dest)
        return dest
```
(Leave `list_simulators` unchanged.) Add the test:
```python
def test_materialize_uses_long_download_timeout(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    client = _FakeClient(tb)
    rbs.materialize_build(client, 45, "32b901")
    assert client.timeout_seen is not None and client.timeout_seen >= 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/eranagmon/code/v2ecoli && .venv/bin/python -m pytest /Users/eranagmon/code/vdash-remote-dash/tests/test_remote_build_source.py::test_materialize_uses_long_download_timeout -v`
Expected: FAIL — `client.timeout_seen` is `None` (materialize calls `download_workspace` with no timeout).

- [ ] **Step 3: Pass a long timeout from `materialize_build`**

In `vivarium_dashboard/lib/remote_build_source.py`, add a module constant near `_COMMIT_RE` (after line 27):
```python
# A real workspace tarball is ~50MB and takes minutes over the SSM tunnel
# (measured ~224s); the client's 30s default would hard-fail the switch.
_DOWNLOAD_TIMEOUT_S = 600.0
```
Then change the download call inside `materialize_build` (line 62):
```python
        tar_path = client.download_workspace(simulator_id, staging, timeout=_DOWNLOAD_TIMEOUT_S)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/eranagmon/code/v2ecoli && .venv/bin/python -m pytest /Users/eranagmon/code/vdash-remote-dash/tests/test_remote_build_source.py -k materialize -v`
Expected: PASS (new timeout test + the pre-existing materialize tests, which now exercise the `timeout=` kwarg through the updated fake).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vdash-remote-dash
git add vivarium_dashboard/lib/remote_build_source.py tests/test_remote_build_source.py
git commit -m "feat(materialize): use a 600s download timeout (tarball takes minutes)"
```

---

### Task 3: `materialize_build` stamps `.viv-build.json` (idempotent, no-clobber)

**Files:**
- Modify: `vivarium_dashboard/lib/remote_build_source.py` — add `import json`; add `_stamp_build_meta`; call it before both `return cache` paths in `materialize_build`
- Test: `tests/test_remote_build_source.py` — two tests

**Interfaces:**
- Produces: after `materialize_build(client, sid, commit)`, the cache dir contains `.viv-build.json` with at least `{"simulator_id": sid, "commit": commit}` — unless a stamp already exists (then it is left untouched). This is what `lib/remote_simulations.py` reads to merge the deployment's runs into the Sim DB.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_remote_build_source.py`:
```python
def test_materialize_stamps_viv_build_json(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    cache = rbs.materialize_build(_FakeClient(tb), 45, "32b901")
    meta = json.loads((cache / ".viv-build.json").read_text())
    assert meta["simulator_id"] == 45
    assert meta["commit"] == "32b901"


def test_materialize_does_not_clobber_existing_stamp(_cache, tmp_path):
    tb = tmp_path / "src.tar.gz"; _make_tarball(tb)
    cache = rbs.materialize_build(_FakeClient(tb), 45, "32b901")
    # simulate switch-build's richer stamp, then re-materialize (reuse path)
    (cache / ".viv-build.json").write_text('{"simulator_id": 45, "branch": "main", "rich": true}')
    rbs.materialize_build(_FakeClient(tb), 45, "32b901")
    meta = json.loads((cache / ".viv-build.json").read_text())
    assert meta.get("rich") is True
```
Ensure the test file imports json at the top (add `import json` if absent).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/eranagmon/code/v2ecoli && .venv/bin/python -m pytest /Users/eranagmon/code/vdash-remote-dash/tests/test_remote_build_source.py::test_materialize_stamps_viv_build_json -v`
Expected: FAIL — `.viv-build.json` does not exist (FileNotFoundError).

- [ ] **Step 3: Add the stamp helper and call it**

In `vivarium_dashboard/lib/remote_build_source.py`, add `import json` to the imports block (after `import os`). Add the helper after `_safe_commit` (after line 43):
```python
def _stamp_build_meta(cache: Path, simulator_id: int, commit: str) -> None:
    """Mark a materialized cache as a remote build so the Simulations DB merges
    the deployment's runs (lib/remote_simulations.py reads this). No-clobber:
    switch-build writes a richer stamp (repo/branch/repo_url); never overwrite it."""
    meta = cache / ".viv-build.json"
    if meta.exists():
        return
    try:
        meta.write_text(json.dumps({"simulator_id": simulator_id, "commit": commit}))
    except OSError:
        pass  # provenance stamp is best-effort, never block materialize
```
Then in `materialize_build`, stamp before each `return cache`. The reuse path (currently lines 55-56):
```python
    if cache.exists() and not force:
        _stamp_build_meta(cache, simulator_id, commit)
        return cache
```
And the fresh path, after `os.replace(...)` and the `finally`, replace the final `return cache` (line 79):
```python
    _stamp_build_meta(cache, simulator_id, commit)
    return cache
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/eranagmon/code/v2ecoli && .venv/bin/python -m pytest /Users/eranagmon/code/vdash-remote-dash/tests/test_remote_build_source.py -v`
Expected: PASS (both new tests + all pre-existing tests in the file).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vdash-remote-dash
git add vivarium_dashboard/lib/remote_build_source.py tests/test_remote_build_source.py
git commit -m "feat(materialize): stamp .viv-build.json so standalone launch activates remote-sims"
```

---

## Self-Review

**Spec coverage (WS3 only):** WS3 calls for (a) download timeout/parameterization → Task 1 + Task 2; (b) `.viv-build.json` on every materialize path → Task 3. The WS3 "surface progress" item is intentionally deferred (YAGNI for the first cut — a 600s timeout removes the hard failure; a progress callback is a follow-up, noted in the spec). WS1/WS2/WS4 are out of scope for this plan (separate plans).

**Placeholder scan:** none — every step has concrete code, commands, and expected output.

**Type consistency:** `timeout: float | None = None` and the `to = timeout if timeout is not None else self.timeout` fallback are identical across `download_workspace`/`download_data`. `_FakeClient.download_workspace` signature updated to match the real client (`timeout=None`). `_stamp_build_meta(cache, simulator_id, commit)` is the single writer; `materialize_build` calls it with its own `simulator_id`/validated `commit`. `_DOWNLOAD_TIMEOUT_S = 600.0 ≥ 300` satisfies the Task 2 assertion.

## Follow-on plans (not in this plan)
- **WS2** — teach the live chart reader the XArray-emitter zarr-v3 layout (the results blocker). Needs a read of `lib/study_charts._extract_paths_from_zarr` first; PoC fixture: the sim-199 336K v3 store.
- **WS1** — thin-client remote-run rewrite (coordinate with `2026-06-26-remote-run-thin-client-design.md`).
- **WS4** — backend-reachability pill, instance identity, branch-push transparency.
