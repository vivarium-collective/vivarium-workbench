"""Golden for ``/api/simulations`` taken on the base BEFORE step P3 F0 (declared backend features).

Providers on, a fake SMS listing, and a run log with every kind of remote row the index builds: an SMS run with its
record, an SMS run without one, a viva-core run, a compose-api run, a ``remote-pending-`` placeholder (#1108) and a
plain local run; the listing adds two rows. F0 adds one key, ``remote_origin.features``; with it stripped the payload
must be byte for byte what it was. (What the key holds is pinned by ``test_backend_features.py``.)
"""
from __future__ import annotations

import copy
import json

import pytest

from vivarium_workbench.lib import run_log
from vivarium_workbench.lib import simulations_index as si

pytestmark = pytest.mark.plugin  # needs the SMS plugin (docs/backend-adapters.md step P3, K1)


def _origin(sim_id, *, exp=None, dep="golden-dep"):
    return {"deployment": dep, "simulation_id": sim_id, "experiment_id": exp, "backend": "q", "s3_uri": None}


def _remote(kind, rid):
    return {"kind": kind, "id": str(rid), "base_url": "http://golden.test", "deployment": "golden-dep",
            "server_id": None}


#: The run log: (run_id, the event's fields).
EVENTS = [
    ("local-run", {"status": "completed", "started_at": 1000.0, "completed_at": 1001.0, "sim_name": "local"}),
    ("sms-recorded", {"status": "completed", "started_at": 2000.0, "completed_at": 2001.0,
                      "remote_origin": _origin(41, exp="exp-41"),
                      "params": {"source": "golden-dep", "simulation_id": 41,
                                 "remote": _remote("sms-legacy", 41)}}),
    ("sms-unrecorded", {"status": "running", "started_at": 3000.0, "remote_origin": _origin(42)}),
    ("viva-core-run", {"status": "completed", "started_at": 4000.0, "completed_at": 4001.0,
                       "remote_origin": _origin("simulation-ABC1234"),
                       "params": {"remote": _remote("viva-core", "simulation-ABC1234")}}),
    ("compose-run", {"status": "failed", "started_at": 5000.0, "completed_at": 5001.0,
                     "remote_origin": _origin("compose-4706"),
                     "params": {"remote": _remote("compose-api", "compose-4706")}}),
    ("remote-pending-77", {"status": "queued", "started_at": 6000.0,
                           "params": {"source": "golden-dep", "simulation_id": 77, "experiment_id": "exp-77"}}),
]

#: What the fake listing answers (the SMS listing's stale-while-revalidate read).
LISTED = [
    {"run_id": "exp-listed-int", "status": "completed", "started_at": 7000.0, "completed_at": 7001.0,
     "remote_origin": _origin(501, exp="exp-listed-int")},
    {"run_id": "exp-listed-str", "status": "completed", "started_at": 8000.0, "completed_at": 8001.0,
     "remote_origin": _origin("simulation-LISTED1", exp="exp-listed-str")},
]


@pytest.fixture
def ws(tmp_path, monkeypatch):
    from vivarium_workbench_sms import remote_simulations as rs  # providers on: the plugin's listing
    (tmp_path / "workspace.yaml").write_text("name: golden\n", encoding="utf-8")
    for rid, fields in EVENTS:
        run_log.append_run_event(tmp_path, {"run_id": rid, "event": "started", **fields})
    monkeypatch.setattr(rs, "list_remote_simulations_swr", lambda ws, *a, **k: copy.deepcopy(LISTED))
    monkeypatch.setattr(rs, "remote_state", lambda ws, *a, **k: {"state": "fresh", "as_of": 1.0, "error": None})
    si.clear_build_cache()
    yield tmp_path
    si.clear_build_cache()


def strip_features(body: dict) -> dict:
    """The payload without F0's additive key."""
    out = copy.deepcopy(body)
    for row in out.get("simulations") or []:
        ro = row.get("remote_origin")
        if isinstance(ro, dict):
            ro.pop("features", None)
    return out


#: The run ids in payload order, and each remote row's ``remote_origin`` (without ``features``).
GOLDEN_ORDER = ["remote-pending-77", "sms-unrecorded", "exp-listed-str", "exp-listed-int", "compose-run",
                "viva-core-run", "sms-recorded", "local-run"]
GOLDEN_ORIGINS = {
    "remote-pending-77": {"deployment": "golden-dep", "simulation_id": 77, "experiment_id": "exp-77",
                          "backend": None, "s3_uri": None},
    "sms-unrecorded": _origin(42),
    "exp-listed-str": _origin("simulation-LISTED1", exp="exp-listed-str"),
    "exp-listed-int": _origin(501, exp="exp-listed-int"),
    "compose-run": _origin("compose-4706"),
    "viva-core-run": _origin("simulation-ABC1234"),
    "sms-recorded": _origin(41, exp="exp-41"),
    "local-run": None,
}
#: sha256 of the whole stripped payload's canonical JSON (taken on the base; tmp paths and source_ref removed).
GOLDEN_SHA256 = "fd0cb54ba1e9c94dd4b8897a544b89b12348c5fe756c305e9085c0c112d82187"
#: The same for the library build publish.py bakes into ``simulations.json`` (raw rows, not SimRow-validated).
GOLDEN_BUILD_SHA256 = "15fbbfd7eb19306ae790c13576736b71ae2d154ec90e6f48282146bdf26303c2"


def _canonical(body: dict, ws) -> str:
    body = strip_features(body)
    for row in body["simulations"]:
        row.pop("source_ref", None)  # the inferred workspace source (git state of the tmp dir)
    return json.dumps(body, sort_keys=True).replace(str(ws), "<ws>")


def test_simulations_payload_matches_the_base_without_features(ws, inproc_client):
    import hashlib

    r = inproc_client(ws).get("/api/simulations")
    assert r.status_code == 200
    body = r.json()
    assert [s["run_id"] for s in body["simulations"]] == GOLDEN_ORDER
    assert {s["run_id"]: s["remote_origin"] for s in strip_features(body)["simulations"]} == GOLDEN_ORIGINS
    text = _canonical(body, ws)
    assert hashlib.sha256(text.encode()).hexdigest() == GOLDEN_SHA256, text


def test_library_build_matches_the_base_without_features(ws):
    import hashlib

    data = si.build_simulations_data(ws)
    assert [s["run_id"] for s in data["simulations"]] == GOLDEN_ORDER
    text = _canonical(data, ws)
    assert hashlib.sha256(text.encode()).hexdigest() == GOLDEN_BUILD_SHA256, text
