"""Task 4: app-startup integration + end-to-end example plugin."""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

FIXTURE_PLUGINS = Path(__file__).parent / "_fixtures" / "plugins"


@pytest.fixture
def _example_on_path():
    p = str(FIXTURE_PLUGINS)
    if p not in sys.path:
        sys.path.insert(0, p)
    yield


def test_create_app_loads_plugins(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_PROVIDERS", "none")  # worktree stale-sms bypass
    from vivarium_workbench.api import app as app_mod

    calls = []
    monkeypatch.setattr(app_mod, "_load_plugins", lambda a, ws, **k: calls.append((a, ws)) or [])
    application = app_mod.create_app()
    assert len(calls) == 1 and calls[0][0] is application


def test_example_plugin_end_to_end(_example_on_path, monkeypatch, tmp_path):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PLUGIN_MODULES", "exampleplugin:ExamplePlugin")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PLUGINS", "example")
    from vivarium_workbench.plugins import load_plugins

    app = FastAPI()
    load_plugins(app, tmp_path)  # real discovery via env + enable + wire
    c = TestClient(app)
    assert c.get("/api/plugins/example/ping").json() == {"ok": True}
    assert "example" in [p["name"] for p in c.get("/api/plugins/manifest").json()]
    assert c.get("/plugins/example/example.js").status_code == 200


def test_real_app_serves_enabled_plugin(_example_on_path, monkeypatch, tmp_path):
    """The full app (WITH the SPA catch-all) must actually reach an enabled plugin's
    routes, static mount, and manifest — not 404 behind the catch-all."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_PROVIDERS", "none")  # worktree stale-sms bypass
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PLUGIN_MODULES", "exampleplugin:ExamplePlugin")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PLUGINS", "example")
    from vivarium_workbench.api import app as app_mod
    from vivarium_workbench.lib import _root

    saved = _root.get_workspace_root()
    _root.set_workspace_root(tmp_path)
    try:
        with TestClient(app_mod.create_app(), base_url="http://127.0.0.1") as c:
            assert c.get("/api/plugins/example/ping").json() == {"ok": True}
            assert "example" in [p["name"] for p in c.get("/api/plugins/manifest").json()]
            assert c.get("/plugins/example/example.js").status_code == 200
    finally:
        if saved is not None:
            _root.set_workspace_root(saved)


def test_core_unaffected_when_disabled():
    from vivarium_workbench.plugins import load_plugins

    app = FastAPI()
    load_plugins(app, None, env={})
    c = TestClient(app)
    assert c.get("/api/plugins/manifest").json() == []
    assert c.get("/api/plugins/example/ping").status_code == 404
