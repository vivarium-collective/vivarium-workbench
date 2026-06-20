import json
import sqlite3
from pathlib import Path

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
        "SELECT simulation_id, metadata FROM simulations WHERE name=?", (run_id,)
    ).fetchone()
    assert sim is not None and sim[0] == run_id
    assert json.loads(sim[1])["simulation_id"] == 49  # provenance in simulations.metadata too

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
