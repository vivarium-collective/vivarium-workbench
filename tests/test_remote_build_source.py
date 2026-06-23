import io
import json
from pathlib import Path

import pytest

from vivarium_dashboard.lib import sms_api_client as sac


class _Resp:
    """Minimal urlopen() context-manager response."""
    def __init__(self, body: bytes):
        self._body = body
        self._pos = 0
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def read(self, size=-1):
        if size < 0:
            result = self._body[self._pos:]
            self._pos = len(self._body)
        else:
            result = self._body[self._pos:self._pos + size]
            self._pos += len(result)
        return result


def test_list_simulators_hits_versions_endpoint(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Resp(json.dumps({"versions": [{"database_id": 1}]}).encode())

    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    out = sac.SmsApiClient("http://x").list_simulators()
    assert out == {"versions": [{"database_id": 1}]}
    assert seen["url"] == "http://x/core/v1/simulator/versions"


def test_download_workspace_streams_to_file(monkeypatch, tmp_path):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Resp(b"TARBALLBYTES")

    monkeypatch.setattr(sac, "urlopen", fake_urlopen)
    out = sac.SmsApiClient("http://x").download_workspace(45, tmp_path)
    assert out == tmp_path / "workspace.tar.gz"
    assert out.read_bytes() == b"TARBALLBYTES"
    assert seen["url"] == "http://x/api/v1/simulations/workspace?simulator_id=45"
