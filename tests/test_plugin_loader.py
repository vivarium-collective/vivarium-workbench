"""Task 3: loader wiring, namespacing, /api/plugins/manifest, fail-soft."""
from fastapi import APIRouter, FastAPI
from starlette.testclient import TestClient

from vivarium_workbench.plugins import WorkbenchPlugin
from vivarium_workbench.plugins.loader import load_plugins


def _app_with(plugins, ws=None, enabled=("ex",)):
    app = FastAPI()
    load_plugins(app, ws, discovered=plugins,
                 env={"VIVARIUM_WORKBENCH_PLUGINS": ",".join(enabled)})
    return app


def _router(path="/ping"):
    r = APIRouter()
    r.add_api_route(path, lambda: {"ok": True})
    return r


def test_router_mounted_under_namespace_and_manifest():
    r = _router()

    class Ex(WorkbenchPlugin):
        name = "ex"

        def register(self, reg):
            reg.api_router(r)

    c = TestClient(_app_with([Ex()]))
    assert c.get("/api/plugins/ex/ping").json() == {"ok": True}
    assert any(p["name"] == "ex" for p in c.get("/api/plugins/manifest").json())


def test_manifest_empty_when_nothing_enabled():
    c = TestClient(_app_with([], enabled=()))
    assert c.get("/api/plugins/manifest").json() == []


def test_failsoft_one_plugin_raises_others_survive():
    good = _router()

    class Bad(WorkbenchPlugin):
        name = "bad"

        def register(self, reg):
            raise RuntimeError("boom")

    class Good(WorkbenchPlugin):
        name = "good"

        def register(self, reg):
            reg.api_router(good)

    c = TestClient(_app_with([Bad(), Good()], enabled=("bad", "good")))
    assert c.get("/api/plugins/good/ping").status_code == 200
    assert [p["name"] for p in c.get("/api/plugins/manifest").json()] == ["good"]


def test_non_url_safe_name_is_skipped():
    r = _router()

    class Bad(WorkbenchPlugin):
        name = "a/b"  # would escape /api/plugins/<name> if wired raw

        def register(self, reg):
            reg.api_router(r)

    c = TestClient(_app_with([Bad()], enabled=("a/b",)))
    assert c.get("/api/plugins/manifest").json() == []
    assert c.get("/api/plugins/a/b/ping").status_code == 404


def test_static_dir_escaping_package_skips_plugin():
    r = _router()

    class Bad(WorkbenchPlugin):
        name = "esc"

        def register(self, reg):
            reg.api_router(r)
            reg.static_dir("../../../../etc")  # escapes the plugin package dir

    c = TestClient(_app_with([Bad()], enabled=("esc",)))
    assert c.get("/api/plugins/manifest").json() == []


def test_missing_ui_manifest_isolated_api_still_loads():
    r = _router()

    class Ex(WorkbenchPlugin):
        name = "ex"

        def register(self, reg):
            reg.api_router(r)
            reg.ui("static/does-not-exist.json")

    c = TestClient(_app_with([Ex()]))
    assert c.get("/api/plugins/ex/ping").status_code == 200
    entry = next(p for p in c.get("/api/plugins/manifest").json() if p["name"] == "ex")
    assert entry["css"] == [] and entry["js"] == [] and entry["panels"] == []
