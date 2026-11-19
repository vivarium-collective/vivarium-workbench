"""Which backend each remote-run view sends a request to, pinned exactly as it is.

docs/backend-adapters.md §4 step 1a moves the inline SMS code out of ``remote_run_views`` into
``lib/backends/sms_legacy.py`` and sends the views' calls through adapter objects. That move must not change
routing. The routing is not by run-id form alone: each view checks its own keys in its own order, strips some ids
and not others, and gates viva-core landing on the named backend. These tests were written and run against
``main`` before the move, and pin those rules -- quirks included -- so the move cannot regularise them by accident.
Making the routing uniform (``backends.for_run``) is a later, deliberate change that edits these tests on purpose.

Observation points that work both before and after the move:

* compose-api / viva-core: recorders on ``remote_run_compose.X`` / ``remote_run_viva_v1.X`` (the adapters call
  through the module attribute at call time);
* the legacy SMS path: a recording fake ``remote_run_views.SmsApiClient`` (the moved code reads it through
  ``remote_run_views`` at call time).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vivarium_workbench.lib import remote_pinned, remote_run_compose, remote_run_viva_v1
from vivarium_workbench.lib import remote_run_views as rrv
from vivarium_workbench.lib import remote_trace


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

    monkeypatch.setattr(rrv, "SmsApiClient", FakeClient)
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    monkeypatch.setattr(rrv, "_run_auth_ok", lambda: True)
    monkeypatch.setattr(remote_pinned, "uses_viva_v1_dispatch", lambda ws: False)
    spec_file = tmp_path / "study.yaml"
    spec_file.write_text("baseline: [{composite: c}]\n")
    monkeypatch.setattr(rrv.study_spec, "study_spec_path", lambda ws, name: spec_file)
    monkeypatch.setattr(rrv.study_spec, "study_dir", lambda ws, name: tmp_path)
    monkeypatch.setattr(rrv, "load_spec", lambda p: {"baseline": [{"composite": "c"}]})
    monkeypatch.setattr(rrv, "land_remote_run", lambda *a, **k: rec.append(("sms", "land_remote_run", None)) or "r1")
    return rec


def _first(calls):
    return calls[0][:2] if calls else None


# --- land ------------------------------------------------------------------------------------------------------

def test_land_compose_id_goes_to_compose_without_gate_or_auth(calls, monkeypatch, tmp_path):
    monkeypatch.setattr(rrv, "_run_auth_ok", lambda: False)
    assert rrv.remote_run_land(tmp_path, {"simulation_id": "compose-7"}) == ({"via": "compose", "method": "land"}, 200)
    assert calls == [("compose", "land", {"simulation_id": "compose-7"})]


def test_land_compose_id_in_run_id_only_is_not_compose(calls, tmp_path):
    body, status = rrv.remote_run_land(tmp_path, {"run_id": "compose-7"})
    assert status == 400 and "simulation_id" in body["error"]
    assert calls == []


def test_land_viva_id_with_gate_goes_to_viva(calls, monkeypatch, tmp_path):
    monkeypatch.setattr(remote_pinned, "uses_viva_v1_dispatch", lambda ws: True)
    rrv.remote_run_land(tmp_path, {"simulation_id": "simulation-D55"})
    assert _first(calls) == ("viva", "land")


def test_land_viva_id_without_gate_unauthenticated_is_401(calls, monkeypatch, tmp_path):
    monkeypatch.setattr(rrv, "_run_auth_ok", lambda: False)
    assert rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": "simulation-D55"})[1] == 401
    assert calls == []


def test_land_viva_id_without_gate_falls_into_legacy_int(calls, tmp_path):
    with pytest.raises(ValueError):
        rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": "simulation-D55"})


def test_land_integer_id_goes_to_legacy(calls, tmp_path):
    body, status = rrv.remote_run_land(tmp_path, {"study": "s", "simulation_id": 12})
    assert (body, status) == ({"run_id": "r1"}, 200)
    seen = [c[1] for c in calls]
    assert "download_data" in seen and seen[-1] == "land_remote_run"
    assert all(c[0] == "sms" for c in calls)


# --- status ----------------------------------------------------------------------------------------------------

def test_status_compose_simulation_id_beats_non_compose_run_id(calls):
    rrv.remote_run_status({"run_id": "simulation-X", "simulation_id": "compose-3"})
    assert calls == [("compose", "status", "compose-3")]


@pytest.mark.parametrize("run_id, sent", [("123", "123"), (123, "123"), (" ", " "), (" x ", " x ")])
def test_status_run_id_goes_to_viva_unstripped_even_if_digits(calls, run_id, sent):
    rrv.remote_run_status({"run_id": run_id})
    assert calls == [("viva", "status", sent)]


def test_status_compose_id_is_stripped(calls):
    rrv.remote_run_status({"run_id": " compose-3 "})
    assert calls == [("compose", "status", "compose-3")]


def test_status_viva_simulation_id_goes_to_viva(calls):
    rrv.remote_run_status({"simulation_id": "simulation-X"})
    assert calls == [("viva", "status", "simulation-X")]


@pytest.mark.parametrize("params, method", [
    ({"simulation_id": 5, "analysis_id": 7}, "analysis_status"),
    ({"simulation_id": 5, "simulator_id": 9}, "simulation_status"),
    ({"simulator_id": 9}, "simulator_status"),
])
def test_status_legacy_precedence(calls, params, method):
    assert rrv.remote_run_status(params)[1] == 200
    assert [c[1] for c in calls] == [method]


def test_status_nothing_is_400(calls):
    assert rrv.remote_run_status({})[1] == 400


def test_status_legacy_client_error_is_502_shape(calls, monkeypatch):
    class Boom:
        def __init__(self, *a, **k):
            pass

        def simulation_status(self, sid):
            raise rrv.SmsApiError("down", status=503)

    monkeypatch.setattr(rrv, "SmsApiClient", Boom)
    body, status = rrv.remote_run_status({"simulation_id": 5})
    assert status == 502 and body["phase"] == "unreachable" and body["reachable"] is False


# --- chain progress --------------------------------------------------------------------------------------------

def test_progress_run_id_goes_to_viva(calls):
    rrv.remote_run_chain_progress({"run_id": "simulation-X"})
    assert calls == [("viva", "progress", "simulation-X")]


def test_progress_compose_simulation_id_goes_to_compose(calls):
    rrv.remote_run_chain_progress({"simulation_id": "compose-4"})
    assert calls == [("compose", "progress", "compose-4")]


def test_progress_viva_form_simulation_id_falls_into_legacy_int(calls):
    with pytest.raises(ValueError):
        rrv.remote_run_chain_progress({"simulation_id": "simulation-X"})


def test_progress_integer_goes_to_legacy(calls):
    body, status = rrv.remote_run_chain_progress({"simulation_id": 8})
    assert status == 200 and body["kind"] == "chain_progress"
    assert [c[1] for c in calls] == ["simulation_chain_progress"]


# --- cancel ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("body", [{"run_id": "compose-2"}, {"simulation_id": "compose-2"}])
def test_cancel_compose_either_key(calls, body):
    rrv.remote_run_cancel(body)
    assert calls == [("compose", "cancel", "compose-2")]


def test_cancel_non_digit_simulation_id_goes_to_viva(calls):
    rrv.remote_run_cancel({"simulation_id": "simulation-X"})
    assert calls == [("viva", "cancel", "simulation-X")]


def test_cancel_run_id_beats_integer_simulation_id(calls):
    rrv.remote_run_cancel({"run_id": "simulation-X", "simulation_id": 5})
    assert calls == [("viva", "cancel", "simulation-X")]


def test_cancel_integer_goes_to_legacy(calls):
    body, status = rrv.remote_run_cancel({"simulation_id": 5})
    assert status == 200 and body == {"kind": "cancel", "simulation_id": 5, "cancelled": True}


@pytest.mark.parametrize("msg, code", [("x -> 404", 404), ("x -> 500", 502)])
def test_cancel_legacy_errors(calls, monkeypatch, msg, code):
    class Boom:
        def __init__(self, *a, **k):
            pass

        def cancel_simulation(self, sid):
            raise rrv.SmsApiError(msg)

    monkeypatch.setattr(rrv, "SmsApiClient", Boom)
    assert rrv.remote_run_cancel({"simulation_id": 5})[1] == code


# --- trace -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kw", [{"simulation_id": "compose-6"}, {"composite_run_id": "compose-6"}])
def test_trace_compose_id_in_sim_or_run_key(calls, kw):
    assert remote_trace.fetch_trace(object(), **kw)[1] == 200
    assert calls == [("compose", "trace", "compose-6")]


def test_trace_compose_id_in_compose_id_key_is_400(calls):
    assert remote_trace.fetch_trace(object(), compose_id="compose-6")[1] == 400
    assert calls == []


def test_trace_compose_sim_plus_compose_id_is_compose_not_400(calls):
    assert remote_trace.fetch_trace(object(), simulation_id="compose-6", compose_id=5)[1] == 200


def test_trace_viva_sim_plus_compose_run_goes_to_compose(calls):
    remote_trace.fetch_trace(object(), simulation_id="simulation-X", composite_run_id="compose-6")
    assert calls == [("compose", "trace", "compose-6")]


# --- whitespace and key order on every entry point --------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda: rrv.remote_run_status({"simulation_id": " compose-9 "}),
    lambda: rrv.remote_run_chain_progress({"run_id": " compose-9 "}),
    lambda: rrv.remote_run_chain_progress({"simulation_id": " compose-9 "}),
    lambda: rrv.remote_run_cancel({"run_id": " compose-9 "}),
    lambda: rrv.remote_run_cancel({"simulation_id": " compose-9 "}),
    lambda: remote_trace.fetch_trace(object(), simulation_id=" compose-9 "),
    lambda: remote_trace.fetch_trace(object(), composite_run_id=" compose-9 "),
])
def test_compose_ids_are_stripped_everywhere(calls, call):
    call()
    assert [(c[0], c[2]) for c in calls] == [("compose", "compose-9")]


def test_progress_compose_run_id_goes_to_compose(calls):
    rrv.remote_run_chain_progress({"run_id": "compose-4"})
    assert calls == [("compose", "progress", "compose-4")]


@pytest.mark.parametrize("call, method", [
    (lambda: rrv.remote_run_chain_progress({"run_id": " x "}), "progress"),
    (lambda: rrv.remote_run_cancel({"run_id": " x "}), "cancel"),
    (lambda: rrv.remote_run_cancel({"simulation_id": " x "}), "cancel"),
    (lambda: rrv.remote_run_status({"simulation_id": " x "}), "status"),
])
def test_viva_ids_are_not_stripped(calls, call, method):
    call()
    assert calls == [("viva", method, " x ")]


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
