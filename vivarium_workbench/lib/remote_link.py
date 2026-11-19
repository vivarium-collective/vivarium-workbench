"""Process-wide reachability state for the remote sms-api (viva-api) endpoint.

Every sms-api call site used to construct its own :class:`SmsApiClient` and
discover a dead SSM tunnel only by timing out — 30 s x 3 retries ~= 91.5 s per
GET. A wedged tunnel (one whose ``session-manager-plugin`` still accepts the
socket but never answers) is indistinguishable from a merely slow one until that
whole budget is gone.

``RemoteLink`` is a small circuit breaker that turns "the tunnel is dead" into a
microsecond failure instead of a minute-and-a-half one:

* a background thread probes ``GET /version`` every :attr:`RemoteLink.PROBE_EVERY`
  seconds with a short :attr:`RemoteLink.PROBE_TIMEOUT`;
* :meth:`RemoteLink.check` raises :class:`CircuitOpen` **immediately** while the
  link is known-down, re-probing once every :attr:`RemoteLink.OPEN_FOR` seconds
  (the classic half-open state);
* ``SmsApiClient._get``/``_post`` also mark the link up on any success and down on
  any connection-level failure, so the breaker stays fresh between probes even if
  the background thread never started (passive detection atop the active probe).

:class:`CircuitOpen` subclasses :class:`SmsApiError`, so every existing
``except SmsApiError`` path degrades exactly as it does today — just fast.

The breaker is a **no-op-safe default**: a link whose state is ``"unknown"`` (the
probe has never run) never blocks a request. Threads are opt-in — nothing here
spawns one until :meth:`RemoteLink.start` (or :func:`start_probe`) is called, so
tests and the CLI stay single-threaded unless they ask otherwise.

Since step P1a (docs/backend-adapters.md) the breaker itself is the core's (:mod:`backend_config`: ``RemoteLink``,
``CircuitOpen``, the per-URL registry). This module keeps what is SMS-specific: the default URL (the configured
SMS endpoint) and the AWS SSO expiry in :func:`snapshot`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from vivarium_workbench.lib import backend_config as _backend_config
from vivarium_workbench.lib.backend_config import STATE_DOWN as STATE_DOWN
from vivarium_workbench.lib.backend_config import STATE_UNKNOWN as STATE_UNKNOWN
from vivarium_workbench.lib.backend_config import STATE_UP as STATE_UP
from vivarium_workbench.lib.backend_config import CircuitOpen as CircuitOpen
from vivarium_workbench.lib.backend_config import RemoteLink as RemoteLink
from vivarium_workbench.lib.backend_config import _links as _links
from vivarium_workbench.lib.backend_config import _links_lock as _links_lock
from vivarium_workbench.lib.sms_api_client import sms_api_base


def _sso_expiry() -> Optional[str]:
    """Best-effort ISO ``expiresAt`` of the newest AWS SSO cache token.

    Read so the UI can warn "SSO expires in N min" *before* the tunnel dies
    underneath a demo. Laptop-only convenience — returns ``None`` on any failure
    (no cache dir on a deployed workbench, unreadable/rotated files, etc.); never
    raises.
    """
    try:
        cache = Path.home() / ".aws" / "sso" / "cache"
        best: Optional[str] = None
        for f in cache.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - a single bad file must not hide the rest
                continue
            exp = data.get("expiresAt") if isinstance(data, dict) else None
            if isinstance(exp, str) and (best is None or exp > best):
                best = exp
        return best
    except Exception:  # noqa: BLE001 - best-effort; see docstring
        return None


def snapshot(lk: RemoteLink) -> dict:
    """UI-facing state of a link: ``{state, last_ok, error, base_url, sso_expires_at}``."""
    sso = _sso_expiry()
    with lk._lock:
        return {
            "state": lk.state,
            "last_ok": lk.last_ok,
            "error": lk.last_err,
            "base_url": lk.base_url,
            "sso_expires_at": sso,
        }


def link(base_url: Optional[str] = None) -> RemoteLink:
    """The process-wide :class:`RemoteLink` for ``base_url`` (default: the configured endpoint, :func:`sms_api_base`).

    The registry is the core's (:func:`backend_config.link`); this only supplies the SMS default."""
    return _backend_config.link(base_url or sms_api_base())


def start_probe(base_url: Optional[str] = None) -> RemoteLink:
    """Start (idempotently) the background probe for ``base_url``'s link (default: the configured endpoint)."""
    lk = link(base_url)
    lk.start()
    return lk
