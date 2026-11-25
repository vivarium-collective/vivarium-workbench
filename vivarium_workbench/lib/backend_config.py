"""The core's generic remote-backend plumbing (docs/backend-adapters.md §2.4, step P1a).

What any remote backend needs and no backend owns: which URL is configured, the one-backend rule, the deployment
name a run records, the caller's identity header, a ``GET /version`` ping, the per-URL circuit breaker and the core
error type. It lived inside the SMS client (``sms_api_client``, ``remote_link``, ``remote_pinned``), which kept the
core (compose-api, run records, the health view) importing SMS code; those modules now keep thin call-time wrappers.

This module sits outside ``lib/backends/`` on purpose: the SMS client imports it at module level, and
``backends/__init__`` imports the SMS adapters, which import the client. It imports only the stdlib, ``env_compat``
and (lazily) ``github_auth`` -- an import-linter contract forbids SMS modules and ``lib.backends``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from vivarium_workbench.lib.env_compat import get_env


#: The env var naming the backend (``serve --backend-base-url`` sets it), then the alias names a backend provider
#: reads (the SMS plugin: ``VIVA_API_BASE``, ``SMS_API_BASE``). The core reads only the first; the alias names are
#: kept here only so the core can name an alias that no provider claims (``backends.config``): server start-up
#: refuses it when no provider is installed, and warns when providers are turned off. The aliases' precedence, their
#: one-backend check and their default URL are the provider's.
BACKEND_BASE_ENV = "VIVARIUM_WORKBENCH_BACKEND_BASE_URL"
BACKEND_BASE_ENV_VARS = (BACKEND_BASE_ENV, "VIVA_API_BASE", "SMS_API_BASE")

#: The ``env_compat`` suffix of the provider's pinned-run switch (``VIVARIUM_WORKBENCH_REMOTE_PINNED``, or its legacy
#: ``VIVARIUM_DASHBOARD_`` spelling), which the SMS plugin reads. The core reads it only to name it, as it does the
#: aliases above, when it is truthy (:data:`PROVIDER_PIN_TRUTHY`) and no provider is active to read it.
PROVIDER_PIN_ENV = "REMOTE_PINNED"
#: The values the provider treats as on (stripped, case-insensitive); ``0`` or an empty value is off.
PROVIDER_PIN_TRUTHY = frozenset({"1", "true", "yes", "on"})


def normalize_backend_base_url(url: str) -> str:
    """Validate a backend base URL: http(s), a host, no credentials, no trailing slash.

    Credentials are refused because the value is forwarded on a child process's
    argv (``serve --detach``) where any local user can read it.
    """
    from urllib.parse import urlsplit

    raw = (url or "").strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"backend base URL must be http(s)://host[:port][/prefix], got {raw!r}")
    if parts.username or parts.password:
        raise ValueError("backend base URL must not embed credentials")
    if parts.query or parts.fragment:
        raise ValueError("backend base URL must not carry a query or fragment")
    return raw.rstrip("/")


class BackendConfigError(ValueError):
    """More than one remote backend is configured for this process (docs/backend-adapters.md step 1c)."""


def canonical_url(url: str) -> str:
    """``normalize_backend_base_url`` plus a lower-cased scheme and host, for comparing two settings."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(normalize_backend_base_url(url))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))


def named_backend(named: "str | None" = None) -> "str | None":
    """The core half of the one-backend rule: the backend named with ``--backend-base-url`` /
    ``VIVARIUM_WORKBENCH_BACKEND_BASE_URL`` (or ``named``, the flag's value before it reaches the environment) must be
    a valid URL. Returns it (as given), or ``None`` when none is named. Raises :class:`BackendConfigError`.

    One workbench process talks to one remote backend (Jim, 2026-11-19): each provider checks its own settings
    against the named URL (``Provider.check_config``); ``backends.config.check_config`` runs both halves, at process
    entry (``cli.main``, ``serve``), never from :func:`configured_base_url`, which must not fail. Reads the
    environment only; no network call.
    """
    named = named or os.environ.get(BACKEND_BASE_ENV)
    if not named:
        return None
    try:
        canonical_url(named)
    except ValueError as e:
        raise BackendConfigError(f"{BACKEND_BASE_ENV}: {e}") from e
    return named


def explicit_backend_base_url() -> "str | None":
    """The backend the operator NAMED for this process (flag / new env), else ``None``.

    Only an explicit name opts a process into running studies on the backend through ``/viva/v1/composites``;
    a provider's own settings (``backends.config.configured_base_url``) do not.
    """
    return os.environ.get(BACKEND_BASE_ENV) or None


def configured_base_url() -> "str | None":
    """The core's half of the configured URL: the named backend (:func:`explicit_backend_base_url`), else ``None``.

    ``backends.config.configured_base_url`` adds the providers' own settings and defaults (this module may not ask
    providers). Reads the environment only: no network call, and it cannot fail.
    """
    return explicit_backend_base_url()


#: The deployment name when ``VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT`` is unset, no backend is named and no provider
#: supplies its own (``Provider.deployment_default``, read by ``backends.config.deployment_name``).
_DEFAULT_DEPLOYMENT = "default"


def deployment_name(default: "str | None" = None) -> str:
    """The deployment namespace a remote run targets (the run's truthful Origin).

    ``VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT`` when set; unset, a backend the operator named (``serve
    --backend-base-url``) is that deployment: its host; else ``default`` (a provider's own default, passed by
    ``backends.config.deployment_name``), else the neutral :data:`_DEFAULT_DEPLOYMENT`.
    """
    from urllib.parse import urlsplit

    named = explicit_backend_base_url()
    fallback = (urlsplit(named).hostname if named else None) or default or _DEFAULT_DEPLOYMENT
    return (get_env("REMOTE_DEPLOYMENT", fallback) or fallback).strip() or fallback


# --- errors and the caller's identity ---------------------------------------------------------------------------

class BackendError(Exception):
    """Raised when a call to a remote backend fails (non-200 or connection error).

    ``sms_api_client.SmsApiError`` is this class (an alias, docs/backend-adapters.md step P1a), so every existing
    ``except SmsApiError`` also catches :class:`CircuitOpen`.

    ``status`` carries the HTTP status code when the failure was an HTTP error
    (e.g. 404 from a deployment that predates an endpoint), else ``None`` for
    connection-level failures — so callers can distinguish "old server" from
    "unreachable" without parsing the message.
    """

    def __init__(self, message: str, status: "int | None" = None) -> None:
        super().__init__(message)
        self.status = status


def _http_error_detail(e: HTTPError, limit: int = 200) -> str:
    """Best-effort extraction of the server's error body for diagnostics.

    Without this, a FastAPI 422/500 with a JSON ``{"detail": ...}`` body reaches
    the operator as only ``"POST <url> -> 422"`` (CD2 pipeline audit §3.12) —
    ``HTTPError.read()`` is never called. Returns a string like
    ``": missing field 'foo'"`` ready to append to the summary message, or
    ``""`` if the body couldn't be read/decoded (never raises — surfacing a
    better error must not itself produce a worse one).

    A gateway/proxy in front of sms-api (the SSM tunnel, an ALB) answers a
    502/503/504 with a full **HTML error page**, not JSON. Dumping that page
    into the message put a wall of ``<html>…`` markup into the user's alert
    (and the logs). So an HTML body is summarised to its ``<title>`` (e.g.
    ``502 Bad Gateway``) rather than echoed, and every body is capped short.
    """
    try:
        raw = e.read()
    except Exception:  # noqa: BLE001 — reading the error body must never mask the real error
        return ""
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace").strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict) and "detail" in parsed:
        detail = parsed["detail"]
        text = detail if isinstance(detail, str) else json.dumps(detail)
    elif text[:1] == "<" or "<html" in text[:256].lower():
        # HTML error page from a proxy/gateway (e.g. a 502/504 while the tunnel
        # or upstream is down) — summarise, never echo the markup.
        m = re.search(r"<title>\s*(.*?)\s*</title>", text, re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        text = title or "gateway error page"
    if len(text) > limit:
        text = text[:limit] + "…"
    return f": {text}" if text else ""


#: Header viva-api reads caller identity from where a deployment names one
#: (its IDENTITY_HEADER setting). Default matches oauth2-proxy's, which is what
#: sms-api-stanford-test is configured for.
IDENTITY_HEADER = "X-Auth-Request-Email"

#: GitHub session sources that identify a PERSON. `token` is deliberately absent:
#: it is `VIVARIUM_WORKBENCH_GH_TOKEN`, a shared machine credential supplied as a
#: k8s Secret, and on a deployed workbench EVERY user resolves to it. Forwarding
#: that would give every user the same identity -- so they could all cancel each
#: other's tasks, while the record claimed a specific owner. That is worse than
#: anonymous: it looks like attribution and provides none.
_PERSONAL_SOURCES = ("device_flow", "gh_cli")


def caller_identity() -> str | None:
    """The signed-in GitHub login, when a PERSON is signed in. Else ``None``.

    NOT authentication, and viva-api's own docs are explicit that its header is
    not either: this is the best attribution the workbench can currently offer,
    which is a real `@login` GitHub already verified, forwarded so a task has an
    owner instead of being unowned and cancellable by anyone.

    The proper answer is the `Principal` that `session_registry.SessionEntry`
    already reserves space for -- a workbench identity that does not depend on a
    user happening to have signed into GitHub for an unrelated reason.

    Never raises. Identity is a nicety on every path that calls it, and an
    unreachable keyring or a slow `gh` must not fail the request it decorates.
    """
    try:
        from vivarium_workbench.lib import github_auth

        session = github_auth.current_session()
    except Exception:  # noqa: BLE001 - see docstring
        return None
    if session is None or session.source not in _PERSONAL_SOURCES:
        return None
    login = (session.login or "").strip()
    # Qualify it: a bare `octocat` next to `you@example.com` in the same column
    # reads as an email that lost its domain. This says where it came from.
    return f"{login}@github" if login else None


def ping(base_url: str, timeout: "float | None" = None) -> str:
    """GET ``/version``: a lightweight reachability probe (the health indicator, the circuit breaker's probe).

    Returns the server's version string; raises :class:`BackendError` -- with the HTTP ``status`` when the server
    answered, ``status=None`` when it could not be reached. ``timeout`` defaults to 5 s so a health check never hangs.
    Same request as ``SmsApiClient.ping`` (which keeps its own copy until step P1b; a test pins that they agree).
    """
    url = base_url.rstrip("/") + "/version"
    headers = {"Accept": "application/json"}
    identity = caller_identity()
    if identity:
        headers[IDENTITY_HEADER] = identity
    req = Request(url, method="GET", headers=headers)
    try:
        with urlopen(req, timeout=timeout or 5.0) as r:  # noqa: S310 — fixed scheme, configured backend
            body = r.read().decode().strip()
    except HTTPError as e:
        raise BackendError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
    except (URLError, OSError) as e:
        raise BackendError(f"GET {url} failed (backend unreachable — is the endpoint up?): {e}") from e
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body
    if isinstance(parsed, dict):
        return str(parsed.get("version") or parsed.get("__version__") or body)
    return str(parsed)


class OpenApiProbe(Protocol):
    """What the core's capability probe (``server_capabilities.backend_profile``) asks of a client: compose-api
    detection. ``Provider.probe_client`` returns one (the SMS provider's ``SmsApiClient``), and so does
    :class:`ProbeClient` with providers off. Typing only."""

    def openapi_operations(self) -> "tuple[str | None, frozenset[str]]": ...


class ProbeClient:
    """compose-api detection without a provider's client (docs/backend-adapters.md §2.2, step P2).

    ``GET /openapi.json``, probe class (one attempt, 3 s), through the per-URL breaker, with the caller-identity
    header: the same request and the same errors as ``SmsApiClient.for_("probe", base).openapi_operations()`` (a
    test pins that they agree), which keeps being used while providers are on.
    """

    def __init__(self, base_url: str, timeout: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _mark(self, up: bool, error: str = "") -> None:
        """Passive breaker bookkeeping, which must never fail the call (as ``SmsApiClient._mark_link_*``)."""
        try:
            if up:
                link(self.base_url).mark_up()
            else:
                link(self.base_url).mark_down(error)
        except Exception:  # noqa: BLE001 - breaker bookkeeping must not fail a real call
            pass

    def openapi_operations(self) -> "tuple[str | None, frozenset[str]]":
        """The server's ``GET /openapi.json``: its ``info.version`` and every ``operationId``. Raises
        :class:`BackendError` (``CircuitOpen`` while the breaker is open; ``status`` set on an HTTP error, ``None``
        when the server could not be reached), or ``ValueError`` on a body that is not JSON."""
        link(self.base_url).check()
        url = self.base_url + "/openapi.json"
        headers = {"Accept": "application/json"}
        identity = caller_identity()
        if identity:
            headers[IDENTITY_HEADER] = identity
        req = Request(url, method="GET", headers=headers)
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — fixed scheme, configured backend
                doc = json.loads(r.read().decode())
        except HTTPError as e:
            raise BackendError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            self._mark(False, str(e))
            raise BackendError(f"GET {url} failed (backend unreachable — is the endpoint up?): {e}") from e
        self._mark(True)
        doc = doc if isinstance(doc, dict) else {}
        info: dict = doc["info"] if isinstance(doc.get("info"), dict) else {}
        paths: dict = doc["paths"] if isinstance(doc.get("paths"), dict) else {}
        operations = frozenset(
            str(op["operationId"])
            for item in paths.values() if isinstance(item, dict)
            for op in item.values() if isinstance(op, dict) and op.get("operationId"))
        return (str(info["version"]) if info.get("version") else None), operations


# --- the per-URL circuit breaker ---------------------------------------------------------------------------------

STATE_UNKNOWN = "unknown"
STATE_UP = "up"
STATE_DOWN = "down"


class CircuitOpen(BackendError):
    """Raised the instant a call is attempted while the link is known-down.

    A subclass of :class:`BackendError` (``SmsApiError``) on purpose: callers already wrap sms-api
    calls in ``except SmsApiError`` to degrade gracefully, and this makes that
    same degradation fire in microseconds instead of after the full timeout
    budget. ``status`` is ``None`` (there was no HTTP response — the call never
    left the process).
    """


def _fmt(epoch: Optional[float]) -> str:
    if not epoch:
        return "unknown time"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds")


class RemoteLink:
    """Circuit-breaker state for one backend ``base_url``.

    Thread-safe: the background probe, passive marks from request threads, and
    ``check`` readers all coordinate through one short-held lock.
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
        surfaced as :class:`BackendError` with ``status is None``) marks it down.
        Without this, a deployment whose ``/version`` 501s (or a gateway 5xx while
        upstream restarts) would trip the breaker even though the tunnel is fine.
        """
        try:
            ping(self.base_url, timeout=self.PROBE_TIMEOUT)
        except BackendError as e:
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
            f"backend at {self.base_url} unreachable since {_fmt(down_since)}: {last_err}"
        )


_links: "dict[str, RemoteLink]" = {}
_links_lock = threading.Lock()


def link(base_url: str) -> RemoteLink:
    """The process-wide :class:`RemoteLink` singleton for ``base_url``.

    One instance per distinct base_url, so every call site checks and updates the
    same breaker state. ``base_url`` is required here; ``remote_link.link`` supplies
    the SMS default.
    """
    resolved = base_url.rstrip("/")
    with _links_lock:
        lk = _links.get(resolved)
        if lk is None:
            lk = RemoteLink(resolved)
            _links[resolved] = lk
        return lk


def start_probe(base_url: str) -> RemoteLink:
    """Start (idempotently) the background probe for ``base_url``'s link."""
    lk = link(base_url)
    lk.start()
    return lk
