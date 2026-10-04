"""Run wrapper + pytest fixture for study tests.

Tests in studies/<slug>/tests/test_*.py receive a `run` fixture that resolves
to a `Run` bound to the study's latest emitter row.

Schema contract (the writers of record, not a local restatement):

* ``runs_meta`` -- created by ``lib.composite_runs.connect`` and populated by
  ``lib.composite_runs.save_metadata``/``complete_metadata``: one row per run
  keyed by ``run_id``, with ``spec_id`` (composite), ``label`` (the study's
  baseline/variant entry name), ``params_json``, ``started_at``, ``n_steps``,
  ``status`` and ``manifest_json``.
* ``history`` -- written by ``viva_emitters.SQLiteEmitter`` (same DDL as
  ``lib.composite_runs.ensure_history_table``): one row per emitted step,
  ``(simulation_id = run_id, step, global_time, state)`` where ``state`` is the
  JSON of the whole emitted dict for that step (values may be nested maps).
"""
from __future__ import annotations
import json, sqlite3
from pathlib import Path
import numpy as np
import pytest
import yaml

from vivarium_workbench.lib.run_index import row_seed


_MISSING = object()


def _get_dotted(state, name: str):
    """Value at ``name`` in an emitted-state dict; a dotted name ("species.X")
    descends into nested maps. Returns ``_MISSING`` when absent."""
    if isinstance(state, dict) and name in state:
        return state[name]
    node = state
    for part in name.split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _flatten(state: dict, prefix: str = "") -> dict:
    """Nested emitted-state dict -> {dotted.path: leaf value}."""
    out = {}
    for k, v in state.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


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
            "SELECT run_id FROM runs_meta ORDER BY started_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        return row["run_id"] if row else None

    def _load_meta(self) -> dict:
        row = self._db.execute(
            "SELECT * FROM runs_meta WHERE run_id = ?", (self._run_id,),
        ).fetchone()
        if row is None:
            raise RunNotAvailableError(f"run_id {self._run_id!r} not found")
        params = json.loads(row["params_json"]) if row["params_json"] else {}
        return {
            "run_id": row["run_id"],
            "params": params,
            # Same derivation rerun/find_matching_run use: manifest seed,
            # else params["seed"], else None. manifest_json is a migrated
            # column, absent from a runs.db older than it.
            "seed": row_seed({"params": params,
                              "manifest_json": row["manifest_json"]
                              if "manifest_json" in row.keys() else None}),
            "status": row["status"],
            "n_steps": row["n_steps"] or 0,
            "label": row["label"],
            "composite": row["spec_id"],
            "timestamp": row["started_at"],
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
    def label(self) -> str | None:
        """The study baseline/variant entry name this run was launched as."""
        return self._meta["label"]
    @property
    def variant(self) -> str | None:
        """Always ``None``: runs_meta records the launched entry name in
        ``label`` for baseline and variant runs alike, with no field saying
        which kind it was, so the variant cannot be derived from the run row.
        Use :attr:`label`."""
        return None
    @property
    def composite(self) -> str: return self._meta["composite"]
    @property
    def timestamp(self) -> float: return self._meta["timestamp"]

    # Trajectory
    def _history(self) -> list[tuple[int, dict]]:
        """(step, emitted-state dict) for this run, in step order."""
        if not hasattr(self, "_history_cache"):
            rows = self._db.execute(
                "SELECT step, state FROM history WHERE simulation_id = ? ORDER BY step",
                (self._run_id,),
            ).fetchall()
            self._history_cache = [(r["step"], json.loads(r["state"])) for r in rows]
        return self._history_cache

    def observable(self, name: str) -> np.ndarray:
        """Per-step values of ``name`` (dotted paths reach into nested maps,
        e.g. "species.X"); steps where it was not emitted are skipped."""
        values = [_get_dotted(state, name) for _, state in self._history()]
        return np.array([v for v in values if v is not _MISSING], dtype=float)

    @property
    def time(self) -> np.ndarray:
        """The emitted steps, in order."""
        return np.array([step for step, _ in self._history()], dtype=float)

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
    def trajectory(self):
        """DataFrame of (step × observable → value); nested maps are
        flattened to dotted column names ("species.X"). Requires pandas."""
        try:
            import pandas as pd
        except ImportError as e:
            raise ImportError(
                "pandas is required for Run.trajectory; install via "
                "`pip install pandas`"
            ) from e
        hist = self._history()
        frame = pd.DataFrame([_flatten(state) for _, state in hist],
                             index=pd.Index([step for step, _ in hist], name="step"))
        frame.columns.name = "observable"
        return frame


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


def _all_run_ids(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [r["run_id"] for r in conn.execute(
            "SELECT run_id FROM runs_meta ORDER BY started_at ASC, run_id ASC"
        ).fetchall()]
    finally:
        conn.close()


def pytest_generate_tests(metafunc):
    """Parametrize the `runs` fixture with one Run per row in runs.db
    when the study's data_source is `all_runs`."""
    if "runs" not in metafunc.fixturenames:
        return
    test_file = Path(str(metafunc.module.__file__))
    try:
        study_dir = _find_study_dir(test_file)
    except RunNotAvailableError:
        return
    spec = yaml.safe_load((study_dir / "study.yaml").read_text()) or {}
    if (spec.get("tests") or {}).get("data_source") != "all_runs":
        return
    db = study_dir / "runs.db"
    if not db.exists():
        return
    ids = _all_run_ids(db)
    metafunc.parametrize("runs", ids, ids=ids, indirect=True)


@pytest.fixture
def runs(request) -> Run:
    """Parametrized fixture: one Run per row in the study's runs.db.

    Activated when study.yaml has tests.data_source: all_runs. The
    `pytest_generate_tests` hook supplies the run_id parameter; this fixture
    converts it to a Run.
    """
    test_file = Path(str(request.fspath))
    study_dir = _find_study_dir(test_file)
    db = study_dir / "runs.db"
    return Run(db, run_id=request.param)


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
                "SELECT run_id FROM runs_meta ORDER BY started_at ASC, run_id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                raise RunNotAvailableError(f"runs.db at {db} contains no runs")
            return Run(db, run_id=row["run_id"])
        finally:
            conn.close()
    return Run(db)
