"""The route dependencies live in ``api/deps.py`` so a router module can declare routes without importing
``api/app.py`` (docs/backend-adapters.md §2.2: a backend plugin's ``Provider.router()``).

The router below is declared using only ``vivarium_workbench.api.deps`` and fastapi, as a plugin router would.
"""

import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, Request

from vivarium_workbench.api import deps

router = APIRouter()


@router.get("/api/_deps-probe/workspace")
def _probe_workspace(ws: Path = Depends(deps.get_workspace)) -> dict:
    return {"ws": str(ws)}


@router.post("/api/_deps-probe/session")
def _probe_session(request: Request, ws: Path = Depends(deps.get_workspace)) -> dict:
    return {"ws": str(ws), "session": deps.session_key_of(request)}


def _mount(client) -> None:
    """Include the router into the inproc client's app ahead of the SPA catch-all ``/{rel:path}``, which would
    otherwise answer the GET first (the plugin router is to be mounted the same way)."""
    app = client._tc.app
    before = len(app.router.routes)
    app.include_router(router)
    added = app.router.routes[before:]
    del app.router.routes[before:]
    app.router.routes[0:0] = added


def test_app_reexports_the_same_objects():
    from vivarium_workbench.api import app as appmod

    assert appmod.get_workspace is deps.get_workspace
    assert appmod.get_workspace_context is deps.get_workspace_context
    assert appmod._session_key_of is deps.session_key_of


def test_dependency_override_reaches_a_router_declared_with_deps_only(inproc_client, tmp_path):
    ws = tmp_path / "overridden-ws"
    ws.mkdir()
    client = inproc_client(ws)  # overrides api.app.get_workspace -- the same object as deps.get_workspace
    _mount(client)

    r = client.get("/api/_deps-probe/workspace")
    assert r.status_code == 200, r.text
    assert r.json() == {"ws": str(ws)}

    # A mutating route on the router sits behind the app's middleware and still gets the override; the
    # session middleware stashes a key that deps.session_key_of reads back.
    r = client._tc.post("/api/_deps-probe/session", headers={"X-VW-Session": "probe-session"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ws": str(ws), "session": "probe-session"}

    # inproc_client also sets the process-global root to ``ws``, so the un-overridden path would answer the same.
    # Re-key the override (on api.app's name) to a path only the override can produce.
    from vivarium_workbench.api import app as appmod

    elsewhere = tmp_path / "only-via-override"
    client._tc.app.dependency_overrides[appmod.get_workspace] = lambda: elsewhere
    r = client.get("/api/_deps-probe/workspace")
    assert r.json() == {"ws": str(elsewhere)}


def test_importing_deps_does_not_import_the_app():
    code = (
        "import sys, vivarium_workbench.api.deps as d; "
        "assert 'vivarium_workbench.api.app' not in sys.modules, 'deps imported api.app'; "
        "print(d.get_workspace.__module__)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "vivarium_workbench.api.deps"
