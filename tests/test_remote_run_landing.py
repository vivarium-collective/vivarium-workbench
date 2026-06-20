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
