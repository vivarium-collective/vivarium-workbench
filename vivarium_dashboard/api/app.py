"""FastAPI application — the typed seam for the dashboard's HTTP API.

This is the **seed of a strangler-fig migration**: the dashboard is served today
by a 16.9k-line stdlib ``http.server`` handler (``vivarium_dashboard/server.py``)
with hand-dispatched routes and untyped dict payloads. Rather than rewrite it in
one pass, we stand up a FastAPI app here that serves a small, growing set of
routes with **typed pydantic responses** (so they get automatic validation and
an OpenAPI schema). Routes move over a few at a time; both servers back onto the
same ``lib/`` functions, so there is one implementation, not two.

Run it standalone (does not yet replace the stdlib server):

    VIVARIUM_DASHBOARD_WORKSPACE=/path/to/workspace \\
        uvicorn vivarium_dashboard.api.app:app --reload

Today's routes are read-only and stateless (workspace-backed). Stateful routes
(e.g. remote-run status, which reads the in-memory RemoteRunManager owned by the
stdlib server) move over once the two servers share process state.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI

from vivarium_dashboard.lib.models import SimRow, SimulationsPayload
from vivarium_dashboard.lib.simulations_index import list_simulations

WORKSPACE_ENV = "VIVARIUM_DASHBOARD_WORKSPACE"


def get_workspace() -> Path:
    """Resolve the workspace root (overridable in tests via dependency_overrides)."""
    return Path(os.environ.get(WORKSPACE_ENV, ".")).resolve()


def create_app() -> FastAPI:
    app = FastAPI(
        title="vivarium-dashboard API",
        version="0.1.0",
        summary="Typed seam over the dashboard HTTP API (strangler-fig migration).",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/simulations", response_model=SimulationsPayload)
    def simulations(ws: Path = Depends(get_workspace)) -> SimulationsPayload:
        """Workspace-wide simulations index (mirrors the stdlib /api/simulations).

        `current` (the active branch slug) is computed by the stdlib server today
        and will move here when branch state is shared; until then it is null.
        """
        rows = [SimRow.model_validate(r) for r in list_simulations(ws)]
        return SimulationsPayload(simulations=rows, current=None)

    return app


app = create_app()
