"""A minimal example plugin used by tests to prove the seam end-to-end."""
from fastapi import APIRouter, Body

from vivarium_workbench.plugins import WorkbenchPlugin

router = APIRouter()


@router.get("/ping")
def ping():
    return {"ok": True}


@router.post("/echo")
def echo(body: dict = Body(...)):
    return body


class ExamplePlugin(WorkbenchPlugin):
    name = "example"

    def register(self, reg):
        reg.api_router(router)
        reg.static_dir("static")
        reg.ui("static/plugin.json")
