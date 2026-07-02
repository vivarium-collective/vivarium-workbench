"""Tests for the access-log + request-ID middleware (lib/request_logging.py)."""
import logging

from fastapi.testclient import TestClient

from vivarium_workbench.api.app import create_app
from vivarium_workbench.lib.request_logging import REQUEST_ID_HEADER


def test_health_response_carries_request_id():
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    rid = r.headers.get(REQUEST_ID_HEADER)
    assert rid and len(rid) >= 8


def test_provided_request_id_is_echoed_back():
    client = TestClient(create_app())
    r = client.get("/health", headers={REQUEST_ID_HEADER: "trace-me-123"})
    assert r.headers.get(REQUEST_ID_HEADER) == "trace-me-123"


def test_access_line_is_logged(caplog):
    client = TestClient(create_app())
    with caplog.at_level(logging.INFO, logger="vivarium_workbench.access"):
        client.get("/health")
    lines = [rec for rec in caplog.records if rec.name == "vivarium_workbench.access"]
    assert lines, "expected an access log record"
    rec = lines[-1]
    assert rec.path == "/health"
    assert rec.method == "GET"
    assert rec.status == 200
    assert isinstance(rec.duration_ms, float)
