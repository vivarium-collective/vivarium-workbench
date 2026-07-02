"""Access logging + request-ID middleware for the FastAPI app.

The dashboard previously did no structured logging — errors went to ``print`` and
there was no per-request record, which is the biggest observability gap for a
server that orchestrates multi-minute detached runs. This adds one access line
per request (method, path, status, duration) under the ``vivarium_workbench.access``
logger, and assigns/propagates an ``X-Request-ID`` so a request can be correlated
across logs. It is additive: the only response change is the added header.
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request

logger = logging.getLogger("vivarium_workbench.access")

REQUEST_ID_HEADER = "X-Request-ID"


def install_request_logging(app: FastAPI) -> None:
    """Register the access-log + request-ID middleware on ``app``.

    Call this after any other ``@app.middleware`` so it is the outermost layer and
    observes the final status (including CSRF 403s and exception-handler 500s).
    """

    @app.middleware("http")
    async def _access_log_mw(request: Request, call_next):
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        status = 500  # assume failure until we have a response
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = rid
            return response
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            logger.info(
                "%s %s -> %s (%.1fms)",
                request.method,
                request.url.path,
                status,
                duration_ms,
                extra={
                    "request_id": rid,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": duration_ms,
                },
            )
