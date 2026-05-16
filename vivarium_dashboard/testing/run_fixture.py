"""Run wrapper + pytest fixture for study tests.

Tests in studies/<slug>/tests/test_*.py receive a `run` fixture that resolves
to a `Run` bound to the study's latest emitter row.
"""
from __future__ import annotations
import json, sqlite3
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import yaml


class RunNotAvailableError(RuntimeError):
    """Raised when a study has no runs.db, no rows, or no requested run_id."""


class Run:
    """Wrapper around a single row in a study's runs.db."""

    def __init__(self, db_path: Path, run_id: str | None = None):
        db_path = Path(db_path)
        if not db_path.exists():
            raise RunNotAvailableError(f"runs.db not found at {db_path}")
        self._db_path = db_path
        self._db = sqlite3.connect(db_path)
        self._db.row_factory = sqlite3.Row
        self._run_id = run_id or self._latest_run_id()
        if self._run_id is None:
            raise RunNotAvailableError(f"runs.db at {db_path} contains no runs")
        self._meta = self._load_meta()

    def _latest_run_id(self) -> str | None:
        row = self._db.execute(
            "SELECT run_id FROM runs_meta ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return row["run_id"] if row else None

    def _load_meta(self) -> dict:
        row = self._db.execute(
            "SELECT * FROM runs_meta WHERE run_id = ?", (self._run_id,),
        ).fetchone()
        if row is None:
            raise RunNotAvailableError(f"run_id {self._run_id!r} not found")
        params = row["params"]
        return {
            "run_id": row["run_id"],
            "params": json.loads(params) if params else {},
            "seed": row["seed"],
            "status": row["status"],
            "n_steps": row["n_steps"] or 0,
            "variant": row["variant"],
            "composite": row["composite"],
            "timestamp": row["timestamp"],
        }

    # Metadata
    @property
    def run_id(self) -> str: return self._meta["run_id"]
    @property
    def params(self) -> dict: return self._meta["params"]
    @property
    def seed(self) -> int | None: return self._meta["seed"]
    @property
    def status(self) -> str: return self._meta["status"]
    @property
    def n_steps(self) -> int: return self._meta["n_steps"]
    @property
    def variant(self) -> str | None: return self._meta["variant"]
    @property
    def composite(self) -> str: return self._meta["composite"]

    # Trajectory
    def observable(self, name: str) -> np.ndarray:
        rows = self._db.execute(
            "SELECT step, value FROM history WHERE run_id = ? AND observable = ? ORDER BY step",
            (self._run_id, name),
        ).fetchall()
        return np.array([r["value"] for r in rows], dtype=float)

    @property
    def time(self) -> np.ndarray:
        rows = self._db.execute(
            "SELECT DISTINCT step FROM history WHERE run_id = ? ORDER BY step",
            (self._run_id,),
        ).fetchall()
        return np.array([r["step"] for r in rows], dtype=float)

    def final(self, name: str) -> float:
        arr = self.observable(name)
        if len(arr) == 0:
            raise KeyError(f"no values for observable {name!r}")
        return float(arr[-1])

    def initial(self, name: str) -> float:
        arr = self.observable(name)
        if len(arr) == 0:
            raise KeyError(f"no values for observable {name!r}")
        return float(arr[0])

    def cv(self, name: str) -> float:
        arr = self.observable(name)
        mean = float(arr.mean()) if len(arr) else 0.0
        return float(arr.std() / mean) if mean else float("nan")

    @property
    def trajectory(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT step, observable, value FROM history WHERE run_id = ?",
            self._db, params=(self._run_id,),
        ).pivot(index="step", columns="observable", values="value")


def _find_study_dir(test_file: Path) -> Path:
    """Walk up from a test file until study.yaml is found."""
    cur = test_file.resolve()
    if cur.is_file():
        cur = cur.parent
    for ancestor in [cur, *cur.parents]:
        if (ancestor / "study.yaml").is_file():
            return ancestor
    raise RunNotAvailableError(
        f"no study.yaml found walking up from {test_file}; "
        f"the `run` fixture must be invoked from inside a study directory"
    )


@pytest.fixture
def run(request) -> Run:
    """Latest run of the study under test. Reads study.yaml to discover
    `tests.data_source`; defaults to `latest_run`."""
    test_file = Path(str(request.fspath))
    study_dir = _find_study_dir(test_file)
    spec = yaml.safe_load((study_dir / "study.yaml").read_text()) or {}
    data_source = (spec.get("tests") or {}).get("data_source", "latest_run")
    if data_source == "all_runs":
        pytest.skip(
            "data_source: all_runs requires the test to use the parametrized "
            "`runs` fixture instead of `run`"
        )
    db = study_dir / "runs.db"
    if data_source == "first_run":
        # Load earliest row
        conn = sqlite3.connect(db) if db.exists() else None
        if conn is None:
            raise RunNotAvailableError(f"runs.db not found at {db}")
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT run_id FROM runs_meta ORDER BY timestamp ASC LIMIT 1"
            ).fetchone()
            if row is None:
                raise RunNotAvailableError(f"runs.db at {db} contains no runs")
            return Run(db, run_id=row["run_id"])
        finally:
            conn.close()
    return Run(db)
