# Remote Runs — Phase 3a-rev: store-mirroring landing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 3a's SQLite-history landing with **store-mirroring**: download a finished remote run's native store via sms-api's existing `/data` tar.gz endpoint and place it in the study dir in the exact layout the dashboard's native chart reader expects (`<study>/runs.<run_id>.zarr` for zarr; `<study>/parquet-runs/<experiment_id>/history/...` for parquet), then record a `runs_meta` row. Renders through `_extract_paths_from_zarr` / `_extract_paths_from_parquet` like a local run.

**Architecture:** Keep `SmsApiClient` (PR #288) and add a streaming `download_data`. Rewrite `remote_run_landing.py`: extract the tar.gz, detect store kind from contents, place the native store unmodified (zarr: a seed's `store.zarr` is internally identical to the dashboard's expected `runs.<run_id>.zarr` — only the path differs; parquet: copy the hive tree), and write `runs_meta` via `composite_runs`. No state-blob reconstruction, no SQLite history.

**Tech Stack:** Python 3.11+, stdlib `urllib`/`tarfile`/`shutil`/`sqlite3`, `composite_runs`, pytest.

**Repo:** `/Users/eranagmon/code/vivarium-dashboard` (branch `feat/dashboard-remote-runs`).

## Supersedes
- Phase 3a Tasks 3–4 (`_state_blobs` + SQLite `land_remote_run` in `remote_run_landing.py`) — replaced wholesale.
- Phase 3a Tasks 1–2 (`SmsApiClient` GET/POST) — KEPT, extended here with `download_data`.

## Global Constraints
- No new deps — stdlib `urllib` (streaming), `tarfile`, `shutil`, `sqlite3`.
- Place the native store UNMODIFIED. Confirmed from emitter+reader source: a remote Ray `seed_NN/store.zarr` has the same internal datatree (`experiment_id=*/variant=*/lineage_seed=*`, `generation=N`/`time_gen=N`/`id_<leaf>`/leaf-name) the dashboard expects — only the path differs.
- `runs_meta` rows via `composite_runs` (`connect`, `generate_run_id`, `save_metadata`, `complete_metadata`). The store path is recorded so `_emitter_label`/`emitter_type_of` classify it (`.zarr`→XArray, `.parquet`→Parquet).
- Persist remote `simulation_id` in `runs_meta.params_json` (provenance).
- A Ray run has multiple seeds; v1 lands ONE seed (default 0) as `<study>/runs.<run_id>.zarr`.

## File Structure
- Modify `vivarium_dashboard/lib/sms_api_client.py` — add `download_data`.
- Rewrite `vivarium_dashboard/lib/remote_run_landing.py` — `place_zarr_store`, `place_parquet_store`, `land_remote_run` (delete `_state_blobs` + the old `land_remote_run`/`_init_emitter_tables`).
- Rewrite `tests/test_remote_run_landing.py` — fixture tar.gz with a tiny zarr store; assert placement + runs_meta + `_latest_zarr_for_study` finds it.
- Add a test to `tests/test_sms_api_client.py` — `download_data` streams to a file.

---

## Task 1: `SmsApiClient.download_data` (stream `/data` tar.gz to a file)

**Files:**
- Modify: `vivarium_dashboard/lib/sms_api_client.py`
- Test: `tests/test_sms_api_client.py`

**Interfaces:**
- Consumes: `SmsApiClient`, `SmsApiError` (PR #288).
- Produces: `download_data(self, simulation_id: int, dest_dir: Path) -> Path` — POSTs `/api/v1/simulations/{id}/data`, streams the gzip body to `dest_dir/sim_<id>.tar.gz`, returns that path. Raises `SmsApiError` on non-200/connection failure.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sms_api_client.py`:

```python
from pathlib import Path


def test_download_data_streams_to_file(monkeypatch, tmp_path):
    cap = {}
    payload = b"\x1f\x8b\x08fake-gzip-bytes"

    class _RawResp:
        status = 200

        def __init__(self):
            self._b = io.BytesIO(payload)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._b.close()

        def read(self, n=-1):
            return self._b.read(n)

    def fake_urlopen(req, timeout=None):
        cap["url"] = req.full_url
        cap["method"] = req.get_method()
        return _RawResp()

    monkeypatch.setattr("vivarium_dashboard.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080")
    out = c.download_data(49, tmp_path)
    assert out == tmp_path / "sim_49.tar.gz"
    assert out.read_bytes() == payload
    assert cap["method"] == "POST"
    assert cap["url"] == "http://h:8080/api/v1/simulations/49/data"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/eranagmon/code/vivarium-dashboard && .venv/bin/python -m pytest tests/test_sms_api_client.py::test_download_data_streams_to_file -v`
Expected: FAIL — `AttributeError: 'SmsApiClient' object has no attribute 'download_data'`.

- [ ] **Step 3: Write minimal implementation**

Add to `vivarium_dashboard/lib/sms_api_client.py` (add `import shutil` and `from pathlib import Path` to imports):

```python
    def download_data(self, simulation_id: int, dest_dir: Path) -> Path:
        """Stream the run's native-store tar.gz (POST /data) to dest_dir/sim_<id>.tar.gz."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / f"sim_{simulation_id}.tar.gz"
        url = f"{self.base_url}/api/v1/simulations/{simulation_id}/data"
        req = Request(url, data=b"", method="POST", headers={"Accept": "application/gzip"})
        try:
            with urlopen(req, timeout=self.timeout) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"POST {url} -> {e.code}") from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"POST {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        return out_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_sms_api_client.py -v`
Expected: PASS (6 tests — 5 existing + the new one).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/sms_api_client.py tests/test_sms_api_client.py
git commit -m "feat(remote-runs): SmsApiClient.download_data streams /data tar.gz to a file"
```

---

## Task 2: store-mirroring `land_remote_run`

**Files:**
- Rewrite: `vivarium_dashboard/lib/remote_run_landing.py`
- Rewrite: `tests/test_remote_run_landing.py`

**Interfaces:**
- Consumes: `composite_runs.connect/generate_run_id/save_metadata/complete_metadata`; `study_charts._latest_zarr_for_study` (read-back assertion).
- Produces:
  - `_detect_and_locate(extract_root: Path, seed: int) -> tuple[str, Path]` — returns `("zarr", <seed_dir>/store.zarr)` if a `seed_{seed:02d}/store.zarr` exists, else `("parquet", <experiment history root>)` if `**/history/**/*.pq` exists; raises `FileNotFoundError` if neither.
  - `land_remote_run(study_dir, *, spec_id, simulation_id, experiment_id, commit, tar_path: Path, seed: int = 0, label: str | None = None) -> str` — extracts `tar_path`, places the native store, writes `runs_meta`, returns `run_id`.

- [ ] **Step 1: Write the failing test**

Replace the body of `tests/test_remote_run_landing.py` with:

```python
import json
import sqlite3
import tarfile
from pathlib import Path

import numpy as np
import xarray as xr

from vivarium_dashboard.lib.remote_run_landing import land_remote_run


def _make_remote_zarr_tar(tmp_path: Path, seed: int = 0) -> Path:
    """Build a tar.gz mirroring a Ray run: seed_NN/store.zarr with an experiment_id=* partition."""
    staging = tmp_path / "staging"
    # Minimal store: the dashboard reader only needs the runs.*.zarr dir to contain an
    # experiment_id=* child to be selected; internal leaf detail is exercised elsewhere.
    part = staging / f"seed_{seed:02d}" / "store.zarr" / f"experiment_id=exp-seed{seed:02d}"
    part.mkdir(parents=True)
    ds = xr.Dataset({"cell_mass": ("time", np.array([1.0, 2.0, 3.0]))}, coords={"time": [0.0, 1.0, 2.0]})
    ds.to_zarr(part / "leaf.zarr", mode="w")
    tar_path = tmp_path / "sim_49.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(staging, arcname=".")
    return tar_path


def test_land_zarr_places_store_and_writes_runs_meta(tmp_path: Path):
    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = land_remote_run(
        study,
        spec_id="v2ecoli.composites.baseline",
        simulation_id=49,
        experiment_id="exp-abc",
        commit="abc123",
        tar_path=tar,
        seed=0,
    )
    # zarr store placed at <study>/runs.<run_id>.zarr with the experiment_id=* partition intact
    zarr_dir = study / f"runs.{run_id}.zarr"
    assert zarr_dir.is_dir()
    assert next(zarr_dir.glob("experiment_id=*"), None) is not None

    # runs_meta written, status completed, provenance carries simulation_id, store path recorded
    conn = sqlite3.connect(str(study / "runs.db"))
    try:
        meta = conn.execute(
            "SELECT status, params_json FROM runs_meta WHERE run_id=?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert meta[0] == "completed"
    prov = json.loads(meta[1])
    assert prov["simulation_id"] == 49
    assert prov["store_path"].endswith(f"runs.{run_id}.zarr")


def test_landed_zarr_is_discovered_by_study_charts(tmp_path: Path):
    from vivarium_dashboard.lib import study_charts

    study = tmp_path / "study"
    study.mkdir()
    tar = _make_remote_zarr_tar(tmp_path)
    run_id = land_remote_run(
        study, spec_id="s", simulation_id=7, experiment_id="e", commit="c", tar_path=tar, seed=0
    )
    found = study_charts._latest_zarr_for_study(study)
    assert found == study / f"runs.{run_id}.zarr"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_remote_run_landing.py -v`
Expected: FAIL — old `_state_blobs`/`land_remote_run` signature gone / `tar_path` arg unknown.

- [ ] **Step 3: Write minimal implementation**

Replace the entire contents of `vivarium_dashboard/lib/remote_run_landing.py` with:

```python
"""Land a remote simulation's NATIVE store into a study's run directory.

Mirror-the-store-format: extract the run's `/data` tar.gz and place the native
store unmodified where the dashboard's native chart reader expects it
(`<study>/runs.<run_id>.zarr` for zarr; `<study>/parquet-runs/<experiment_id>/`
for parquet), then record a runs_meta row. No reconstruction — a remote
`seed_NN/store.zarr` is internally identical to the dashboard's expected
`runs.<run_id>.zarr`; only the path differs.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import time as _time
from pathlib import Path

from vivarium_dashboard.lib import composite_runs as cr


def _detect_and_locate(extract_root: Path, seed: int) -> tuple[str, Path]:
    """Find the native store under an extracted tar. Returns (kind, source_path)."""
    seed_store = next(extract_root.glob(f"**/seed_{seed:02d}/store.zarr"), None)
    if seed_store is not None and seed_store.is_dir():
        return "zarr", seed_store
    # parquet: locate the experiment dir that contains a history/ subtree of .pq files
    pq = next(extract_root.glob("**/history/**/*.pq"), None)
    if pq is not None:
        # the experiment root is the parent of the `history` dir
        for parent in pq.parents:
            if parent.name == "history":
                return "parquet", parent.parent
    raise FileNotFoundError(f"no zarr (seed_{seed:02d}/store.zarr) or parquet (history/**/*.pq) store in {extract_root}")


def land_remote_run(
    study_dir: Path,
    *,
    spec_id: str,
    simulation_id: int,
    experiment_id: str,
    commit: str,
    tar_path: Path,
    seed: int = 0,
    label: str | None = None,
) -> str:
    """Extract tar_path, place the native store in study_dir, record runs_meta; return run_id."""
    study_dir = Path(study_dir)
    study_dir.mkdir(parents=True, exist_ok=True)

    provenance = {
        "simulation_id": simulation_id,
        "experiment_id": experiment_id,
        "commit": commit,
        "backend": "ray",
        "source": "smsvpctest",
    }
    run_id = cr.generate_run_id(spec_id, params=provenance)

    with tempfile.TemporaryDirectory() as td:
        extract_root = Path(td)
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(extract_root)  # noqa: S202 — trusted internal artifact from our own API
        kind, src = _detect_and_locate(extract_root, seed)
        if kind == "zarr":
            dest = study_dir / f"runs.{run_id}.zarr"
        else:
            dest = study_dir / "parquet-runs" / experiment_id
            dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)

    provenance["store_path"] = str(dest)
    started = _time.time()
    conn = cr.connect(study_dir / "runs.db")
    try:
        cr.save_metadata(
            conn,
            spec_id=spec_id,
            run_id=run_id,
            params=provenance,
            label=label or "Remote run (smsvpctest)",
            started_at=started,
            n_steps=0,
        )
        cr.complete_metadata(conn, run_id=run_id, n_steps=0, status="completed")
    finally:
        conn.close()

    return run_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_remote_run_landing.py -v`
Expected: PASS (2 tests). If `_latest_zarr_for_study` requires the `experiment_id=*` child to contain readable zarr data (not just exist), the fixture's `leaf.zarr` under the partition satisfies the `next(zarr_dir.glob("experiment_id=*"))` existence check at `study_charts.py:641`.

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vivarium-dashboard
git add vivarium_dashboard/lib/remote_run_landing.py tests/test_remote_run_landing.py
git commit -m "feat(remote-runs): store-mirroring land_remote_run (place native zarr/parquet store)"
```

---

## Task 3: full-suite + regression

- [ ] **Step 1:** Run: `.venv/bin/python -m pytest tests/test_sms_api_client.py tests/test_remote_run_landing.py -v` → all pass.
- [ ] **Step 2:** Run: `.venv/bin/python -m pytest tests/ -k "runs or chart or composite" -q` → no NEW failures vs the known pre-existing set (the parquet/pbg_superpowers env failures present on the base commit). Note any failure naming `remote_run_landing`/`sms_api_client`.
- [ ] **Step 3:** Commit any fixes: `git add -A && git commit -m "test(remote-runs): store-mirroring suite green" || echo "nothing"`.

---

## Self-Review

**Spec coverage:** mirror-the-store-format landing (design REVISION 2026-06-19) — `download_data` (Task 1) + store-detecting/placing `land_remote_run` (Task 2), zarr to `runs.<run_id>.zarr` and parquet to `parquet-runs/<experiment_id>/`, recorded in `runs_meta` with provenance, verified discoverable by the real `_latest_zarr_for_study`. The old SQLite landing is fully removed.

**Placeholder scan:** none — complete code throughout; the one read-side assumption (the `experiment_id=*` existence check) is named with its file:line.

**Type consistency:** `download_data(simulation_id:int, dest_dir:Path)->Path` and `land_remote_run(study_dir,*,spec_id,simulation_id,experiment_id,commit,tar_path:Path,seed=0,label=None)->str` are consistent across tasks and tests.

## Follow-ons (separate)
- **sms-api:** generalize `_build_store_uri` to locate parquet stores too (closes the observables-endpoint zarr-only locator gap) — Phase 1 follow-up.
- **Phase 3b:** `RemoteRunManager` wiring push→build→run→poll→`download_data`→`land_remote_run`; login-gated endpoints; `smsApiBase` setting. Note the multi-seed choice (v1 lands seed 0) and that `_latest_zarr_for_study` picks the most-recent `runs.*.zarr` by mtime.
- **Phase 3c:** launch-panel UI.
