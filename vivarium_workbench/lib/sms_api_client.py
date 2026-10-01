"""Thin HTTP client for the sms-api endpoints the remote-run pipeline calls.

Stdlib-only (urllib) to avoid adding a dependency, matching server.py's existing
outbound-HTTP approach. Pure HTTP — no DB, no orchestration. Parameterized by
base_url (the SSM tunnel, default http://localhost:8080).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


#: Env vars naming the backend, highest precedence first. ``serve --backend-base-url``
#: sets the first; the other two are the pre-existing names, kept as aliases.
BACKEND_BASE_ENV_VARS = ("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "VIVA_API_BASE", "SMS_API_BASE")
DEFAULT_BACKEND_BASE = "http://localhost:8080"


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


def backend_configured() -> bool:
    """Whether the operator named a backend at all (flag or any env alias)."""
    return any(os.environ.get(v) for v in BACKEND_BASE_ENV_VARS)


def explicit_backend_base_url() -> "str | None":
    """The backend the operator NAMED for this process (flag / new env), else ``None``.

    Distinct from :func:`sms_api_base`, which also honours the legacy aliases and a
    localhost default: only an explicit name opts a process into running studies on
    the backend through ``/viva/v1/composites``.
    """
    return os.environ.get(BACKEND_BASE_ENV_VARS[0]) or None


def sms_api_base() -> str:
    """Base URL of the backend (viva-api / viva-core; nee sms-api).

    Precedence: ``VIVARIUM_WORKBENCH_BACKEND_BASE_URL`` (what ``serve
    --backend-base-url`` sets) > ``VIVA_API_BASE`` > ``SMS_API_BASE`` (the
    legacy alias, since the backend repo was renamed sms-api -> viva-api) >
    ``http://localhost:8080`` (the SSM tunnel). The single source of truth for
    this lookup — ``workspace_deps_views`` and ``remote_simulations`` re-export
    it under their old ``_sms_api_base`` name.
    """
    for name in BACKEND_BASE_ENV_VARS:
        value = os.environ.get(name)
        if value:
            return value
    return DEFAULT_BACKEND_BASE


class SmsApiError(Exception):
    """Raised when an sms-api call fails (non-200 or connection error).

    ``status`` carries the HTTP status code when the failure was an HTTP error
    (e.g. 404 from a deployment that predates an endpoint), else ``None`` for
    connection-level failures — so callers can distinguish "old server" from
    "unreachable" without parsing the message.
    """

    def __init__(self, message: str, status: "int | None" = None) -> None:
        super().__init__(message)
        self.status = status


#: Multi-GB native-store / results.zip / workspace-tarball downloads must not run
#: on the same 30s default used for small JSON status calls (CD2 pipeline audit
#: §3.12) — a slow SSM tunnel pulling a multi-GB store easily exceeds that.
#: Callers can still pass an explicit ``timeout=`` per call.
DOWNLOAD_TIMEOUT = 1800.0  # 30 minutes

#: Bounded retry policy for idempotent GET/status calls ONLY. Never applied to
#: POST (a retried `run_simulation`/`compose_submit`/`register_simulator` could
#: double-submit a run) or DELETE. Exponential backoff between attempts:
#: ``backoff * 2**attempt``.
_GET_RETRIES = 3
_RETRY_BACKOFF = 0.5

#: e3 — timeouts and retry budgets by call class. A probe must fail fast (a
#: wedged tunnel usually fails the first byte); a status poll is latency-
#: sensitive; a list can be large; a download runs on ``DOWNLOAD_TIMEOUT``.
#: ``SmsApiClient.for_(kind)`` builds a client wired to the right policy so call
#: sites stop hand-rolling ``timeout=``/``max_retries=`` themselves.
_CALL_CLASS_POLICY: "dict[str, tuple[float, int]]" = {
    "probe": (3.0, 1),
    "status": (5.0, 2),
    "list": (15.0, 2),
    "download": (DOWNLOAD_TIMEOUT, 1),
}


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


#: Capability names the path resolver keys on (viva-api's ``viva_core.api.capabilities``;
#: the strings are the stable public API). Membership only, never a version (D14).
#:
#: ``viva-v1-surface``: core's routers answer under ``/viva/v1`` -- for this client,
#: ``/viva/v1/compose`` (the interim compose spelling, dated M5).
CAPABILITY_VIVA_V1_SURFACE = "viva-v1-surface"
#: ``viva-v1-workers``: the env-worker surface at its final spelling, ``/viva/v1/workers``.
CAPABILITY_VIVA_V1_WORKERS = "viva-v1-workers"

#: How long one client trusts a capability advertisement before asking again. Most
#: clients live for one request; the env-worker launchers hold one for the life of
#: the process, and a redeployed viva-api should not need a workbench restart.
_CAPABILITY_TTL = 600.0

_COMPOSE_PREFIX = "/compose/v1"
_ENV_WORKER_PREFIX = "/env-worker/v1"


def _workers_path(rest: str) -> str:
    """An ``/env-worker/v1`` path (minus that prefix) at its ``/viva/v1/workers`` spelling.

    Mirrors viva-api's ``viva_core/api/routers/workers.py::PATHS``: the dial-back
    workers ARE the family (``/workers[/{job}]`` -> ``/viva/v1/workers[/{job}]``); the
    relay's lifecycle sits under ``/workers/relay``; everything asked OF a held worker
    (``/call`` and the named reads) nests under the worker; the task tier is
    ``/workers/tasks``. Returns ``None`` for a path that table does not cover, so an
    unmapped route keeps its legacy spelling rather than being guessed at.
    """
    m = re.fullmatch(r"/relay/workers/([^/]+)/(.+)", rest)
    if m:
        return f"/viva/v1/workers/{m.group(1)}/{m.group(2)}"
    m = re.fullmatch(r"/relay/workers(/[^/]+)?", rest)
    if m:
        return "/viva/v1/workers/relay" + (m.group(1) or "")
    m = re.fullmatch(r"/workers(/[^/]+)?", rest)
    if m:
        return "/viva/v1/workers" + (m.group(1) or "")
    if rest == "/tasks" or rest.startswith("/tasks/"):
        return "/viva/v1/workers" + rest
    return None


def resolve_path(logical: str, capabilities: "frozenset[str] | set[str]") -> str:
    """Where ``logical`` (a path at its legacy spelling) answers on a server advertising ``capabilities``.

    The one place a viva-api path changes spelling (vivarium-workbench#1150, W1). A
    path moves only when the server says, by capability membership, that the new
    spelling is served; otherwise it is returned unchanged -- the legacy spelling,
    which viva-api keeps answering until every caller has shipped a release like
    this one (D14). Paths with no successor (``/api/v1/*``, ``/core/v1/*``,
    ``/version``) pass through untouched.
    """
    if logical.startswith(_COMPOSE_PREFIX + "/") and CAPABILITY_VIVA_V1_SURFACE in capabilities:
        return "/viva/v1/compose" + logical[len(_COMPOSE_PREFIX):]
    if logical.startswith(_ENV_WORKER_PREFIX + "/") and CAPABILITY_VIVA_V1_WORKERS in capabilities:
        moved = _workers_path(logical[len(_ENV_WORKER_PREFIX):])
        if moved is not None:
            return moved
    return logical


def _has_successor(logical: str) -> bool:
    """Whether ``logical`` could move at all -- so a path that cannot never costs a capability probe."""
    return logical.startswith((_COMPOSE_PREFIX + "/", _ENV_WORKER_PREFIX + "/"))


#: ``viva-v1-environments``: ``GET /viva/v1/environments[/{id}]`` answers (W2).
CAPABILITY_VIVA_V1_ENVIRONMENTS = "viva-v1-environments"
#: ``viva-v1-environments-build``: ``POST /viva/v1/environments`` can select or build (W2).
#: Its own name: a deployment may serve the reads without a build path.
CAPABILITY_VIVA_V1_ENVIRONMENTS_BUILD = "viva-v1-environments-build"
#: ``viva-v1-environments-filters``: ``GET /viva/v1/environments`` HONOURS
#: ``?repo_url=&branch=`` (the pair; exact match on the linked simulator's
#: ``git_branch``) and ``?legacy_simulator_id=``. Its own name because a server
#: without it silently ignores those parameters and answers the UNFILTERED list,
#: so a branch lookup may trust the filter only when this is advertised.
CAPABILITY_VIVA_V1_ENVIRONMENTS_FILTERS = "viva-v1-environments-filters"

#: ``viva-v1-composites``: ``POST/GET/DELETE /viva/v1/composites[/{id}[/status|progress|jobs|...]]``
#: answer -- the generic run surface (an environment + a composite id with params, or
#: a document), not one simulator's.
CAPABILITY_VIVA_V1_COMPOSITES = "viva-v1-composites"
#: ``viva-v1-composites-documents``: that surface also runs a process-bigraph ``document``.
CAPABILITY_VIVA_V1_COMPOSITES_DOCUMENTS = "viva-v1-composites-documents"

_COMPOSITES = "/viva/v1/composites"


def _run_path(run_id: "str | int", leaf: str = "") -> str:
    """``/viva/v1/composites/{id}[/leaf]`` -- the id is opaque, so it is quoted whole."""
    return f"{_COMPOSITES}/{quote(str(run_id), safe='')}" + (f"/{leaf}" if leaf else "")


#: Environment statuses (``/viva/v1/environments``). A build is ready only when
#: EVERY variant row of it is (a vEcoli build has three: ``arm64``, ``amd64``,
#: ``amd64-submit``).
_ENV_READY = "ready"
_ENV_FAILED = "failed"
_ENV_BUILDING = "building"
_ENV_PENDING = "pending"

#: One page of ``GET /viva/v1/environments`` (the server's maximum), and how many
#: pages a full listing may take before it stops (a runaway guard, not a limit
#: anyone should reach: dev held 286 rows on 2026-09-27).
_ENV_PAGE = 200
_ENV_MAX_PAGES = 25

#: Environment ids a POST on THIS process returned, by (base_url, simulator id), so
#: a status poll asks for exactly those rows (``GET /viva/v1/environments/{id}``).
#: Bounded; a miss just means the status poll finds the rows by listing instead.
_ENV_IDS: "dict[tuple[str, int], list[str]]" = {}
_ENV_IDS_MAX = 512


def _remember_environment_ids(base_url: str, simulator_id: int, ids: "list[str]") -> None:
    if not ids:
        return
    if len(_ENV_IDS) >= _ENV_IDS_MAX:
        _ENV_IDS.pop(next(iter(_ENV_IDS)))
    _ENV_IDS[(base_url, simulator_id)] = list(ids)


def environments_as_simulators(rows: "list[dict]") -> "list[dict]":
    """``/viva/v1/environments`` rows in the ``/core/v1/simulator/versions`` shape.

    The one conversion W2 rests on (vivarium-workbench#1150, the W2 brief):
    everything downstream -- ``_build_id``, ``_scope_build_ids``,
    ``simulator_commit``, the build dropdown -- reads ``database_id``, and an
    environment record has none. Left unconverted, ``_build_id`` would return
    the record's own ``id``: the ENVIRONMENT id, which passed to
    ``run_simulation`` names the wrong simulator. So each row becomes the
    legacy record it belongs to:

    ``database_id = legacy_simulator_id``, ``git_repo_url = repo_url``,
    ``git_commit_hash = commit``, ``created_at``, ``temporary``, ``label``.

    A row with a null ``legacy_simulator_id`` is skipped (nothing in the
    workbench can address it). Rows are grouped by simulator -- one entry per
    build, in first-seen order (the listing is newest first) -- and each entry
    carries its rows' ``environment_ids``. There is NO ``git_branch``: an
    environment stores none, which is why branch lookups do not use this.
    """
    out: "dict[int, dict]" = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        lid = row.get("legacy_simulator_id")
        if lid is None:
            continue
        lid = int(lid)
        entry = out.get(lid)
        if entry is None:
            entry = out[lid] = {
                "database_id": lid,
                "git_repo_url": row.get("repo_url"),
                "git_commit_hash": row.get("commit"),
                "created_at": row.get("created_at"),
                "temporary": bool(row.get("temporary")),
                "label": row.get("label"),
                "environment_ids": [],
            }
        if row.get("id") is not None:
            entry["environment_ids"].append(str(row["id"]))
    return list(out.values())


def environment_build_status(rows: "list[dict]") -> str:
    """One build's status from its variant rows: ``failed`` if any failed,
    ``ready`` only if all are ready, ``building`` if any is, else ``pending``."""
    statuses = {str(r.get("status") or "").lower() for r in rows if isinstance(r, dict)}
    if _ENV_FAILED in statuses:
        return _ENV_FAILED
    if statuses and statuses == {_ENV_READY}:
        return _ENV_READY
    if _ENV_BUILDING in statuses or _ENV_READY in statuses:
        return _ENV_BUILDING
    return _ENV_PENDING


def repo_key(url: str) -> str:
    """A lenient repository key, ``org/repo`` lower-cased: no scheme, host,
    ``.git`` or trailing slash; ``git@host:org/repo`` and the ``org/repo``
    shorthand give the same key. Used to find the EXACT spellings the server
    registered (its ``repo_url`` filter is an exact match) -- the callers then
    apply their own matching rule to what comes back, exactly as before."""
    u = (url or "").strip().rstrip("/")
    if u.lower().endswith(".git"):
        u = u[: -len(".git")]
    u = u.lower()
    if "://" in u:
        u = u.split("://", 1)[1]
        u = u.split("/", 1)[1] if "/" in u else ""
    elif "@" in u and ":" in u:
        u = u.split(":", 1)[1]
    parts = [p for p in u.split("/") if p]
    return "/".join(parts[-2:])


class BranchHeadUnresolved(SmsApiError):
    """The server could not say which commit a branch's head is."""


def register_branch_head_legacy(client: "Any", repo_url: str, branch: str) -> dict:
    """The pre-W2 head register: ``latest_simulator`` for the head's commit, then
    ``register_simulator``. Module-level so a stand-in client can reuse it."""
    latest = client.latest_simulator(repo_url, branch)
    commit = latest.get("git_commit_hash") or ""
    if not commit:
        raise BranchHeadUnresolved("could not resolve branch HEAD via sms-api")
    reg = dict(client.register_simulator(repo_url, branch, commit) or {})
    reg["git_commit_hash"] = commit
    return reg


class SmsApiClient:
    def __init__(self, base_url: str = "http://localhost:8080", timeout: float = 30.0,
                 max_retries: int = _GET_RETRIES, *, force_link: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Default retry budget for idempotent GET/status calls made through this
        # client. Latency-sensitive callers (the SWR "fresh" path on
        # /api/simulations) construct a client with a short timeout and
        # ``max_retries=1`` so a wedged tunnel can't pin the request thread for
        # ``timeout * _GET_RETRIES`` seconds. Never consulted by _post/_delete.
        self.max_retries = max_retries
        # When True, this client bypasses the RemoteLink circuit breaker
        # (``check(force=True)``) — used by the breaker's own probe and by
        # user-initiated explicit refreshes, which must be able to re-test a link
        # the breaker currently holds open. Normal calls leave it False so a
        # known-down tunnel fails in microseconds instead of the full timeout.
        self.force_link = force_link
        # The server's capability advertisement, fetched on the first call whose
        # path could move (see ``_path``) and kept for ``_CAPABILITY_TTL``.
        self._caps: "frozenset[str] | None" = None
        self._caps_at = 0.0

    @classmethod
    def for_(cls, kind: str, base_url: str | None = None, *, force_link: bool = False) -> "SmsApiClient":
        """Build a client wired to the timeout/retry policy for a call *class*.

        ``kind`` is one of ``"probe"``, ``"status"``, ``"list"``, ``"download"``
        (see :data:`_CALL_CLASS_POLICY`). ``base_url`` defaults to the configured
        endpoint (:func:`sms_api_base`).
        """
        try:
            timeout, retries = _CALL_CLASS_POLICY[kind]
        except KeyError:
            raise ValueError(
                f"unknown call class {kind!r}; expected one of {sorted(_CALL_CLASS_POLICY)}"
            ) from None
        return cls(base_url or sms_api_base(), timeout=timeout,
                   max_retries=retries, force_link=force_link)

    def _link(self) -> Any:
        """The RemoteLink circuit breaker for this client's base_url (lazy import
        to avoid an import cycle: remote_link imports this module)."""
        from vivarium_workbench.lib.remote_link import link

        return link(self.base_url)

    def _mark_link_up(self) -> None:
        """Passive success signal to the breaker — never let bookkeeping raise."""
        try:
            self._link().mark_up()
        except Exception:  # noqa: BLE001 - breaker bookkeeping must not fail a real call
            pass

    def _mark_link_down(self, error: str) -> None:
        """Passive connection-failure signal to the breaker (never raises)."""
        try:
            self._link().mark_down(error)
        except Exception:  # noqa: BLE001 - see _mark_link_up
            pass

    def _server_capabilities(self) -> "frozenset[str]":
        """This deployment's capability names, fetched once per client (cached for
        ``_CAPABILITY_TTL``) through ``server_capabilities.fetch_capabilities``.

        Never raises. A server that predates the endpoint (404) advertises nothing,
        and that answer is cached. Any other failure -- unreachable, 5xx, a
        malformed body -- is NOT cached and reads as "nothing advertised" for this
        one call: every path then keeps its legacy spelling, which still answers,
        and the real request that follows surfaces the real error. Capability
        detection must never be the thing that fails a call.
        """
        now = time.monotonic()
        if self._caps is not None and now - self._caps_at < _CAPABILITY_TTL:
            return self._caps
        # lazy: server_capabilities imports this module
        from vivarium_workbench.lib.server_capabilities import fetch_capabilities

        # A probe-class budget: one attempt, short timeout, so a wedged tunnel
        # costs one fast failure here rather than a retried one.
        probe = SmsApiClient(self.base_url, timeout=min(self.timeout, 5.0),
                             max_retries=1, force_link=self.force_link)
        # The probe's own connection failure must not trip the RemoteLink
        # breaker: the real request that follows gets its full attempt (and its
        # own retries), and is what reports -- and records -- an outage.
        probe._mark_link_down = lambda error: None  # type: ignore[method-assign]
        try:
            caps = frozenset(fetch_capabilities(probe).capabilities)
        except Exception:  # noqa: BLE001 - see docstring: fall back, never fail the call
            return frozenset()
        self._caps, self._caps_at = caps, now
        return caps

    def _path(self, logical: str) -> str:
        """The path to request for ``logical`` (its legacy spelling) on THIS server.

        Every request this client makes goes through here -- ``_get``, ``_post``,
        ``_delete`` and the streaming helpers -- so a spelling change is one table
        (:func:`resolve_path`), never a per-method edit. A path with no successor
        is returned without asking the server anything.
        """
        if not _has_successor(logical):
            return logical
        return resolve_path(logical, self._server_capabilities())

    def _headers(self, accept: str = "application/json") -> dict[str, str]:
        """Request headers, carrying the caller's identity when there is one.

        Sent on every request rather than only on task submits: viva-api ignores
        an unrecognised header, and a client that identified itself for some
        calls and not others would be harder to reason about than one that
        always does.
        """
        headers = {"Accept": accept}
        identity = caller_identity()
        if identity:
            headers[IDENTITY_HEADER] = identity
        return headers

    def _get(
        self,
        path: str,
        params: dict | None = None,
        *,
        retries: int | None = None,
        backoff: float = _RETRY_BACKOFF,
    ) -> dict:
        """GET a JSON endpoint, retrying transient failures.

        GET is idempotent (unlike ``_post``), so a bounded exponential-backoff
        retry is safe here: connection errors/timeouts and 5xx responses are
        retried up to ``retries`` attempts total; a 4xx is a client error, not a
        transient one, and is raised immediately without retrying.
        """
        if retries is None:
            retries = self.max_retries
        # Fail fast when the tunnel is known-down (raises CircuitOpen, itself an
        # SmsApiError). force_link bypasses it for the probe / explicit refresh.
        self._link().check(force=self.force_link)
        url = self.base_url + self._path(path)
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        req = Request(url, method="GET", headers=self._headers())
        attempt = 0
        while True:
            attempt += 1
            try:
                with urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — fixed scheme, internal tunnel
                    payload = json.loads(r.read().decode())
                self._mark_link_up()
                return payload
            except HTTPError as e:
                # The server answered — the tunnel is alive; do not trip the
                # breaker on an HTTP status (a 4xx/5xx is not a link failure).
                if e.code >= 500 and attempt < retries:
                    time.sleep(backoff * (2 ** (attempt - 1)))
                    continue
                raise SmsApiError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
            except (URLError, OSError) as e:
                if attempt < retries:
                    time.sleep(backoff * (2 ** (attempt - 1)))
                    continue
                self._mark_link_down(str(e))
                raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e

    def latest_simulator(self, repo_url: str, branch: str) -> dict:
        """GET /core/v1/simulator/latest -- legacy only; see :meth:`register_branch_head`."""
        return self._get("/core/v1/simulator/latest", {"git_branch": branch, "git_repo_url": repo_url})

    def register_simulator(self, repo_url: str, branch: str, commit: str) -> dict:
        """Register a repo@commit build (async image build). See :meth:`upload_simulator`."""
        return self.upload_simulator({
            "git_repo_url": repo_url, "git_branch": branch, "git_commit_hash": commit,
        })

    def register_branch_head(self, repo_url: str, branch: str) -> dict:
        """Register the build at ``branch``'s current head; returns the legacy shape
        (``database_id``, ``git_commit_hash`` = the commit the head resolved to).

        With ``viva-v1-environments-build`` this is ONE call --
        ``POST /viva/v1/environments {repo_url, branch}`` with no ``commit``: the
        server resolves the head and says which commit it used. Without it, the
        legacy pair: ``GET /core/v1/simulator/latest`` for the head, then the
        upload. Raises :class:`SmsApiError` when the head does not resolve.
        """
        if CAPABILITY_VIVA_V1_ENVIRONMENTS_BUILD in self._server_capabilities():
            created = self._create_environment(repo_url=repo_url, branch=branch, commit=None, force=False)
            if created is not None:
                return created
        return register_branch_head_legacy(self, repo_url, branch)

    def _create_environment(self, *, repo_url: str, branch: "str | None", commit: "str | None",
                            force: bool) -> "dict | None":
        """``POST /viva/v1/environments`` (select-or-build), answered in the legacy
        ``SimulatorVersion`` shape the callers read. ``None`` when the answer names
        no simulator (``legacy_simulator_id`` null -- e.g. ready rows selected that
        were never linked to one): the caller then takes the legacy route, which
        the server serves through the same builder (viva-api P5-8)."""
        body: dict = {"repo_url": repo_url, "force": bool(force)}
        if commit:
            body["commit"] = commit
        if branch:
            body["branch"] = branch
        resp = self._post("/viva/v1/environments", json_body=body)
        lid = resp.get("legacy_simulator_id")
        if lid is None:
            return None
        rows = [r for r in (resp.get("environments") or []) if isinstance(r, dict)]
        env_ids = [str(r["id"]) for r in rows if r.get("id") is not None]
        _remember_environment_ids(self.base_url, int(lid), env_ids)
        return {
            "database_id": int(lid),
            "git_repo_url": repo_url,
            "git_commit_hash": resp.get("commit") or commit,
            "git_branch": branch,
            "selected": bool(resp.get("selected")),
            "environment_ids": env_ids,
        }

    # -- env workers (REFACTOR-PLAN §2A.8, #942) ----------------------------
    # The workbench cannot create Jobs (§2B.2 gives it no cluster access), so it
    # asks viva-api to run a simulator image as a worker. We tell it where to
    # dial back and with what token — we already know our own address, so
    # viva-api needs to discover nothing.

    def start_env_worker(self, *, commit: str, callback_host: str, callback_port: int,
                         token: str, workspace: str | None = None,
                         session_key: str | None = None) -> dict:
        """POST /env-worker/v1/workers — run the prebuilt image for ``commit``."""
        body: dict = {
            "commit": commit,
            "callback_host": callback_host,
            "callback_port": callback_port,
            "token": token,
        }
        if workspace:
            body["workspace"] = workspace
        if session_key:
            body["session_key"] = session_key
        return self._post("/env-worker/v1/workers", json_body=body)

    def env_worker_status(self, job_name: str, *, include_logs: bool = False) -> dict:
        return self._get(f"/env-worker/v1/workers/{job_name}",
                         {"include_logs": "true"} if include_logs else None)

    def stop_env_worker(self, job_name: str) -> dict:
        """DELETE /env-worker/v1/workers/{job_name} — idempotent."""
        return self._delete(f"/env-worker/v1/workers/{job_name}")

    # -- relay (plan §C) ----------------------------------------------------
    #
    # The three above run the IN-CLUSTER shape: we tell viva-api where to dial
    # back, because we can be dialled. A laptop cannot — its SSM tunnel is
    # laptop-initiated with no inbound path — so these hand the socket to
    # viva-api instead and reach the worker over HTTP.

    def start_relayed_env_worker(self, *, commit: str, workspace: str | None = None,
                                 session_key: str | None = None,
                                 accept_timeout: float | None = None) -> dict:
        """POST /env-worker/v1/relay/workers — viva-api holds the connection.

        Note what is ABSENT versus ``start_env_worker``: no callback host, port
        or token. viva-api binds its own listener and mints its own token, which
        is the whole point — we have no address a worker could dial.
        """
        body: dict = {"commit": commit}
        if workspace:
            body["workspace"] = workspace
        if session_key:
            body["session_key"] = session_key
        if accept_timeout is not None:
            body["accept_timeout"] = accept_timeout
        return self._post("/env-worker/v1/relay/workers", json_body=body)

    def call_relayed_env_worker(self, job_name: str, *, method: str,
                                params: dict | None = None,
                                timeout: float | None = None) -> dict:
        """POST /env-worker/v1/relay/workers/{job}/call — one JSON-RPC call."""
        body: dict = {"method": method, "params": params or {}}
        if timeout is not None:
            body["timeout"] = timeout
        return self._post(f"/env-worker/v1/relay/workers/{job_name}/call", json_body=body)

    def stop_relayed_env_worker(self, job_name: str) -> dict:
        """DELETE /env-worker/v1/relay/workers/{job_name} — idempotent."""
        return self._delete(f"/env-worker/v1/relay/workers/{job_name}")

    # -- the task tier (plan §E option (e)) ---------------------------------
    #
    # For calls that cannot be a synchronous HTTP request. `run_study` runs a
    # study's baseline and every variant to completion; holding a socket open
    # for that is what produced the double-run bug, because the socket timeout
    # fired and the pool re-ran the whole study.

    def submit_env_worker_task(self, job_name: str, *, method: str,
                               params: dict | None = None) -> dict:
        """POST /env-worker/v1/tasks — 202 with a task_id; the row exists first."""
        return self._post("/env-worker/v1/tasks", json_body={
            "job_name": job_name, "method": method, "params": params or {},
        })

    def get_env_worker_task(self, task_id: int) -> dict:
        return self._get(f"/env-worker/v1/tasks/{task_id}")

    def simulator_status(self, simulator_id: int) -> dict:
        """A simulator build's status, in the legacy ``HpcRun``-ish shape
        (``status``, ``error_message``).

        With ``viva-v1-environments``: the build's environment rows -- by the ids
        this process's own POST returned (``GET /viva/v1/environments/{id}``), else
        by listing ``?legacy_simulator_id=`` -- aggregated across every variant
        (:func:`environment_build_status`: ``ready`` only when all are). The
        listing is re-checked client-side: a server that predates that filter
        ignores it and answers the unfiltered list, so only rows that really
        carry this id count. No rows (a SLURM build has none; an old server's
        unfiltered page may miss an old build) -> the legacy call. An environment
        carries no error text, so a ``failed`` build asks the legacy status for
        its ``error_message``, best effort, while that route lasts.
        """
        sid = int(simulator_id)
        if CAPABILITY_VIVA_V1_ENVIRONMENTS in self._server_capabilities():
            rows = self._environment_rows_for(sid)
            if rows:
                status = environment_build_status(rows)
                out: dict = {"status": status, "error_message": None, "database_id": sid,
                             "environments": rows}
                if status == _ENV_FAILED:
                    try:
                        legacy = self._get("/core/v1/simulator/status", {"simulator_id": sid})
                        out["error_message"] = legacy.get("error_message")
                    except SmsApiError:
                        pass
                return out
        return self._get("/core/v1/simulator/status", {"simulator_id": sid})

    def _environment_rows_for(self, simulator_id: int) -> "list[dict]":
        known = _ENV_IDS.get((self.base_url, simulator_id))
        if known:
            return [self._get(f"/viva/v1/environments/{eid}") for eid in known]
        page = self._get("/viva/v1/environments", {
            "legacy_simulator_id": simulator_id, "temporary": "any", "limit": _ENV_PAGE,
        })
        rows = [r for r in (page.get("environments") or [])
                if isinstance(r, dict) and r.get("legacy_simulator_id") == simulator_id]
        return rows

    def _environment_pages(self, params: dict) -> "list[dict]":
        """Every row of ``GET /viva/v1/environments`` for ``params``, page by page."""
        rows: "list[dict]" = []
        offset: "int | None" = 0
        for _ in range(_ENV_MAX_PAGES):
            if offset is None:
                break
            page = self._get("/viva/v1/environments", {**params, "limit": _ENV_PAGE, "offset": offset})
            rows.extend(r for r in (page.get("environments") or []) if isinstance(r, dict))
            nxt = page.get("next_offset")
            offset = int(nxt) if nxt is not None else None
        return rows

    def list_branch_builds(self, repo_url: str, branch: str) -> dict:
        """The builds registered for ``repo_url``@``branch``, as
        ``{"versions": [SimulatorVersion-shaped]}`` -- what a branch lookup
        (``resolve_pinned_build``, ``comparison_pinning`` for a branch ref)
        filters and picks the newest of.

        With ``viva-v1-environments-filters``: ``GET /viva/v1/environments
        ?repo_url=&branch=`` (the pair), converted by
        :func:`environments_as_simulators`, each entry given ``git_branch =
        branch`` (the response carries no branch; the server matched it exactly).
        The server's ``repo_url`` match is exact where the workbench's is not
        (case, ``.git``, the ``org/repo`` shorthand), so the spellings actually
        registered are found first -- every distinct ``repo_url`` in the listing
        whose :func:`repo_key` equals this one's -- and each is asked for; the
        caller's own repo match then runs on the answer as before.

        No ``status`` filter: today's lookup is "the newest REGISTERED build on
        the branch" (a newer build still building, or failed, is resolved and
        then refused by the submit / ``verify_build_ready``), not the newest
        ready one. Temporaries are left out (the server's default): a
        marked-temporary build is never picked by default (D11).

        Without the capability: ``GET /core/v1/simulator/versions``, unchanged
        (the caller filters by repo and branch).
        """
        caps = self._server_capabilities()
        if CAPABILITY_VIVA_V1_ENVIRONMENTS_FILTERS not in caps or CAPABILITY_VIVA_V1_ENVIRONMENTS not in caps:
            return self._get("/core/v1/simulator/versions")
        want = repo_key(repo_url)
        spellings: "list[str]" = []
        for row in self._environment_pages({}):
            url = row.get("repo_url")
            if isinstance(url, str) and url not in spellings and repo_key(url) == want:
                spellings.append(url)
        versions: "list[dict]" = []
        for url in spellings:
            for v in environments_as_simulators(self._environment_pages({"repo_url": url, "branch": branch})):
                v["git_branch"] = branch
                versions.append(v)
        return {"versions": versions}

    def list_simulators(self, *, branch_lookup: bool = False) -> dict:
        """All registered simulator builds, as ``{"versions": [SimulatorVersion-shaped]}``.

        With ``viva-v1-environments`` (and ``branch_lookup`` false): every page of
        ``GET /viva/v1/environments?temporary=any`` (temporaries included, as the
        legacy listing includes them; each entry carries ``temporary``/``label``),
        converted by :func:`environments_as_simulators`. Otherwise
        ``GET /core/v1/simulator/versions``.

        ``branch_lookup=True`` always takes the legacy route, because it is the
        only listing that carries every build's ``git_branch``: an environment
        stores no branch, and the ``?repo_url=&branch=`` filter
        (``viva-v1-environments-filters``) answers "which builds are on THIS
        branch", not "which branch is each build on". A lookup of one known
        branch uses :meth:`list_branch_builds`; the one caller left here is the
        build dropdown's branch column (``list_build_sources``, which
        ``switch_build`` also reads), which shows every build's branch.
        """
        if branch_lookup or CAPABILITY_VIVA_V1_ENVIRONMENTS not in self._server_capabilities():
            return self._get("/core/v1/simulator/versions")
        return {"versions": environments_as_simulators(self._environment_pages({"temporary": "any"}))}

    def capabilities(self) -> dict:
        """The deployment's capability advertisement: ``{version, capabilities: [str, ...]}``.

        ``GET /viva/v1/capabilities`` first, then ``GET /core/v1/capabilities``
        when that 404s (a server that predates core's route) -- both answer the
        same list, and ``/core/v1/*`` is dated (M3, which waits on this release).
        Clients branch on MEMBERSHIP in ``capabilities``, never on ``version``
        (for humans/logs). A deployment predating both 404s -- callers use
        ``lib.server_capabilities.fetch_capabilities``, which maps that to
        "advertises nothing" per the endpoint's own contract.
        """
        try:
            return self._get("/viva/v1/capabilities")
        except SmsApiError as e:
            if e.status != 404:
                raise
        return self._get("/core/v1/capabilities")

    def ping(self, timeout: float | None = None) -> str:
        """GET /version — lightweight reachability probe for the health indicator.

        Returns the sms-api version string; raises :class:`SmsApiError` if the
        endpoint is unreachable. Uses a short timeout by default (min of 5 s and
        the client timeout) so a health check never hangs the UI.
        """
        url = self.base_url + "/version"
        req = Request(url, method="GET", headers=self._headers())
        try:
            with urlopen(req, timeout=timeout or min(self.timeout, 5.0)) as r:  # noqa: S310 — fixed scheme, internal tunnel
                body = r.read().decode().strip()
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return body
        if isinstance(parsed, dict):
            return str(parsed.get("version") or parsed.get("__version__") or body)
        return str(parsed)

    def list_build_simulations(self, simulator_id: int) -> list:
        """GET /api/v1/simulations?simulator_id=N — simulation runs on the
        deployment. The ``simulator_id`` query param is required by the API but
        does not actually filter (the server returns every recorded simulation),
        so callers must filter the returned list by ``simulator_id`` themselves.
        Returns the raw list of simulation records."""
        return self._get("/api/v1/simulations", {"simulator_id": simulator_id})

    def composite_resolve(self, simulator_id: int, composite_ref: str,
                          overrides: dict | None = None, timeout: float | None = None) -> dict:
        """Resolve a composite IN a build's environment, on the deployment.

        POST /core/v1/simulator/{id}/composite-resolve — sms-api runs build_core
        for ``composite_ref`` (with ``overrides``) inside build ``simulator_id``'s
        image and returns the resolved-composite JSON (shape-compatible with the
        dashboard's local /api/composite-resolve). Raises SmsApiError on failure.
        """
        return self._post(
            f"/core/v1/simulator/{simulator_id}/composite-resolve",
            json_body={"composite_ref": composite_ref, "overrides": overrides or {}},
        )

    def download_workspace(self, simulator_id: int, dest_dir: Path, timeout: float | None = None) -> Path:
        """Stream a build's repo@commit workspace tarball (SP1's endpoint) to
        dest_dir/workspace.tar.gz."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / "workspace.tar.gz"
        url = f"{self.base_url}{self._path('/api/v1/simulations/workspace')}?simulator_id={simulator_id}"
        req = Request(url, method="GET", headers=self._headers("application/gzip"))
        to = timeout if timeout is not None else DOWNLOAD_TIMEOUT
        try:
            with urlopen(req, timeout=to) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        return out_path

    def simulation_status(self, simulation_id: int) -> dict:
        return self._get(f"/api/v1/simulations/{simulation_id}/status")

    def get_simulation(self, simulation_id: int) -> dict:
        """GET /api/v1/simulations/{id} — the full simulation record sms-api
        holds server-side: ``config`` (the actually-merged run config),
        ``simulator_id``, ``parca_dataset_id``, ``experiment_id``, ``num_seeds``.

        Unlike :meth:`simulation_status` (status/error_message only), this is
        what the P1-11 remote-run-provenance fix reads to record the REAL
        merged config and requested seed count sms-api resolved, instead of
        recomputing/guessing them on the landing laptop (audit §3.9)."""
        return self._get(f"/api/v1/simulations/{simulation_id}")

    def cancel_simulation(self, simulation_id: int) -> dict:
        """DELETE /api/v1/simulations/{id}/cancel (item 53).

        For a chain-dispatch campaign row (``chain_final_job_ids`` set),
        viva-api's own handler walks every seed's own dependsOn chain and
        cancels/terminates whichever job is actually non-terminal per seed —
        not just a single job id. Idempotent: already-terminal rows short-
        circuit server-side and return their existing status. Same ``_delete``
        pattern as :meth:`stop_env_worker`."""
        return self._delete(f"/api/v1/simulations/{simulation_id}/cancel")

    def simulator_commit(self, simulator_id: int) -> str | None:
        """Resolve ``simulator_id`` -> the git commit sms-api actually built
        and ran, from sms-api's OWN simulator registry (:meth:`list_simulators`:
        ``/viva/v1/environments`` or ``/core/v1/simulator/versions``) — never the landing laptop's local
        checkout (that mismatch was the P1-11 "partly wrong" finding, audit
        §3.9). ``None`` (not raised) when the id can't be resolved — e.g. an
        old/pruned registry — since this is best-effort provenance and must
        never block landing."""
        try:
            versions = self.list_simulators().get("versions") or []
        except SmsApiError:
            return None
        for row in versions:
            if isinstance(row, dict) and row.get("database_id") == simulator_id:
                return row.get("git_commit_hash")
        return None

    def simulation_chain_progress(self, simulation_id: int) -> dict:
        """Backlog item 6: real per-seed aggregate progress for a chain-dispatch
        campaign (viva-api PR #257) — {seeds_total, seeds_succeeded, seeds_failed,
        seeds_in_progress, terminal, status}. 404 unknown simulation, 409 when the
        simulation exists but isn't a chain-dispatch campaign (nothing to
        aggregate — callers should use ``simulation_status`` for those)."""
        return self._get(f"/api/v1/simulations/{simulation_id}/chain-progress")

    def simulation_trace(self, simulation_id: int) -> bytes:
        """GET /api/v1/simulations/{id}/trace -- the run's trace as a Chrome Trace
        Event JSON document, returned as the raw bytes (never parsed: it goes
        straight to the browser's Perfetto). Gated by ``viva-v1-trace``; callers
        check the capability (``lib.remote_trace``)."""
        return self._get_bytes(f"/api/v1/simulations/{simulation_id}/trace")

    def composite_run_trace(self, run_id: str) -> bytes:
        """GET /viva/v1/composites/{id}/trace -- a composite run's trace (same
        format as :meth:`simulation_trace`). ``run_id`` is the composite run id,
        which for a ``/compose/v1`` submission is its ``correlation_id``."""
        return self._get_bytes(f"/viva/v1/composites/{quote(str(run_id), safe='')}/trace")

    # -- /viva/v1/composites: the generic run surface -----------------------
    # Contract: docs/backend-viva-v1.md (verified against the deployment's own
    # /viva/v1/openapi.json). A run ``id`` is opaque -- pass it back as given.

    def create_composite_run(
        self, *, environment: dict, composite: "dict | None" = None,
        document: "dict | None" = None, execution: "dict | None" = None,
        label: "str | None" = None,
    ) -> dict:
        """``POST /viva/v1/composites`` -> the run record (HTTP 202).

        ``environment`` is ``{"id": ...}`` or ``{"name": ...}``; exactly one of
        ``composite`` (``{"id", "params"}``, a composite the environment provides)
        or ``document`` (a process-bigraph document) names what runs. Never
        retried (``_post``): a retried submit could double-spend a real run.
        """
        if (composite is None) == (document is None):
            raise ValueError("exactly one of composite / document is required")
        body: dict = {"environment": environment}
        if composite is not None:
            body["composite"] = composite
        else:
            body["document"] = document
        if execution:
            body["execution"] = execution
        if label:
            body["label"] = label
        return self._post(_COMPOSITES, json_body=body)

    def list_composite_runs(self, **filters: Any) -> dict:
        """``GET /viva/v1/composites`` (``status`` repeatable, ``composite_id``,
        ``environment_id``, ``created_by``, ``limit``, ``offset``) -> a page."""
        return self._get(_COMPOSITES, {k: v for k, v in filters.items() if v is not None})

    def composite_run(self, run_id: "str | int") -> dict:
        return self._get(_run_path(run_id))

    def composite_run_status(self, run_id: "str | int") -> dict:
        """``{id, status, message}``; ``status`` is a JobStatus (lower-case)."""
        return self._get(_run_path(run_id, "status"))

    def composite_run_progress(self, run_id: "str | int") -> dict:
        """``{id, status, total, by_kind: {kind: {status: n}}}``."""
        return self._get(_run_path(run_id, "progress"))

    def composite_run_jobs(self, run_id: "str | int") -> dict:
        return self._get(_run_path(run_id, "jobs"))

    def composite_run_datasets(self, run_id: "str | int", *, limit: "int | None" = None,
                               offset: "int | None" = None) -> dict:
        """The run's datasets; 503 (:class:`SmsApiError`) on a deployment with no dataset store."""
        params = {k: v for k, v in (("limit", limit), ("offset", offset)) if v is not None}
        return self._get(_run_path(run_id, "datasets"), params or None)

    def composite_run_log(self, run_id: "str | int", *, full: bool = False) -> str:
        """The run's log as text (``text/plain``)."""
        path = _run_path(run_id, "log") + ("?full=true" if full else "")
        return self._get_bytes(path, accept="text/plain").decode("utf-8", errors="replace")

    def cancel_composite_run(self, run_id: "str | int") -> dict:
        """``DELETE /viva/v1/composites/{id}`` -> ``{run, pending}``. The record is kept;
        ``pending`` names what is still being stopped (HTTP 202)."""
        return self._delete(_run_path(run_id))

    def health_v1(self) -> dict:
        """``GET /viva/v1/health`` -> ``{status, version, services: {name: bool}}``."""
        return self._get("/viva/v1/health")

    def _get_bytes(self, path: str, accept: str = "application/json") -> bytes:
        """GET ``path`` and return the body undecoded. One attempt: a trace is
        assembled on demand server-side and the caller is a person clicking."""
        self._link().check(force=self.force_link)
        url = self.base_url + self._path(path)
        req = Request(url, method="GET", headers=self._headers(accept))
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — fixed scheme, internal tunnel
                body = bytes(r.read())
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            self._mark_link_down(str(e))
            raise SmsApiError(f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        self._mark_link_up()
        return body

    def _delete(self, path: str) -> dict:
        url = self.base_url + self._path(path)
        req = Request(url, method="DELETE", headers=self._headers())
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310 — fixed scheme, internal tunnel
                body = r.read().decode()
                return json.loads(body) if body else {}
        except HTTPError as e:
            raise SmsApiError(f"DELETE {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"DELETE {url} failed (sms-api unreachable): {e}") from e

    def _post(self, path: str, params: dict | None = None, json_body: dict | None = None) -> dict:
        # doseq=True so list-valued params become repeated keys (?observables=a&observables=b)
        # Fail fast when the tunnel is known-down (CircuitOpen, an SmsApiError).
        self._link().check(force=self.force_link)
        url = self.base_url + self._path(path)
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"
        data = json.dumps(json_body).encode() if json_body is not None else None
        headers = self._headers()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, method="POST", headers=headers)
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                payload = json.loads(r.read().decode())
            self._mark_link_up()
            return payload
        except HTTPError as e:
            # Server answered — link is alive; do not trip the breaker on status.
            raise SmsApiError(f"POST {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            self._mark_link_down(str(e))
            raise SmsApiError(f"POST {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e

    def upload_simulator(self, simulator: dict, force: bool = False) -> dict:
        """Register (select-or-build) ``simulator`` = ``{git_repo_url, git_branch,
        git_commit_hash}``; returns the legacy ``SimulatorVersion`` shape, whose
        ``database_id`` is the simulator id every later call takes.

        With ``viva-v1-environments-build``: ``POST /viva/v1/environments
        {repo_url, commit, branch, force}`` -- 200 selected (already ready, nothing
        built) or 202 sent to the builder; ``force`` against a built or building
        one is a 409 (write-once, D11). Otherwise, or when that answer names no
        simulator: ``POST /core/v1/simulator/upload``.
        """
        if CAPABILITY_VIVA_V1_ENVIRONMENTS_BUILD in self._server_capabilities():
            created = self._create_environment(
                repo_url=str(simulator.get("git_repo_url") or ""),
                branch=simulator.get("git_branch") or None,
                commit=simulator.get("git_commit_hash") or None,
                force=force,
            )
            if created is not None:
                return created
        return self._legacy_upload(simulator, force=force)

    def _legacy_upload(self, simulator: dict, force: bool = False) -> dict:
        params = {"force": "true"} if force else None
        return self._post("/core/v1/simulator/upload", params=params, json_body=simulator)

    def run_simulation(
        self,
        *,
        simulator_id: int,
        num_generations: int,
        num_seeds: int,
        run_parca: bool,
        observables: list[str],
        experiment_id: str | None = None,
        description: str | None = None,
        config_filename: str | None = None,
        analysis_options: dict | None = None,
        extra_params: dict | None = None,
    ) -> dict:
        params: dict = {
            "simulator_id": simulator_id,
            "num_generations": num_generations,
            "num_seeds": num_seeds,
            "run_parca": run_parca,
        }
        if experiment_id is not None:
            params["experiment_id"] = experiment_id
        if description is not None:
            params["description"] = description
        if config_filename is not None:
            # sms-api's own Query() default (api_simulation_default.json) only
            # exists in the public vEcoli-lineage repos — sms-ecoli has never
            # had that file (confirmed via GitHub code search), so any sms-ecoli
            # dispatch that omits this 404s. GET /api/v1/simulations/discovery
            # on sms-api lists real, valid values for the pinned commit.
            params["simulation_config_filename"] = config_filename
        if observables:
            params["observables"] = observables  # list → repeated key via doseq
        # analysis_options/extra_params are both nested-dict-shaped bodies with no
        # Query()/Body() wrapper on the sms-api side — FastAPI reads them from the
        # JSON request body, not the query string (nested dicts don't survive
        # urlencode sensibly), so they go in json_body rather than alongside the
        # other flat/scalar params above.
        json_body: dict = {}
        if analysis_options:
            json_body["analysis_options"] = analysis_options
        if extra_params:
            json_body["extra_params"] = extra_params
        return self._post("/api/v1/simulations", params=params, json_body=json_body or None)

    def run_analysis(self, simulation_id: int, modules: dict) -> dict:
        """POST /api/v1/simulations/{id}/analysis — trigger standalone analysis on
        a completed simulation's output. Returns immediately with a job_id and
        (for Ray-backend simulators) a database_id -- pass the latter to
        analysis_status() to poll for real completion."""
        # modules is read via query param (?modules=<json>) on the sms-api side,
        # not a request body -- matches the endpoint's own OpenAPI shape.
        return self._post(
            f"/api/v1/simulations/{simulation_id}/analysis",
            params={"modules": json.dumps(modules)},
        )

    def analysis_status(self, analysis_id: int) -> dict:
        """GET /api/v1/analyses/{id}/status — poll a triggered analysis's real status.
        Only meaningful when run_analysis() returned a database_id (Ray-backend
        simulators); resolved server-side via S3-exists probe, since there is
        no persistent job-status API for the backing K8s Job."""
        return self._get(f"/api/v1/analyses/{analysis_id}/status")

    def list_analyses(self, simulation_id: int) -> list:
        """GET /api/v1/simulations/{id}/analyses — the analyses attached to a
        completed simulation. Each entry carries a ``result_uri`` (an ``s3://``
        prefix like ``.../analyses/analysis-mnp-...``) that, for a completed
        analysis, holds the rendered ``viz/*.html`` figures and ``ptools/*.tsv``
        overlays. ``result_uri`` is nullable (older dispatch paths, or a
        failed/pending analysis), so callers must handle ``None``.

        Returns the raw list of analysis dicts; ``[]`` when none / unreachable."""
        out = self._get(f"/api/v1/simulations/{simulation_id}/analyses")
        return out if isinstance(out, list) else []

    # ------------------------------------------------------------------
    # Compose endpoints (generic .pbg runner, Phase C)
    # ------------------------------------------------------------------

    def compose_submit(
        self,
        pbg_bytes: bytes,
        extra_pip_deps: list[str] | None = None,
        interval_time: float = 1.0,
        filename: str = "composite.pbg",
        analysis_options: dict | None = None,
    ) -> int:
        """POST /compose/v1/simulation/run — submit a .pbg file for execution.

        The file is uploaded as multipart/form-data with the field name
        ``uploaded_file`` (required by the sms-api endpoint).  Any
        ``extra_pip_deps`` are appended as repeated ``extra_pip_deps`` query
        parameters so the container can install them before running.

        Parameters
        ----------
        pbg_bytes:
            Raw bytes of the ``.pbg`` JSON document.
        extra_pip_deps:
            Additional pip-installable dependencies (e.g.
            ``["git+https://github.com/org/repo.git@sha"]``).
        interval_time:
            Step interval forwarded to the sms-api run endpoint.
        filename:
            Filename reported in the multipart header (cosmetic).
        analysis_options:
            v2ecoli-shaped ``{scale: {name: params}}`` analyses to run
            server-side (composite-auto-results Task 8). Sent as a
            JSON-encoded string in a multipart ``analysis_options`` form
            field — NOT a query param. sms-api's ``/compose/v1/simulation/run``
            route reads this via ``Form()`` + ``json.loads()``, not
            ``Query()``; a query param there is silently dropped by FastAPI
            and no analyses ever run (the bug behind #1022 being a silent
            no-op). Omitted entirely (no field at all) when ``None``.

        Returns
        -------
        int
            ``simulation_database_id`` from the response.
        """
        boundary = "----vivdash00boundary"
        parts = [
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="uploaded_file"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n"
                "\r\n"
            ).encode() + pbg_bytes + b"\r\n"
        ]
        if analysis_options:
            parts.append(
                (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="analysis_options"\r\n'
                    "\r\n"
                    f"{json.dumps(analysis_options)}\r\n"
                ).encode()
            )
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        content_type = f"multipart/form-data; boundary={boundary}"

        params: dict = {"interval_time": interval_time}
        if extra_pip_deps:
            params["extra_pip_deps"] = extra_pip_deps  # list → repeated key via doseq

        url = self.base_url + self._path("/compose/v1/simulation/run")
        if params:
            url = f"{url}?{urlencode(params, doseq=True)}"

        req = Request(
            url,
            data=body,
            method="POST",
            headers={**self._headers(), "Content-Type": content_type},
        )
        try:
            with urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                data = json.loads(r.read().decode())
        except HTTPError as e:
            raise SmsApiError(f"POST {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(
                f"POST {url} failed (sms-api unreachable — is the tunnel up?): {e}"
            ) from e
        return int(data["simulation_database_id"])

    def compose_status(self, task_id: int) -> dict:
        """GET /compose/v1/simulation/{id}/status — poll run status."""
        return self._get(f"/compose/v1/simulation/{task_id}/status")

    def compose_status_batch(self, ids: "list[int]") -> "list[dict]":
        """GET /compose/v1/simulations/status/batch?ids=… — many runs, one call.

        viva-api returns a JSON **list** here (``list[ComposeHpcRun]``), unlike
        every other endpoint on this client, so the ``_get`` result is widened
        rather than trusted as a dict. Existing to serve reconcile-style polling:
        a caller holding N in-flight ``simulation_id``s asks once instead of N
        times (REFACTOR-PLAN §2A.8 / run-orchestration-consolidation §A2').
        """
        if not ids:
            return []
        raw: Any = self._get("/compose/v1/simulations/status/batch",
                             {"ids": [int(i) for i in ids]})
        return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []

    def download_compose_results(self, sim_id: int, dest: Path, timeout: float | None = None) -> Path:
        """GET /compose/v1/simulation/{id}/results — stream results.tar.gz to dest.

        The route is backend-aware server-side (compose-results-land-p0 T5a):
        a Ray/Batch (GovCloud) simulation streams a gzip tarball of its S3
        output prefix (mirroring the study path's ``download_data``), while a
        SLURM simulation's SSH/SCP branch is unchanged. Both are served under
        the same ``.tar.gz`` contract this client now expects, so
        ``land_remote_run``/``fold_analyses`` (which already read ``.tar.gz``)
        work unmodified once this lands.

        A SLURM compose run serves a zip from the same route; the file is named for what it
        is (its leading bytes decide), so a reader never has to guess from the name.

        Returns
        -------
        Path
            ``dest / "results.tar.gz"``, or ``dest / "results.zip"`` for a zip
        """
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        out_path = dest / "results.tar.gz"
        url = self.base_url + self._path(f"/compose/v1/simulation/{sim_id}/results")
        req = Request(url, method="GET", headers=self._headers("application/gzip"))
        to = timeout if timeout is not None else DOWNLOAD_TIMEOUT
        try:
            with urlopen(req, timeout=to) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"GET {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(
                f"GET {url} failed (sms-api unreachable — is the tunnel up?): {e}"
            ) from e
        with open(out_path, "rb") as f:
            is_zip = f.read(4) == b"PK\x03\x04"
        if is_zip:
            return out_path.replace(dest / "results.zip")
        return out_path

    def download_data(self, simulation_id: int, dest_dir: Path, timeout: float | None = None) -> Path:
        """Stream the run's native-store tar.gz (POST /data) to dest_dir/sim_<id>.tar.gz."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_path = dest_dir / f"sim_{simulation_id}.tar.gz"
        url = self.base_url + self._path(f"/api/v1/simulations/{simulation_id}/data")
        req = Request(url, data=b"", method="POST", headers=self._headers("application/gzip"))
        to = timeout if timeout is not None else DOWNLOAD_TIMEOUT
        try:
            with urlopen(req, timeout=to) as r, open(out_path, "wb") as f:  # noqa: S310
                shutil.copyfileobj(r, f)
        except HTTPError as e:
            raise SmsApiError(f"POST {url} -> {e.code}{_http_error_detail(e)}", status=e.code) from e
        except (URLError, OSError) as e:
            raise SmsApiError(f"POST {url} failed (sms-api unreachable — is the tunnel up?): {e}") from e
        return out_path
