"""M2 slice 1: the env-worker transport + lifecycle, end to end.

Spawns the real ``env_worker.py`` subprocess over a socketpair and exercises the
JSON-RPC contract (docs/env-worker-protocol.md §5-9). Runs on whatever OS the
suite runs on — so macOS locally and Linux in CI both cover the fd-passing +
framing transport (spec §2, platform support).
"""
import socket
from pathlib import Path

import pytest

from vivarium_workbench.lib.env_worker_client import (
    EnvWorker,
    EnvWorkerError,
    EnvWorkerUnavailable,
)


def test_initialize_handshake(tmp_path):
    with EnvWorker(tmp_path) as w:
        info = w.call("initialize")
        assert info["protocol_version"] == "1.0"
        assert info["workspace"] == str(tmp_path)
        assert "ping" in info["capabilities"]
        assert info["pid"] > 0


def test_ping(tmp_path):
    with EnvWorker(tmp_path) as w:
        r1 = w.call("ping")
        assert r1["ok"] is True
        assert r1["uptime_s"] >= 0
        # serial, multiple calls on one worker
        r2 = w.call("ping")
        assert r2["uptime_s"] >= r1["uptime_s"]


def test_unknown_method_is_structured_error(tmp_path):
    with EnvWorker(tmp_path) as w:
        with pytest.raises(EnvWorkerError) as ei:
            w.call("does_not_exist")
        assert ei.value.code == -32601
        # the worker stays alive after an error (spec §9: env error != crash)
        assert w.call("ping")["ok"] is True


def test_crash_is_reported_not_hung(tmp_path):
    w = EnvWorker(tmp_path)
    try:
        assert w.call("ping")["ok"] is True
        w._proc.kill()          # simulate a worker crash
        w._proc.wait(timeout=5)
        with pytest.raises(EnvWorkerUnavailable):
            w.call("ping")      # EOF -> unavailable, never a hang
    finally:
        w.close()


def test_close_is_clean_and_idempotent(tmp_path):
    w = EnvWorker(tmp_path)
    assert w.call("ping")["ok"] is True
    w.close()
    assert not w.alive()
    w.close()  # idempotent


def test_timeout_surfaces_as_unavailable(tmp_path):
    """A worker that never replies must raise, not hang, once the timeout elapses."""
    w = EnvWorker(tmp_path, timeout=0.5)
    try:
        # Suspend the worker so it stops reading/replying, then a call must time out.
        import os
        import signal
        os.kill(w._proc.pid, signal.SIGSTOP)
        with pytest.raises(EnvWorkerUnavailable):
            w.call("ping")
        os.kill(w._proc.pid, signal.SIGCONT)
    finally:
        w._proc.kill()
        w.close()


def test_transport_is_socketpair_no_filesystem(tmp_path):
    """The channel is a socketpair (spec §5) — no named UDS path to leak/limit."""
    w = EnvWorker(tmp_path)
    try:
        assert w._sock.family == socket.AF_UNIX
        assert w.call("ping")["ok"] is True
    finally:
        w.close()


# ---------------------------------------------------------------------------
# Slice 2: list_generators — the worker holds the workspace env in ITS process
# ---------------------------------------------------------------------------
def _make_ws(root, pkg, gen_name):
    """A minimal workspace whose package registers one @composite_generator."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(f"name: {pkg}\npackage_path: {pkg}\n")
    comp = root / pkg / "composites"
    comp.mkdir(parents=True)
    (root / pkg / "__init__.py").write_text("from . import composites\n")
    (comp / "__init__.py").write_text(
        "from pbg_superpowers.composite_generator import composite_generator\n"
        f"@composite_generator(name='{gen_name}', description='')\n"
        "def g(core=None):\n    return {}\n"
    )
    return root


def test_list_generators_finds_the_workspace_package_generator(tmp_path):
    pytest.importorskip("pbg_superpowers")
    ws = _make_ws(tmp_path / "wsA", "pbg_wa", "gen_a")
    with EnvWorker(ws) as w:
        gens = w.call("list_generators")["generators"]
        assert "pbg_wa.composites.gen_a" in gens


def test_two_workers_have_isolated_registries(tmp_path):
    """The load-bearing M2 property: process isolation. Each worker holds only
    its own workspace's generators — one process cannot do this in-place."""
    pytest.importorskip("pbg_superpowers")
    a = _make_ws(tmp_path / "a", "pbg_iso_a", "gen_a")
    b = _make_ws(tmp_path / "b", "pbg_iso_b", "gen_b")
    with EnvWorker(a) as wa, EnvWorker(b) as wb:
        ga = wa.call("list_generators")["generators"]
        gb = wb.call("list_generators")["generators"]
        assert "pbg_iso_a.composites.gen_a" in ga
        assert "pbg_iso_b.composites.gen_b" in gb
        assert "pbg_iso_b.composites.gen_b" not in ga   # A never sees B's env
        assert "pbg_iso_a.composites.gen_a" not in gb   # and vice versa


def test_list_generators_tolerates_a_workspace_with_no_package(tmp_path):
    pytest.importorskip("pbg_superpowers")
    (tmp_path / "workspace.yaml").write_text("name: bare\n")
    with EnvWorker(tmp_path) as w:
        assert isinstance(w.call("list_generators")["generators"], list)  # no crash


# ---------------------------------------------------------------------------
# Slice 3: registry_catalog — build_core + introspection, faithful to the
# existing embedded-subprocess path in registry.build_registry.
# ---------------------------------------------------------------------------
_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_registry_catalog_matches_build_registry(monkeypatch):
    """Strong port check: the worker's registry_catalog reproduces
    build_registry's introspection (name/address/kind/source) exactly."""
    pytest.importorskip("pbg_superpowers")
    from vivarium_workbench.lib import registry

    expected = registry.build_registry(_FIXTURE, bypass_cache=True)
    if expected.get("error"):
        pytest.skip(f"build_registry unavailable in this env: {expected['error']}")

    with EnvWorker(_FIXTURE) as w:
        got = w.call("registry_catalog")
    assert not got.get("error"), got.get("error")

    def _core(entries):
        # compare on the introspection fields (ignore workbench post-processing
        # like emitter is_workspace_default that build_registry adds on top).
        return sorted((p["name"], p["address"], p["kind"], p["source"])
                      for p in entries)

    assert _core(got["processes"]) == _core(expected["processes"])
    assert [t["name"] for t in got["types"]] == [t["name"] for t in expected["types"]]


# ---------------------------------------------------------------------------
# Slice: viz_classes — build_core + viz/analysis discovery in the worker,
# a faithful port of visualization_classes.list_visualization_classes.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_viz_classes_discovers_workspace_and_default_classes():
    """The worker builds the fixture core and returns its viz classes as JSON —
    the workspace's own Demo* classes plus the pbg_superpowers defaults."""
    pytest.importorskip("pbg_superpowers")
    with EnvWorker(_FIXTURE) as w:
        classes = w.call("viz_classes")["classes"]
    names = {c["name"] for c in classes}
    # pbg_superpowers defaults are always injected...
    assert {"Distribution", "Heatmap", "ParamVsObservable", "PhaseSpace"} <= names
    # ...alongside the workspace core's own registered viz classes.
    assert any(n.startswith("Demo") for n in names), sorted(names)
    assert all(c["kind"] in ("visualization", "analysis") for c in classes)
    assert all(c["address"].startswith("local:") for c in classes)


@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_viz_classes_lib_entrypoint_routes_through_the_worker():
    """The lib entry point produces the same catalog the worker does — i.e. it is
    now backed by the worker, not an in-process build_core."""
    pytest.importorskip("pbg_superpowers")
    from vivarium_workbench.lib.env_worker_pool import get_pool
    from vivarium_workbench.lib.visualization_classes import list_visualization_classes

    try:
        via_lib = list_visualization_classes(_FIXTURE)
        with EnvWorker(_FIXTURE) as w:
            via_worker = w.call("viz_classes")
    finally:
        get_pool().close_all()
    assert sorted(c["name"] for c in via_lib["classes"]) == \
        sorted(c["name"] for c in via_worker["classes"])


# ---------------------------------------------------------------------------
# Slice: resolve_composite_state — build a @composite_generator in the worker
# (the old sys.executable subprocess), summarized + doc-decorated.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_resolve_composite_state_builds_registered_generator():
    """A registered generator ref builds → {state, module, emitters}; the module
    is the workspace's, proving the workspace package was imported in-worker."""
    pytest.importorskip("pbg_superpowers")
    ref = "pbg_ws_increase_demo.composites.hint_test"
    with EnvWorker(_FIXTURE.resolve()) as w:
        r = w.call("resolve_composite_state", {"ref": ref})
    assert set(r) == {"state", "module", "emitters"}, r
    assert r["module"] == "pbg_ws_increase_demo.composites"
    assert isinstance(r["emitters"], list)


@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_resolve_composite_state_unknown_ref_is_not_registered():
    with EnvWorker(_FIXTURE.resolve()) as w:
        r = w.call("resolve_composite_state", {"ref": "no.such.generator"})
    assert r == {"__not_registered__": True}


@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_build_composite_state_routes_generator_branch_through_worker():
    """End-to-end through the lib entry point: the generator branch returns the
    worker-built state with kind='generator'."""
    pytest.importorskip("pbg_superpowers")
    from vivarium_workbench.lib import composite_state_views as csv
    from vivarium_workbench.lib.env_worker_pool import get_pool

    csv.clear_cache()
    try:
        body, status = csv.build_composite_state(
            _FIXTURE.resolve(), "pbg_ws_increase_demo.composites.hint_test", fresh=True)
    finally:
        get_pool().close_all()
    assert status == 200, body
    assert body["kind"] == "generator"
    assert "state" in body


# ---------------------------------------------------------------------------
# Slice: observables + study_readout_check — build + available_observables +
# validate_readouts, all in-worker (needs the live core + polars).
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_observables_generator_ref_returns_leaves_and_catalogs():
    pytest.importorskip("pbg_superpowers")
    pytest.importorskip("polars")  # available_observables needs it (the `test` extra)
    ref = "pbg_ws_increase_demo.composites.hint_test"
    with EnvWorker(_FIXTURE.resolve()) as w:
        r = w.call("observables", {"ref": ref})
    assert set(r) == {"leaves", "catalogs"}, r
    assert isinstance(r["leaves"], list) and isinstance(r["catalogs"], dict)


@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_observables_unknown_ref_is_not_registered():
    with EnvWorker(_FIXTURE.resolve()) as w:
        r = w.call("observables", {"ref": "no.such.composite"})
    assert r == {"__not_registered__": True}


@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_study_readout_check_validates_against_real_structure():
    """A selector pointing at a path the composite does not expose is flagged
    (never-fabricate), proving validate_readouts ran on the worker-built core."""
    pytest.importorskip("pbg_superpowers")
    pytest.importorskip("polars")
    ref = "pbg_ws_increase_demo.composites.hint_test"
    spec = {"baseline": [{"composite": ref}],
            "readouts": [{"name": "phantom", "selector": "totally.fabricated.path"}]}
    with EnvWorker(_FIXTURE.resolve()) as w:
        r = w.call("study_readout_check", {"ref": ref, "spec": spec})
    assert "readouts" in r, r
    assert {x["name"] for x in r["readouts"]} == {"phantom"}


@pytest.mark.skipif(not _FIXTURE.is_dir(), reason="fixture workspace not present")
def test_build_observables_routes_generator_ref_through_worker():
    """The lib entry point routes a generator ref through the worker and returns a
    well-formed 200. (The worker imports the workspace package itself, so this is
    order-independent — unlike the in-process build_composite_state_for_observables,
    which shares the process-global pbg_superpowers registry. Real-composite
    behavioral parity is covered by tests/test_observables_views_lib.py.)"""
    pytest.importorskip("pbg_superpowers")
    pytest.importorskip("polars")
    from vivarium_workbench.lib import observables_views as ov
    from vivarium_workbench.lib.env_worker_pool import get_pool

    ref = "pbg_ws_increase_demo.composites.hint_test"
    ws = _FIXTURE.resolve()
    ov.clear_cache()
    try:
        body, status = ov.build_observables(ws, ref)  # worker-backed
    finally:
        get_pool().close_all()
    assert status == 200, body
    assert body["ref"] == ref
    assert isinstance(body["leaves"], list) and isinstance(body["catalogs"], dict)
    # hint_test is a trivial generator ({}) → no observable leaves.
    assert body["leaves"] == [] and body["catalogs"] == {}


# ---------------------------------------------------------------------------
# Opt-in real-workspace check: run the worker against a real v2ecoli checkout
# on ITS OWN venv interpreter. Skips unless ../v2ecoli/.venv exists (build it
# with `cd ../v2ecoli && uv sync`). This is the e2e that the minimal fixture
# can't give: a heavy workspace, its own 3.12.12 interpreter, real generators.
# ---------------------------------------------------------------------------
_V2ECOLI = Path(__file__).resolve().parent.parent.parent / "v2ecoli"
_V2ECOLI_VENV = _V2ECOLI / ".venv" / "bin" / "python"


@pytest.mark.skipif(not _V2ECOLI_VENV.is_file(),
                    reason="no ../v2ecoli/.venv (build with `cd ../v2ecoli && uv sync`)")
def test_env_worker_against_real_v2ecoli():
    with EnvWorker(_V2ECOLI, interpreter=str(_V2ECOLI_VENV), timeout=600) as w:
        info = w.call("initialize")
        assert info["python"].startswith("3.12"), info["python"]  # the venv's, not the workbench's

        gens = w.call("list_generators")["generators"]
        assert "v2ecoli.composites.baseline" in gens
        assert len(gens) > 10   # a real, heavy workspace

        cat = w.call("registry_catalog")
        assert not cat.get("error"), cat.get("error")
        assert len(cat["processes"]) > 50 and len(cat["types"]) > 20
        assert "v2ecoli" in cat["workspace_pkgs"]
        assert any(p["source"] == "in_workspace" for p in cat["processes"])

        viz = w.call("viz_classes")["classes"]
        assert any(c["kind"] == "analysis" for c in viz)       # v2ecoli Analysis steps
        assert any(c["kind"] == "visualization" for c in viz)  # + the default viz classes

        # resolve_composite_state: build the real baseline generator. Either it
        # builds (state doc) or raises for an environmental reason (e.g. an
        # absent ParCa cache) — both are valid worker responses, never a crash,
        # and any returned state must be finite JSON (numpy summarized away).
        cs = w.call("resolve_composite_state", {"ref": "v2ecoli.composites.baseline"})
        assert "state" in cs or "__build_error__" in cs, cs
        if "state" in cs:
            import json as _json
            _json.dumps(cs["state"])   # must be serializable (no raw numpy)
            assert cs["module"]


def test_build_registry_serves_v2ecoli_via_its_own_venv():
    """/api/registry (build_registry) serves v2ecoli through its OWN venv
    interpreter — impossible before EnvironmentResolver, since the workbench venv
    has no v2ecoli. Skips unless ../v2ecoli/.venv exists."""
    if not _V2ECOLI_VENV.is_file():
        import pytest as _pytest
        _pytest.skip("no ../v2ecoli/.venv (build with `cd ../v2ecoli && uv sync`)")
    from vivarium_workbench.lib import registry
    d = registry.build_registry(_V2ECOLI, bypass_cache=True)
    assert not d.get("error"), d.get("error")
    assert len(d["processes"]) > 50 and len(d["types"]) > 20
    assert any(p["source"] == "in_workspace" for p in d["processes"])


# ---------------------------------------------------------------------------
# attach_process_docs — decorate a resolved state doc with per-process docstrings
# in the worker (composite-state static-fallback/spec + resolve/report paths).
# ---------------------------------------------------------------------------
def test_attach_process_docs_tolerates_unresolvable_addresses(tmp_path):
    """The method decorates process/step nodes and, for an address that can't be
    imported, leaves the node undecorated — never crashes, structure preserved."""
    doc = {
        "processes": {"p": {"_type": "process", "address": "no.such.module.Cls"}},
        "step": {"_type": "step", "address": "local:also.missing.Cls"},
        "leaf": [1, 2, 3],
        "scalar": 5,
    }
    with EnvWorker(tmp_path) as w:
        r = w.call("attach_process_docs", {"document": doc})
    d = r["document"]
    assert "doc" not in d["processes"]["p"]      # unresolvable → no doc attached
    assert d["leaf"] == [1, 2, 3] and d["scalar"] == 5   # structure preserved


def test_attach_process_docs_via_worker_soft_degrades(monkeypatch):
    """If the worker is unavailable, decoration returns the doc unchanged rather
    than failing the composite-state / resolve / report request."""
    from vivarium_workbench.lib import env_worker_pool, process_docs

    class _Down:
        def call(self, *a, **k):
            raise RuntimeError("worker down")

    monkeypatch.setattr(env_worker_pool, "get_pool", lambda: _Down())
    doc = {"processes": {"p": {"_type": "process", "address": "x.Y"}}}
    assert process_docs.attach_process_docs_via_worker("/ws", doc) is doc
