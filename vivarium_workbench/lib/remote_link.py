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
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from vivarium_workbench.lib.sms_api_client import SmsApiError, sms_api_base

STATE_UNKNOWN = "unknown"
STATE_UP = "up"
STATE_DOWN = "down"


class CircuitOpen(SmsApiError):
    """Raised the instant a call is attempted while the link is known-down.

    A subclass of :class:`SmsApiError` on purpose: callers already wrap sms-api
    calls in ``except SmsApiError`` to degrade gracefully, and this makes that
    same degradation fire in microseconds instead of after the full timeout
    budget. ``status`` is ``None`` (there was no HTTP response — the call never
    left the process).
    """


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


def _fmt(epoch: Optional[float]) -> str:
    if not epoch:
        return "unknown time"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


class RemoteLink:
    """Circuit-breaker state for one sms-api ``base_url``.

    Thread-safe: the background probe, passive marks from request threads, and
    ``check``/``snapshot`` readers all coordinate through one short-held lock.
    """

    PROBE_EVERY = 30.0
    PROBE_TIMEOUT = 3.0
    OPEN_FOR = 60.0

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.state = STATE_UNKNOWN
        self.last_ok: Optional[float] = None      # epoch of last successful call/probe
        self.last_err: Optional[str] = None
        self._down_since: Optional[float] = None  # epoch of the transition into "down"
        self._retry_at: float = 0.0               # monotonic deadline for the next half-open probe
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the background probe thread once (idempotent, daemon)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            t = threading.Thread(target=self._loop, daemon=True, name="remote-link-probe")
            self._thread = t
        t.start()

    def _loop(self) -> None:
        while True:
            try:
                self.probe()
            except Exception:  # noqa: BLE001 - a probe must never kill its own loop
                pass
            time.sleep(self.PROBE_EVERY)

    # -- probing / marking -------------------------------------------------

    def probe(self) -> bool:
        """Actively probe ``GET /version`` and record the outcome. Returns up?.

        Reachability is the tunnel being *alive*, not the endpoint being happy: an
        HTTP response of ANY status — 2xx or 4xx/5xx — means the server answered,
        so the link is up. Only a connection-level failure (URLError/timeout/DNS,
        surfaced as :class:`SmsApiError` with ``status is None``) marks it down.
        Without this, a deployment whose ``/version`` 501s (or a gateway 5xx while
        upstream restarts) would trip the breaker even though the tunnel is fine.
        """
        from vivarium_workbench.lib.sms_api_client import SmsApiClient

        try:
            SmsApiClient(
                self.base_url, timeout=self.PROBE_TIMEOUT, max_retries=1, force_link=True
            ).ping(timeout=self.PROBE_TIMEOUT)
        except SmsApiError as e:
            if e.status is None:
                # Genuine connection-level failure — the tunnel is unreachable.
                self.mark_down(str(e))
                return False
            # The server answered (an HTTP status) — reachable, so mark up.
        self.mark_up()
        return True

    def mark_up(self) -> None:
        """Record a success (from the probe or any passing request)."""
        with self._lock:
            self.state = STATE_UP
            self.last_ok = time.time()
            self.last_err = None
            self._down_since = None

    def mark_down(self, error: str) -> None:
        """Record a connection-level failure and (re)arm the half-open window."""
        with self._lock:
            if self.state != STATE_DOWN:
                self._down_since = time.time()
            self.state = STATE_DOWN
            self.last_err = error
            # Every observed failure pushes the next half-open probe OPEN_FOR out,
            # so a persistently dead tunnel is probed at most once per window
            # rather than on every request.
            self._retry_at = time.monotonic() + self.OPEN_FOR

    # -- the gate ----------------------------------------------------------

    def check(self, *, force: bool = False) -> None:
        """Return immediately if a call may proceed; raise :class:`CircuitOpen` if not.

        ``force=True`` bypasses the breaker entirely — used by the probe itself
        and by user-initiated explicit refreshes, which must be able to re-test a
        link the breaker currently considers open.
        """
        if force:
            return
        with self._lock:
            if self.state != STATE_DOWN:
                return
            due = time.monotonic() >= self._retry_at
            down_since = self._down_since
            last_err = self.last_err
        if due and self.probe():
            return  # half-open probe succeeded — link is back up
        if due:
            # Half-open probe failed; mark_down has re-read/refreshed the state.
            with self._lock:
                last_err = self.last_err
                down_since = self._down_since
        raise CircuitOpen(
            f"sms-api at {self.base_url} unreachable since {_fmt(down_since)}: {last_err}"
        )

    def snapshot(self) -> dict:
        """UI-facing state: ``{state, last_ok, error, base_url, sso_expires_at}``."""
        sso = _sso_expiry()
        with self._lock:
            return {
                "state": self.state,
                "last_ok": self.last_ok,
                "error": self.last_err,
                "base_url": self.base_url,
                "sso_expires_at": sso,
            }


_links: "dict[str, RemoteLink]" = {}
_links_lock = threading.Lock()


def link(base_url: Optional[str] = None) -> RemoteLink:
    """The process-wide :class:`RemoteLink` singleton for ``base_url``.

    ``base_url`` defaults to the configured endpoint (:func:`sms_api_base`). One
    instance per distinct base_url, so every call site checks and updates the
    same breaker state.
    """
    resolved = (base_url or sms_api_base()).rstrip("/")
    with _links_lock:
        lk = _links.get(resolved)
        if lk is None:
            lk = RemoteLink(resolved)
            _links[resolved] = lk
        return lk


def start_probe(base_url: Optional[str] = None) -> RemoteLink:
    """Start (idempotently) the background probe for ``base_url``'s link."""
    lk = link(base_url)
    lk.start()
    return lk
