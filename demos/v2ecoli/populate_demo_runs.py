#!/usr/bin/env python3
"""Populate ``.pbg/composite-runs.db`` with synthetic demo run entries.

The Simulations DB tab would be empty in the v2ecoli workspace (all run data
lives on other machines; only static charts were committed).  This script seeds
a handful of lightweight, clearly-labeled demo entries so the table has content
to browse during a demo — showing different emitters, origins, and statuses.

All entries go to ``.pbg/composite-runs.db`` (already gitignored).  No
existing v2ecoli files are modified.

Usage:
    cd ~/vivarium-app/vivarium-dashboard
    python demos/v2ecoli/populate_demo_runs.py

The script is idempotent — re-running it deletes the old demo DB and recreates
it, so you can always get a clean slate.
"""

from __future__ import annotations

import json
import os as _os
import sys
import time
from pathlib import Path

import v2ecoli as _v2ecoli

from vivarium_dashboard.lib.composite_runs import (
    complete_metadata,
    connect,
    generate_run_id,
    save_metadata,
)

WORKSPACE_ROOT = Path(_os.environ.get(
    "V2ECOLI_ROOT", str(Path(_v2ecoli.__file__).resolve().parent.parent)))
DB_PATH = WORKSPACE_ROOT / ".pbg" / "composite-runs.db"

# Demo entries — each tuple is (label, spec_id, study_slug, investigation_slug,
# emitter_kind, is_remote, n_steps, status, hours_ago)
DEMO_RUNS: list[dict] = [
    # ── showcase-1: ParCa rebuilds ──
    dict(label="ParCa full 51-TF rebuild", spec_id="v2ecoli.composites.parca",
         study="showcase-1-parca", investigation="v2ecoli-baseline-showcase",
         emitter="xarray", remote=False, n_steps=9, status="completed", ago=72),
    dict(label="ParCa fast-mode debug", spec_id="v2ecoli.composites.parca",
         study="showcase-1-parca", investigation="v2ecoli-baseline-showcase",
         emitter="sqlite", remote=False, n_steps=9, status="completed", ago=70),

    # ── showcase-2: baseline ensembles ──
    dict(label="Baseline WT 2-seed ensemble", spec_id="v2ecoli.composites.baseline",
         study="showcase-2-baseline-figures", investigation="v2ecoli-baseline-showcase",
         emitter="parquet", remote=False, n_steps=2000, status="completed", ago=48),
    dict(label="Baseline WT seed-0 rerun", spec_id="v2ecoli.composites.baseline",
         study="showcase-2-baseline-figures", investigation="v2ecoli-baseline-showcase",
         emitter="parquet", remote=False, n_steps=2000, status="completed", ago=24),
    dict(label="Baseline WT seed-1 rerun", spec_id="v2ecoli.composites.baseline",
         study="showcase-2-baseline-figures", investigation="v2ecoli-baseline-showcase",
         emitter="xarray", remote=False, n_steps=2000, status="completed", ago=23),

    # ── showcase-4: variant sweep ──
    dict(label="5-variant sweep Δglucose", spec_id="v2ecoli.composites.baseline",
         study="showcase-4-variant-comparison", investigation="v2ecoli-baseline-showcase",
         emitter="parquet", remote=False, n_steps=1800, status="completed", ago=20),
    dict(label="5-variant sweep Δsuccinate", spec_id="v2ecoli.composites.baseline",
         study="showcase-4-variant-comparison", investigation="v2ecoli-baseline-showcase",
         emitter="parquet", remote=False, n_steps=1800, status="completed", ago=19),
    dict(label="5-variant sweep ΔO2", spec_id="v2ecoli.composites.baseline",
         study="showcase-4-variant-comparison", investigation="v2ecoli-baseline-showcase",
         emitter="parquet", remote=False, n_steps=1800, status="failed", ago=18),
    dict(label="5-variant sweep full re-run", spec_id="v2ecoli.composites.baseline",
         study="showcase-4-variant-comparison", investigation="v2ecoli-baseline-showcase",
         emitter="xarray", remote=False, n_steps=1800, status="completed", ago=10),

    # ── mbp-03: reactor-coupled ──
    dict(label="BiRD reactor + WCM cell", spec_id="v2ecoli.composites.reactor_bird_coupled.reactor_bird_coupled",
         study="mbp-03-bird-reactor-coupling", investigation="multiscale-bioprocess",
         emitter="xarray", remote=False, n_steps=3600, status="completed", ago=96),
    dict(label="BiRD reactor + Millard cell", spec_id="v2ecoli.composites.reactor_bird_coupled_millard.reactor_bird_coupled_millard",
         study="mbp-03-bird-reactor-coupling", investigation="multiscale-bioprocess",
         emitter="xarray", remote=False, n_steps=3600, status="running", ago=0.5),

    # ── colony runs ──
    dict(label="Colony 50-cell growth", spec_id="v2ecoli.composites.colony.colony",
         study="colonies-01-hpc-readiness", investigation="colonies",
         emitter="parquet", remote=False, n_steps=5000, status="completed", ago=168),

    # ── sms-api remote runs (pre-landed) ──
    dict(label="sms-api baseline ensemble", spec_id="baseline",
         study="showcase-2-baseline-figures", investigation="v2ecoli-baseline-showcase",
         emitter="xarray", remote=True, n_steps=2000, status="completed", ago=5,
         remote_payload={"simulation_id": 1042, "experiment_id": "demo-ensemble-1",
                         "backend": "ray", "source": "smsvpctest",
                         "s3_uri": "s3://sms-vpctest/demo-ensemble-1/"}),
    dict(label="sms-api large ensemble (256 seeds)", spec_id="baseline",
         study="showcase-6-equivalence-large", investigation="v2ecoli-baseline-showcase",
         emitter="parquet", remote=True, n_steps=2000, status="completed", ago=3,
         remote_payload={"simulation_id": 1058, "experiment_id": "demo-large-256",
                         "backend": "ray", "source": "smsvpctest",
                         "s3_uri": "s3://sms-vpctest/demo-large-256/"}),
    dict(label="sms-api PDMP inference ensemble", spec_id="millard_pdmp_baseline",
         study="pdmp-02-jump-processes", investigation="v2ecoli-pdmp",
         emitter="xarray", remote=True, n_steps=5000, status="completed", ago=1,
         remote_payload={"simulation_id": 1103, "experiment_id": "demo-pdmp-1",
                         "backend": "ray", "source": "smsvpctest",
                         "s3_uri": "s3://sms-vpctest/demo-pdmp-1/"}),

    # ── ketchup (external simulator) ──
    dict(label="KETCHUP baseline fit", spec_id="pbg_ketchup.composites.estimation.ketchup_baseline",
         study="ketchup-exchange-comparison", investigation="ketchup-baseline-comparison",
         emitter="sqlite", remote=False, n_steps=500, status="completed", ago=120),
]


def make_params(r: dict) -> dict:
    """Build the params_json dict for a run entry."""
    p: dict = {}
    if r["remote"] and r.get("remote_payload"):
        p.update(r["remote_payload"])
    # Store path for emitter type detection
    kind = r["emitter"]
    slug = r["study"]
    if kind == "xarray":
        p["store_path"] = f"studies/{slug}/runs.demo.{r['label'][:20]}.zarr"
    elif kind == "parquet":
        p["store_path"] = f"studies/{slug}/parquet-runs/demo-{slug}/"
    p.update({"_demo": True, "_label": r["label"]})
    return p


def main() -> int:
    print("Seeding demo runs for Simulations DB tab\n")

    if DB_PATH.exists():
        print(f"Removing existing {DB_PATH}")
        DB_PATH.unlink()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(DB_PATH))

    for r in DEMO_RUNS:
        params = make_params(r)
        run_id = generate_run_id(r["spec_id"], params=params)

        started = time.time() - (r["ago"] * 3600)
        completed = started + (r["n_steps"] * 0.5) if r["status"] == "completed" else None

        save_metadata(
            conn,
            spec_id=r["spec_id"],
            run_id=run_id,
            params=params,
            label=r["label"],
            started_at=started,
            n_steps=r["n_steps"],
        )

        if r["status"] != "running":
            complete_metadata(conn, run_id=run_id, n_steps=r["n_steps"], status=r["status"])

        icon = "☁️" if r["remote"] else "💻"
        print(f"  {icon} {r['label']:50s} [{r['emitter']:7s}] {r['status']}")

    conn.close()

    print(f"\n✓ {len(DEMO_RUNS)} demo runs written to {DB_PATH}")
    print("  The Simulations DB tab will now show these entries.")
    print(f"  Re-run this script to reset them at any time.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
