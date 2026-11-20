"""Which backend each remote-run view sends a request to, pinned exactly as it is.

docs/backend-adapters.md §4 step 1a moves the inline SMS code out of ``remote_run_views`` into
``lib/backends/sms_legacy.py`` and sends the views' calls through adapter objects. That move must not change
routing. The routing is not by run-id form alone: each view checks its own keys in its own order, strips some ids
and not others, and gates viva-core landing on the named backend. These tests were written and run against
``main`` before the move, and pin those rules -- quirks included -- so the move cannot regularise them by accident.
Step 1c-2 then replaced the per-view rules with one: the id (``run_id``, else ``simulation_id``; for land the
reverse), stripped, is looked up in the run's record (``backends.for_existing``), falling back to its form. The
tests it changed say "step 1c-2 (deliberate change)" and what the old behaviour was.

Observation points that work both before and after the move:

* compose-api / viva-core: recorders on ``remote_run_compose.X`` / ``remote_run_viva_v1.X`` (the adapters call
  through the module attribute at call time);
* the legacy SMS path: a recording fake ``sms_legacy.SmsApiClient`` (the SMS code reads it through ``sms_legacy``
  at call time).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vivarium_workbench.lib import remote_run_compose
from vivarium_workbench_sms import remote_pinned, remote_run_viva_v1
from vivarium_workbench.lib import remote_run_views as rrv
from vivarium_workbench_sms import sms_landing, sms_legacy
from vivarium_workbench.lib import remote_trace



# Step 1c-2: the views look the run up in the workspace's records; this empty workspace has none, so the id's form
# decides (the record-driven cases are in tests/test_backends_existing.py).
import tempfile as _tempfile  # noqa: E402
_WS = Path(_tempfile.mkdtemp(prefix="no-records-ws-"))

class _Calls(list):
    """``(backend, method, arg)`` in call order."""


@pytest.fixture
def calls(monkeypatch, tmp_path):
    rec = _Calls()

    def recorder(backend, name):
        def fn(*args):
            rec.append((backend, name, args[-1]))
            if name == "trace":
                return b"{}", 200, "t.json"
            return {"via": backend, "method": name}, 200
        return fn

    for name in ("land", "status", "progress", "cancel", "trace"):
        monkeypatch.setattr(remote_run_compose, name, recorder("compose", name))
    for name in ("land", "status", "progress", "cancel"):
        monkeypatch.setattr(remote_run_viva_v1, name, recorder("viva", name))

    class FakeClient:
        """Records every SMS client method called on it; answers with a plausible dict."""

        def __init__(self, *a, **k):
            pass

        def __getattr__(self, method):
            def fn(*args, **kwargs):
                rec.append(("sms", method, args[0] if args else None))
                if method == "download_data":
                    p = Path(args[1]) / "sim.tar.gz"
                    p.write_bytes(b"TAR")
                    return p
                if method == "simulation_chain_progress":
                    return {"status": "running", "terminal": False}
                if method == "cancel_simulation":
                    return {"cancelled": True}
                return {"status": "running", "database_id": 1}
            return fn

    monkeypatch.setattr(sms_legacy, "SmsApiClient", FakeClient)
    monkeypatch.setattr(sms_legacy, "_sms_api_base", lambda: "http://sms.local")
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: True)
    spec_file = tmp_path / "study.yaml"
    spec_file.write_text("baseline: [{composite: c}]\n")
    monkeypatch.setattr(rrv.study_spec, "study_spec_path", lambda ws, name: spec_file)
    monkeypatch.setattr(rrv.study_spec, "study_dir", lambda ws, name: tmp_path)
    monkeypatch.setattr(sms_legacy, "load_spec", lambda p: {"baseline": [{"composite": "c"}]})
    monkeypatch.setattr(sms_landing, "land_remote_run", lambda *a, **k: rec.append(("sms", "land_remote_run", None)) or "r1")
    return rec


def _first(calls):
    return calls[0][:2] if calls else None


# --- land ------------------------------------------------------------------------------------------------------

def test_land_compose_id_goes_to_compose_without_gate_or_auth(calls, monkeypatch, tmp_path):
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: False)
    assert rrv.remote_run_land(tmp_path, {"simulation_id": "compose-7"}) == ({"via": "compose", "method": "land"}, 200)
    assert calls == [("compose", "land", {"simulation_id": "compose-7"})]


def test_land_compose_id_in_run_id_only_is_compose(calls, tmp_path):
    # step 1c-2 (deliberate change): land reads simulation_id, then run_id; it was a 400
    rrv.remote_run_land(tmp_path, {"run_id": "compose-7"})
    assert calls == [("compose", "land", {"run_id": "compose-7", "simulation_id": "compose-7"})]


def test_land_viva_id_goes_to_viva_whatever_is_named(calls, monkeypatch, tmp_path):
    # step 1c-2 (deliberate change): no named-backend gate in the view (viva-core land refuses 409 no-backend
    # itself when nothing is named); it was 401 / a ValueError (500) through the SMS path
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: False)
    rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": "simulation-D55"})
    assert _first(calls) == ("viva", "land")


def test_land_integer_id_goes_to_legacy(calls, tmp_path):
    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 12})
    assert (body, status) == ({"run_id": "r1"}, 200)
    seen = [c[1] for c in calls]
    assert "download_data" in seen and seen[-1] == "land_remote_run"
    assert all(c[0] == "sms" for c in calls)


# --- status ----------------------------------------------------------------------------------------------------

def test_status_run_id_beats_simulation_id(calls):
    # step 1c-2 (deliberate change): one id rule, run_id first; a compose simulation_id used to win
    rrv.remote_run_status({"run_id": "simulation-X", "simulation_id": "compose-3"}, ws_root=_WS)
    assert calls == [("viva", "status", "simulation-X")]


@pytest.mark.parametrize("run_id, backend, sent", [
    ("123", "sms", 123), (123, "sms", 123), (" x ", "viva", "x"),
])
def test_status_run_id_goes_to_the_backend_its_form_names_stripped(calls, run_id, backend, sent):
    # step 1c-2 (deliberate change): a digit run_id is an SMS simulation (was viva-core), ids are stripped
    rrv.remote_run_status({"run_id": run_id}, ws_root=_WS)
    if backend == "sms":
        assert [c[1] for c in calls] == ["simulation_status"] and calls[0][2] == sent
    else:
        assert calls == [("viva", "status", sent)]


def test_status_blank_run_id_is_no_id(calls):
    # step 1c-2 (deliberate change): a whitespace run_id is ignored (was sent to viva-core as " ")
    assert rrv.remote_run_status({"run_id": " "}, ws_root=_WS)[1] == 400 and calls == []


def test_status_compose_id_is_stripped(calls):
    rrv.remote_run_status({"run_id": " compose-3 "}, ws_root=_WS)
    assert calls == [("compose", "status", "compose-3")]


def test_status_viva_simulation_id_goes_to_viva(calls):
    rrv.remote_run_status({"simulation_id": "simulation-X"}, ws_root=_WS)
    assert calls == [("viva", "status", "simulation-X")]


@pytest.mark.parametrize("params, method", [
    ({"simulation_id": 5, "analysis_id": 7}, "analysis_status"),
    ({"simulation_id": 5, "simulator_id": 9}, "simulation_status"),
    ({"simulator_id": 9}, "simulator_status"),
])
def test_status_legacy_precedence(calls, params, method):
    assert rrv.remote_run_status(params, ws_root=_WS)[1] == 200
    assert [c[1] for c in calls] == [method]


def test_status_nothing_is_400(calls):
    assert rrv.remote_run_status({}, ws_root=_WS)[1] == 400


def test_status_legacy_client_error_is_502_shape(calls, monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            pass

        def simulation_status(self, sid):
            raise sms_legacy.SmsApiError("down", status=503)

    monkeypatch.setattr(sms_legacy, "SmsApiClient", Boom)
    body, status = rrv.remote_run_status({"simulation_id": 5}, ws_root=_WS)
    assert status == 502 and body["phase"] == "unreachable" and body["reachable"] is False


# --- chain progress --------------------------------------------------------------------------------------------

def test_progress_run_id_goes_to_viva(calls):
    rrv.remote_run_chain_progress({"run_id": "simulation-X"}, ws_root=_WS)
    assert calls == [("viva", "progress", "simulation-X")]


def test_progress_compose_simulation_id_goes_to_compose(calls):
    rrv.remote_run_chain_progress({"simulation_id": "compose-4"}, ws_root=_WS)
    assert calls == [("compose", "progress", "compose-4")]


def test_progress_viva_form_simulation_id_goes_to_viva(calls):
    # step 1c-2 (deliberate change): it was a ValueError (500) through the SMS path
    rrv.remote_run_chain_progress({"simulation_id": "simulation-X"}, ws_root=_WS)
    assert calls == [("viva", "progress", "simulation-X")]


def test_progress_integer_goes_to_legacy(calls):
    body, status = rrv.remote_run_chain_progress({"simulation_id": 8}, ws_root=_WS)
    assert status == 200 and body["kind"] == "chain_progress"
    assert [c[1] for c in calls] == ["simulation_chain_progress"]


# --- cancel ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("body", [{"run_id": "compose-2"}, {"simulation_id": "compose-2"}])
def test_cancel_compose_either_key(calls, body):
    rrv.remote_run_cancel(body, ws_root=_WS)
    assert calls == [("compose", "cancel", "compose-2")]


def test_cancel_non_digit_simulation_id_goes_to_viva(calls):
    rrv.remote_run_cancel({"simulation_id": "simulation-X"}, ws_root=_WS)
    assert calls == [("viva", "cancel", "simulation-X")]


def test_cancel_run_id_beats_integer_simulation_id(calls):
    rrv.remote_run_cancel({"run_id": "simulation-X", "simulation_id": 5}, ws_root=_WS)
    assert calls == [("viva", "cancel", "simulation-X")]


def test_cancel_integer_goes_to_legacy(calls):
    body, status = rrv.remote_run_cancel({"simulation_id": 5}, ws_root=_WS)
    assert status == 200 and body == {"kind": "cancel", "simulation_id": 5, "cancelled": True}


@pytest.mark.parametrize("msg, code", [("x -> 404", 404), ("x -> 500", 502)])
def test_cancel_legacy_errors(calls, monkeypatch, msg, code):
    class Boom:
        def __init__(self, *a, **k):
            pass

        def cancel_simulation(self, sid):
            raise sms_legacy.SmsApiError(msg)

    monkeypatch.setattr(sms_legacy, "SmsApiClient", Boom)
    assert rrv.remote_run_cancel({"simulation_id": 5}, ws_root=_WS)[1] == code


# --- trace -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kw", [{"simulation_id": "compose-6"}, {"composite_run_id": "compose-6"}])
def test_trace_compose_id_in_sim_or_run_key(calls, kw):
    assert remote_trace.fetch_trace(object(), **kw, ws_root=_WS)[1] == 200
    assert calls == [("compose", "trace", "compose-6")]


def test_trace_compose_id_in_compose_id_key_is_400(calls):
    assert remote_trace.fetch_trace(object(), compose_id="compose-6", ws_root=_WS)[1] == 400
    assert calls == []


def test_trace_compose_sim_plus_compose_id_is_compose_not_400(calls):
    assert remote_trace.fetch_trace(object(), simulation_id="compose-6", compose_id=5, ws_root=_WS)[1] == 200


def test_trace_viva_sim_plus_compose_run_goes_to_compose(calls):
    remote_trace.fetch_trace(object(), simulation_id="simulation-X", composite_run_id="compose-6", ws_root=_WS)
    assert calls == [("compose", "trace", "compose-6")]


# --- whitespace and key order on every entry point --------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda: rrv.remote_run_status({"simulation_id": " compose-9 "}, ws_root=_WS),
    lambda: rrv.remote_run_chain_progress({"run_id": " compose-9 "}, ws_root=_WS),
    lambda: rrv.remote_run_chain_progress({"simulation_id": " compose-9 "}, ws_root=_WS),
    lambda: rrv.remote_run_cancel({"run_id": " compose-9 "}, ws_root=_WS),
    lambda: rrv.remote_run_cancel({"simulation_id": " compose-9 "}, ws_root=_WS),
    lambda: remote_trace.fetch_trace(object(), simulation_id=" compose-9 ", ws_root=_WS),
    lambda: remote_trace.fetch_trace(object(), composite_run_id=" compose-9 ", ws_root=_WS),
])
def test_compose_ids_are_stripped_everywhere(calls, call):
    call()
    assert [(c[0], c[2]) for c in calls] == [("compose", "compose-9")]


def test_progress_compose_run_id_goes_to_compose(calls):
    rrv.remote_run_chain_progress({"run_id": "compose-4"}, ws_root=_WS)
    assert calls == [("compose", "progress", "compose-4")]


@pytest.mark.parametrize("call, method", [
    (lambda: rrv.remote_run_chain_progress({"run_id": " x "}, ws_root=_WS), "progress"),
    (lambda: rrv.remote_run_cancel({"run_id": " x "}, ws_root=_WS), "cancel"),
    (lambda: rrv.remote_run_cancel({"simulation_id": " x "}, ws_root=_WS), "cancel"),
    (lambda: rrv.remote_run_status({"simulation_id": " x "}, ws_root=_WS), "status"),
])
def test_viva_ids_are_stripped(calls, call, method):
    # step 1c-2 (deliberate change): one id rule strips every id (viva-core ids were passed unstripped)
    call()
    assert calls == [("viva", method, "x")]


# --- for_run / owns (new in step 1a; not used by production routing yet) ---------------------------------------

@pytest.mark.parametrize("run_id, owner", [
    ("compose-1", "compose-api"), ("compose-1 ", "compose-api"), (" compose-1", "compose-api"),
    ("compose-x", "viva-core"), ("compose-", "viva-core"), ("COMPOSE-1", "viva-core"),
    ("simulation-D55", "viva-core"),
    ("123", "sms-legacy"), (123, "sms-legacy"), (" 123 ", "sms-legacy"), (0, None),
    ("", None), (" ", None), (None, None),
])
def test_for_run_owner_is_unique(run_id, owner):
    from vivarium_workbench.lib import backends

    owners = [b.name for b in (backends.COMPOSE, backends.VIVA_CORE, backends.SMS_LEGACY) if b.runs.owns(run_id)]
    assert len(owners) <= 1, owners
    found = backends.for_run(run_id)
    assert (found.name if found else None) == owner


# --- submit (step 1b) --------------------------------------------------------------------------------------------

@pytest.fixture
def submit_world(monkeypatch, tmp_path):
    """Its own setup: a named backend comes from a stubbed profile, not from the ``calls`` fixture's patches."""
    from vivarium_workbench.lib import server_capabilities as sc
    from vivarium_workbench_sms.sms_api_client import BACKEND_BASE_ENV_VARS

    for v in BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: False)
    sc._PROFILE_CACHE.clear()
    rec: list = []
    for mod, tag in ((remote_run_compose, "compose"), (remote_run_viva_v1, "viva")):
        monkeypatch.setattr(mod, "submit", lambda ws, body, _t=tag: rec.append((_t, "submit")) or ({"via": _t}, 202))

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def run_simulation(self, **kw):
            rec.append(("sms", "run_simulation"))
            return {"database_id": 77}

    monkeypatch.setattr(sms_legacy, "SmsApiClient", FakeClient)
    monkeypatch.setattr(sms_legacy, "_sms_api_base", lambda: "http://sms.local")
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: True)
    spec_file = tmp_path / "study.yaml"
    spec_file.write_text("baseline: [{composite: c}]\n")
    monkeypatch.setattr(rrv.study_spec, "study_spec_path", lambda ws, name: spec_file)
    monkeypatch.setattr(rrv.study_spec, "study_dir", lambda ws, name: tmp_path)
    monkeypatch.setattr(rrv.study_spec, "collect_study_observables", lambda spec: [])
    monkeypatch.setattr(sms_legacy, "load_spec", lambda p: {"baseline": [{"composite": "c"}]})

    def name(kind):
        profiles = {
            "compose": {"reachable": True, "dispatch": sc.DISPATCH_COMPOSE, "error": None},
            "viva": {"reachable": True, "dispatch": sc.DISPATCH_DOCUMENT, "error": None},
            "legacy": {"reachable": True, "dispatch": sc.DISPATCH_LEGACY, "error": None},
            "unreachable": {"reachable": False, "dispatch": sc.DISPATCH_LEGACY, "error": "down"},
        }
        monkeypatch.setenv(BACKEND_BASE_ENV_VARS[0], f"http://{kind}.test")
        monkeypatch.setattr(sc, "backend_profile", lambda client: {"version": "t", "capabilities": [],
                                                                   "services": {}, **profiles[kind]})

    yield rec, name, tmp_path
    sc._PROFILE_CACHE.clear()


SMS_BODY = {"study": "s", "simulator_id": 5, "num_generations": 1, "num_seeds": 1}


@pytest.mark.parametrize("kind", ["compose", "viva"])
def test_submit_without_simulator_goes_to_the_named_backend(submit_world, kind):
    rec, name, ws = submit_world
    name(kind)
    assert rrv.remote_run_submit(ws, {"study": "s"}) == ({"via": kind}, 202)
    assert rec == [(kind, "submit")]


@pytest.mark.parametrize("kind", [None, "legacy"])
def test_submit_without_simulator_and_no_document_backend_is_sms_400(submit_world, kind):
    rec, name, ws = submit_world
    if kind:
        name(kind)
    body, status = rrv.remote_run_submit(ws, {"study": "s"})
    assert status == 400 and "simulator_id" in body["error"] and rec == []


def test_submit_without_simulator_on_an_unreachable_named_backend_raises_502(submit_world):
    from vivarium_workbench.lib.errors import APIError

    rec, name, ws = submit_world
    name("unreachable")
    with pytest.raises(APIError) as e:
        rrv.remote_run_submit(ws, {"study": "s"})
    assert e.value.status_code == 502 and rec == []


@pytest.mark.parametrize("kind", [None, "compose", "viva", "unreachable"])
def test_submit_with_simulator_is_sms_whatever_is_named(submit_world, kind):
    rec, name, ws = submit_world
    if kind:
        name(kind)
    body, status = rrv.remote_run_submit(ws, dict(SMS_BODY))
    assert (status, body["simulation_id"]) == (202, 77)
    assert rec == [("sms", "run_simulation")]


def test_submit_pinned_without_simulator_is_sms_even_with_compose_named(submit_world, monkeypatch):
    rec, name, ws = submit_world
    name("compose")
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: True)
    body, status = rrv.remote_run_submit(ws, {"study": "s"})
    assert status == 400 and rec == []
