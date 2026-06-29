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


def test_resolve_generator_without_artifact_degrades(tmp_path, monkeypatch):
    from process_bigraph import composite_spec as cs
    from vivarium_dashboard.lib import composite_resolve as cr
    cs.clear_registry()
    cs.register(cs.CompositeSpec(id="m.g", name="g", builder=lambda core=None: {"state": {}},
                                 default_state_ref="m.g.default-state.json",
                                 parameters={"seed": {"type": "integer", "default": 0}}))
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)  # don't walk real distributions
    out = cr.resolve_composite(tmp_path, "m.g")
    assert out["wiring_status"] == "unavailable" and out["notice"]
    assert out["parameters"]["seed"]["type"] == "integer"   # metadata present without build
    assert out["state"] is None and out["kind"] == "generator"


def test_resolve_static_via_find_path(tmp_path, monkeypatch):
    # A real static spec file resolved through the dashboard's id scheme
    # "<pkg>.composites.<stem>" via find_composite_path.
    from vivarium_dashboard.lib import composite_resolve as cr
    from process_bigraph import composite_spec as cs
    cs.clear_registry()
    (tmp_path / "workspace.yaml").write_text("name: demo-ws\npackage_path: pbg_demo\n", encoding="utf-8")
    comp = tmp_path / "pbg_demo" / "composites"
    comp.mkdir(parents=True)
    (comp / "c.composite.yaml").write_text(
        "name: c\nschema:\n  v: float\nstate:\n  v: 1\n", encoding="utf-8")
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    out = cr.resolve_composite(tmp_path, "pbg_demo.composites.c")
    assert out is not None and out["id"] == "pbg_demo.composites.c"  # requested id round-trips
    assert out["wiring_status"] == "ready" and out["state"] == {"v": 1}
    assert out["schema"] == {"v": "float"} and out["kind"] == "spec"


def test_resolve_unregistered_returns_none(tmp_path, monkeypatch):
    from process_bigraph import composite_spec as cs
    from vivarium_dashboard.lib import composite_resolve as cr
    cs.clear_registry()
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    assert cr.resolve_composite(tmp_path, "pbg_demo.composites.absent") is None
