"""Task 1: the WorkbenchPlugin contract + PluginRegistry."""
import pytest


def test_registry_records_contributions():
    from vivarium_workbench.plugins import WorkbenchPlugin, PluginRegistry
    from fastapi import APIRouter

    r = APIRouter()

    class P(WorkbenchPlugin):
        name = "demo"

        def register(self, reg):
            reg.api_router(r)
            reg.static_dir("static")
            reg.ui("static/plugin.json")
            reg.workspace_store("demo")
            reg.skills("skills")

    p = P()
    reg = PluginRegistry(p)
    p.register(reg)
    c = reg.contributions()
    assert c.name == "demo" and r in c.routers
    assert c.static_dir == p.package_dir() / "static"
    assert c.ui_manifest == p.package_dir() / "static/plugin.json"
    assert c.workspace_folder == "demo" and c.skills_dir == p.package_dir() / "skills"


def test_base_register_is_abstract():
    from vivarium_workbench.plugins import WorkbenchPlugin, PluginRegistry

    with pytest.raises(NotImplementedError):
        WorkbenchPlugin().register(PluginRegistry(WorkbenchPlugin()))
