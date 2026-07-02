"""Deprecation shim for the retired stdlib dashboard server.

The dashboard is served by the FastAPI app in ``vivarium_dashboard.api.app``
(run under uvicorn via ``vivarium-dashboard serve``). The old ~9,600-line
``http.server``/``BaseHTTPRequestHandler`` implementation that used to live here
is **gone** — all of its real logic was relocated to ``vivarium_dashboard.lib``
and the dashboard's own tests import from there.

This module is retained ONLY as a thin re-export shim so external repositories
that still ``from vivarium_dashboard.server import ...`` keep working until they
migrate to the ``lib`` paths:

  * v2ecoli, sms-ecoli   -> ``_json_default`` / ``_json_sanitize`` / ``_json_body``
  * pbg-superpowers      -> ``_build_iset_summary_for_test`` /
                            ``_build_iset_detail_for_test`` / ``_observables_for_ref``

Do NOT add new dependencies on this module. Import the ``lib`` homes directly:

  * ``vivarium_dashboard.lib.json_serialize`` — JSON serialization helpers
  * ``vivarium_dashboard.lib.iset_test_shims`` — investigation summary/detail builders
  * ``vivarium_dashboard.lib.observables_views`` — observables payload builder
"""

from vivarium_dashboard.lib.json_serialize import (  # noqa: F401
    _json_default,
    _json_sanitize,
    _json_body,
)
from vivarium_dashboard.lib.iset_test_shims import (  # noqa: F401
    _build_iset_summary_for_test,
    _build_iset_detail_for_test,
)
from vivarium_dashboard.lib.observables_views import _observables_for_ref  # noqa: F401

__all__ = [
    "_json_default",
    "_json_sanitize",
    "_json_body",
    "_build_iset_summary_for_test",
    "_build_iset_detail_for_test",
    "_observables_for_ref",
]
