# Remote Runs — Phase 3b: orchestration + endpoints — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Phase 3a pieces into a usable flow: a logged-in user POSTs to start a remote v2ecoli run on smsvpctest, a background `RemoteRunManager` runs push→build→run→poll→download→land, and the UI polls status. The run lands as a study run (Phase 3a-rev). No UI yet (Phase 3c).

**Architecture:** A dedicated `RemoteRunManager` (mirrors `run_jobs.manager`) runs one multi-STEP pipeline per job on a background thread, tracking per-step status. The pipeline is a pure function with the `SmsApiClient`, the git-push step, and `land_remote_run` injected, so it unit-tests without a live sms-api or network. Two new endpoints: `POST /api/remote-run-start` (gated by `github_auth.current_session()`) and `GET /api/remote-run-status`. `smsApiBase` comes from `SMS_API_BASE` env (default `http://localhost:8080`).

**Tech Stack:** Python 3.11+, stdlib `threading`/`subprocess`/`tempfile`, `SmsApiClient` + `land_remote_run` (Phase 3a), `composite_runs`, `github_auth`, `investigations.load_spec`, pytest.

**Repo:** `/Users/eranagmon/code/vivarium-dashboard` (branch `feat/dashboard-remote-runs`).

## Design choices (encoded here — flag if wrong)
- **What runs:** the run builds a simulator from the **workspace's current GitHub branch at HEAD** (push it, take the commit SHA) — i.e. "run my current v2ecoli code on the remote for this study." `git_repo_url` = `git remote get-url origin`; `git_branch` = current branch; `git_commit_hash` = `git rev-parse HEAD` after push.
- **Run config:** `num_generations`, `num_seeds`, `run_parca` are **dashboard inputs** (request body). `observables` (the emitter config) come from the **study's** observables (`_collect_study_observables`). `n_steps` is NOT a run-endpoint param (Ray uses `config.ray_n_steps`), so it's omitted.
- **Backend:** Ray is auto-selected by sms-api for v2ecoli repos; the dashboard doesn't choose it.
- **Landing:** Phase 3a-rev `land_remote_run` (download `/data` tar.gz → place native store). Provenance carries `simulation_id`.

## Global Constraints
- No new deps — stdlib only; reuse `SmsApiClient`, `land_remote_run`, `composite_runs`, `github_auth`.
- `smsApiBase` = `os.environ.get("SMS_API_BASE", "http://localhost:8080")` — one helper, read by the start handler.
- `POST /api/remote-run-start` MUST 401 when `github_auth.current_session()` is None.
- The pipeline must be injectable (client, push function, landing function as params) so tests use fakes — no real network/git in unit tests.
- Per-step status strings: `pending | running | done | failed`. Job status: `queued | running | done | failed`.

## Confirmed interfaces (use verbatim)
- `run_jobs` pattern: `manager.submit(investigation, items, worker_fn)`, `manager.get(job_id)`, `manager.list_recent(10)`, `RunJob.to_dict()`, `job.update_item(idx, **fields)` (`lib/run_jobs.py:51-120`).
- Git: `git remote get-url origin`; `git rev-parse --abbrev-ref HEAD`; `git push -u origin <branch>` with `env = os.environ | github_auth.current_token_env()`; `git rev-parse HEAD`. `_has_origin_remote()` (server.py:6604). `WORKSPACE` is the workspace root global in server.py.
- `github_auth.current_session() -> Session | None`; `current_token_env() -> dict` (`lib/github_auth.py:458,506`).
- Study: `_study_spec_path(name)` + `investigations.load_spec(path) -> dict`; `_collect_study_observables(spec) -> list[str]` (server.py:4982).
- `SmsApiClient` (Phase 3a): `latest_simulator`, `upload_simulator(simulator: dict, force=False)`, `simulator_status(id)`, `run_simulation(*, simulator_id, num_generations, num_seeds, run_parca, observables, experiment_id=None)`, `simulation_status(id)`, `download_data(id, dest_dir) -> Path`. `SmsApiError`.
- `land_remote_run(study_dir, *, spec_id, simulation_id, experiment_id, commit, tar_path, seed=0, label=None) -> str` (Phase 3a-rev).
- `_json(data, code)`; POST via `_POST_ROUTE_MAP`; GET via `do_GET` startswith dispatch; query via `parse_qs(urlparse(self.path).query)`.

## File Structure
- Create `vivarium_dashboard/lib/remote_run_jobs.py` — `RemoteRunStep`, `RemoteRunJob`, `RemoteRunManager` (+ module singleton `manager`), and `run_remote_pipeline(job, *, ctx)` (the injectable worker).
- Modify `vivarium_dashboard/server.py` — `_sms_api_base()` helper; `_post_remote_run_start`; `_get_remote_run_status`; register both routes; a `_remote_push_and_sha()` helper (real git push).
- Test `tests/test_remote_run_jobs.py` — manager threading + pipeline happy-path + per-step failure (fakes).
- Test `tests/test_remote_run_endpoints.py` — `_sms_api_base` default/override; the 401 gate (light, via the handler helpers).

---

## Task 1: `RemoteRunJob` + `RemoteRunManager`

**Files:**
- Create: `vivarium_dashboard/lib/remote_run_jobs.py`
- Test: `tests/test_remote_run_jobs.py`

**Interfaces:**
- Produces:
  - `STEP_NAMES = ["push", "build", "run", "poll", "download", "land"]`
  - `RemoteRunJob`: attrs `job_id` (12-hex), `study`, `status` ("queued"), `steps` (list of `{name, status, message}` one per STEP_NAMES, all "pending"), `run_id` (None), `error` (None), `started_at`, `completed_at`; methods `set_step(name, status, message="")` (thread-safe), `to_dict()`.
  - `RemoteRunManager`: `submit(study: str, worker_fn: Callable[[RemoteRunJob], None]) -> RemoteRunJob` (starts daemon thread; sets status running→done/failed and completed_at on exit), `get(job_id) -> RemoteRunJob | None`, `list_recent(n) -> list[dict]`.
  - module singleton `manager = RemoteRunManager()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_remote_run_jobs.py`:

```python
import time

from vivarium_dashboard.lib.remote_run_jobs import RemoteRunManager, STEP_NAMES


def _wait(job, timeout=5.0):
    t0 = time.time()
    while job.status in ("queued", "running") and time.time() - t0 < timeout:
        time.sleep(0.01)


def test_job_has_a_step_per_name_all_pending():
    mgr = RemoteRunManager()
    done = []

    def worker(job):
        done.append(job.job_id)

    job = mgr.submit("study-a", worker)
    _wait(job)
    d = job.to_dict()
    assert [s["name"] for s in d["steps"]] == STEP_NAMES
    assert d["study"] == "study-a"
    assert d["status"] == "done"
    assert mgr.get(job.job_id) is job


def test_worker_exception_marks_job_failed():
    mgr = RemoteRunManager()

    def worker(job):
        job.set_step("push", "running")
        raise RuntimeError("boom")

    job = mgr.submit("s", worker)
    _wait(job)
    d = job.to_dict()
    assert d["status"] == "failed"
    assert "boom" in (d["error"] or "")


def test_set_step_updates_status_and_message():
    mgr = RemoteRunManager()

    def worker(job):
        job.set_step("build", "done", "simulator 15")

    job = mgr.submit("s", worker)
    _wait(job)
    build = next(s for s in job.to_dict()["steps"] if s["name"] == "build")
    assert build["status"] == "done"
    assert build["message"] == "simulator 15"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && .venv/bin/python -m pytest tests/test_remote_run_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: vivarium_dashboard.lib.remote_run_jobs`.

- [ ] **Step 3: Write minimal implementation**

Create `vivarium_dashboard/lib/remote_run_jobs.py`:

```python
"""Background orchestration for remote (smsvpctest) simulation runs.

Mirrors lib/run_jobs.py but models ONE multi-step pipeline per job (push →
build → run → poll → download → land) rather than many items. The pipeline
worker is injected (see run_remote_pipeline) so it can be unit-tested with
fakes — this module has no network/git/sms-api knowledge itself.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable

STEP_NAMES = ["push", "build", "run", "poll", "download", "land"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class RemoteRunJob:
    def __init__(self, study: str) -> None:
        self.job_id = uuid.uuid4().hex[:12]
        self.study = study
        self.status = "queued"
        self.steps = [{"name": n, "status": "pending", "message": ""} for n in STEP_NAMES]
        self.run_id: str | None = None
        self.error: str | None = None
        self.started_at = _now()
        self.completed_at: str | None = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def set_step(self, name: str, status: str, message: str = "") -> None:
        with self._lock:
            for s in self.steps:
                if s["name"] == name:
                    s["status"] = status
                    if message:
                        s["message"] = message
                    break

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "job_id": self.job_id,
                "study": self.study,
                "status": self.status,
                "steps": [dict(s) for s in self.steps],
                "run_id": self.run_id,
                "error": self.error,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
            }


class RemoteRunManager:
    def __init__(self) -> None:
        self._jobs: dict[str, RemoteRunJob] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def submit(self, study: str, worker_fn: Callable[[RemoteRunJob], None]) -> RemoteRunJob:
        job = RemoteRunJob(study)
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)

        def _run() -> None:
            job.status = "running"
            try:
                worker_fn(job)
                job.status = "failed" if job.error else "done"
            except Exception:  # noqa: BLE001 — record any worker failure on the job
                job.error = traceback.format_exc(limit=4)
                job.status = "failed"
            finally:
                job.completed_at = _now()

        t = threading.Thread(target=_run, daemon=True)
        job._worker = t
        t.start()
        return job

    def get(self, job_id: str) -> RemoteRunJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, n: int = 10) -> list[dict]:
        with self._lock:
            ids = self._order[-n:][::-1]
            jobs = [self._jobs[i] for i in ids]
        return [j.to_dict() for j in jobs]


manager = RemoteRunManager()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remote_run_jobs.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/remote_run_jobs.py tests/test_remote_run_jobs.py
git commit -m "feat(remote-runs): RemoteRunManager + RemoteRunJob (multi-step pipeline jobs)"
```

---

## Task 2: `run_remote_pipeline` (injectable worker)

**Files:**
- Modify: `vivarium_dashboard/lib/remote_run_jobs.py`
- Test: `tests/test_remote_run_jobs.py`

**Interfaces:**
- Consumes: `RemoteRunJob`, `SmsApiError`.
- Produces:
  - `@dataclass PipelineCtx`: `study: str`, `study_dir: Path`, `spec_id: str`, `repo_url: str`, `branch: str`, `observables: list[str]`, `num_generations: int`, `num_seeds: int`, `run_parca: bool`, `client` (SmsApiClient-like), `push_and_sha: Callable[[], str]` (pushes, returns commit SHA), `land: Callable[..., str]` (land_remote_run-like), `poll_interval: float = 5.0`, `poll_timeout: float = 3600.0`.
  - `run_remote_pipeline(job: RemoteRunJob, ctx: PipelineCtx) -> None` — runs the 6 steps, updating job; sets `job.run_id`; on any failure sets `job.error` and marks the current step failed (raises nothing — the manager reads `job.error`).
  - `_poll(get_status: Callable[[], dict], terminal: set[str], interval, timeout) -> dict` — polls until `status` field in terminal or timeout.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_remote_run_jobs.py`:

```python
from pathlib import Path

from vivarium_dashboard.lib.remote_run_jobs import PipelineCtx, run_remote_pipeline


class _FakeClient:
    def __init__(self):
        self.calls = []

    def upload_simulator(self, simulator, force=False):
        self.calls.append(("upload", simulator))
        return {"database_id": 15, "status": "running"}

    def simulator_status(self, simulator_id):
        return {"status": "completed"}

    def run_simulation(self, **kw):
        self.calls.append(("run", kw))
        return {"database_id": 50}

    def simulation_status(self, simulation_id):
        return {"status": "completed"}

    def download_data(self, simulation_id, dest_dir):
        p = Path(dest_dir) / f"sim_{simulation_id}.tar.gz"
        p.write_bytes(b"x")
        return p


def _ctx(tmp_path, client, **over):
    landed = {}

    def land(study_dir, **kw):
        landed["kw"] = kw
        return "run_abc"

    base = dict(
        study="s", study_dir=tmp_path, spec_id="v2ecoli.composites.baseline",
        repo_url="https://github.com/x/v2ecoli", branch="main",
        observables=["listeners/mass/cell_mass"], num_generations=1, num_seeds=1,
        run_parca=True, client=client, push_and_sha=lambda: "deadbeef",
        land=land, poll_interval=0.0, poll_timeout=5.0,
    )
    base.update(over)
    return PipelineCtx(**base), landed


def test_pipeline_happy_path(tmp_path):
    from vivarium_dashboard.lib.remote_run_jobs import RemoteRunJob

    client = _FakeClient()
    ctx, landed = _ctx(tmp_path, client)
    job = RemoteRunJob("s")
    run_remote_pipeline(job, ctx)
    assert job.error is None
    assert job.run_id == "run_abc"
    assert all(s["status"] == "done" for s in job.steps)
    # observables threaded into run_simulation; commit threaded into upload + land
    run_kw = next(c[1] for c in client.calls if c[0] == "run")
    assert run_kw["observables"] == ["listeners/mass/cell_mass"]
    assert run_kw["simulator_id"] == 15
    assert landed["kw"]["simulation_id"] == 50
    assert landed["kw"]["commit"] == "deadbeef"


def test_pipeline_marks_failed_step_on_error(tmp_path):
    from vivarium_dashboard.lib.remote_run_jobs import RemoteRunJob

    client = _FakeClient()

    def boom():
        raise RuntimeError("push rejected")

    ctx, _ = _ctx(tmp_path, client, push_and_sha=boom)
    job = RemoteRunJob("s")
    run_remote_pipeline(job, ctx)
    assert job.error is not None and "push rejected" in job.error
    push = next(s for s in job.steps if s["name"] == "push")
    assert push["status"] == "failed"
    # later steps never ran
    assert next(s for s in job.steps if s["name"] == "build")["status"] == "pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_remote_run_jobs.py -k pipeline -v`
Expected: FAIL — `ImportError: cannot import name 'PipelineCtx'`.

- [ ] **Step 3: Write minimal implementation**

Add to `vivarium_dashboard/lib/remote_run_jobs.py` (add imports `import tempfile`, `from dataclasses import dataclass`, `from pathlib import Path`):

```python
@dataclass
class PipelineCtx:
    study: str
    study_dir: Path
    spec_id: str
    repo_url: str
    branch: str
    observables: list[str]
    num_generations: int
    num_seeds: int
    run_parca: bool
    client: object
    push_and_sha: Callable[[], str]
    land: Callable[..., str]
    poll_interval: float = 5.0
    poll_timeout: float = 3600.0


def _poll(get_status: Callable[[], dict], terminal: set[str], interval: float, timeout: float) -> dict:
    t0 = time.time()
    while True:
        st = get_status()
        if str(st.get("status", "")).lower() in terminal:
            return st
        if time.time() - t0 > timeout:
            raise TimeoutError(f"polling timed out after {timeout}s (last status {st.get('status')!r})")
        time.sleep(interval)


_TERMINAL_OK = {"completed", "done", "succeeded"}
_TERMINAL_BAD = {"failed", "cancelled", "error"}


def run_remote_pipeline(job: RemoteRunJob, ctx: PipelineCtx) -> None:
    """Run push→build→run→poll→download→land, updating job. Records job.error on failure."""
    try:
        # 1. push → commit SHA
        job.set_step("push", "running")
        commit = ctx.push_and_sha()
        job.set_step("push", "done", commit[:12])

        # 2. build simulator from the pushed commit
        job.set_step("build", "running")
        simulator = {"git_commit_hash": commit, "git_repo_url": ctx.repo_url, "git_branch": ctx.branch}
        uploaded = ctx.client.upload_simulator(simulator)
        simulator_id = uploaded["database_id"]
        build = _poll(
            lambda: ctx.client.simulator_status(simulator_id),
            _TERMINAL_OK | _TERMINAL_BAD, ctx.poll_interval, ctx.poll_timeout,
        )
        if str(build.get("status", "")).lower() in _TERMINAL_BAD:
            raise RuntimeError(f"simulator build failed: {build.get('error_message') or build.get('status')}")
        job.set_step("build", "done", f"simulator {simulator_id}")

        # 3. run simulation (Ray auto-selected for v2ecoli)
        job.set_step("run", "running")
        sim = ctx.client.run_simulation(
            simulator_id=simulator_id, num_generations=ctx.num_generations,
            num_seeds=ctx.num_seeds, run_parca=ctx.run_parca, observables=ctx.observables,
        )
        simulation_id = sim["database_id"]
        experiment_id = sim.get("experiment_id") or f"sim{simulator_id}-{ctx.study}"
        job.set_step("run", "done", f"simulation {simulation_id}")

        # 4. poll to terminal
        job.set_step("poll", "running")
        st = _poll(
            lambda: ctx.client.simulation_status(simulation_id),
            _TERMINAL_OK | _TERMINAL_BAD, ctx.poll_interval, ctx.poll_timeout,
        )
        if str(st.get("status", "")).lower() in _TERMINAL_BAD:
            raise RuntimeError(f"simulation failed: {st.get('error_message') or st.get('status')}")
        job.set_step("poll", "done")

        # 5. download native store
        job.set_step("download", "running")
        with tempfile.TemporaryDirectory() as td:
            tar_path = ctx.client.download_data(simulation_id, Path(td))
            job.set_step("download", "done")

            # 6. land as a study run
            job.set_step("land", "running")
            run_id = ctx.land(
                ctx.study_dir, spec_id=ctx.spec_id, simulation_id=simulation_id,
                experiment_id=experiment_id, commit=commit, tar_path=tar_path,
            )
        job.run_id = run_id
        job.set_step("land", "done", run_id)
    except Exception as e:  # noqa: BLE001 — surface any step failure on the job
        job.error = f"{type(e).__name__}: {e}"
        for s in job.steps:
            if s["status"] == "running":
                job.set_step(s["name"], "failed", str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remote_run_jobs.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/remote_run_jobs.py tests/test_remote_run_jobs.py
git commit -m "feat(remote-runs): run_remote_pipeline injectable worker (push->build->run->poll->download->land)"
```

---

## Task 3: server endpoints + `smsApiBase` + git-push helper

**Files:**
- Modify: `vivarium_dashboard/server.py`
- Test: `tests/test_remote_run_endpoints.py`

**Interfaces:**
- Consumes: `remote_run_jobs.manager/run_remote_pipeline/PipelineCtx`, `SmsApiClient`, `land_remote_run`, `github_auth`, `_collect_study_observables`, `_study_spec_path`, `load_spec`, `_has_origin_remote`, `WORKSPACE`.
- Produces:
  - `_sms_api_base() -> str` → `os.environ.get("SMS_API_BASE", "http://localhost:8080")`.
  - `_remote_push_and_sha() -> str` — push the current branch with the GH token env, return `git rev-parse HEAD`. Raises `RuntimeError` on push failure.
  - `_post_remote_run_start(self, body)` — 401 if not logged in; 409 if no origin remote; reads study observables; submits a job whose worker calls `run_remote_pipeline`; returns `{job_id}` (202).
  - `_get_remote_run_status(self)` — `?job_id=` → `manager.get(...).to_dict()` (404 if missing) else `{jobs: manager.list_recent(10)}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_remote_run_endpoints.py`:

```python
import importlib
import os


def test_sms_api_base_default_and_override(monkeypatch):
    server = importlib.import_module("vivarium_dashboard.server")
    monkeypatch.delenv("SMS_API_BASE", raising=False)
    assert server._sms_api_base() == "http://localhost:8080"
    monkeypatch.setenv("SMS_API_BASE", "http://localhost:9000")
    assert server._sms_api_base() == "http://localhost:9000"


def test_remote_run_start_requires_login(monkeypatch):
    server = importlib.import_module("vivarium_dashboard.server")
    from vivarium_dashboard.lib import github_auth

    monkeypatch.setattr(github_auth, "current_session", lambda: None)

    captured = {}

    class _H:
        _json = lambda self, data, code: captured.update(data=data, code=code)
        _post_remote_run_start = server.DashboardHandler._post_remote_run_start

    _H()._post_remote_run_start({"study": "s"})
    assert captured["code"] == 401
```

Note: confirm the handler class name in server.py (the class that owns `_post_*` methods — e.g. `DashboardHandler` / `Handler`); use the actual name at the `class ...(BaseHTTPRequestHandler)` definition.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_remote_run_endpoints.py -v`
Expected: FAIL — `AttributeError: ... has no attribute '_sms_api_base'` / `_post_remote_run_start`.

- [ ] **Step 3: Write minimal implementation**

In `server.py`, add module-level helpers (near other git helpers like `_has_origin_remote`):

```python
def _sms_api_base() -> str:
    """Base URL of the sms-api (the SSM tunnel by default)."""
    return os.environ.get("SMS_API_BASE", "http://localhost:8080")


def _remote_push_and_sha() -> str:
    """Push the workspace's current branch to origin with the GH token, return HEAD SHA."""
    from vivarium_dashboard.lib import github_auth

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=WORKSPACE,
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if not branch or branch == "HEAD":
        raise RuntimeError("workspace is not on a named branch")
    env = os.environ | github_auth.current_token_env()
    push = subprocess.run(
        ["git", "push", "-u", "origin", branch], cwd=WORKSPACE,
        capture_output=True, text=True, timeout=120, env=env,
    )
    if push.returncode != 0:
        raise RuntimeError(f"git push failed: {(push.stderr or push.stdout)[-300:]}")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=WORKSPACE, capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    if not sha:
        raise RuntimeError("could not resolve HEAD commit")
    return sha


def _remote_repo_url() -> str | None:
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=WORKSPACE,
        capture_output=True, text=True, timeout=5,
    )
    return r.stdout.strip() or None if r.returncode == 0 else None
```

Add the handler methods on the dashboard request-handler class:

```python
    def _post_remote_run_start(self, body: dict):
        """POST /api/remote-run-start {study, num_generations?, num_seeds?, run_parca?}"""
        from vivarium_dashboard.lib import github_auth
        from vivarium_dashboard.lib.investigations import load_spec
        from vivarium_dashboard.lib.remote_run_jobs import PipelineCtx, manager, run_remote_pipeline
        from vivarium_dashboard.lib.remote_run_landing import land_remote_run
        from vivarium_dashboard.lib.sms_api_client import SmsApiClient

        if github_auth.current_session() is None:
            return self._json({"error": "not authenticated"}, 401)
        study = (body.get("study") or "").strip()
        if not study:
            return self._json({"error": "study is required"}, 400)
        if not _has_origin_remote():
            return self._json({"error": "no GitHub remote configured"}, 409)
        repo_url = _remote_repo_url()
        if not repo_url:
            return self._json({"error": "could not resolve origin remote url"}, 409)

        spec_path = _study_spec_path(study)
        if spec_path is None or not spec_path.is_file():
            return self._json({"error": f"study {study!r} not found"}, 404)
        spec = load_spec(spec_path)
        observables = _collect_study_observables(spec)

        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=WORKSPACE,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()

        client = SmsApiClient(_sms_api_base())
        ctx = PipelineCtx(
            study=study,
            study_dir=_study_dir(study),
            spec_id=(spec.get("baseline", [{}])[0].get("name") if spec.get("baseline") else study),
            repo_url=repo_url,
            branch=branch,
            observables=observables,
            num_generations=int(body.get("num_generations") or 1),
            num_seeds=int(body.get("num_seeds") or 1),
            run_parca=bool(body.get("run_parca", True)),
            client=client,
            push_and_sha=_remote_push_and_sha,
            land=land_remote_run,
        )
        job = manager.submit(study, lambda j: run_remote_pipeline(j, ctx))
        return self._json({"job_id": job.job_id}, 202)

    def _get_remote_run_status(self):
        """GET /api/remote-run-status?job_id=<id>"""
        from urllib.parse import parse_qs, urlparse

        from vivarium_dashboard.lib.remote_run_jobs import manager

        qs = parse_qs(urlparse(self.path).query)
        job_id = (qs.get("job_id") or [""])[0]
        if not job_id:
            return self._json({"jobs": manager.list_recent(10)}, 200)
        job = manager.get(job_id)
        if job is None:
            return self._json({"error": "job not found"}, 404)
        return self._json(job.to_dict(), 200)
```

Register routes: add `"/api/remote-run-start": "_post_remote_run_start"` to `_POST_ROUTE_MAP`, and in `do_GET` add `if self.path.startswith("/api/remote-run-status"): return self._get_remote_run_status()`.

(If `_study_dir`/`_study_spec_path`/`_collect_study_observables` are methods rather than module functions, call them via `self.`/the right scope — match how peer handlers call them in server.py.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remote_run_endpoints.py -v`
Expected: PASS (2 tests). If the handler class name differs, fix the test's class reference to the real one and re-run.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/server.py tests/test_remote_run_endpoints.py
git commit -m "feat(remote-runs): remote-run-start/-status endpoints + smsApiBase + push helper"
```

---

## Task 4: full-suite + regression

- [ ] **Step 1:** Run: `.venv/bin/python -m pytest tests/test_remote_run_jobs.py tests/test_remote_run_endpoints.py tests/test_sms_api_client.py tests/test_remote_run_landing.py -v` → all pass.
- [ ] **Step 2:** Run: `.venv/bin/python -m pytest tests/ -k "remote or runs or chart" -q` → no NEW failures vs the known pre-existing set; note any failure naming the new modules.
- [ ] **Step 3:** Commit any fixes: `git add -A && git commit -m "test(remote-runs): phase 3b suite green" || echo "nothing"`.

---

## Self-Review

**Spec coverage:** `RemoteRunManager`/`RemoteRunJob` (Task 1) + injectable `run_remote_pipeline` running all 6 steps (Task 2) + login-gated `remote-run-start` / `remote-run-status` endpoints, `smsApiBase` env, real git-push helper (Task 3). Run config from dashboard input + study observables; lands via Phase 3a-rev. UI = Phase 3c.

**Placeholder scan:** none — complete code; the two "confirm the class name / call scope" notes name exactly what to check and the remedy.

**Type consistency:** `PipelineCtx` fields match `run_remote_pipeline` usage and the Task-2 test; `SmsApiClient`/`land_remote_run` calls match Phase 3a signatures; `_sms_api_base`/`_remote_push_and_sha`/`_remote_repo_url` consistent across Task 3.

## Follow-ons
- **Phase 3c:** the login-gated "Run on remote (smsvpctest)" launch panel + four/six-stage progress strip in `static/`, calling these endpoints.
- **sms-api:** generalize `_build_store_uri` to locate parquet stores (observables-endpoint parity).
- **Polish:** record real `n_steps` on the landed run; surface the run's `simulation_id`/store in the study UI.
