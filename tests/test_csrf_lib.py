"""Truth-table tests for the pure CSRF predicate (``lib.csrf``).

``is_request_allowed`` is the single source of the same-origin decision shared
by ``server.Handler._csrf_ok`` and the FastAPI middleware.  It is byte-identical
to the legacy inline logic: env-disabled → allow; no Origin → allow; Origin
netloc non-empty AND == Host → allow; else deny.
"""
from __future__ import annotations

import pytest

from vivarium_workbench.lib import csrf


@pytest.mark.parametrize(
    "origin, host, disabled, expected",
    [
        # disabled escape hatch → always allow (even cross-origin).
        ("http://evil.example.com", "127.0.0.1:8080", True, True),
        # no Origin → allow (curl / CLI / same-origin nav).
        (None, "127.0.0.1:8080", False, True),
        ("", "127.0.0.1:8080", False, True),
        # matching origin netloc == host → allow (the SPA path).
        ("http://127.0.0.1:8080", "127.0.0.1:8080", False, True),
        ("https://app.example.com", "app.example.com", False, True),
        # mismatched origin → deny.
        ("http://evil.example.com", "127.0.0.1:8080", False, False),
        # empty netloc (no scheme://host) → deny (netloc non-empty requirement).
        ("not-a-url", "127.0.0.1:8080", False, False),
        ("/relative/path", "127.0.0.1:8080", False, False),
        # host absent + present origin → deny (netloc != "").
        ("http://127.0.0.1:8080", None, False, False),
    ],
)
def test_is_request_allowed_truth_table(origin, host, disabled, expected):
    assert csrf.is_request_allowed(origin, host, disabled=disabled) is expected


def test_is_disabled_via_env():
    assert csrf.is_disabled_via_env({"VIVARIUM_DASHBOARD_DISABLE_CSRF": "1"}) is True
    assert csrf.is_disabled_via_env({"VIVARIUM_DASHBOARD_DISABLE_CSRF": "0"}) is False
    assert csrf.is_disabled_via_env({"VIVARIUM_DASHBOARD_DISABLE_CSRF": "true"}) is False
    assert csrf.is_disabled_via_env({}) is False
