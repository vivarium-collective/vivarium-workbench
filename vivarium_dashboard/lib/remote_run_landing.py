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
