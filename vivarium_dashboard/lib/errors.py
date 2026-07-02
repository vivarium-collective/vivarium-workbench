"""Canonical error envelope for the dashboard HTTP API.

Every error response the API emits uses one shape::

    {"error": "<human-readable message>", ...optional extra keys}

This matches the ~157 hand-rolled ``JSONResponse({"error": ...})`` returns
already in ``api/app.py`` and the ``.body.error`` reads in the frontend. The
exception handlers registered in ``api.app.create_app`` normalize the two shapes
that used to escape this convention — FastAPI's request-validation ``{"detail":
[...]}`` (422) and uvicorn's default unhandled-exception 500 — onto it as well.

Routes and ``lib`` functions should ``raise APIError(status, message)`` instead
of hand-building ``JSONResponse({"error": ...}, status_code=...)`` so the
envelope stays in one place.
"""
from __future__ import annotations

from typing import Any


class APIError(Exception):
    """Raise to return the canonical error envelope with a chosen status code.

    ``extra`` keys are merged into the response body alongside ``error`` — e.g.
    ``raise APIError(400, "composite not found", unresolved=[...], ref=name)``
    yields ``{"error": "composite not found", "unresolved": [...], "ref": name}``.
    """

    def __init__(self, status_code: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.extra = extra

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.message}
        body.update(self.extra)
        return body


def error_body(message: str, **extra: Any) -> dict[str, Any]:
    """Build a canonical error body without raising (for routes still returning
    ``JSONResponse`` directly)."""
    body: dict[str, Any] = {"error": message}
    body.update(extra)
    return body
