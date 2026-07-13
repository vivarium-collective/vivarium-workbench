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


# ---------------------------------------------------------------------------
# Reverse-proxy support: an opt-in ``trust_forwarded`` lets the predicate
# compare Origin against ``X-Forwarded-Host`` instead of the raw (possibly
# proxy-rewritten) ``Host`` — see api/app.py's ``_csrf_mw`` and the
# ``--trust-proxy`` CLI flag. Must never be inferred from the header's mere
# presence: an untrusted direct request could otherwise spoof it to bypass
# the guard.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "origin, host, forwarded_host, trust_forwarded, expected",
    [
        # Trusted + forwarded host matches Origin, even though raw Host (an
        # internal pod address) does not -> allow.
        ("https://app.example.com", "10.0.1.5:8000", "app.example.com", True, True),
        # Same inputs, but NOT trusted (default) -> the forwarded header is
        # ignored entirely; raw Host mismatch -> deny. This is the core
        # security property: opting in is required.
        ("https://app.example.com", "10.0.1.5:8000", "app.example.com", False, False),
        # Trusted, but no forwarded host supplied -> falls back to raw Host,
        # identical to the non-proxied behavior (no regression).
        ("http://127.0.0.1:8080", "127.0.0.1:8080", None, True, True),
        ("http://127.0.0.1:8080", "127.0.0.1:8080", "", True, True),
        # Trusted, forwarded host present but does not match Origin -> deny.
        ("https://app.example.com", "10.0.1.5:8000", "evil.example.com", True, False),
    ],
)
def test_is_request_allowed_trust_forwarded(
    origin, host, forwarded_host, trust_forwarded, expected
):
    assert csrf.is_request_allowed(
        origin, host, disabled=False,
        forwarded_host=forwarded_host, trust_forwarded=trust_forwarded,
    ) is expected


def test_is_trust_proxy_via_env():
    assert csrf.is_trust_proxy_via_env({"VIVARIUM_WORKBENCH_TRUST_PROXY": "1"}) is True
    assert csrf.is_trust_proxy_via_env({"VIVARIUM_WORKBENCH_TRUST_PROXY": "0"}) is False
    assert csrf.is_trust_proxy_via_env({}) is False
    # Dual-reads the deprecated pre-rename prefix.
    assert csrf.is_trust_proxy_via_env({"VIVARIUM_DASHBOARD_TRUST_PROXY": "1"}) is True
