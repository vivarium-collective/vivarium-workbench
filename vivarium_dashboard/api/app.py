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

from vivarium_dashboard.lib.models import (
    DashConfig,
    InvestigationSummary,
    SimRow,
    SimulationsPayload,
)
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

    @app.get("/api/config", response_model=DashConfig)
    def config() -> DashConfig:
        """Client data-source selector (mirrors the stdlib /api/config)."""
        return DashConfig(mode="local-server")

    @app.get("/api/iset-list", response_model=list[InvestigationSummary])
    def iset_list(ws: Path = Depends(get_workspace)) -> list[InvestigationSummary]:
        """Investigations summary list (mirrors the stdlib /api/iset-list).

        Transitional: backed by server.py's HTTP-free `_build_iset_summary_for_test`
        builder. That builder (and its helpers) move into `lib/` in a follow-up;
        the typed pydantic response + OpenAPI schema land now.
        """
        # Imported lazily so the heavy stdlib server module only loads when this
        # route is actually used, keeping import of the FastAPI app light.
        from vivarium_dashboard import server

        return [
            InvestigationSummary.model_validate(d)
            for d in server._build_iset_summary_for_test(ws)
        ]

    return app


app = create_app()
