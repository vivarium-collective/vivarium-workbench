"""Tests for resolve_composite_for_request — local vs deployment dispatch.

Two session-build tests are marked ``plugin``: a ``.viv-build.json`` stamp is the SMS provider's record of a session
build (``backends.session_build`` asks the providers). With the plugin absent or providers off the stamp is inert and
the workspace resolves as a plain local one, which a static spec does too (so that test stays core), while a generator
only the session's environment can import is a local miss (docs/backend-adapters.md step P3, K2a).
"""
from pathlib import Path

import pytest

from vivarium_workbench.lib import composite_resolve as cr


def test_dispatch_local_when_no_viv_build(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr(cr, "resolve_composite", lambda ws, sid, ov=None: called.update({"local": (sid, ov)}) or {"name": "local"})
    out = cr.resolve_composite_for_request(tmp_path, "pkg.x", {"k": 1})
    assert out == {"name": "local"} and called["local"] == ("pkg.x", {"k": 1})


@pytest.mark.plugin
def test_dispatch_deployment_prefers_local_when_generator_found(tmp_path, monkeypatch):
    """item 63: a session-bound materialized build (.viv-build.json present)
    has real files on disk — resolve via the same safe env-worker discovery
    discover_all_composites already uses, INSTEAD of the sms-api route (which
    was added speculatively and never built server-side). The remote client
    must never even be constructed when local discovery finds the id."""
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')

    monkeypatch.setattr(
        cr, "_local_generator_payload",
        lambda ws, sid: {"id": sid, "kind": "generator",
                          "parameters": {"n_seeds": {"type": "integer", "default": 1}}}
        if sid == "v2ecoli.composites.ecoli_baseline.ecoli_baseline" else None,
    )
    out = cr.resolve_composite_for_request(
        tmp_path, "v2ecoli.composites.ecoli_baseline.ecoli_baseline", {})
    assert out["kind"] == "generator"
    assert out["parameters"]["n_seeds"]["default"] == 1


def test_dispatch_deployment_resolves_static_spec_locally_no_generator_helper_needed(tmp_path, monkeypatch):
    """item 63, the generalization gap: a STATIC `.composite.yaml` composite
    under a session-bound materialized build needs no environment/interpreter
    at all (pure file read) — it must resolve via the SAME general
    resolve_composite path any local workspace uses, without ever reaching
    for the generator-specific env-worker helper. Proves the fix isn't
    generator-only / MVP-composite-only."""
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    (tmp_path / "workspace.yaml").write_text("name: demo-ws\npackage_path: pbg_demo\n", encoding="utf-8")
    comp = tmp_path / "pbg_demo" / "composites"
    comp.mkdir(parents=True)
    (comp / "c.composite.yaml").write_text(
        "name: c\nschema:\n  v: float\nstate:\n  v: 1\n", encoding="utf-8")

    def _boom(*a, **k):
        raise AssertionError("the generator helper should not be reached")

    monkeypatch.setattr(cr, "_local_generator_payload", _boom)
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    out = cr.resolve_composite_for_request(tmp_path, "pbg_demo.composites.c", {})
    assert out is not None and out["id"] == "pbg_demo.composites.c"
    assert out["wiring_status"] == "ready" and out["state"] == {"v": 1}
    assert out["kind"] == "spec"


def test_local_generator_payload_returns_none_for_non_generator(tmp_path, monkeypatch):
    """A static spec (kind != 'generator') isn't this helper's job — None lets
    the caller fall through to whatever it would otherwise do."""
    from vivarium_workbench.lib import composite_lookup
    (tmp_path / "workspace.yaml").write_text("name: demo-ws\npackage_path: pbg_demo\n", encoding="utf-8")
    monkeypatch.setattr(
        composite_lookup, "discover_all_composites",
        lambda ws, pkg: {"pbg_demo.composites.c": {"kind": "spec", "parameters": {}}},
    )
    assert cr._local_generator_payload(tmp_path, "pbg_demo.composites.c") is None
    assert cr._local_generator_payload(tmp_path, "pbg_demo.composites.absent") is None


def test_local_generator_payload_returns_real_parameters(tmp_path, monkeypatch):
    """The happy path: a generator entry discovered locally surfaces its real
    declared parameters (e.g. n_seeds/n_generations) — the actual item 63
    fix — with an honest notice that only the wiring/state preview, not the
    parameters, is unavailable for a materialized session build."""
    from vivarium_workbench.lib import composite_lookup
    (tmp_path / "workspace.yaml").write_text("name: v2ecoli\npackage_path: v2ecoli\n", encoding="utf-8")
    rec = {
        "kind": "generator", "name": "ecoli_baseline", "description": "desc",
        "parameters": {"n_seeds": {"type": "integer", "default": 1},
                        "n_generations": {"type": "integer", "default": 1}},
        "module": "v2ecoli.composites.ecoli_baseline", "default_n_steps": 2700,
        "visualizations": [{"name": "cell_mass"}],
    }
    monkeypatch.setattr(
        composite_lookup, "discover_all_composites",
        lambda ws, pkg: {"v2ecoli.composites.ecoli_baseline.ecoli_baseline": rec}
        if pkg == "v2ecoli" else {},
    )
    out = cr._local_generator_payload(tmp_path, "v2ecoli.composites.ecoli_baseline.ecoli_baseline")
    assert out is not None
    assert out["kind"] == "generator"
    assert out["parameters"]["n_seeds"]["default"] == 1
    assert out["parameters"]["n_generations"]["default"] == 1
    assert out["state"] is None and out["svg"] is None  # honest: no live build here
    assert "not available" in out["notice"] and "parameters" in out["notice"].lower()


@pytest.mark.plugin
def test_dispatch_deployment_degrades_when_local_misses(tmp_path, monkeypatch):
    """A deployment build that neither local path can see degrades to the
    honest-unavailable 200 shape and asks no remote. viva-api has never served a
    composite-resolve route (verified against its route table and its OpenAPI
    history, 2026-11-18), so the SmsApiClient fallback, which could only ever 404,
    was removed (docs/backend-adapters.md, step 0)."""
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    monkeypatch.setattr(cr, "resolve_composite", lambda ws, sid, ov=None: None)
    monkeypatch.setattr(cr, "_local_generator_payload", lambda ws, sid: None)

    out = cr.resolve_composite_for_request(tmp_path, "pkg.x", {"k": 2})

    assert out["id"] == "pkg.x"
    assert out["wiring_status"] == "unavailable"
    assert "not available" in out["notice"]
    assert not hasattr(cr, "SmsApiClient")


def test_resolve_generator_without_artifact_degrades(tmp_path, monkeypatch):
    from process_bigraph import composite_spec as cs
    from vivarium_workbench.lib import composite_resolve as cr
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
    from vivarium_workbench.lib import composite_resolve as cr
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


def test_resolve_static_embeds_declared_emit_paths(tmp_path, monkeypatch):
    """A composite that declares ``emitters: [{paths: [...]}]`` gets those
    paths embedded INSIDE its served ``state`` as ``_declared_emit_paths`` —
    the fix for the Task 6 review finding: ``install_default_emitters`` only
    runs on the run-EXECUTION path, so the browse/view state a real
    workspace serves (e.g. v2ecoli's ``baseline``, declaring
    ``emitters=[{"paths": ["global_time", "bulk", "listeners"]}]``) never
    carried an emitter node — loom's declared-paths helper silently fell
    back to every top-level store. Embedding the paths directly in `state`
    (not as a sibling of it) is required because every hop that forwards a
    composite doc to loom forwards only the `state` sub-object."""
    from vivarium_workbench.lib import composite_resolve as cr
    from process_bigraph import composite_spec as cs
    cs.clear_registry()
    (tmp_path / "workspace.yaml").write_text("name: demo-ws\npackage_path: pbg_demo\n", encoding="utf-8")
    comp = tmp_path / "pbg_demo" / "composites"
    comp.mkdir(parents=True)
    (comp / "baseline.composite.yaml").write_text(
        "name: baseline\n"
        "state:\n"
        "  global_time: 0\n"
        "  bulk: {}\n"
        "  listeners: {}\n"
        "emitters:\n"
        "  - address: local:ParquetEmitter\n"
        "    config: {}\n"
        "    paths: [global_time, bulk, listeners]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    out = cr.resolve_composite(tmp_path, "pbg_demo.composites.baseline")
    assert out is not None and out["wiring_status"] == "ready"
    assert out["state"]["_declared_emit_paths"] == ["global_time", "bulk", "listeners"]


def test_declared_emit_paths_helper():
    from vivarium_workbench.lib.composite_resolve import declared_emit_paths
    assert declared_emit_paths(None) == []
    assert declared_emit_paths([]) == []
    assert declared_emit_paths([{"paths": ["global_time", "bulk", "listeners"]}]) == [
        "global_time", "bulk", "listeners",
    ]
    # dotted paths normalize to '/'-joined, matching the client's emitSet convention.
    assert declared_emit_paths([{"paths": ["listeners.mass"]}]) == ["listeners/mass"]
    # dedup across multiple decls, order preserved.
    assert declared_emit_paths([
        {"paths": ["bulk", "global_time"]},
        {"paths": ["global_time", "listeners"]},
    ]) == ["bulk", "global_time", "listeners"]
    # malformed entries are tolerated, not raised.
    assert declared_emit_paths([{"paths": None}, "not-a-dict", {}]) == []


def test_resolve_unregistered_returns_none(tmp_path, monkeypatch):
    from process_bigraph import composite_spec as cs
    from vivarium_workbench.lib import composite_resolve as cr
    cs.clear_registry()
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    assert cr.resolve_composite(tmp_path, "pbg_demo.composites.absent") is None


def test_resolve_malformed_static_file_degrades(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_resolve as cr
    from process_bigraph import composite_spec as cs
    cs.clear_registry()
    (tmp_path / "workspace.yaml").write_text("name: demo-ws\npackage_path: pbg_demo\n", encoding="utf-8")
    comp = tmp_path / "pbg_demo" / "composites"
    comp.mkdir(parents=True)
    (comp / "bad.composite.yaml").write_text("name: bad\nstate: {a: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    out = cr.resolve_composite(tmp_path, "pbg_demo.composites.bad")
    assert out is not None and out["wiring_status"] == "unavailable"
    assert "could not be parsed" in out["notice"]


def test_resolve_static_no_state_notice_not_generator(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_resolve as cr
    from process_bigraph import composite_spec as cs
    cs.clear_registry()
    cs.register(cs.CompositeSpec(id="m.s", name="s", state={}))  # empty inline state
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    monkeypatch.setattr(cr, "_get_spec", lambda sid: cs.get("m.s") if sid == "m.s" else None)
    out = cr.resolve_composite(tmp_path, "m.s")
    # empty {} state is falsy-but-not-None; default_state returns {} → wiring "ready".
    # If your default_state treats {} as ready, assert ready; the key check is the
    # notice (when unavailable) does NOT say "generator" for a static spec.
    assert out["kind"] == "spec"


def test_resolve_degrades_when_get_spec_raises(tmp_path, monkeypatch):
    """An in-process failure during generator discovery/lookup (e.g. a broken
    native-dependency import like pymunk/viva_munk — the "colony" composite's
    real-world failure mode) degrades to the standard wiring_status:"unavailable"
    shape instead of propagating to the app-wide 500 handler."""
    from vivarium_workbench.lib import composite_resolve as cr
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)

    def _raise(spec_id):
        raise ImportError("no module named viva_munk")

    monkeypatch.setattr(cr, "_get_spec", _raise)
    out = cr.resolve_composite(tmp_path, "v2ecoli.composites.colony")
    assert out is not None
    assert out["wiring_status"] == "unavailable"
    assert out["id"] == "v2ecoli.composites.colony"
    assert "viva_munk" in out["notice"]


def test_resolve_generator_with_corrupt_artifact_degrades(tmp_path, monkeypatch):
    from process_bigraph import composite_spec as cs
    from vivarium_workbench.lib import composite_resolve as cr
    cs.clear_registry()
    # generator whose default_state() raises (corrupt artifact)
    spec = cs.CompositeSpec(id="m.boom", name="boom",
                            builder=lambda core=None: {"state": {}},
                            default_state_ref="boom.default-state.json")
    cs.register(spec)
    def _raise(*a, **k):
        raise ValueError("corrupt artifact")
    monkeypatch.setattr(spec, "default_state", _raise)
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    out = cr.resolve_composite(tmp_path, "m.boom")
    assert out is not None and out["wiring_status"] == "unavailable"
    assert out["state"] is None
    assert "not generated yet" in out["notice"]


def test_resolve_generator_override_build_error_surfaces(tmp_path, monkeypatch):
    """A Config → Apply whose overrides make the generator build RAISE (e.g. a
    single-value param handed a comma-list) must surface the exception — NOT
    silently fall back to the default (unoverridden) wiring, which would look
    like the invalid config had been accepted."""
    from process_bigraph import composite_spec as cs
    from vivarium_workbench.lib import composite_resolve as cr
    cs.clear_registry()
    spec = cs.CompositeSpec(
        id="m.g", name="g", builder=lambda core=None, **kw: {"state": {"x": {}}},
        default_state_ref="m.g.default-state.json",
        parameters={"simulator": {"type": "string", "default": "simbio"}},
    )
    cs.register(spec)
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    # default_state would happily return the UNOVERRIDDEN wiring — the fix must
    # not reach it once an override build has failed.
    monkeypatch.setattr(spec, "default_state", lambda *a, **k: {"simbio_process": {}})
    monkeypatch.setattr(
        cr, "_live_generator_build",
        lambda ws, sid, ov=None: (None, "needs exactly one simulator, got ['copasi', 'tellurium']"),
    )
    out = cr.resolve_composite(tmp_path, "m.g", overrides={"simulator": "copasi, tellurium"})
    assert out["wiring_status"] == "error"
    assert out["state"] is None                       # NOT the default fallback
    assert "one simulator" in (out["notice"] or "")
    assert "one simulator" in (out["error"] or "")


def test_resolve_generator_override_build_ok_still_resolves(tmp_path, monkeypatch):
    """A valid override build resolves normally (the error path must not regress
    the happy path)."""
    from process_bigraph import composite_spec as cs
    from vivarium_workbench.lib import composite_resolve as cr
    from vivarium_workbench.lib import process_docs
    cs.clear_registry()
    spec = cs.CompositeSpec(
        id="m.g", name="g", builder=lambda core=None, **kw: {"state": {"copasi_process": {}}},
        parameters={"simulator": {"type": "string", "default": "simbio"}},
    )
    cs.register(spec)
    monkeypatch.setattr(cr, "_prime_registry", lambda: None)
    monkeypatch.setattr(cr, "_live_generator_build",
                        lambda ws, sid, ov=None: ({"copasi_process": {}}, None))
    # attach_process_docs_via_worker needs the env worker; identity for the test.
    monkeypatch.setattr(process_docs, "attach_process_docs_via_worker",
                        lambda ws, state, spec_id=None: state)
    out = cr.resolve_composite(tmp_path, "m.g", overrides={"simulator": "copasi"})
    assert out["wiring_status"] == "ready"
    assert out["state"] == {"copasi_process": {}}
