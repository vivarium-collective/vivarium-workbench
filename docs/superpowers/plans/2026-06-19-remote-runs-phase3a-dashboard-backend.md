# Remote Runs — Phase 3a: dashboard backend core (sms-api client + land-as-study-run) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two pure, unit-testable dashboard libs that the remote-run orchestration (Phase 3b) and UI (Phase 3c) build on: (1) `SmsApiClient` — a thin HTTP client for the sms-api endpoints the run pipeline calls; (2) `land_remote_run` — writes a remote simulation's fetched observable timeseries into a study's `runs.db` so it renders through the EXISTING SQLite chart pipeline like a local run.

**Architecture:** `SmsApiClient` wraps the sms-api REST endpoints (`/core/v1/simulator/*`, `/api/v1/simulations/*`, `/api/v1/simulations/{id}/observables*`) over stdlib `urllib.request` (no new dep), parameterized by `base_url`. `land_remote_run` reconstructs per-timestep composite-state JSON from `{time, series}`, then writes the `runs_meta` row (dashboard's `composite_runs`) + the `simulations` row and `history` rows (pbg-emitters' SQLite schema) into `studies/<slug>/runs.db`. Both are side-effect-isolated and tested without a live sms-api or subprocess.

**Tech Stack:** Python 3.11+, stdlib `urllib`/`sqlite3`/`json`, `vivarium_dashboard.lib.composite_runs`, `pbg_emitters.sqlite_emitter`, pytest.

**Repo:** All paths in `/Users/eranagmon/code/vivarium-dashboard` (branch `feat/dashboard-remote-runs`).

## Global Constraints

- No new dependencies — use stdlib `urllib.request` for HTTP (matches the existing `_http_get_json` approach in `server.py`).
- The landing format is **"reconstruct history rows (unified)"**: per-timestep `history.state` JSON = `{"observables": {<name>: <value>}}`; charts select via path `observables/<name>`. Do not invent a parallel renderer.
- Reuse, do not reimplement: `runs_meta` rows via `vivarium_dashboard.lib.composite_runs` (`connect`, `generate_run_id`, `save_metadata`, `complete_metadata`). The `simulations`+`history` tables are created INLINE (see schema below) — **do NOT import `pbg_emitters`**; it is a workspace-venv emitter dep, NOT installed in the dashboard venv (the dashboard only reads runs.db).
- `runs.db` is ONE sqlite file holding `runs_meta` (dashboard) + `simulations` + `history` (pbg-emitters). All three writes target the same file.
- The remote `simulation_id` (an int) is the durable reference handle: persist it in the `runs_meta` `params_json` and the `simulations` `metadata`.
- AI-free: these libs are pure data/IO, no AI/skill imports (dashboard convention).

## Confirmed interfaces (from the codebase — use verbatim)

- `vivarium_dashboard.lib.composite_runs`:
  - `connect(db_path) -> sqlite3.Connection` (bootstraps `runs_meta`)
  - `generate_run_id(spec_id: str, params: dict | None = None, now: float | None = None) -> str` → `"<spec_id>__<ts>__<hash6>"`
  - `save_metadata(conn, *, spec_id, run_id, params, label, started_at, n_steps, log_path=None, generation_id=None)` (inserts `runs_meta`, status='running', commits)
  - `complete_metadata(conn, *, run_id, n_steps, status)` (updates completed_at+status, commits)
- `simulations`+`history` schema (from pbg-emitters; create INLINE in the dashboard — do NOT import pbg_emitters):
  ```sql
  CREATE TABLE IF NOT EXISTS history (
      simulation_id TEXT NOT NULL, step INTEGER NOT NULL,
      global_time REAL, state TEXT NOT NULL,
      PRIMARY KEY (simulation_id, step)
  );
  CREATE INDEX IF NOT EXISTS idx_history_sim_time ON history(simulation_id, global_time);
  CREATE TABLE IF NOT EXISTS simulations (
      simulation_id TEXT PRIMARY KEY, name TEXT, started_at TEXT NOT NULL,
      completed_at TEXT, elapsed_seconds REAL, composite_config TEXT, metadata TEXT
  );
  ```
  The `simulations` row: `simulation_id`=run_id, `name`=run_id, `started_at`=UTC ISO string `%Y-%m-%dT%H:%M:%SZ`, `metadata`=JSON provenance.
- sms-api endpoints (base `http://localhost:8080` via tunnel): `GET /core/v1/simulator/latest?git_branch=&git_repo_url=`, `POST /core/v1/simulator/upload` (JSON body=Simulator, `?force=`), `GET /core/v1/simulator/status?simulator_id=`, `POST /api/v1/simulations?simulator_id=&num_generations=&num_seeds=&run_parca=&observables=` (repeated `observables`), `GET /api/v1/simulations/{id}/status`, `GET /api/v1/simulations/{id}/observables/index?seed=`, `GET /api/v1/simulations/{id}/observables?names=&seed=`.

## File Structure

- Create `vivarium_dashboard/lib/sms_api_client.py` — `SmsApiClient` (one HTTP concern; no DB).
- Create `vivarium_dashboard/lib/remote_run_landing.py` — `land_remote_run` + `_state_blobs` (one DB-landing concern; no HTTP).
- Test `tests/test_sms_api_client.py` — mock `urllib.request.urlopen`.
- Test `tests/test_remote_run_landing.py` — temp `runs.db`; assert rows + that `study_charts` can read the landed run.

---

## Task 1: `SmsApiClient` — GET helpers + simulator/simulation reads

**Files:**
- Create: `vivarium_dashboard/lib/sms_api_client.py`
- Test: `tests/test_sms_api_client.py`

**Interfaces:**
- Produces:
  - `class SmsApiClient: __init__(self, base_url: str = "http://localhost:8080", timeout: float = 30.0)`
  - `latest_simulator(self, repo_url: str, branch: str) -> dict`
  - `simulator_status(self, simulator_id: int) -> dict`
  - `simulation_status(self, simulation_id: int) -> dict`
  - `observables_index(self, simulation_id: int, seed: int = 0) -> dict`
  - `observables(self, simulation_id: int, names: list[str], seed: int = 0) -> dict`
  - private `_get(self, path: str, params: dict | None = None) -> dict` (urllib GET → JSON; raises `SmsApiError` on non-200/connection failure)
  - `class SmsApiError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sms_api_client.py`:

```python
import io
import json
from contextlib import contextmanager

import pytest

from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError


class _Resp(io.BytesIO):
    status = 200

    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@contextmanager
def _patch_urlopen(monkeypatch, capture, payload, status=200):
    def fake_urlopen(req, timeout=None):
        capture["url"] = req.full_url
        capture["method"] = req.get_method()
        capture["body"] = req.data
        if status != 200:
            from urllib.error import HTTPError

            raise HTTPError(req.full_url, status, "err", {}, io.BytesIO(b"boom"))
        return _Resp(payload, status)

    monkeypatch.setattr("vivarium_dashboard.lib.sms_api_client.urlopen", fake_urlopen)
    yield


def test_latest_simulator_builds_query(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"git_commit_hash": "abc123"}):
        c = SmsApiClient("http://h:8080")
        out = c.latest_simulator("https://github.com/x/v2ecoli", "master")
    assert out["git_commit_hash"] == "abc123"
    assert cap["url"].startswith("http://h:8080/core/v1/simulator/latest?")
    assert "git_branch=master" in cap["url"]
    assert "git_repo_url=https%3A%2F%2Fgithub.com%2Fx%2Fv2ecoli" in cap["url"]


def test_observables_repeats_names_param(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"time": [0.0], "series": {"mass": [1.0]}}):
        c = SmsApiClient("http://h:8080")
        out = c.observables(49, ["mass", "volume"], seed=0)
    assert out["series"]["mass"] == [1.0]
    assert "/api/v1/simulations/49/observables?" in cap["url"]
    assert "names=mass%2Cvolume" in cap["url"]
    assert "seed=0" in cap["url"]


def test_non_200_raises(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {}, status=404):
        c = SmsApiClient("http://h:8080")
        with pytest.raises(SmsApiError):
            c.simulation_status(999)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_sms_api_client.py -v`
Expected: FAIL — `ModuleNotFoundError: vivarium_dashboard.lib.sms_api_client`.

- [ ] **Step 3: Write minimal implementation**

Create `vivarium_dashboard/lib/sms_api_client.py`:

```python
"""Thin HTTP client for the sms-api endpoints the remote-run pipeline calls.

Stdlib-only (urllib) to avoid adding a dependency, matching server.py's existing
outbound-HTTP approach. Pure HTTP — no DB, no orchestration. Parameterized by
base_url (the SSM tunnel, default http://localhost:8080).
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class SmsApiError(Exception):
    """Raised when an sms-api call fails (non-200 or connection error)."""


class SmsApiClient:
    def __init__(self, base_url: str = "http://localhost:8080", timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = self.base_url + path
        if params:
            url = f"{url}?{urlencode(params)}"
        req = Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — fixed scheme, internal tunnel
                return json.loads(r.read().decode())
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}") from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e

    def latest_simulator(self, repo_url: str, branch: str) -> dict:
        return self._get("/core/v1/simulator/latest", {"git_branch": branch, "git_repo_url": repo_url})

    def simulator_status(self, simulator_id: int) -> dict:
        return self._get("/core/v1/simulator/status", {"simulator_id": simulator_id})

    def simulation_status(self, simulation_id: int) -> dict:
        return self._get(f"/api/v1/simulations/{simulation_id}/status")

    def observables_index(self, simulation_id: int, seed: int = 0) -> dict:
        return self._get(f"/api/v1/simulations/{simulation_id}/observables/index", {"seed": seed})

    def observables(self, simulation_id: int, names: list[str], seed: int = 0) -> dict:
        params = {"seed": seed}
        if names:
            params["names"] = ",".join(names)
        return self._get(f"/api/v1/simulations/{simulation_id}/observables", params)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sms_api_client.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/sms_api_client.py tests/test_sms_api_client.py
git commit -m "feat(remote-runs): SmsApiClient GET helpers (simulator/simulation/observables reads)"
```

---

## Task 2: `SmsApiClient` — POST helpers (upload + run)

**Files:**
- Modify: `vivarium_dashboard/lib/sms_api_client.py`
- Test: `tests/test_sms_api_client.py`

**Interfaces:**
- Consumes: `SmsApiClient`, `_get`, `SmsApiError` (Task 1).
- Produces:
  - private `_post(self, path: str, params: dict | None = None, json_body: dict | None = None) -> dict`
  - `upload_simulator(self, simulator: dict, force: bool = False) -> dict` → `POST /core/v1/simulator/upload`
  - `run_simulation(self, *, simulator_id: int, num_generations: int, num_seeds: int, run_parca: bool, observables: list[str], experiment_id: str | None = None, description: str | None = None) -> dict` → `POST /api/v1/simulations` (params in query, repeated `observables`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sms_api_client.py`:

```python
from urllib.parse import parse_qs, urlsplit


def test_run_simulation_query_and_repeated_observables(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"database_id": 50}):
        c = SmsApiClient("http://h:8080")
        out = c.run_simulation(
            simulator_id=15, num_generations=1, num_seeds=1, run_parca=True,
            observables=["mass", "volume"], experiment_id="exp1",
        )
    assert out["database_id"] == 50
    assert cap["method"] == "POST"
    qs = parse_qs(urlsplit(cap["url"]).query)
    assert qs["simulator_id"] == ["15"]
    assert qs["num_generations"] == ["1"]
    assert qs["run_parca"] == ["True"]
    assert qs["observables"] == ["mass", "volume"]  # repeated key, not comma-joined
    assert qs["experiment_id"] == ["exp1"]


def test_upload_simulator_sends_json_body(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"database_id": 16, "status": "running"}):
        c = SmsApiClient("http://h:8080")
        out = c.upload_simulator({"git_commit_hash": "abc", "git_repo_url": "u", "git_branch": "b"}, force=True)
    assert out["database_id"] == 16
    assert cap["method"] == "POST"
    assert json.loads(cap["body"].decode())["git_commit_hash"] == "abc"
    assert "force=true" in cap["url"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sms_api_client.py -k "run_simulation or upload_simulator" -v`
Expected: FAIL — `AttributeError: 'SmsApiClient' object has no attribute 'run_simulation'`.

- [ ] **Step 3: Write minimal implementation**

Add to `vivarium_dashboard/lib/sms_api_client.py` (imports: add `from urllib.parse import urlencode` already present):

```python
    def _post(self, path: str, params: dict | None = None, json_body: dict | None = None) -> dict:
        # doseq=True so list-valued params become repeated keys (?observables=a&observables=b)
        url = self.base_url + path
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        data = json.dumps(json_body).encode() if json_body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, method="POST", headers=headers)
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                return json.loads(r.read().decode())
        except HTTPError as e:
            raise SmsApiError(f"POST {url} -> {e.code}") from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"POST {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e

    def upload_simulator(self, simulator: dict, force: bool = False) -> dict:
        params = {"force": "true"} if force else None
        return self._post("/core/v1/simulator/upload", params=params, json_body=simulator)

    def run_simulation(
        self,
        *,
        simulator_id: int,
        num_generations: int,
        num_seeds: int,
        run_parca: bool,
        observables: list[str],
        experiment_id: str | None = None,
        description: str | None = None,
    ) -> dict:
        params: dict = {
            "simulator_id": simulator_id,
            "num_generations": num_generations,
            "num_seeds": num_seeds,
            "run_parca": run_parca,
        }
        if experiment_id is not None:
            params["experiment_id"] = experiment_id
        if description is not None:
            params["description"] = description
        if observables:
            params["observables"] = observables  # list → repeated key via doseq
        return self._post("/api/v1/simulations", params=params)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sms_api_client.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/sms_api_client.py tests/test_sms_api_client.py
git commit -m "feat(remote-runs): SmsApiClient POST helpers (upload simulator, run simulation)"
```

---

## Task 3: `land_remote_run` — state-blob reconstruction

**Files:**
- Create: `vivarium_dashboard/lib/remote_run_landing.py`
- Test: `tests/test_remote_run_landing.py`

**Interfaces:**
- Produces:
  - `_state_blobs(observables: dict) -> list[tuple[int, float, str]]` — turns `{"time":[...], "series":{name:[...]}}` into `[(step, global_time, state_json), ...]`, where `state_json = json.dumps({"observables": {name: value_at_step}})`. Non-finite values already arrive as `None` from the endpoint.

- [ ] **Step 1: Write the failing test**

Create `tests/test_remote_run_landing.py`:

```python
import json

from vivarium_dashboard.lib.remote_run_landing import _state_blobs


def test_state_blobs_aligns_series_to_time():
    obs = {"time": [0.0, 1.0, 2.0], "series": {"mass": [1.0, 2.0, 3.0], "vol": [0.1, 0.2, 0.3]}}
    blobs = _state_blobs(obs)
    assert len(blobs) == 3
    step, gt, state = blobs[1]
    assert step == 1
    assert gt == 1.0
    parsed = json.loads(state)
    assert parsed["observables"]["mass"] == 2.0
    assert parsed["observables"]["vol"] == 0.2


def test_state_blobs_preserves_none():
    obs = {"time": [0.0, 1.0], "series": {"mass": [1.0, None]}}
    blobs = _state_blobs(obs)
    assert json.loads(blobs[1][2])["observables"]["mass"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_remote_run_landing.py -v`
Expected: FAIL — `ModuleNotFoundError: vivarium_dashboard.lib.remote_run_landing`.

- [ ] **Step 3: Write minimal implementation**

Create `vivarium_dashboard/lib/remote_run_landing.py`:

```python
"""Land a remote simulation's observable timeseries into a study's runs.db.

Reconstructs per-timestep composite-state JSON from the sms-api observables
payload ({time, series}) so the EXISTING SQLite chart pipeline renders the
remote run identically to a local one. Writes three things into the one
runs.db file: the dashboard runs_meta row, the pbg-emitters simulations row,
and the history rows. Pure DB/IO — no HTTP.
"""

from __future__ import annotations

import json


def _state_blobs(observables: dict) -> list[tuple[int, float, str]]:
    """Turn {time, series:{name:[...]}} into [(step, global_time, state_json), ...].

    Each state blob is {"observables": {name: value_at_that_step}}; chart
    selectors address values as ``observables/<name>``.
    """
    time = observables.get("time") or []
    series = observables.get("series") or {}
    blobs: list[tuple[int, float, str]] = []
    for i, t in enumerate(time):
        state = {"observables": {name: vals[i] for name, vals in series.items()}}
        blobs.append((i, float(t), json.dumps(state)))
    return blobs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_remote_run_landing.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/remote_run_landing.py tests/test_remote_run_landing.py
git commit -m "feat(remote-runs): _state_blobs — reconstruct per-step state from observables"
```

---

## Task 4: `land_remote_run` — write runs_meta + simulations + history

**Files:**
- Modify: `vivarium_dashboard/lib/remote_run_landing.py`
- Test: `tests/test_remote_run_landing.py`

**Interfaces:**
- Consumes: `_state_blobs` (Task 3); `composite_runs.connect/generate_run_id/save_metadata/complete_metadata`.
- Produces:
  - `_init_emitter_tables(conn: sqlite3.Connection) -> None` — creates `history`+`simulations` tables inline (schema in Global Constraints).
  - `land_remote_run(study_dir, *, spec_id: str, simulation_id: int, experiment_id: str, commit: str, observables: dict, label: str | None = None) -> str` — returns the new `run_id`. Writes runs_meta (status completed), the simulations row (name=run_id, metadata carries provenance), and history rows. `study_dir` is a `pathlib.Path`; writes `study_dir/runs.db`. Does NOT import pbg_emitters.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_remote_run_landing.py`:

```python
import sqlite3
from pathlib import Path

from vivarium_dashboard.lib.remote_run_landing import land_remote_run


def test_land_remote_run_writes_all_three_tables(tmp_path: Path):
    obs = {"time": [0.0, 1.0, 2.0], "series": {"mass": [1.0, 2.0, 3.0]}}
    run_id = land_remote_run(
        tmp_path,
        spec_id="v2ecoli.composites.baseline",
        simulation_id=49,
        experiment_id="exp-abc",
        commit="abc123",
        observables=obs,
        label="Remote run (smsvpctest)",
    )
    db = tmp_path / "runs.db"
    assert db.exists()
    conn = sqlite3.connect(str(db))

    meta = conn.execute(
        "SELECT spec_id, status, n_steps, params_json FROM runs_meta WHERE run_id=?", (run_id,)
    ).fetchone()
    assert meta[0] == "v2ecoli.composites.baseline"
    assert meta[1] == "completed"
    assert meta[2] == 3
    assert json.loads(meta[3])["simulation_id"] == 49  # provenance persisted

    sim = conn.execute(
        "SELECT simulation_id FROM simulations WHERE name=?", (run_id,)
    ).fetchone()
    assert sim is not None and sim[0] == run_id

    hist = conn.execute(
        "SELECT step, global_time, state FROM history WHERE simulation_id=? ORDER BY step", (run_id,)
    ).fetchall()
    assert len(hist) == 3
    assert json.loads(hist[2][2])["observables"]["mass"] == 3.0


def test_landed_run_is_readable_by_study_charts(tmp_path: Path):
    from vivarium_dashboard.lib import study_charts

    obs = {"time": [0.0, 1.0], "series": {"mass": [10.0, 20.0]}}
    run_id = land_remote_run(
        tmp_path, spec_id="s", simulation_id=7, experiment_id="e", commit="c", observables=obs
    )
    # The chart layer resolves the latest run from `simulations` then reads `history`.
    # _load_latest_run(db_path: Path) -> (parsed_states, times, simulation_id)  [study_charts.py:1075]
    parsed, times, sim_id = study_charts._load_latest_run(tmp_path / "runs.db")
    assert sim_id == run_id
    assert times == [0.0, 1.0]
    assert parsed[1]["observables"]["mass"] == 20.0
```

Note: `study_charts._load_latest_run(db_path: Path)` is confirmed at `lib/study_charts.py:1075` — it returns `(parsed_states, times, simulation_id)` and takes a `Path` (not str). Use it exactly as written above.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_remote_run_landing.py -k land_remote_run -v`
Expected: FAIL — `ImportError: cannot import name 'land_remote_run'`.

- [ ] **Step 3: Write minimal implementation**

Add to `vivarium_dashboard/lib/remote_run_landing.py`:

```python
import datetime as _dt
import json
import sqlite3
import time as _time
from pathlib import Path

from vivarium_dashboard.lib import composite_runs as cr


def _init_emitter_tables(conn: sqlite3.Connection) -> None:
    """Create the pbg-emitters `history` + `simulations` tables inline.

    We replicate the pbg-emitters schema rather than importing pbg_emitters,
    which is a workspace-venv emitter dependency and not installed in the
    dashboard venv (the dashboard only reads runs.db).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history ("
        " simulation_id TEXT NOT NULL, step INTEGER NOT NULL,"
        " global_time REAL, state TEXT NOT NULL,"
        " PRIMARY KEY (simulation_id, step))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_sim_time ON history(simulation_id, global_time)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS simulations ("
        " simulation_id TEXT PRIMARY KEY, name TEXT, started_at TEXT NOT NULL,"
        " completed_at TEXT, elapsed_seconds REAL, composite_config TEXT, metadata TEXT)"
    )


def land_remote_run(
    study_dir: Path,
    *,
    spec_id: str,
    simulation_id: int,
    experiment_id: str,
    commit: str,
    observables: dict,
    label: str | None = None,
) -> str:
    """Land a remote run's observables into study_dir/runs.db; return the run_id."""
    study_dir = Path(study_dir)
    study_dir.mkdir(parents=True, exist_ok=True)
    db_path = study_dir / "runs.db"

    provenance = {
        "simulation_id": simulation_id,
        "experiment_id": experiment_id,
        "commit": commit,
        "backend": "ray",
        "source": "smsvpctest",
    }
    run_id = cr.generate_run_id(spec_id, params=provenance)
    blobs = _state_blobs(observables)
    started = _time.time()
    started_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. dashboard runs_meta row (status running)
    conn = cr.connect(db_path)
    try:
        cr.save_metadata(
            conn,
            spec_id=spec_id,
            run_id=run_id,
            params=provenance,
            label=label or "Remote run (smsvpctest)",
            started_at=started,
            n_steps=len(blobs),
        )
    finally:
        conn.close()

    # 2. simulations + history tables and rows (inline schema)
    hconn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        _init_emitter_tables(hconn)
        hconn.execute(
            "INSERT OR REPLACE INTO simulations "
            "(simulation_id, name, started_at, metadata) VALUES (?, ?, ?, ?)",
            (run_id, run_id, started_iso, json.dumps(provenance)),
        )
        hconn.executemany(
            "INSERT OR REPLACE INTO history (simulation_id, step, global_time, state) VALUES (?, ?, ?, ?)",
            [(run_id, step, gt, state) for (step, gt, state) in blobs],
        )
    finally:
        hconn.close()

    # 3. mark runs_meta completed
    conn = cr.connect(db_path)
    try:
        cr.complete_metadata(conn, run_id=run_id, n_steps=len(blobs), status="completed")
    finally:
        conn.close()

    return run_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_remote_run_landing.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/remote_run_landing.py tests/test_remote_run_landing.py
git commit -m "feat(remote-runs): land_remote_run writes runs_meta + simulations + history"
```

---

## Task 5: Full-suite + integration sanity

**Files:** none (verification)

- [ ] **Step 1: Run both new test files**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && python -m pytest tests/test_sms_api_client.py tests/test_remote_run_landing.py -v`
Expected: PASS (9 tests).

- [ ] **Step 2: Confirm no regression in the runs/charts area**

Run: `python -m pytest tests/ -k "runs or chart or composite" -q`
Expected: PASS (or pre-existing skips only — note any failure that names files you did NOT touch and do not fix it here).

- [ ] **Step 3: Commit (if anything was adjusted)**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add -A && git commit -m "test(remote-runs): phase 3a full-suite green" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage (Phase 3a scope):** `SmsApiClient` covers every sms-api call the Phase 3b pipeline needs (latest/upload/status simulator; run/status simulation; observables index+series) — Tasks 1–2. `land_remote_run` implements the chosen "reconstruct history rows (unified)" landing (runs_meta + simulations + history, provenance keyed by `simulation_id`), verified readable by the real `study_charts` reader — Tasks 3–4. Phase 3b (RemoteRunManager + `POST /api/remote-run-start` / `GET /api/remote-run-status`, login-gated, push→build→run→poll→fetch→land) and Phase 3c (launch-panel UI) are separate plans.

**Placeholder scan:** none — all code complete; the one verify-against-real-symbol note (Task 4 `study_charts.load_latest_run`) names the exact file:line to confirm and the remedy.

**Type consistency:** `SmsApiClient` method names/signatures match between Tasks 1–2 and the Phase 3b interface list; `land_remote_run(study_dir, *, spec_id, simulation_id, experiment_id, commit, observables, label=None) -> str` and `_state_blobs(observables) -> list[tuple[int,float,str]]` are consistent across Tasks 3–4 and their tests.

## Follow-on (separate plans)

- **Phase 3b — orchestration + endpoints:** `RemoteRunManager` (mirror `RunJobManager`) running push (existing `_post_work_push` + `current_token_env`) → `upload_simulator` → poll `simulator_status` → `run_simulation` → poll `simulation_status` → `observables` → `land_remote_run`; `POST /api/remote-run-start` (gated by `github_auth.current_session()`) + `GET /api/remote-run-status`; server setting for `smsApiBase` (default `http://localhost:8080`).
- **Phase 3c — UI:** the login-gated "Run on remote (smsvpctest)" launch panel + four-stage progress strip in `static/`.
- **Phase 2 — sms-cdk:** api-pod IRSA `s3:GetObject`/`ListBucket` on `{s3_output_prefix}`.
