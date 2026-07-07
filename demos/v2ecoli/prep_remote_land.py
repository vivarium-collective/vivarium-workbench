#!/usr/bin/env python3
"""Pre-land a remote sms-api simulation run for the dashboard Simulations DB demo.

Reads the pre-built ``simulator_id`` from ``demos/v2ecoli/.demo_state.json``,
submits a short baseline run to sms-api, waits for completion, downloads the
results, and lands them so the Simulations DB tab shows ``Origin: remote`` entries
alongside local runs.

The landed results and runs_meta go into ``demos/v2ecoli/demo-runs/`` and
``.pbg/composite-runs.db`` (both gitignored) — this script does NOT modify any
existing v2ecoli studies, composites, or configuration files.

Usage:
    cd ~/vivarium-app/vivarium-dashboard
    python demos/v2ecoli/prep_remote_land.py

Pre-requisites:
    - prep_remote_build.py has been run (build is ready on sms-api)
    - sms-api SSM tunnel active (localhost:8080)
"""

from __future__ import annotations

import json
import os as _os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import v2ecoli as _v2ecoli

from vivarium_dashboard.lib.composite_runs import (
    complete_metadata,
    connect,
    generate_run_id,
    save_metadata,
)
from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError
from vivarium_dashboard.lib.workspace_deps_views import _sms_api_base

WORKSPACE_ROOT = Path(_os.environ.get(
    "V2ECOLI_ROOT", str(Path(_v2ecoli.__file__).resolve().parent.parent)))
DEMO_DIR = WORKSPACE_ROOT / "demos" / "dashboard"
STATE_FILE = DEMO_DIR / ".demo_state.json"
DEMO_RUNS_DIR = DEMO_DIR / "demo-runs"
POLL_INTERVAL = 15  # seconds between polling run status
MAX_WAIT = 900  # max seconds to wait for run completion


def load_state() -> dict:
    if not STATE_FILE.exists():
        print(f"ERROR: {STATE_FILE} not found — run prep_remote_build.py first")
        sys.exit(1)
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def _detect_store(extract_root: Path, seed: int = 0) -> tuple[str, Path]:
    """Find the native store under an extracted tar. Returns (kind, source_path)."""
    seed_store = next(extract_root.glob(f"**/seed_{seed:02d}/store.zarr"), None)
    if seed_store is not None and seed_store.is_dir():
        return "zarr", seed_store
    pq = next(extract_root.glob("**/history/**/*.pq"), None)
    if pq is not None:
        for parent in pq.parents:
            if parent.name == "history":
                return "parquet", parent.parent
    raise FileNotFoundError(
        f"no zarr (seed_{seed:02d}/store.zarr) or parquet (history/**/*.pq) store in {extract_root}"
    )


def land_store(
    tar_path: Path,
    *,
    spec_id: str,
    simulation_id: int,
    experiment_id: str,
    commit: str,
) -> str:
    """Extract tar_path, place the native store in demos/v2ecoli/demo-runs/,
    record runs_meta in .pbg/composite-runs.db. Returns run_id."""

    provenance = {
        "simulation_id": simulation_id,
        "experiment_id": experiment_id,
        "commit": commit,
        "backend": "ray",
        "source": "smsvpctest",
        "s3_uri": f"s3://sms-vpctest/{experiment_id}/",
    }
    run_id = generate_run_id(spec_id, params=provenance)

    with tempfile.TemporaryDirectory() as td:
        extract_root = Path(td)
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(extract_root, filter="data")  # noqa: S202
        kind, src = _detect_store(extract_root, seed=0)

        if kind == "zarr":
            dest = DEMO_RUNS_DIR / f"runs.{run_id}.zarr"
        else:
            dest = DEMO_RUNS_DIR / "parquet-runs" / experiment_id
            dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        provenance["store_path"] = str(dest)

    # Record in .pbg/composite-runs.db so the Simulations DB tab picks it up
    db_path = WORKSPACE_ROOT / ".pbg" / "composite-runs.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    try:
        save_metadata(
            conn,
            spec_id=spec_id,
            run_id=run_id,
            params=provenance,
            label=f"Demo remote run — {experiment_id}",
            started_at=time.time() - 60,  # approximate
            n_steps=2,
        )
        complete_metadata(conn, run_id=run_id, n_steps=2, status="completed")
    finally:
        conn.close()

    print(f"  Landed: {run_id}")
    print(f"  Store:  {dest}")
    print(f"  DB:     {db_path}")
    return run_id


def main() -> int:
    print("Pre-landing remote sms-api simulation run\n")

    state = load_state()
    sim_id = state.get("simulator_id")
    commit = state.get("commit", "unknown")
    branch = state.get("branch", "main")

    if not sim_id:
        print("ERROR: .demo_state.json missing simulator_id")
        return 1

    client = SmsApiClient(_sms_api_base())
    experiment_id = f"dashboard-demo-{int(time.time())}"

    # Check if we already landed this build
    landed_key = f"landed_{sim_id}"
    if state.get(landed_key):
        print(f"Already landed run for build #{sim_id}:")
        print(json.dumps(state.get(landed_key, {}), indent=2))
        print("\nTo re-land, delete the 'landed_*' key in .demo_state.json")
        return 0

    # Verify build is ready
    print(f"[1/5] Checking build #{sim_id} status...")
    try:
        status = client.simulator_status(sim_id)
        s = status.get("status", "unknown")
        print(f"  Status: {s}")
        if s not in ("ready", "built", "complete", "completed"):
            print("ERROR: build not ready — run prep_remote_build.py and wait for it to complete")
            return 1
    except SmsApiError as e:
        print(f"ERROR: sms-api unreachable: {e}")
        return 1

    # Submit simulation
    print(f"\n[2/5] Submitting demo simulation (2 generations, 1 seed)...")
    try:
        result = client.run_simulation(
            simulator_id=sim_id,
            num_generations=2,
            num_seeds=1,
            run_parca=False,
            observables=[],
            experiment_id=experiment_id,
            description="Dashboard demo — pre-landed remote run",
        )
        remote_run_id = result.get("simulation_id") or result.get("database_id")
        if not remote_run_id:
            print(f"ERROR: sms-api response missing simulation_id: {json.dumps(result)[:200]}")
            return 1
        print(f"  Submitted: simulation #{remote_run_id}")
    except SmsApiError as e:
        print(f"ERROR: run submission failed: {e}")
        return 1

    # Poll for completion
    print(f"\n[3/5] Polling run status (every {POLL_INTERVAL}s)...")
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        try:
            s = client.simulation_status(remote_run_id)
            status_str = s.get("status", "unknown")
            print(f"  [{elapsed:.0f}s] status: {status_str}")

            if status_str in ("completed", "done", "succeeded"):
                print(f"\n  Run completed ✓")
                break

            if status_str in ("failed", "error", "cancelled"):
                print(f"\n  Run failed: {s.get('message', status_str)}")
                return 1

        except SmsApiError as e:
            print(f"  Poll error: {e}")

        if elapsed > MAX_WAIT:
            print(f"\n  Timed out after {MAX_WAIT}s")
            return 1

        time.sleep(POLL_INTERVAL)

    # Download results
    print(f"\n[4/5] Downloading results for simulation #{remote_run_id}...")
    download_dir = DEMO_DIR / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    try:
        tar_path = client.download_data(remote_run_id, download_dir, timeout=300)
        size_mb = tar_path.stat().st_size / (1024 * 1024)
        print(f"  Downloaded: {tar_path.name} ({size_mb:.1f} MB)")
    except SmsApiError as e:
        print(f"ERROR: download failed: {e}")
        return 1

    # Land results
    print(f"\n[5/5] Landing results into demo-runs/...")
    try:
        run_id = land_store(
            tar_path,
            spec_id="baseline",
            simulation_id=remote_run_id,
            experiment_id=experiment_id,
            commit=commit,
        )
    except Exception as e:
        print(f"ERROR: landing failed: {e}")
        return 1

    # Save state
    state[landed_key] = {
        "run_id": run_id,
        "simulation_id": remote_run_id,
        "experiment_id": experiment_id,
        "commit": commit,
        "landed_at": time.time(),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)

    print(f"\nDone. The Simulations DB tab will now show this remote run.")
    print(f"Run the dashboard with:  vivarium-workbench serve --workspace .")
    return 0


if __name__ == "__main__":
    sys.exit(main())
