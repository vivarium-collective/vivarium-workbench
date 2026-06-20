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
