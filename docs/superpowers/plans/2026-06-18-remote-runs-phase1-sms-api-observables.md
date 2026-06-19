# Remote Runs — Phase 1: sms-api observables endpoint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an emitter-aware read API to sms-api — `GET /api/v1/simulations/{id}/observables/index` and `GET /api/v1/simulations/{id}/observables` — that reads a v2ecoli Ray run's XArray/zarr (or Parquet) emitter store from S3 and returns JSON timeseries.

**Architecture:** A pure, store-agnostic reader module (`observable_reader.py`) opens an emitter store by fsspec URI (so `file://` works in tests, `s3://` in prod), detects zarr vs parquet, and returns observable metadata + timeseries. Two FastAPI routes in the existing simulation router resolve `simulation_id → experiment_id → S3 store URI`, call the reader off the event loop, and return pydantic models. This phase is independently shippable and testable without the dashboard.

**Tech Stack:** Python 3.12, FastAPI, pydantic, xarray + zarr + s3fs (new deps), aioboto3 (existing), pandas/pyarrow (existing via polars extra), pytest + pytest-asyncio + httpx ASGITransport.

**Repo:** All paths are in `/Users/eranagmon/code/sms-api`.

## Global Constraints

- Python pinned 3.12.9; line length 120; ruff lint+format; mypy strict (excludes `sms_api/api/client/`).
- New routes MUST live under `/api/v1/*` (the sms-cdk ALB only routes `/api`, `/core`, `/docs`, `/ws`, `/health`, `/version`, `/openapi.json`, `/home` to the API). Do NOT add a new top-level path prefix.
- The simulation router is `sms_api/api/routers/sms.py`: `config = get_router_config(prefix="api", version_major=False)` → `config.router` has prefix `/v1`, `config.prefix` is `/api`; final paths are `/api/v1/...`.
- Reader must be emitter-format-aware: zarr (XArray, default) and parquet. Default emitter is XArray zarr.
- S3 store layout (from `simulation_service_ray.py`): `s3://{settings.s3_work_bucket}/{settings.s3_output_prefix}/{experiment_id}/seed_{NN}/store.zarr/`.
- Settings access is `from sms_api.config import get_settings; settings = get_settings()`. Relevant fields: `s3_work_bucket`, `s3_output_prefix`, `batch_region`.
- DB access: `from sms_api.dependencies import get_database_service`; `db = get_database_service()`; `sim = await db.get_simulation(simulation_id=id)` → `Simulation | None`; `sim.experiment_id: str`.
- pydantic base: `from sms_api.simulation.models import BaseModel` (`model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")`).
- Run quality gates after each task: `uv run pytest <test> -v`, and before each commit `uv run ruff format <files> && uv run ruff check <files>`.

---

## File Structure

- Create `sms_api/simulation/observable_reader.py` — pure store reader (zarr + parquet) over fsspec URIs. One responsibility: turn a store URI into observable metadata + timeseries.
- Modify `sms_api/simulation/models.py` — add response models: `ObservableInfo`, `SimulationObservableIndex`, `SimulationObservables`.
- Modify `sms_api/api/routers/sms.py` — add the two GET routes + a private `_build_store_uri` helper.
- Modify `pyproject.toml` — add `xarray`, `zarr`, `s3fs` deps.
- Create `tests/simulation/test_observable_reader.py` — reader unit tests against fixture stores (file:// zarr + parquet).
- Create `tests/api/ecoli/test_observables_endpoint.py` — route tests with a fake DB + monkeypatched reader.

---

## Task 1: Add deps + reader skeleton with zarr listing

**Files:**
- Modify: `pyproject.toml` (dependencies list, around line 46 next to `aioboto3`)
- Create: `sms_api/simulation/observable_reader.py`
- Test: `tests/simulation/test_observable_reader.py`

**Interfaces:**
- Produces:
  - `@dataclass ObservableInfo(name: str, dims: list[str], shape: list[int])`
  - `@dataclass StoreIndex(store: str, observables: list[ObservableInfo])`
  - `detect_store_kind(store_uri: str) -> str` — returns `"zarr"` or `"parquet"`
  - `list_observables(store_uri: str) -> StoreIndex`

- [ ] **Step 1: Add dependencies**

Edit `pyproject.toml` — add to the main `dependencies` array (next to `"aioboto3>=13.3.0",`):

```toml
    "xarray>=2024.6.0",
    "zarr>=2.18.0,<3",
    "s3fs>=2024.6.0",
```

Then sync:

Run: `cd /Users/eranagmon/code/sms-api && uv sync`
Expected: resolves and installs xarray, zarr, s3fs.

- [ ] **Step 2: Write the failing test**

Create `tests/simulation/test_observable_reader.py`:

```python
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from sms_api.simulation.observable_reader import (
    StoreIndex,
    detect_store_kind,
    list_observables,
)


def _write_fixture_zarr(tmp_path: Path) -> str:
    """Write a tiny XArray zarr store and return its file:// URI."""
    ds = xr.Dataset(
        data_vars={
            "mass": ("time", np.array([1.0, 2.0, 3.0])),
            "volume": ("time", np.array([0.1, 0.2, 0.3])),
        },
        coords={"time": np.array([0.0, 1.0, 2.0])},
    )
    store_path = tmp_path / "store.zarr"
    ds.to_zarr(store_path, mode="w")
    return f"file://{store_path}"


def test_detect_store_kind_zarr(tmp_path: Path) -> None:
    uri = _write_fixture_zarr(tmp_path)
    assert detect_store_kind(uri) == "zarr"


def test_list_observables_zarr(tmp_path: Path) -> None:
    uri = _write_fixture_zarr(tmp_path)
    idx = list_observables(uri)
    assert isinstance(idx, StoreIndex)
    assert idx.store == "zarr"
    names = {o.name for o in idx.observables}
    assert names == {"mass", "volume"}
    mass = next(o for o in idx.observables if o.name == "mass")
    assert mass.dims == ["time"]
    assert mass.shape == [3]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/simulation/test_observable_reader.py -v`
Expected: FAIL — `ModuleNotFoundError: sms_api.simulation.observable_reader`.

- [ ] **Step 4: Write minimal implementation**

Create `sms_api/simulation/observable_reader.py`:

```python
"""Read a simulation's emitter store (XArray/zarr or Parquet) into plain Python.

The reader is store-agnostic over fsspec URIs: ``file://`` paths work in tests,
``s3://`` paths work in production (s3fs handles credentials via the pod's role).
It has one job — turn a store URI into observable metadata and timeseries — so it
can be unit-tested without S3 or a database.
"""

from __future__ import annotations

from dataclasses import dataclass

import fsspec


@dataclass
class ObservableInfo:
    name: str
    dims: list[str]
    shape: list[int]


@dataclass
class StoreIndex:
    store: str  # "zarr" | "parquet"
    observables: list[ObservableInfo]


def detect_store_kind(store_uri: str) -> str:
    """Return 'zarr' if the store is a zarr group (has a .zgroup/.zattrs), else 'parquet'."""
    fs, path = fsspec.core.url_to_fs(store_uri)
    if fs.exists(f"{path}/.zgroup") or fs.exists(f"{path}/zarr.json"):
        return "zarr"
    return "parquet"


def list_observables(store_uri: str) -> StoreIndex:
    """Open the emitter store and return its observable variables (name, dims, shape)."""
    kind = detect_store_kind(store_uri)
    if kind == "zarr":
        import xarray as xr

        ds = xr.open_zarr(store_uri)
        try:
            obs = [
                ObservableInfo(name=str(name), dims=[str(d) for d in var.dims], shape=[int(s) for s in var.shape])
                for name, var in ds.data_vars.items()
            ]
        finally:
            ds.close()
        return StoreIndex(store="zarr", observables=obs)
    raise NotImplementedError("parquet store listing is added in Task 3")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/simulation/test_observable_reader.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint + commit**

```bash
cd /Users/eranagmon/code/sms-api
uv run ruff format sms_api/simulation/observable_reader.py tests/simulation/test_observable_reader.py pyproject.toml
uv run ruff check sms_api/simulation/observable_reader.py tests/simulation/test_observable_reader.py
git add pyproject.toml uv.lock sms_api/simulation/observable_reader.py tests/simulation/test_observable_reader.py
git commit -m "feat(observables): zarr store listing in observable_reader + xarray/zarr/s3fs deps"
```

---

## Task 2: Read zarr timeseries

**Files:**
- Modify: `sms_api/simulation/observable_reader.py`
- Test: `tests/simulation/test_observable_reader.py`

**Interfaces:**
- Consumes: `StoreIndex`, `detect_store_kind` (Task 1)
- Produces: `read_observables(store_uri: str, names: list[str]) -> tuple[list[float], dict[str, list[float]]]` — returns `(time, {name: values})`; empty `names` means all observables.

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/test_observable_reader.py`:

```python
from sms_api.simulation.observable_reader import read_observables


def test_read_observables_zarr_selected(tmp_path: Path) -> None:
    uri = _write_fixture_zarr(tmp_path)
    time, series = read_observables(uri, names=["mass"])
    assert time == [0.0, 1.0, 2.0]
    assert series == {"mass": [1.0, 2.0, 3.0]}


def test_read_observables_zarr_all(tmp_path: Path) -> None:
    uri = _write_fixture_zarr(tmp_path)
    time, series = read_observables(uri, names=[])
    assert set(series) == {"mass", "volume"}
    assert series["volume"] == [0.1, 0.2, 0.3]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/simulation/test_observable_reader.py -k read_observables -v`
Expected: FAIL — `ImportError: cannot import name 'read_observables'`.

- [ ] **Step 3: Write minimal implementation**

Add to `sms_api/simulation/observable_reader.py`:

```python
def read_observables(store_uri: str, names: list[str]) -> tuple[list[float], dict[str, list[float]]]:
    """Return (time, {name: values}) for the requested observables.

    ``names=[]`` returns every observable in the store. The time axis is taken from
    the ``time`` coordinate if present, else a 0..N index.
    """
    kind = detect_store_kind(store_uri)
    if kind == "zarr":
        import numpy as np
        import xarray as xr

        ds = xr.open_zarr(store_uri)
        try:
            wanted = names or [str(n) for n in ds.data_vars]
            missing = [n for n in wanted if n not in ds.data_vars]
            if missing:
                raise KeyError(f"observables not in store: {missing}")
            if "time" in ds.coords:
                time = [float(t) for t in np.asarray(ds["time"].values).ravel()]
            else:
                first = ds[wanted[0]]
                time = [float(i) for i in range(int(first.shape[0]))]
            series = {n: [float(v) for v in np.asarray(ds[n].values).ravel()] for n in wanted}
        finally:
            ds.close()
        return time, series
    raise NotImplementedError("parquet reads are added in Task 3")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/simulation/test_observable_reader.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd /Users/eranagmon/code/sms-api
uv run ruff format sms_api/simulation/observable_reader.py tests/simulation/test_observable_reader.py
uv run ruff check sms_api/simulation/observable_reader.py tests/simulation/test_observable_reader.py
git add sms_api/simulation/observable_reader.py tests/simulation/test_observable_reader.py
git commit -m "feat(observables): read zarr timeseries (selected + all)"
```

---

## Task 3: Parquet support

**Files:**
- Modify: `sms_api/simulation/observable_reader.py`
- Test: `tests/simulation/test_observable_reader.py`

**Interfaces:**
- Consumes: `StoreIndex`, `ObservableInfo`, `list_observables`, `read_observables` (Tasks 1–2)
- Produces: parquet branch for both `list_observables` and `read_observables`; a parquet store is a single `.parquet` file (or directory of them) with a `time` column and one column per observable.

- [ ] **Step 1: Write the failing test**

Append to `tests/simulation/test_observable_reader.py`:

```python
import pandas as pd


def _write_fixture_parquet(tmp_path: Path) -> str:
    df = pd.DataFrame({"time": [0.0, 1.0, 2.0], "mass": [1.0, 2.0, 3.0], "volume": [0.1, 0.2, 0.3]})
    p = tmp_path / "store.parquet"
    df.to_parquet(p)
    return f"file://{p}"


def test_detect_store_kind_parquet(tmp_path: Path) -> None:
    uri = _write_fixture_parquet(tmp_path)
    assert detect_store_kind(uri) == "parquet"


def test_list_observables_parquet(tmp_path: Path) -> None:
    uri = _write_fixture_parquet(tmp_path)
    idx = list_observables(uri)
    assert idx.store == "parquet"
    assert {o.name for o in idx.observables} == {"mass", "volume"}  # time excluded


def test_read_observables_parquet(tmp_path: Path) -> None:
    uri = _write_fixture_parquet(tmp_path)
    time, series = read_observables(uri, names=["volume"])
    assert time == [0.0, 1.0, 2.0]
    assert series == {"volume": [0.1, 0.2, 0.3]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/simulation/test_observable_reader.py -k parquet -v`
Expected: FAIL — `NotImplementedError` from `list_observables`/`read_observables`, and `detect_store_kind` returns "parquet" already (that one passes).

- [ ] **Step 3: Write minimal implementation**

In `observable_reader.py`, replace the `raise NotImplementedError("parquet store listing is added in Task 3")` line in `list_observables` with:

```python
    import pandas as pd

    df = pd.read_parquet(store_uri)
    cols = [c for c in df.columns if c != "time"]
    obs = [ObservableInfo(name=str(c), dims=["time"], shape=[int(len(df))]) for c in cols]
    return StoreIndex(store="parquet", observables=obs)
```

And replace the `raise NotImplementedError("parquet reads are added in Task 3")` line in `read_observables` with:

```python
    import pandas as pd

    df = pd.read_parquet(store_uri)
    wanted = names or [str(c) for c in df.columns if c != "time"]
    missing = [n for n in wanted if n not in df.columns]
    if missing:
        raise KeyError(f"observables not in store: {missing}")
    time = [float(t) for t in df["time"].tolist()] if "time" in df.columns else [float(i) for i in range(len(df))]
    series = {n: [float(v) for v in df[n].tolist()] for n in wanted}
    return time, series
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/simulation/test_observable_reader.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd /Users/eranagmon/code/sms-api
uv run ruff format sms_api/simulation/observable_reader.py tests/simulation/test_observable_reader.py
uv run ruff check sms_api/simulation/observable_reader.py tests/simulation/test_observable_reader.py
git add sms_api/simulation/observable_reader.py tests/simulation/test_observable_reader.py
git commit -m "feat(observables): parquet store listing + reads"
```

---

## Task 4: Response models

**Files:**
- Modify: `sms_api/simulation/models.py` (add after the `Simulation` class, ~line 391)
- Test: `tests/simulation/test_observable_models.py` (create)

**Interfaces:**
- Produces:
  - `ObservableInfoModel(name: str, dims: list[str], shape: list[int])`
  - `SimulationObservableIndex(simulation_id: int, experiment_id: str, seed: int, store: str, observables: list[ObservableInfoModel])`
  - `SimulationObservables(simulation_id: int, experiment_id: str, seed: int, store: str, time: list[float], series: dict[str, list[float]])`

- [ ] **Step 1: Write the failing test**

Create `tests/simulation/test_observable_models.py`:

```python
from sms_api.simulation.models import (
    ObservableInfoModel,
    SimulationObservableIndex,
    SimulationObservables,
)


def test_index_model_roundtrips() -> None:
    idx = SimulationObservableIndex(
        simulation_id=49,
        experiment_id="exp-abc",
        seed=0,
        store="zarr",
        observables=[ObservableInfoModel(name="mass", dims=["time"], shape=[3])],
    )
    dumped = idx.model_dump()
    assert dumped["observables"][0]["name"] == "mass"


def test_observables_model_roundtrips() -> None:
    obs = SimulationObservables(
        simulation_id=49,
        experiment_id="exp-abc",
        seed=0,
        store="zarr",
        time=[0.0, 1.0],
        series={"mass": [1.0, 2.0]},
    )
    assert obs.model_dump()["series"]["mass"] == [1.0, 2.0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/simulation/test_observable_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'ObservableInfoModel'`.

- [ ] **Step 3: Write minimal implementation**

In `sms_api/simulation/models.py`, after the `Simulation` class, add:

```python
class ObservableInfoModel(BaseModel):
    name: str
    dims: list[str]
    shape: list[int]


class SimulationObservableIndex(BaseModel):
    simulation_id: int
    experiment_id: str
    seed: int
    store: str  # "zarr" | "parquet"
    observables: list[ObservableInfoModel]


class SimulationObservables(BaseModel):
    simulation_id: int
    experiment_id: str
    seed: int
    store: str  # "zarr" | "parquet"
    time: list[float]
    series: dict[str, list[float]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/simulation/test_observable_models.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd /Users/eranagmon/code/sms-api
uv run ruff format sms_api/simulation/models.py tests/simulation/test_observable_models.py
uv run ruff check sms_api/simulation/models.py tests/simulation/test_observable_models.py
git add sms_api/simulation/models.py tests/simulation/test_observable_models.py
git commit -m "feat(observables): response models (index + observables)"
```

---

## Task 5: Store-URI helper

**Files:**
- Modify: `sms_api/api/routers/sms.py` (add a private helper near the top, after imports)
- Test: `tests/api/ecoli/test_store_uri.py` (create)

**Interfaces:**
- Produces: `_build_store_uri(experiment_id: str, seed: int) -> str` — returns `s3://{s3_work_bucket}/{s3_output_prefix}/{experiment_id}/seed_{seed:02d}/store.zarr`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/ecoli/test_store_uri.py`:

```python
from sms_api.api.routers.sms import _build_store_uri
from sms_api.config import get_settings


def test_build_store_uri(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "s3_work_bucket", "my-bucket")
    monkeypatch.setattr(settings, "s3_output_prefix", "vecoli-output")
    uri = _build_store_uri("exp-abc", 0)
    assert uri == "s3://my-bucket/vecoli-output/exp-abc/seed_00/store.zarr"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/ecoli/test_store_uri.py -v`
Expected: FAIL — `ImportError: cannot import name '_build_store_uri'`.

- [ ] **Step 3: Write minimal implementation**

In `sms_api/api/routers/sms.py`, add near the other module-level helpers:

```python
def _build_store_uri(experiment_id: str, seed: int) -> str:
    """Build the S3 URI of a Ray run's per-seed XArray emitter store.

    Layout written by simulation_service_ray.py:
    ``s3://{bucket}/{output_prefix}/{experiment_id}/seed_{NN}/store.zarr``.
    """
    from sms_api.config import get_settings

    settings = get_settings()
    return f"s3://{settings.s3_work_bucket}/{settings.s3_output_prefix}/{experiment_id}/seed_{seed:02d}/store.zarr"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/ecoli/test_store_uri.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd /Users/eranagmon/code/sms-api
uv run ruff format sms_api/api/routers/sms.py tests/api/ecoli/test_store_uri.py
uv run ruff check sms_api/api/routers/sms.py tests/api/ecoli/test_store_uri.py
git add sms_api/api/routers/sms.py tests/api/ecoli/test_store_uri.py
git commit -m "feat(observables): _build_store_uri helper for Ray emitter store"
```

---

## Task 6: `/observables/index` route

**Files:**
- Modify: `sms_api/api/routers/sms.py` (add route after `get_simulation_data`, ~line 330)
- Test: `tests/api/ecoli/test_observables_endpoint.py` (create)

**Interfaces:**
- Consumes: `_build_store_uri` (Task 5); `list_observables`, `StoreIndex` (Task 1); `SimulationObservableIndex`, `ObservableInfoModel` (Task 4); `get_database_service`, `get_simulation` (existing).
- Produces: `GET /api/v1/simulations/{id}/observables/index?seed=0` → `SimulationObservableIndex`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/ecoli/test_observables_endpoint.py`:

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from sms_api.api.main import app
from sms_api.dependencies import get_database_service, set_database_service
from sms_api.simulation.models import Simulation
from sms_api.simulation.observable_reader import ObservableInfo, StoreIndex

BASE = "/api/v1"


class _FakeDB:
    def __init__(self, sim: Simulation | None) -> None:
        self._sim = sim

    async def get_simulation(self, simulation_id: int) -> Simulation | None:
        return self._sim


def _sim(experiment_id: str = "exp-abc") -> Simulation:
    # Construct a minimal valid Simulation; extra="allow" tolerates omitted optionals.
    return Simulation(database_id=49, experiment_id=experiment_id)


@asynccontextmanager
async def _client() -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.mark.asyncio
async def test_observables_index_ok(monkeypatch) -> None:
    saved = get_database_service()
    set_database_service(_FakeDB(_sim()))
    monkeypatch.setattr(
        "sms_api.api.routers.sms.list_observables",
        lambda uri: StoreIndex(store="zarr", observables=[ObservableInfo("mass", ["time"], [3])]),
    )
    try:
        async with _client() as c:
            r = await c.get(f"{BASE}/simulations/49/observables/index")
        assert r.status_code == 200
        body = r.json()
        assert body["experiment_id"] == "exp-abc"
        assert body["store"] == "zarr"
        assert body["observables"][0]["name"] == "mass"
    finally:
        set_database_service(saved)


@pytest.mark.asyncio
async def test_observables_index_404_when_missing(monkeypatch) -> None:
    saved = get_database_service()
    set_database_service(_FakeDB(None))
    try:
        async with _client() as c:
            r = await c.get(f"{BASE}/simulations/999/observables/index")
        assert r.status_code == 404
    finally:
        set_database_service(saved)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/ecoli/test_observables_endpoint.py -v`
Expected: FAIL — 404 route not found (returns wrong shape / 404 for the OK test too).

- [ ] **Step 3: Write minimal implementation**

At the top of `sms_api/api/routers/sms.py`, add to imports:

```python
import asyncio

from sms_api.simulation.models import (
    ObservableInfoModel,
    SimulationObservableIndex,
    SimulationObservables,
)
from sms_api.simulation.observable_reader import list_observables, read_observables
```

Add the route after `get_simulation_data` (~line 330):

```python
@config.router.get(
    path="/simulations/{id}/observables/index",
    response_model=SimulationObservableIndex,
    operation_id="get-simulation-observables-index",
    tags=["Simulations"],
    summary="List observables available in a simulation's emitter store (S3)",
)
async def get_simulation_observables_index(
    id: int = FastAPIPath(description="Database ID of the simulation"),
    seed: int = 0,
) -> SimulationObservableIndex:
    db = get_database_service()
    if db is None:
        raise HTTPException(503, "database service unavailable")
    sim = await db.get_simulation(simulation_id=id)
    if sim is None:
        raise HTTPException(404, f"Simulation {id} not found")
    store_uri = _build_store_uri(sim.experiment_id, seed)
    try:
        idx = await asyncio.to_thread(list_observables, store_uri)
    except FileNotFoundError:
        raise HTTPException(404, f"No emitter store for simulation {id} (seed {seed})")
    return SimulationObservableIndex(
        simulation_id=id,
        experiment_id=sim.experiment_id,
        seed=seed,
        store=idx.store,
        observables=[ObservableInfoModel(name=o.name, dims=o.dims, shape=o.shape) for o in idx.observables],
    )
```

Confirm `FastAPIPath`, `HTTPException`, and `get_database_service` are already imported in `sms.py` (the existing `get_simulation_status` route uses them). If `get_database_service` is only referenced via `Depends`, add `from sms_api.dependencies import get_database_service` to the imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/ecoli/test_observables_endpoint.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd /Users/eranagmon/code/sms-api
uv run ruff format sms_api/api/routers/sms.py tests/api/ecoli/test_observables_endpoint.py
uv run ruff check sms_api/api/routers/sms.py tests/api/ecoli/test_observables_endpoint.py
git add sms_api/api/routers/sms.py tests/api/ecoli/test_observables_endpoint.py
git commit -m "feat(observables): GET /api/v1/simulations/{id}/observables/index"
```

---

## Task 7: `/observables` route (timeseries)

**Files:**
- Modify: `sms_api/api/routers/sms.py` (add route after the index route)
- Test: `tests/api/ecoli/test_observables_endpoint.py` (extend)

**Interfaces:**
- Consumes: `_build_store_uri`, `read_observables`, `detect_store_kind`, `SimulationObservables`, `get_simulation`.
- Produces: `GET /api/v1/simulations/{id}/observables?names=a,b&seed=0` → `SimulationObservables`. `names` is a comma-separated list; empty/omitted = all.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/ecoli/test_observables_endpoint.py`:

```python
@pytest.mark.asyncio
async def test_observables_series_ok(monkeypatch) -> None:
    saved = get_database_service()
    set_database_service(_FakeDB(_sim()))
    monkeypatch.setattr(
        "sms_api.api.routers.sms.detect_store_kind",
        lambda uri: "zarr",
    )
    monkeypatch.setattr(
        "sms_api.api.routers.sms.read_observables",
        lambda uri, names: ([0.0, 1.0, 2.0], {"mass": [1.0, 2.0, 3.0]}),
    )
    try:
        async with _client() as c:
            r = await c.get(f"{BASE}/simulations/49/observables", params={"names": "mass"})
        assert r.status_code == 200
        body = r.json()
        assert body["time"] == [0.0, 1.0, 2.0]
        assert body["series"]["mass"] == [1.0, 2.0, 3.0]
    finally:
        set_database_service(saved)


@pytest.mark.asyncio
async def test_observables_series_bad_name_400(monkeypatch) -> None:
    saved = get_database_service()
    set_database_service(_FakeDB(_sim()))
    monkeypatch.setattr("sms_api.api.routers.sms.detect_store_kind", lambda uri: "zarr")

    def _raise(uri, names):
        raise KeyError("observables not in store: ['nope']")

    monkeypatch.setattr("sms_api.api.routers.sms.read_observables", _raise)
    try:
        async with _client() as c:
            r = await c.get(f"{BASE}/simulations/49/observables", params={"names": "nope"})
        assert r.status_code == 400
    finally:
        set_database_service(saved)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/ecoli/test_observables_endpoint.py -k series -v`
Expected: FAIL — route not found.

- [ ] **Step 3: Write minimal implementation**

Add the route after the index route in `sms.py`:

```python
@config.router.get(
    path="/simulations/{id}/observables",
    response_model=SimulationObservables,
    operation_id="get-simulation-observables",
    tags=["Simulations"],
    summary="Read observable timeseries from a simulation's emitter store (S3)",
)
async def get_simulation_observables(
    id: int = FastAPIPath(description="Database ID of the simulation"),
    names: str = "",
    seed: int = 0,
) -> SimulationObservables:
    db = get_database_service()
    if db is None:
        raise HTTPException(503, "database service unavailable")
    sim = await db.get_simulation(simulation_id=id)
    if sim is None:
        raise HTTPException(404, f"Simulation {id} not found")
    requested = [n.strip() for n in names.split(",") if n.strip()]
    store_uri = _build_store_uri(sim.experiment_id, seed)
    try:
        store_kind = await asyncio.to_thread(detect_store_kind, store_uri)
        time, series = await asyncio.to_thread(read_observables, store_uri, requested)
    except FileNotFoundError:
        raise HTTPException(404, f"No emitter store for simulation {id} (seed {seed})")
    except KeyError as e:
        raise HTTPException(400, str(e))
    return SimulationObservables(
        simulation_id=id,
        experiment_id=sim.experiment_id,
        seed=seed,
        store=store_kind,
        time=time,
        series=series,
    )
```

Add `detect_store_kind` to the reader import line:

```python
from sms_api.simulation.observable_reader import detect_store_kind, list_observables, read_observables
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/ecoli/test_observables_endpoint.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
cd /Users/eranagmon/code/sms-api
uv run ruff format sms_api/api/routers/sms.py tests/api/ecoli/test_observables_endpoint.py
uv run ruff check sms_api/api/routers/sms.py tests/api/ecoli/test_observables_endpoint.py
git add sms_api/api/routers/sms.py tests/api/ecoli/test_observables_endpoint.py
git commit -m "feat(observables): GET /api/v1/simulations/{id}/observables (timeseries)"
```

---

## Task 8: Full-suite + type check + integration note

**Files:** none (verification task)

- [ ] **Step 1: Run the new tests together**

Run: `uv run pytest tests/simulation/test_observable_reader.py tests/simulation/test_observable_models.py tests/api/ecoli/test_observables_endpoint.py tests/api/ecoli/test_store_uri.py -v`
Expected: PASS (all 15 tests).

- [ ] **Step 2: Type check the new modules**

Run: `uv run mypy sms_api/simulation/observable_reader.py sms_api/api/routers/sms.py sms_api/simulation/models.py`
Expected: no errors (fix any annotations inline; reader returns are fully typed).

- [ ] **Step 3: Lint the whole change**

Run: `uv run ruff check sms_api/ tests/`
Expected: clean.

- [ ] **Step 4: Manual integration check via the tunnel (documented, not automated)**

With the smsvpctest tunnel up on `localhost:8080` and a completed Ray simulation id (from `GET /api/v1/simulations`):

```bash
curl -s "http://localhost:8080/api/v1/simulations/<ID>/observables/index" | head -c 400
curl -s "http://localhost:8080/api/v1/simulations/<ID>/observables?names=<name>" | head -c 400
```

Expected: JSON index lists observables; the series call returns `time` + `series`. If the store path differs from `seed_{NN}/store.zarr` on the real deployment, adjust `_build_store_uri` and re-run Task 5's test. (This is the one assumption to validate against a real run; everything else is covered by fixtures.)

- [ ] **Step 5: Commit any fixes**

```bash
cd /Users/eranagmon/code/sms-api
git add -A && git commit -m "test(observables): full-suite + mypy green for observables endpoint" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage (Phase 1 scope):** the new sms-api endpoint (spec §3) — `/observables/index` + `/observables`, emitter-format-aware (zarr default + parquet), under `/api/*` (no ALB change), reads the Ray run's S3 store. Covered by Tasks 1–7. S3 IRSA read permission is a sms-cdk concern → **Phase 2**. Dashboard orchestration/UI/landing → **Phase 3**.

**Placeholder scan:** none — every code step has complete code; the only deliberately-deferred item is the real-deployment store-path validation in Task 8 Step 4, with the exact remediation pointer.

**Type consistency:** reader returns `StoreIndex`/`(list[float], dict[str, list[float]])` consistently; route models `SimulationObservableIndex`/`SimulationObservables` match field-for-field between models.py (Task 4) and the routes (Tasks 6–7); `_build_store_uri(experiment_id, seed)` signature is identical across Tasks 5–7.

## Follow-on phases (separate plans)

- **Phase 2 — sms-cdk:** verify/add the api pod IRSA `s3:GetObject`/`s3:ListBucket` on `{s3_output_prefix}` in the shared bucket; `cdk deploy smsvpctest` if changed. Small.
- **Phase 3 — vivarium-dashboard:** `sms_api_client` + `RemoteRunManager` (mirror `RunJobManager`), `POST /api/remote-run-start` / `GET /api/remote-run-status` (GitHub-login gated), launch-panel UI, and landing results as a study run in `runs.db` (needs a short exploration of the SQLiteEmitter history-table schema in `lib/composite_runs.py` before its tasks are fully specified).
