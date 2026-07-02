"""Tests for the canonical API error envelope (lib/errors.py + api.app handlers).

Every error the API emits must use the shape ``{"error": "<message>", ...}``.
These tests exercise the three handlers registered in ``create_app`` via throwaway
POST probe routes added to a real app instance (POST so the static GET catch-all
cannot shadow them).
"""
from fastapi.testclient import TestClient
from pydantic import BaseModel

from vivarium_dashboard.api.app import create_app
from vivarium_dashboard.lib.errors import APIError, error_body


def _app_with_probe_routes():
    app = create_app()

    class _Body(BaseModel):
        n: int

    @app.post("/_probe/apierror")
    def _raise_api_error():
        raise APIError(404, "thing not found", ref="abc")

    @app.post("/_probe/boom")
    def _raise_boom():
        raise RuntimeError("kaboom")

    @app.post("/_probe/validate")
    def _needs_int(body: _Body):
        return {"ok": body.n}

    return app


def test_api_error_emits_canonical_envelope():
    client = TestClient(_app_with_probe_routes())
    r = client.post("/_probe/apierror")
    assert r.status_code == 404
    assert r.json() == {"error": "thing not found", "ref": "abc"}


def test_validation_error_normalized_to_error_envelope():
    client = TestClient(_app_with_probe_routes())
    r = client.post("/_probe/validate", json={"n": "not-an-int"})
    assert r.status_code == 422
    body = r.json()
    assert "error" in body and "validation error" in body["error"]
    # Structured detail preserved so anything reading ``.detail`` still works.
    assert isinstance(body["detail"], list) and body["detail"]


def test_unhandled_exception_emits_500_envelope():
    client = TestClient(_app_with_probe_routes(), raise_server_exceptions=False)
    r = client.post("/_probe/boom")
    assert r.status_code == 500
    assert r.json() == {"error": "internal server error"}


def test_error_body_helper():
    assert error_body("x") == {"error": "x"}
    assert error_body("x", ref="y") == {"error": "x", "ref": "y"}
