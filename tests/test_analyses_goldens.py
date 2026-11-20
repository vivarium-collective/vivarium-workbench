"""Route-level goldens for the analysis-output and re-run routes (docs/backend-adapters.md §2.1, step P2 "Analyses and
figures"), providers on.

Recorded on the base before the routes went behind the ``artifacts`` / ``analyses`` facets: every branch of
``/api/remote-run-land-artifacts``, ``/api/remote-run-analysis``, ``/api/remote-analysis-figures``,
``/api/remote-analysis-figure`` and ``/api/study-remote-figures`` with its exact status and body. A row marked
DECLARED is one of the PR's declared changes; every other row is byte-for-byte the base's answer.
"""
from __future__ import annotations

import pytest

from test_backends_existing import ELSEWHERE, HERE, _record, asked, ws  # noqa: F401 -- fixtures, found by name
from vivarium_workbench_sms import remote_analysis_figures as raf
from vivarium_workbench_sms import sms_landing, sms_legacy
from vivarium_workbench_sms.sms_api_client import SmsApiError

LAND = "/api/remote-run-land-artifacts"
RERUN = "/api/remote-run-analysis"


@pytest.fixture
def landed(monkeypatch):
    seen: list = []
    monkeypatch.setattr(sms_landing, "land_remote_simulation_artifacts",
                        lambda ws_root, sim, run_id, client: seen.append((sim, run_id))
                        or {"run_id": run_id, "ptools": 2, "analyses": True})
    return seen


@pytest.fixture
def records(ws):
    _record(ws, "7", "viva-core", HERE)
    _record(ws, "8", "sms-legacy", ELSEWHERE, dep="prod-elsewhere")
    _record(ws, "simulation-Z", "sms-legacy", HERE)
    return ws


def _other_backend(rid):
    return {"error": f"this run was dispatched to prod-elsewhere ({ELSEWHERE}); this workbench is configured for "
                     f"{HERE} (a different URL)",
            "reason": "other-backend", "phase": "other-backend", "terminal": True, "reachable": False,
            "simulation_id": rid, "run": rid, "dispatched_to": "prod-elsewhere"}


def _not_sms(rid, error):
    return {"error": error, "reason": "not-sms", "phase": "not-sms", "terminal": True, "reachable": False,
            "simulation_id": rid, "run": rid}


_NOT_SMS_ID = "run simulation-Z is not an SMS simulation id; this operation exists only for SMS simulations"


def _not_offered(rid, backend, what):
    return f"run {rid} ran on {backend}; this operation exists only for SMS simulations"


# --- land-artifacts ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("payload, expected", [
    ({}, (401, {"error": "not authenticated"})),
    ({"simulation_id": 12, "run_id": "r"}, (401, {"error": "not authenticated"})),
    ({"simulation_id": "7", "run_id": "r"}, (401, {"error": "not authenticated"})),  # a viva-core record
])
def test_land_artifacts_unauthenticated(records, asked, landed, inproc_client, monkeypatch, payload, expected):
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: False)
    r = inproc_client(records).post(LAND, json=payload)
    assert (r.status_code, r.json()) == expected
    assert asked == [] and landed == []


@pytest.mark.parametrize("payload, expected", [
    ({"run_id": "r"}, (400, {"error": "simulation_id and run_id are required"})),
    ({"simulation_id": 12}, (400, {"error": "simulation_id and run_id are required"})),
    ({"simulation_id": 12, "run_id": "  "}, (400, {"error": "simulation_id and run_id are required"})),
    ({"simulation_id": 0, "run_id": "r"}, (400, {"error": "simulation_id and run_id are required"})),
    ({"simulation_id": "0", "run_id": "r"}, (400, {"error": "simulation_id is required"})),
    ({"simulation_id": "7", "run_id": "r"},
     (409, _not_sms("7", _not_offered("7", "viva-core", "analysis outputs (land, figures)")))),
    ({"simulation_id": "compose-7", "run_id": "r"},
     (409, _not_sms("compose-7", _not_offered("compose-7", "compose-api", "analysis outputs (land, figures)")))),
    ({"simulation_id": 8, "run_id": "r"}, (409, _other_backend("8"))),
    ({"simulation_id": "simulation-Z", "run_id": "r"}, (409, _not_sms("simulation-Z", _NOT_SMS_ID))),
])
def test_land_artifacts_refusals(records, asked, landed, inproc_client, payload, expected):
    r = inproc_client(records).post(LAND, json=payload)
    assert (r.status_code, r.json()) == expected
    assert asked == [] and landed == []


@pytest.mark.parametrize("given", [12, "12", " 12 "])
def test_land_artifacts_lands_an_sms_run(records, asked, landed, inproc_client, given):
    r = inproc_client(records).post(LAND, json={"simulation_id": given, "run_id": " r1 "})
    assert (r.status_code, r.json()) == (200, {"run_id": "r1", "ptools": 2, "analyses": True})
    assert landed == [(12, "r1")]


@pytest.mark.parametrize("run_id", ["../x", "a/b", ".."])
def test_land_artifacts_run_id_is_a_path(records, asked, landed, inproc_client, run_id):
    """The app-wide identifier guard (``_plain_identifiers``) refuses it before the route runs."""
    r = inproc_client(records).post(LAND, json={"simulation_id": 12, "run_id": run_id})
    assert (r.status_code, r.json()) == (400, {"error": "invalid run_id: it must be a plain name, not a path"})
    assert landed == [] and asked == []


@pytest.mark.parametrize("exc, expected", [
    (SmsApiError("GET /data -> 502", status=502), (502, {"error": "land failed: sms-api unavailable: GET /data -> 502"})),
    (OSError("disk full"), (500, {"error": "land failed: OSError: disk full"})),
])
def test_land_artifacts_failures(records, asked, inproc_client, monkeypatch, exc, expected):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(sms_landing, "land_remote_simulation_artifacts", boom)
    r = inproc_client(records).post(LAND, json={"simulation_id": 12, "run_id": "r1"})
    assert (r.status_code, r.json()) == expected


# --- re-run ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [{}, {"simulation_id": 12}, {"simulation_id": "7"}])
def test_rerun_unauthenticated(records, asked, inproc_client, monkeypatch, payload):
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: False)
    r = inproc_client(records).post(RERUN, json=payload)
    assert (r.status_code, r.json()) == (401, {"error": "not authenticated"})
    assert asked == []


@pytest.mark.parametrize("payload, expected", [
    ({}, (400, {"error": "simulation_id is required"})),
    ({"simulation_id": 0}, (400, {"error": "simulation_id is required"})),
    ({"simulation_id": "0"}, (400, {"error": "simulation_id is required"})),
    ({"simulation_id": "7"}, (409, _not_sms("7", _not_offered("7", "viva-core", "analysis re-runs")))),
    ({"simulation_id": 8}, (409, _other_backend("8"))),
    ({"simulation_id": "simulation-Z"}, (409, _not_sms("simulation-Z", _NOT_SMS_ID))),
    ({"simulation_id": 12, "study": "nope"}, (404, {"error": "study 'nope' not found"})),
])
def test_rerun_refusals(records, asked, inproc_client, payload, expected):
    r = inproc_client(records).post(RERUN, json=payload)
    assert (r.status_code, r.json()) == expected
    assert asked == []


@pytest.mark.parametrize("given", [12, "12", " 12 "])
def test_rerun_an_sms_run(records, asked, inproc_client, given):
    r = inproc_client(records).post(RERUN, json={"simulation_id": given, "modules": {"single": {"m": {}}}})
    assert (r.status_code, r.json()) == (202, {"analysis_id": 1, "analysis_name": None, "simulation_id": 12,
                                               "phase": "analyzing"})
    assert asked == [("sms", "run_analysis")]


@pytest.mark.parametrize("status, expected", [(401, 401), (500, 502), (None, 502)])
def test_rerun_failures(records, inproc_client, monkeypatch, status, expected):
    class Client:
        def __init__(self, *a, **k):
            pass

        def run_analysis(self, sim, modules):
            raise SmsApiError("nope", status=status)

    monkeypatch.setattr(sms_legacy, "SmsApiClient", Client)
    monkeypatch.setattr(sms_legacy, "_run_auth_ok", lambda: True)
    r = inproc_client(records).post(RERUN, json={"simulation_id": 12})
    assert (r.status_code, r.json()) == (expected, {"error": "nope", "simulation_id": 12})


# --- figures -----------------------------------------------------------------------------------------------------

@pytest.fixture
def figs(monkeypatch):
    seen: list = []
    monkeypatch.setattr(raf, "list_remote_analysis_figures",
                        lambda client, sim, **k: seen.append(("list", sim)) or {"available": True, "analyses": ["a"]})

    def fetch(client, sim, analysis, path):
        seen.append(("fetch", sim, analysis, path))
        return None if analysis == "missing" else (b"PNG", "image/png")
    monkeypatch.setattr(raf, "fetch_by_analysis", fetch)
    monkeypatch.setattr(raf, "study_remote_figures",
                        lambda ws_root, client, study, max_sims: seen.append(("study", study, max_sims))
                        or {"available": True, "study": study, "sims": []})
    return seen


@pytest.mark.parametrize("rid, expected", [
    ("", (400, {"error": "simulation_id required"})),
    ("0", (400, {"error": "simulation_id required"})),
    ("7", (409, _not_sms("7", _not_offered("7", "viva-core", "analysis outputs (land, figures)")))),
    ("8", (409, _other_backend("8"))),
    ("simulation-Z", (409, _not_sms("simulation-Z", _NOT_SMS_ID))),
    ("%2B12", (200, {"available": True, "analyses": ["a"]})),
])
def test_figures(records, figs, inproc_client, rid, expected):
    r = inproc_client(records).get(f"/api/remote-analysis-figures?simulation_id={rid}&study=s")
    assert (r.status_code, r.json()) == expected
    assert figs == ([("list", 12)] if expected[0] == 200 else [])


@pytest.mark.parametrize("rid, analysis, path, expected", [
    ("", "a", "viz/x.png", (400, b'{"error":"simulation_id, analysis, path required"}', "application/json")),
    ("12", "", "viz/x.png", (400, b'{"error":"simulation_id, analysis, path required"}', "application/json")),
    ("12", "a", "", (400, b'{"error":"simulation_id, analysis, path required"}', "application/json")),
    ("12", "a", "viz/x.png", (200, b"PNG", "image/png")),
    ("12", "missing", "viz/x.png", (404, b'{"error":"not found"}', "application/json")),
])
def test_figure_bytes(records, figs, inproc_client, rid, analysis, path, expected):
    r = inproc_client(records).get(f"/api/remote-analysis-figure?simulation_id={rid}&analysis={analysis}"
                                   f"&path={path}&study=s")
    assert (r.status_code, r._body, r.headers["content-type"]) == expected
    if expected[0] in (200, 404):
        assert figs == [("fetch", 12, analysis, path)]


@pytest.mark.parametrize("rid, expected", [
    ("7", (409, _not_sms("7", _not_offered("7", "viva-core", "analysis outputs (land, figures)")))),
    ("8", (409, _other_backend("8"))),
    ("simulation-Z", (409, _not_sms("simulation-Z", _NOT_SMS_ID))),
])
def test_figure_refusals(records, figs, inproc_client, rid, expected):
    r = inproc_client(records).get(f"/api/remote-analysis-figure?simulation_id={rid}&analysis=a&path=viz/x.png"
                                   f"&study=s")
    assert (r.status_code, r.json()) == expected
    assert figs == []


@pytest.mark.parametrize("query, expected", [
    ("", (400, {"error": "study required"}, None)),
    ("study=s", (200, {"available": True, "study": "s", "sims": []}, 10)),
    ("study=s&limit=0", (200, {"available": True, "study": "s", "sims": []}, 10)),
    ("study=s&limit=99", (200, {"available": True, "study": "s", "sims": []}, 30)),
    ("study=s&limit=-5", (200, {"available": True, "study": "s", "sims": []}, 1)),
    ("study=s&limit=4", (200, {"available": True, "study": "s", "sims": []}, 4)),
])
def test_study_figures(records, figs, inproc_client, query, expected):
    r = inproc_client(records).get(f"/api/study-remote-figures?{query}")
    assert (r.status_code, r.json()) == expected[:2]
    assert figs == ([("study", "s", expected[2])] if expected[2] else [])
