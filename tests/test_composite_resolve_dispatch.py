"""Tests for resolve_composite_for_request — local vs deployment dispatch."""
from pathlib import Path
from vivarium_dashboard.lib import composite_resolve as cr


def test_dispatch_local_when_no_viv_build(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr(cr, "resolve_composite", lambda ws, sid, ov=None: called.update({"local": (sid, ov)}) or {"name": "local"})
    out = cr.resolve_composite_for_request(tmp_path, "pkg.x", {"k": 1})
    assert out == {"name": "local"} and called["local"] == ("pkg.x", {"k": 1})


def test_dispatch_deployment_when_viv_build(tmp_path, monkeypatch):
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    captured = {}
    class _FakeClient:
        def __init__(self, base=None): pass
        def composite_resolve(self, sid, ref, ov=None):
            captured.update(sid=sid, ref=ref, ov=ov); return {"name": "remote"}
    monkeypatch.setattr(cr, "SmsApiClient", _FakeClient)
    monkeypatch.setattr(cr, "_sms_api_base", lambda: "http://sms")
    out = cr.resolve_composite_for_request(tmp_path, "pkg.x", {"k": 2})
    assert out == {"name": "remote"}
    assert captured == {"sid": 66, "ref": "pkg.x", "ov": {"k": 2}}
