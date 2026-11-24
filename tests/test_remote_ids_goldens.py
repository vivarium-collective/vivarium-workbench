"""Goldens for the remote-run id handling step P3 I1 touches, recorded on the base before the change.

The composite-run routes the loom (and viva-superpowers' skills) poll with the synthetic ``remote-sim-<id>`` run id
(status, stop, trajectory), and the refusal bodies of the SMS-only operations (re-run, land-artifacts) for a run
whose backend does not offer them. Whole bodies are compared, so any drift shows.

Needs the SMS plugin (its fixtures fake the plugin's client); the pure-core P3 I1 pins live in
``test_remote_run_prefix.py``.
"""
from __future__ import annotations

import pytest

import vivarium_workbench_sms  # noqa: F401 -- needs the plugin (core-only mode ignores this module)
from test_backends_existing import HERE, _record, asked, ws  # noqa: F401 -- fixtures, found by name
from vivarium_workbench.lib import composite_run_views as crv
from vivarium_workbench.lib import composite_stop_views as csv_
from vivarium_workbench.lib import remote_run_views as rrv

pytestmark = pytest.mark.plugin  # needs the SMS plugin (docs/backend-adapters.md step P3, K1)

# The refusal literal and the stop note (declared wire changes in I1 are re-pointed here, and only here).
# DECLARED (P3 I1): "not-sms" -> "not-offered"; the stop note was "Cloud runs continue on GovCloud — track it in the
# Runs tab." (neutral text, Jim's decision B).
NOT_OFFERED = "not-offered"
STOP_NOTE = "Remote runs continue on their backend — track it in the Runs tab."


# --- /api/composite-run/remote-sim-<id>/status -----------------------------------------------------------------

def test_status_of_an_sms_integer_suffix(ws, asked):
    body, status = crv.build_composite_run_status(ws, "remote-sim-9")
    assert (status, body) == (200, {"run_id": "remote-sim-9", "status": "running", "progress_step": None,
                                    "n_steps": None, "heartbeat_at": None, "remote": True, "simulation_id": 9,
                                    "raw_status": "running"})
    assert type(body["simulation_id"]) is int
    assert asked == [("sms", "simulation_status")]


@pytest.mark.parametrize("given", ["remote-sim-0012", "remote-sim-+12"])
def test_status_of_an_integer_spelling_is_the_sms_integer(ws, asked, given):
    body, status = crv.build_composite_run_status(ws, given)
    assert (status, body["simulation_id"], body["run_id"]) == (200, 12, given)
    assert asked == [("sms", "simulation_status")]


def test_status_of_a_string_suffix_is_routed_by_its_record(ws, asked):
    _record(ws, "simulation-V", "viva-core", HERE)
    body, status = crv.build_composite_run_status(ws, "remote-sim-simulation-V")
    assert (status, body) == (200, {"run_id": "remote-sim-simulation-V", "status": "running", "progress_step": None,
                                    "n_steps": None, "heartbeat_at": None, "remote": True,
                                    "simulation_id": "simulation-V", "raw_status": None})
    assert asked == [("viva", "status")]


def test_status_of_another_backends_all_digit_id(ws, asked):
    """A viva-core record whose id happens to be all digits: its backend's status body carries no id, so the
    response's ``simulation_id`` is the suffix. DECLARED (P3 I1): the suffix is passed on and echoed as the string
    ``"123"`` (the base ``int()``-ed it to ``123``); SMS's own answer is still its integer (above)."""
    _record(ws, "123", "viva-core", HERE)
    body, status = crv.build_composite_run_status(ws, "remote-sim-123")
    assert (status, body["simulation_id"]) == (200, "123")
    assert asked == [("viva", "status")]


@pytest.mark.parametrize("given", ["remote-sim-", "remote-sim-  "])
def test_status_without_an_id_is_404(ws, asked, given):
    assert crv.build_composite_run_status(ws, given) == ({"error": "run not found"}, 404)
    assert asked == []


@pytest.mark.parametrize("given", ["remote-sim-0", "remote-sim-00", "remote-sim-+0", "remote-sim--0",
                                   "remote-sim- 0 "])
def test_status_of_a_zero_suffix_is_the_sms_required_answer(ws, asked, given):
    """Every spelling of zero (the base ``int()``-ed it to 0) is no id: the SMS half's "required" answer."""
    assert crv.build_composite_run_status(ws, given) == (
        {"error": "simulator_id, simulation_id or analysis_id required"}, 400)
    assert asked == []


def test_status_of_a_negative_suffix_is_passed_on_as_given(ws, asked):
    """DECLARED (P3 I1): a negative integer spelling is no longer ``int()``-ed; it is passed on (and echoed) as the
    string ``"-12"`` (the base echoed ``-12``); it went to the same backend before. No backend issues negative ids."""
    body, status = crv.build_composite_run_status(ws, "remote-sim--12")
    assert (status, body["simulation_id"], body["run_id"]) == (200, "-12", "remote-sim--12")
    assert asked == [("viva", "status")]


def test_status_route_keeps_the_prefix_and_the_status_key(ws, asked, inproc_client):
    """viva-superpowers' skills poll this route with the run id and read ``status``."""
    r = inproc_client(ws).get("/api/composite-run/remote-sim-9/status")
    assert r.status_code == 200
    assert (r.json()["run_id"], r.json()["status"]) == ("remote-sim-9", "running")


# --- /api/composite-run/remote-sim-<id>/stop, and the trajectory ----------------------------------------------

@pytest.mark.parametrize("rid", ["remote-sim-9", "remote-sim-simulation-V"])
def test_stop_detaches(ws, asked, rid):
    assert csv_.stop_composite_run(ws, rid) == (
        {"run_id": rid, "outcome": "remote-uncancellable", "note": STOP_NOTE}, 200)
    assert asked == []


@pytest.mark.parametrize("rid", ["remote-sim-9", "remote-sim-simulation-V"])
def test_trajectory_is_empty(ws, asked, rid):
    assert crv.build_composite_run(ws, rid) == ({"run_id": rid, "trajectory": [], "remote": True}, 200)


# --- the refused re-run / land-artifacts bodies ------------------------------------------------------------------

def _refused(rid: str, error: str) -> dict:
    return {"error": error, "reason": NOT_OFFERED, "phase": NOT_OFFERED, "terminal": True, "reachable": False,
            "simulation_id": rid, "run": rid}


def test_a_refused_rerun_of_another_backends_run(ws, asked):
    _record(ws, "simulation-V", "viva-core", HERE)
    assert rrv.remote_run_analysis(ws, {"simulation_id": "simulation-V"}) == (
        _refused("simulation-V", "run simulation-V ran on viva-core, which does not offer analysis re-runs"), 409)
    assert asked == []


def test_a_refused_land_artifacts_of_another_backends_run(ws, asked):
    _record(ws, "simulation-V", "viva-core", HERE)
    assert rrv.remote_run_land_artifacts(ws, {"simulation_id": "simulation-V", "run_id": "r1"}) == (
        _refused("simulation-V",
                 "run simulation-V ran on viva-core, which does not offer analysis outputs (land, figures)"), 409)
    assert asked == []


@pytest.mark.parametrize("call", ["rerun", "land_artifacts"])
def test_a_refused_sms_record_whose_id_is_not_an_integer(ws, asked, call):
    _record(ws, "simulation-Z", "sms-legacy", HERE)
    if call == "rerun":
        got = rrv.remote_run_analysis(ws, {"simulation_id": "simulation-Z"})
    else:
        got = rrv.remote_run_land_artifacts(ws, {"simulation_id": "simulation-Z", "run_id": "r1"})
    assert got == (_refused("simulation-Z", "run simulation-Z is not an SMS simulation id; this operation exists "
                                            "only for SMS simulations"), 409)
    assert asked == []


def test_the_refusal_through_the_route(ws, asked, inproc_client):
    _record(ws, "simulation-V", "viva-core", HERE)
    r = inproc_client(ws).post("/api/remote-run-analysis", json={"simulation_id": "simulation-V"})
    assert (r.status_code, r.json()["reason"], r.json()["phase"]) == (409, NOT_OFFERED, NOT_OFFERED)


# --- P3 I1 plugin pin ------------------------------------------------------------------------------------------------

def test_the_plugins_cloud_dispatch_uses_the_core_prefix():
    from _plugin_paths import PLUGIN_DIR

    src = (PLUGIN_DIR / "cloud_dispatch.py").read_text()
    assert '"remote-sim-' not in src and "remote_run_id(sim_db_id)" in src
