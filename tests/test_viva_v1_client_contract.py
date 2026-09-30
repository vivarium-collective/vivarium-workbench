"""SmsApiClient's /viva/v1 surface, against a stub GENERATED from viva-core's own OpenAPI.

Each green here proves: the client's paths, methods, query names and request bodies are
accepted by the published contract (the stub 422s an undeclared field/param/shape).
It does NOT prove a live deployment's behaviour (auth, environment readiness, that a
composite id exists there) -- that needs a real run, which is the owner's call.
"""
import jsonschema
import pytest

from _viva_v1_stub import VivaV1Stub, example, load_spec
from vivarium_workbench.lib import server_capabilities as sc
from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError

CAPS = "/viva/v1/capabilities"
HEALTH = "/viva/v1/health"


@pytest.fixture
def stub():
    s = VivaV1Stub()
    s.url = s.start()
    yield s
    s.stop()


@pytest.fixture
def client(stub):
    return SmsApiClient(stub.url, timeout=5, max_retries=1)


def _advertise(stub, caps, services=None):
    stub.respond("GET", CAPS, 200, {"version": "0.1.9", "capabilities": caps})
    stub.respond("GET", HEALTH, 200, {"status": "ok", "version": "0.1.9", "services": services or {}})


# -- the stub itself agrees with the spec ---------------------------------

def test_every_default_response_satisfies_its_schema():
    """The generated examples are contract-valid, for every JSON 2xx in the spec."""
    stub = VivaV1Stub()
    n = 0
    for tmpl, item in stub.spec["paths"].items():
        for method, op in item.items():
            for status, resp in op["responses"].items():
                content = (resp.get("content") or {}).get("application/json")
                if status.startswith("2") and content:
                    stub.validate_response(method, tmpl, int(status), example(stub.spec, content["schema"]))
                    n += 1
    assert n > 40


def test_stub_refuses_a_field_the_schema_does_not_declare(stub, client):
    with pytest.raises(SmsApiError) as e:
        client._post("/viva/v1/composites", json_body={
            "environment": {"name": "e"}, "composite": {"id": "c"}, "simulator_id": 3})
    assert e.value.status == 422


def test_stub_refuses_an_undeclared_query_param(stub, client):
    with pytest.raises(SmsApiError) as e:
        client._get("/viva/v1/composites", {"experiment_id": "x"})
    assert e.value.status == 422


def test_stub_will_not_lie_about_the_contract(stub):
    with pytest.raises(jsonschema.ValidationError):
        stub.respond("GET", HEALTH, 200, {"status": "ok"})  # version/services required


# -- the client, against the contract --------------------------------------

def test_create_composite_run_by_id_sends_the_documented_body(stub, client):
    run = client.create_composite_run(
        environment={"id": "env-1"},
        composite={"id": "ecoli-simulation", "params": {"n": 2}},
        label="wb run")
    (call,) = stub.calls_to("viva-create-composite-run")
    assert call.body == {"environment": {"id": "env-1"},
                         "composite": {"id": "ecoli-simulation", "params": {"n": 2}}, "label": "wb run"}
    assert call.status == 202 and "id" in run


def test_create_composite_run_by_document(stub, client):
    client.create_composite_run(environment={"name": "deployment-default"},
                                document={"state": {}}, execution={"options": {"interval_time": 5.0}})
    (call,) = stub.calls_to("viva-create-composite-run")
    assert call.body["document"] == {"state": {}} and "composite" not in call.body
    assert call.status == 202


def test_create_needs_exactly_one_of_composite_or_document(client):
    with pytest.raises(ValueError):
        client.create_composite_run(environment={"id": "e"})
    with pytest.raises(ValueError):
        client.create_composite_run(environment={"id": "e"}, composite={"id": "c"}, document={})


def test_reads_hit_the_documented_routes(stub, client):
    rid = "simulation-D55FF7B"
    client.composite_run(rid)
    client.composite_run_status(rid)
    client.composite_run_progress(rid)
    client.composite_run_jobs(rid)
    client.composite_run_datasets(rid, limit=5, offset=0)
    assert client.composite_run_log(rid, full=True).startswith("line 1")
    client.list_composite_runs(status=["running", "queued"], composite_id="c", limit=3)
    assert [c.status for c in stub.calls] == [200] * len(stub.calls)
    assert len({c.operation_id for c in stub.calls}) == 7  # all seven routes resolved to distinct operations
    assert None not in {c.operation_id for c in stub.calls}


def test_cancel_uses_delete_and_returns_the_kept_run(stub, client):
    out = client.cancel_composite_run("simulation-D55FF7B")
    assert "run" in out
    (call,) = [c for c in stub.calls if c.method == "DELETE"]
    assert call.path == "/viva/v1/composites/simulation-D55FF7B" and call.status == 200


def test_opaque_run_id_is_quoted_not_parsed(stub, client):
    client.composite_run("a/b c")
    # the whole id is ONE path segment: it reached the get-run route, not a sub-route
    assert stub.calls[-1].operation_id == "viva-get-composite-run"


# -- capability negotiation ------------------------------------------------

def test_profile_document_when_composites_and_documents_advertised(stub, client):
    _advertise(stub, ["viva-v1-composites", "viva-v1-composites-documents", "viva-v1-environments"],
               {"composites": True})
    p = sc.backend_profile(client)
    assert p["dispatch"] == sc.DISPATCH_DOCUMENT and p["services"] == {"composites": True}


def test_profile_legacy_when_composites_are_served_by_id_only(stub, client):
    """A run surface without documents cannot run an arbitrary workspace composite."""
    _advertise(stub, ["viva-v1-composites", "viva-v1-environments"])
    assert sc.backend_profile(client)["dispatch"] == sc.DISPATCH_LEGACY


def test_profile_document_on_a_standalone_core(stub, client):
    """What sms.cam.uchc.edu advertises (viva-core 0.1.9, read live on 2026-09-30)."""
    _advertise(stub, ["viva-v1-composites", "viva-v1-composites-documents", "viva-v1-jobs",
                      "viva-v1-surface", "viva-v1-workers"], {"environment_records": False})
    assert sc.backend_profile(client)["dispatch"] == sc.DISPATCH_DOCUMENT


def test_profile_legacy_without_the_run_surface(stub, client):
    _advertise(stub, ["viva-v1-surface"])
    assert sc.backend_profile(client)["dispatch"] == sc.DISPATCH_LEGACY


def test_profile_legacy_when_the_backend_predates_capabilities():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()
        def log_message(self, *a): pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = SmsApiClient(f"http://127.0.0.1:{srv.server_address[1]}", timeout=3, max_retries=1)
        p = sc.backend_profile(c)
        assert p["dispatch"] == sc.DISPATCH_LEGACY and p["reachable"] is True
    finally:
        srv.shutdown(); srv.server_close()


def test_profile_reports_unreachable_without_raising():
    c = SmsApiClient("http://127.0.0.1:1", timeout=1, max_retries=1)
    p = sc.backend_profile(c)
    assert p["reachable"] is False and p["dispatch"] == sc.DISPATCH_LEGACY and p["error"]
