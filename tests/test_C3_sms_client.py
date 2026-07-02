"""C3: Tests for SmsApiClient compose methods.

Tests:
- compose_check(pbg_bytes) - validate server connectivity
- compose_submit(pbg_bytes, extra_pip_deps=None) - POST multipart to /compose/v1/simulation/run
- compose_status(task_id) - GET /compose/v1/simulation/{id}/status
- download_compose_results(sim_id, dest) - GET /compose/v1/simulation/{id}/results

Protocol requirements:
- Multipart field name MUST be 'uploaded_file'
- extra_pip_deps sent as repeated query params (?extra_pip_deps=...&extra_pip_deps=...)
"""
from __future__ import annotations

import io
import json
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError


class _JsonResp(io.BytesIO):
    """Mock HTTP response returning JSON payload."""
    status = 200

    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class _BinaryResp(io.BytesIO):
    """Mock HTTP response returning raw bytes."""
    status = 200

    def __init__(self, data: bytes, status: int = 200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def read(self, n=-1):
        return super().read(n)


@contextmanager
def _patch_urlopen(monkeypatch, capture: dict, payload, status: int = 200):
    """Patch urlopen to capture requests and return a fake response."""
    def fake_urlopen(req, timeout=None):
        capture["url"] = req.full_url
        capture["method"] = req.get_method()
        capture["headers"] = dict(req.headers)
        capture["body"] = req.data
        if status != 200:
            from urllib.error import HTTPError
            raise HTTPError(req.full_url, status, "err", {}, io.BytesIO(b"error"))
        if isinstance(payload, bytes):
            return _BinaryResp(payload, status)
        return _JsonResp(payload, status)

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    yield


# ---------------------------------------------------------------------------
# compose_check
# ---------------------------------------------------------------------------

def test_compose_check_sends_get_to_health_or_check_endpoint(monkeypatch):
    """compose_check makes a GET request to verify server reachability."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"status": "ok"}):
        c = SmsApiClient("http://h:8080")
        result = c.compose_check(b"fake-pbg-bytes")
    assert cap["method"] in ("GET", "POST")
    assert "/compose/" in cap["url"]


def test_compose_check_raises_sms_api_error_on_failure(monkeypatch):
    """compose_check raises SmsApiError on non-200."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {}, status=503):
        c = SmsApiClient("http://h:8080")
        with pytest.raises(SmsApiError):
            c.compose_check(b"fake-pbg-bytes")


# ---------------------------------------------------------------------------
# compose_submit
# ---------------------------------------------------------------------------

def test_compose_submit_posts_to_correct_endpoint(monkeypatch):
    """compose_submit POSTs to /compose/v1/simulation/run."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"simulation_database_id": 42}):
        c = SmsApiClient("http://h:8080")
        result = c.compose_submit(b"fake-pbg-bytes")
    assert cap["method"] == "POST"
    assert "/compose/v1/simulation/run" in cap["url"]


def test_compose_submit_returns_simulation_id(monkeypatch):
    """compose_submit returns the simulation_database_id from the response."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"simulation_database_id": 99}):
        c = SmsApiClient("http://h:8080")
        result = c.compose_submit(b"some-pbg-content")
    assert result == 99


def test_compose_submit_uses_multipart_uploaded_file_field(monkeypatch):
    """compose_submit encodes the file as multipart with field name 'uploaded_file'."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"simulation_database_id": 7}):
        c = SmsApiClient("http://h:8080")
        c.compose_submit(b"pbg-content-here")
    # Content-Type header must declare multipart/form-data
    ct = cap["headers"].get("Content-type", "")
    assert "multipart/form-data" in ct, f"Expected multipart/form-data, got: {ct!r}"
    # Body must contain the field name 'uploaded_file'
    body_str = cap["body"].decode("latin-1")
    assert 'name="uploaded_file"' in body_str, (
        f"Expected multipart field 'uploaded_file' in body"
    )


def test_compose_submit_sends_file_content_in_body(monkeypatch):
    """compose_submit includes the pbg bytes in the multipart body."""
    pbg_content = b"BINARY-PBG-CONTENT-XYZ"
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"simulation_database_id": 3}):
        c = SmsApiClient("http://h:8080")
        c.compose_submit(pbg_content)
    assert pbg_content in cap["body"], "PBG content must be in the request body"


def test_compose_submit_no_extra_deps_no_query_params(monkeypatch):
    """compose_submit with no extra_pip_deps sends no extra_pip_deps params."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"simulation_database_id": 1}):
        c = SmsApiClient("http://h:8080")
        c.compose_submit(b"pbg", extra_pip_deps=None)
    qs = parse_qs(urlsplit(cap["url"]).query)
    assert "extra_pip_deps" not in qs


def test_compose_submit_extra_pip_deps_as_repeated_query_params(monkeypatch):
    """extra_pip_deps are sent as repeated ?extra_pip_deps= query parameters."""
    cap = {}
    deps = ["git+https://github.com/x/y.git@abc", "some-package>=1.0"]
    with _patch_urlopen(monkeypatch, cap, {"simulation_database_id": 5}):
        c = SmsApiClient("http://h:8080")
        c.compose_submit(b"pbg-bytes", extra_pip_deps=deps)
    qs = parse_qs(urlsplit(cap["url"]).query)
    assert "extra_pip_deps" in qs, f"URL query: {urlsplit(cap['url']).query!r}"
    assert set(qs["extra_pip_deps"]) == set(deps), (
        f"Expected {deps}, got {qs['extra_pip_deps']}"
    )


def test_compose_submit_raises_on_server_error(monkeypatch):
    """compose_submit raises SmsApiError on non-200 response."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {}, status=500):
        c = SmsApiClient("http://h:8080")
        with pytest.raises(SmsApiError):
            c.compose_submit(b"pbg")


# ---------------------------------------------------------------------------
# compose_status
# ---------------------------------------------------------------------------

def test_compose_status_gets_correct_url(monkeypatch):
    """compose_status GETs /compose/v1/simulation/{id}/status."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"status": "running", "simulation_database_id": 42}):
        c = SmsApiClient("http://h:8080")
        result = c.compose_status(42)
    assert cap["method"] == "GET"
    assert "/compose/v1/simulation/42/status" in cap["url"]


def test_compose_status_returns_dict(monkeypatch):
    """compose_status returns the response dict."""
    cap = {}
    payload = {"status": "completed", "simulation_database_id": 17}
    with _patch_urlopen(monkeypatch, cap, payload):
        c = SmsApiClient("http://h:8080")
        result = c.compose_status(17)
    assert result["status"] == "completed"
    assert result["simulation_database_id"] == 17


def test_compose_status_raises_on_not_found(monkeypatch):
    """compose_status raises SmsApiError on 404."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {}, status=404):
        c = SmsApiClient("http://h:8080")
        with pytest.raises(SmsApiError):
            c.compose_status(9999)


# ---------------------------------------------------------------------------
# download_compose_results
# ---------------------------------------------------------------------------

def test_download_compose_results_streams_to_file(monkeypatch, tmp_path):
    """download_compose_results streams the results.zip to dest/results.zip."""
    cap = {}
    fake_zip = b"PK\x03\x04fake-zip-content"

    def fake_urlopen(req, timeout=None):
        cap["url"] = req.full_url
        cap["method"] = req.get_method()
        return _BinaryResp(fake_zip)

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080")
    out = c.download_compose_results(42, tmp_path)
    assert out == tmp_path / "results.zip"
    assert out.read_bytes() == fake_zip
    assert cap["method"] == "GET"
    assert "/compose/v1/simulation/42/results" in cap["url"]
