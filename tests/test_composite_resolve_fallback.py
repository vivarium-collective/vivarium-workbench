"""The committed-artifact fallback for generator default state.

When a generator declares no ``default_state_ref``, ``CompositeSpec.default_state``
returns None and the Composite Explorer shows "not generated yet". The resolver
falls back to the regen script's committed artifact at
``reports/composite-state/<id>.json`` so every generator's wiring renders without
per-generator annotation.
"""
import json

from vivarium_workbench.lib.composite_resolve import _committed_default_state


def _write_artifact(tmp_path, cid, payload):
    d = tmp_path / "reports" / "composite-state"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_fallback_reads_committed_state(tmp_path):
    _write_artifact(tmp_path, "v2ecoli.composites.baseline",
                    {"state": {"agents": {"0": {}}, "global_time": 0.0}})
    st = _committed_default_state(tmp_path, "v2ecoli.composites.baseline")
    assert isinstance(st, dict) and "agents" in st and "global_time" in st


def test_fallback_missing_file_returns_none(tmp_path):
    assert _committed_default_state(tmp_path, "v2ecoli.composites.nope") is None


def test_fallback_malformed_json_returns_none(tmp_path):
    d = tmp_path / "reports" / "composite-state"
    d.mkdir(parents=True)
    (d / "bad.json").write_text("{not json", encoding="utf-8")
    assert _committed_default_state(tmp_path, "bad") is None


def test_fallback_artifact_without_state_key_returns_none(tmp_path):
    _write_artifact(tmp_path, "x", {"wiring_status": "unavailable"})
    assert _committed_default_state(tmp_path, "x") is None


def test_fallback_non_dict_state_returns_none(tmp_path):
    _write_artifact(tmp_path, "x", {"state": "not-a-dict"})
    assert _committed_default_state(tmp_path, "x") is None


# --- live-build fallback -----------------------------------------------------
# A generator with NEITHER a declared default_state_ref NOR a committed artifact
# (i.e. every newly-authored one) used to have no path to wiring at all here,
# even though GET /api/composite-state builds it via the env worker. resolve now
# reaches for that same build before reporting "not generated yet".

def _register_generator(spec_id="m.g", name="g"):
    from process_bigraph import composite_spec as cs
    cs.clear_registry()
    cs.register(cs.CompositeSpec(id=spec_id, name=name,
                                 builder=lambda core=None: {"state": {}}))
    return spec_id


def test_resolve_builds_generator_when_no_artifact(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_resolve as cr
    sid = _register_generator()
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    monkeypatch.setattr(cr, "_live_generator_state", lambda ws, i: {"store": {"x": 1}})
    out = cr.resolve_composite(tmp_path, sid)
    assert out["wiring_status"] == "ready"
    assert out["state"]["store"] == {"x": 1}
    assert out["notice"] is None


def test_resolve_allow_build_false_skips_the_build(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_resolve as cr
    sid = _register_generator()
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    called = []
    monkeypatch.setattr(cr, "_live_generator_state",
                        lambda ws, i: called.append(i) or {"store": {}})
    out = cr.resolve_composite(tmp_path, sid, allow_build=False)
    assert called == []
    assert out["wiring_status"] == "unavailable"
    assert "not generated yet" in out["notice"]


def test_resolve_prefers_committed_artifact_over_building(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_resolve as cr
    sid = _register_generator()
    _write_artifact(tmp_path, sid, {"state": {"committed": True}})
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    called = []
    monkeypatch.setattr(cr, "_live_generator_state", lambda ws, i: called.append(i))
    out = cr.resolve_composite(tmp_path, sid)
    assert out["state"] == {"committed": True} and called == []


def test_live_generator_state_unwraps_document_envelope(tmp_path, monkeypatch):
    """The builder returns the document ({"state": {...}}); the artifact/None
    contract here is the bare store mapping, so one envelope layer is peeled."""
    from vivarium_workbench.lib import composite_resolve as cr
    import vivarium_workbench.lib.composite_state_views as csv_mod
    monkeypatch.setattr(csv_mod, "build_composite_state",
                        lambda ws, ref, **kw: ({"state": {"state": {"batch": {}}}}, 200))
    assert cr._live_generator_state(tmp_path, "m.g") == {"batch": {}}


def test_live_generator_state_keeps_a_bare_store_mapping(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_resolve as cr
    import vivarium_workbench.lib.composite_state_views as csv_mod
    doc = {"agents": {"0": {}}, "global_time": 0.0}
    monkeypatch.setattr(csv_mod, "build_composite_state",
                        lambda ws, ref, **kw: ({"state": doc}, 200))
    assert cr._live_generator_state(tmp_path, "m.g") == doc


def test_live_generator_state_none_on_build_failure(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_resolve as cr
    import vivarium_workbench.lib.composite_state_views as csv_mod
    monkeypatch.setattr(csv_mod, "build_composite_state",
                        lambda ws, ref, **kw: ({"error": "generator build failed"}, 400))
    assert cr._live_generator_state(tmp_path, "m.g") is None


def test_live_generator_state_none_when_worker_raises(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_resolve as cr
    import vivarium_workbench.lib.composite_state_views as csv_mod
    def _boom(ws, ref, **kw):
        raise RuntimeError("no worker")
    monkeypatch.setattr(csv_mod, "build_composite_state", _boom)
    assert cr._live_generator_state(tmp_path, "m.g") is None
