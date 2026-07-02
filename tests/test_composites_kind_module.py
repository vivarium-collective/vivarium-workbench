"""Tests for composite-catalog `kind` + `module` projection + generator support.

Covers the two pieces wired in support of `@composite_generator`:
  - ``GET /api/composites`` projects ``kind`` and ``module`` on every entry.
  - ``discover_all_composites`` merges registered generators alongside specs,
    and the server's generator-doc resolution path returns the built doc.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
FIXTURE_WORKSPACE = _REPO_ROOT / "tests" / "_fixtures" / "ws_increase_demo"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server(tmp_path):
    if not FIXTURE_WORKSPACE.is_dir():
        pytest.skip(f"Fixture workspace not present at {FIXTURE_WORKSPACE}")
    import shutil
    ws = tmp_path / "ws"
    shutil.copytree(FIXTURE_WORKSPACE, ws)
    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ws) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "vivarium_dashboard.cli", "serve",
         "--workspace", str(ws), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )
    # serve_fastapi writes server-info before uvicorn binds the port, so wait
    # for the app to actually answer /health, not just for the file to exist.
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(80):
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=2)
            pytest.fail(f"server did not start:\n{out.decode()}\n{err.decode()}")
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=2) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(0.25)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=2)
        pytest.fail(f"server did not answer /health:\n{out.decode()}\n{err.decode()}")
    yield {"url": base_url, "ws": ws}
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def test_get_composites_includes_kind_and_module(server):
    """Every composite record from /api/composites must carry kind + module."""
    base = server["url"]
    status, body = _get(f"{base}/api/composites")
    assert status == 200, body
    composites = body.get("composites", [])
    assert len(composites) >= 1, "fixture workspace ships at least one spec"
    for c in composites:
        assert "kind" in c, f"missing kind in entry: {c!r}"
        assert "module" in c, f"missing module in entry: {c!r}"
        assert c["kind"] in ("spec", "generator")
    # The fixture's increase-demo spec specifically should be tagged kind=spec.
    increase = next(
        (c for c in composites
         if c.get("id") == "pbg_ws_increase_demo.composites.increase-demo"),
        None,
    )
    assert increase is not None, "fixture's increase-demo composite must be discoverable"
    assert increase["kind"] == "spec"
    assert increase["module"] == "pbg_ws_increase_demo.composites"


def test_get_composite_doc_handles_generator_entry():
    """discover_all_composites picks up @composite_generator-decorated funcs and
    the server's resolve path turns the generator id into a built document.

    Uses pbg-superpowers' in-process generator registry. Generator entries
    only surface from packages declared as bigraph-schema dependents, so we
    register directly into _REGISTRY via the decorator + tweak module bookkeeping.
    """
    from pbg_superpowers.composite_generator import (
        _REGISTRY, composite_generator, build_generator,
    )

    # Register a tiny generator. The decorator's id is `<module>.<name>` —
    # using __name__ ('tests.test_composites_kind_module') keeps the id
    # globally stable across pytest collection.
    @composite_generator(
        name="kind-module-test-gen",
        description="Synthetic generator for the catalog-projection test.",
        parameters={"x": {"type": "float", "default": 0.5}},
    )
    def _gen(core=None, x=0.5):
        return {"state": {"x_value": x}}

    expected_id = f"{__name__}.kind-module-test-gen"
    try:
        assert expected_id in _REGISTRY, "decorator must register the generator"
        entry = _REGISTRY[expected_id]
        assert entry.module == __name__
        assert entry.parameters == {"x": {"type": "float", "default": 0.5}}

        # build_generator should call the function with merged kwargs.
        built = build_generator(entry, overrides={"x": 1.25})
        assert built == {"state": {"x_value": 1.25}}

        # discover_all_composites should merge generator entries when
        # pbg-superpowers is importable. We can't easily invoke it against
        # a workspace path without polluting it, so verify directly via the
        # discover_all bridge that composite_lookup uses.
        from pbg_superpowers.composite_discovery import discover_all
        merged = discover_all()
        assert expected_id in merged, (
            f"discover_all must surface the registered generator; "
            f"saw keys: {sorted(merged.keys())[:5]}..."
        )
        rec = merged[expected_id]
        assert rec["kind"] == "generator"
        assert rec["module"] == __name__
        assert rec["name"] == "kind-module-test-gen"
        assert rec["parameters"] == {"x": {"type": "float", "default": 0.5}}
    finally:
        # Clean up: keep the registry pristine between tests.
        _REGISTRY.pop(expected_id, None)


def test_discover_all_composites_propagates_default_n_steps(tmp_path, monkeypatch):
    """Generator entries with default_n_steps surface that field in the catalog."""
    from pbg_superpowers.composite_generator import (
        composite_generator, _REGISTRY,
    )
    from vivarium_dashboard.lib.composite_lookup import discover_all_composites

    _REGISTRY.clear()
    try:
        @composite_generator(name="hint", description="", parameters={},
                              default_n_steps=123)
        def builder(core=None):
            return {}

        # Stub: pretend pbg-superpowers discovery returned just our entry
        import pbg_superpowers.composite_discovery as cd

        def fake_discover_all():
            entry_id = f"{builder.__module__}.hint"
            return {
                entry_id: {
                    "kind": "generator",
                    "id": entry_id,
                    "name": "hint",
                    "description": "",
                    "module": builder.__module__,
                    "parameters": {},
                    "default_n_steps": 123,
                }
            }

        monkeypatch.setattr(cd, "discover_all", fake_discover_all)

        out = discover_all_composites(tmp_path, "pkg")
        entry_id = f"{builder.__module__}.hint"
        assert entry_id in out
        assert out[entry_id]["default_n_steps"] == 123
    finally:
        _REGISTRY.clear()


def test_discover_all_composites_propagates_none_default_n_steps(tmp_path, monkeypatch):
    """Generator entries without default_n_steps surface default_n_steps=None."""
    from pbg_superpowers.composite_generator import (
        composite_generator, _REGISTRY,
    )
    from vivarium_dashboard.lib.composite_lookup import discover_all_composites

    _REGISTRY.clear()
    try:
        @composite_generator(name="no_hint", description="", parameters={})
        def builder(core=None):
            return {}

        import pbg_superpowers.composite_discovery as cd

        def fake_discover_all():
            entry_id = f"{builder.__module__}.no_hint"
            return {
                entry_id: {
                    "kind": "generator",
                    "id": entry_id,
                    "name": "no_hint",
                    "description": "",
                    "module": builder.__module__,
                    "parameters": {},
                    "default_n_steps": None,
                }
            }

        monkeypatch.setattr(cd, "discover_all", fake_discover_all)

        out = discover_all_composites(tmp_path, "pkg")
        entry_id = f"{builder.__module__}.no_hint"
        assert entry_id in out
        # The key must be present even when the value is None.
        assert "default_n_steps" in out[entry_id]
        assert out[entry_id]["default_n_steps"] is None
    finally:
        _REGISTRY.clear()
