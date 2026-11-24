"""A plugin can hide specific routes from the in-app AI agent via reg.ai_excluded();
the loader namespaces them, and ai_tools.excluded_paths unions them in."""
import pytest
from fastapi import APIRouter, FastAPI

from vivarium_workbench.plugins import PluginRegistry, WorkbenchPlugin, load_plugins
from vivarium_workbench.plugins.discovery import PLUGINS_ENV


class _Excl(WorkbenchPlugin):
    name = "secretplug"

    def register(self, reg):
        r = APIRouter()

        @r.get("/open")
        def open_():  # pragma: no cover - never called
            return {}

        @r.get("/secret")
        def secret():  # pragma: no cover - never called
            return {}

        reg.api_router(r)
        reg.ai_excluded(["/secret"])


def test_registry_collects_ai_excluded():
    p = _Excl()
    reg = PluginRegistry(p)
    p.register(reg)
    assert reg.contributions().ai_excluded == ["/secret"]


def test_loader_namespaces_plugin_ai_excluded():
    app = FastAPI()
    load_plugins(app, None, discovered=[_Excl()], env={PLUGINS_ENV: "secretplug"})
    assert "/api/plugins/secretplug/secret" in app.state.plugin_ai_excluded
    assert "/api/plugins/secretplug/open" not in app.state.plugin_ai_excluded


def test_excluded_paths_unions_plugin_exclusions():
    pytest.importorskip("pydantic_ai")
    from vivarium_workbench.lib.ai_tools import excluded_paths

    app = FastAPI()
    app.state.plugin_ai_excluded = frozenset({"/api/plugins/secretplug/secret"})
    assert "/api/plugins/secretplug/secret" in excluded_paths(app)
