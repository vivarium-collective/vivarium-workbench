"""GitHub OAuth Device Flow + ``gh``-CLI delegate for vivarium-dashboard.

Implements Phase B-bis of todo #8: in-UI "Sign in with GitHub" without requiring
the user to run ``gh auth login`` in a terminal beforehand.

Two auth paths, in resolution order:

1. **``gh`` CLI delegate.** If ``gh`` is installed and ``gh auth status`` exits
   0, we read the token via ``gh auth token`` and treat that as the active
   session. Zero-click for power users who already authenticated.
2. **OAuth Device Flow.** Standard GitHub Device Flow
   (https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow).
   No client secret, no redirect URI — appropriate for a binary that runs
   locally. Requires a public ``client_id`` registered against an OAuth App
   in the ``vivarium-collective`` org. The app's client_id is read from the
   ``VIVARIUM_DASHBOARD_GH_CLIENT_ID`` env var (no default ships, so an
   unconfigured deployment cannot accidentally talk to a wrong app).

Tokens are persisted in the system keychain via ``keyring`` when available.
Falls back to in-memory only when keyring is missing/broken (e.g. headless
Linux without a secret-service daemon).

Never logs tokens in plaintext: use :func:`mask_token` before printing any
captured stdout/stderr from gh-cli or HTTP responses.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from threading import Lock
from typing import Literal

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_KEYRING_SERVICE = "vivarium-dashboard"

# Public OAuth App client_id. Must be set via env for the device flow to work;
# the gh-cli delegate path doesn't need it. Empty string disables device flow
# (start_device_flow returns a 503-shaped error in that case).
_CLIENT_ID_ENV = "VIVARIUM_DASHBOARD_GH_CLIENT_ID"

# Minimum sufficient scopes for the dashboard's actions:
#   - repo:          create / push to user repos and orgs (Phase C, Todo #1).
#   - read:org:      list orgs the user belongs to (Phase B org dropdown).
#   - write:packages: future GHCR visibility flip (Todo #4 follow-up).
_OAUTH_SCOPES = "repo read:org write:packages"

_DEVICE_CODE_URL = "https://github.com/login/device/code"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"

# Token-shape regex covers all current GitHub PAT/OAuth prefixes. Used by
# :func:`mask_token` to scrub log output before write/return.
_TOKEN_RE = re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b")


# ---------------------------------------------------------------------------
# Session shape (returned by status / read_session helpers)
# ---------------------------------------------------------------------------

Source = Literal["device_flow", "gh_cli"]


@dataclass(frozen=True)
class Session:
    login: str
    token: str
    source: Source
    scopes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# In-process state (device flows + cached session)
# ---------------------------------------------------------------------------

_STATE_LOCK = Lock()

# Pending device flows, keyed by a UUID we hand to the client. Each entry:
#   {"device_code": str, "expires_at": float, "interval": int,
#    "scopes": tuple[str, ...]}
# device_code never leaves the server.
_PENDING_FLOWS: dict[str, dict] = {}

# Cached active session. Populated by:
#   - successful poll() (source="device_flow")
#   - first read_gh_cli_session() hit (source="gh_cli")
# Cleared by logout(). None = no session yet (caller should resolve from
# gh-cli + keyring).
_CACHED_SESSION: Session | None = None


# ---------------------------------------------------------------------------
# Token masking — applied to every log/error string that could carry a token
# ---------------------------------------------------------------------------


def mask_token(text: str) -> str:
    """Replace any GitHub-shaped token in ``text`` with ``<redacted>``.

    Safe to call on already-masked text (idempotent). Use before logging or
    surfacing captured subprocess output / HTTP responses that might contain
    a token.
    """
    return _TOKEN_RE.sub("<redacted>", text)


# ---------------------------------------------------------------------------
# Keyring storage — wraps the optional ``keyring`` library so missing/broken
# backends degrade to "in-memory only" rather than crashing.
# ---------------------------------------------------------------------------


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
        return True
    except Exception:
        return False


def _keyring_get(login: str) -> str | None:
    if not _keyring_available():
        return None
    try:
        import keyring
        return keyring.get_password(_KEYRING_SERVICE, login)
    except Exception as e:
        log.warning("keyring read failed for login=%s: %s", login, mask_token(str(e)))
        return None


def _keyring_set(login: str, token: str) -> bool:
    if not _keyring_available():
        return False
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, login, token)
        return True
    except Exception as e:
        log.warning("keyring write failed for login=%s: %s", login, mask_token(str(e)))
        return False


def _keyring_delete(login: str) -> None:
    if not _keyring_available():
        return
    try:
        import keyring
        keyring.delete_password(_KEYRING_SERVICE, login)
    except Exception:
        # delete is best-effort; absent entries are not an error.
        pass


# Tiny on-disk hint file under ~/.config/vivarium-dashboard/ to remember the
# *last* login string — keyring requires both service AND username to read
# back, and we don't want to scan all usernames. This file holds *only* the
# login (a username string like ``alexpatrie``), never the token.
def _last_login_path():
    from pathlib import Path
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "vivarium-dashboard" / "last_login"


def _remember_login(login: str) -> None:
    try:
        p = _last_login_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(login.strip() + "\n")
    except OSError:
        pass


def _recall_login() -> str | None:
    try:
        p = _last_login_path()
        if p.is_file():
            v = p.read_text().strip()
            return v or None
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# gh-CLI delegate
# ---------------------------------------------------------------------------


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _gh_auth_ok() -> bool:
    if not _gh_available():
        return False
    try:
        r = subprocess.run(
            ["gh", "auth", "status", "--hostname", "github.com"],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _gh_token() -> str | None:
    if not _gh_available():
        return None
    try:
        r = subprocess.run(
            ["gh", "auth", "token", "--hostname", "github.com"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        tok = r.stdout.strip()
        return tok or None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _gh_login() -> str | None:
    """Return the authenticated user's GitHub login from ``gh``, or None."""
    if not _gh_available():
        return None
    try:
        r = subprocess.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return None
        login = r.stdout.strip()
        return login or None
    except (subprocess.TimeoutExpired, OSError):
        return None


def read_gh_cli_session() -> Session | None:
    """If ``gh`` is installed and authenticated, return its session.

    Cheap to call repeatedly: each call shells out three times (status, token,
    user). Callers that hit this on every status request should cache.
    """
    if not _gh_auth_ok():
        return None
    tok = _gh_token()
    login = _gh_login()
    if not tok or not login:
        return None
    return Session(login=login, token=tok, source="gh_cli")


# ---------------------------------------------------------------------------
# Device Flow
# ---------------------------------------------------------------------------


def _client_id() -> str:
    return os.environ.get(_CLIENT_ID_ENV, "").strip()


def _http_post(url: str, data: dict, *, headers: dict | None = None) -> tuple[int, dict]:
    """POST form data; parse JSON response. Returns (status, json_payload).

    Caller is responsible for masking any token in error/log strings.
    """
    body = urllib.parse.urlencode(data).encode()
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
            return resp.status, payload
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            payload = {"error": "http_error", "status": e.code}
        return e.code, payload


def _http_get(url: str, *, token: str | None = None) -> tuple[int, dict]:
    hdrs = {"Accept": "application/vnd.github+json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            payload = {"error": "http_error", "status": e.code}
        return e.code, payload


def start_device_flow(scopes: str = _OAUTH_SCOPES) -> dict:
    """Initiate a Device Flow.

    Returns ``{flow_id, user_code, verification_uri, verification_uri_complete?,
    expires_in, interval}`` on success, or ``{"error": "..."}`` on failure
    (e.g. missing client_id, GitHub rejected the request).

    ``device_code`` is intentionally NOT included in the return value — it's
    held server-side and looked up by ``flow_id`` in :func:`poll_device_flow`.
    """
    cid = _client_id()
    if not cid:
        return {"error": "no_client_id",
                "hint": f"set {_CLIENT_ID_ENV} or use the gh-cli fallback"}

    status, payload = _http_post(_DEVICE_CODE_URL, {"client_id": cid, "scope": scopes})
    if status != 200 or "device_code" not in payload:
        return {"error": "device_code_failed",
                "status": status,
                "detail": payload.get("error_description") or payload.get("error")}

    flow_id = uuid.uuid4().hex
    expires_in = int(payload.get("expires_in", 900))
    interval = int(payload.get("interval", 5))
    with _STATE_LOCK:
        _PENDING_FLOWS[flow_id] = {
            "device_code": payload["device_code"],
            "expires_at": time.monotonic() + expires_in,
            "interval": interval,
            "scopes": tuple(scopes.split()),
        }
        _gc_pending_flows_locked()

    out = {
        "flow_id": flow_id,
        "user_code": payload["user_code"],
        "verification_uri": payload["verification_uri"],
        "expires_in": expires_in,
        "interval": interval,
    }
    if "verification_uri_complete" in payload:
        out["verification_uri_complete"] = payload["verification_uri_complete"]
    return out


def _gc_pending_flows_locked() -> None:
    """Drop pending flows that have already expired. Caller holds _STATE_LOCK."""
    now = time.monotonic()
    stale = [fid for fid, e in _PENDING_FLOWS.items() if e["expires_at"] <= now]
    for fid in stale:
        del _PENDING_FLOWS[fid]


def poll_device_flow(flow_id: str) -> dict:
    """Poll the token endpoint for a pending flow.

    Returns one of:
      - ``{"status": "pending", "interval": int}`` — still waiting on user.
      - ``{"status": "ok", "login": str}`` — auth succeeded; session cached
        + persisted to keyring.
      - ``{"status": "expired"}`` — the device_code expired.
      - ``{"status": "denied"}`` — user denied access.
      - ``{"status": "error", "detail": str}`` — other failures (network,
        unknown flow_id, etc.).
    """
    cid = _client_id()
    if not cid:
        return {"status": "error", "detail": "no_client_id"}

    with _STATE_LOCK:
        entry = _PENDING_FLOWS.get(flow_id)
        if entry is None:
            return {"status": "error", "detail": "unknown_flow"}
        device_code = entry["device_code"]
        interval = entry["interval"]
        scopes = entry["scopes"]

    status, payload = _http_post(_TOKEN_URL, {
        "client_id": cid,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    })

    err = payload.get("error")
    if err == "authorization_pending":
        return {"status": "pending", "interval": interval}
    if err == "slow_down":
        # GitHub asked us to back off. Bump our recorded interval.
        new_interval = int(payload.get("interval", interval + 5))
        with _STATE_LOCK:
            if flow_id in _PENDING_FLOWS:
                _PENDING_FLOWS[flow_id]["interval"] = new_interval
        return {"status": "pending", "interval": new_interval}
    if err == "expired_token":
        with _STATE_LOCK:
            _PENDING_FLOWS.pop(flow_id, None)
        return {"status": "expired"}
    if err == "access_denied":
        with _STATE_LOCK:
            _PENDING_FLOWS.pop(flow_id, None)
        return {"status": "denied"}
    if err:
        return {"status": "error", "detail": str(err)}

    # Success: payload has access_token.
    token = payload.get("access_token")
    if not token:
        return {"status": "error", "detail": "no_access_token_in_response"}

    # Resolve the login.
    ustatus, uresp = _http_get(_USER_URL, token=token)
    if ustatus != 200 or "login" not in uresp:
        return {"status": "error", "detail": f"user_lookup_failed_{ustatus}"}
    login = uresp["login"]

    session = Session(login=login, token=token, source="device_flow", scopes=scopes)
    _set_cached_session(session)
    _keyring_set(login, token)
    _remember_login(login)

    with _STATE_LOCK:
        _PENDING_FLOWS.pop(flow_id, None)

    return {"status": "ok", "login": login}


# ---------------------------------------------------------------------------
# Cached-session helpers
# ---------------------------------------------------------------------------


def _set_cached_session(session: Session | None) -> None:
    global _CACHED_SESSION
    with _STATE_LOCK:
        _CACHED_SESSION = session


def _get_cached_session() -> Session | None:
    with _STATE_LOCK:
        return _CACHED_SESSION


def current_session() -> Session | None:
    """Return the active session, in resolution order:

    1. ``gh auth status`` succeeds → return that.
    2. In-process cache (set by a prior device flow).
    3. Keyring lookup for the last-known login (rehydrates the cache).
    4. ``None`` — caller should treat as unauthenticated.

    Cached after first hit so repeated calls don't shell out.
    """
    cached = _get_cached_session()
    if cached is not None:
        return cached

    gh = read_gh_cli_session()
    if gh is not None:
        _set_cached_session(gh)
        return gh

    login = _recall_login()
    if login:
        tok = _keyring_get(login)
        if tok:
            session = Session(login=login, token=tok, source="device_flow")
            _set_cached_session(session)
            return session

    return None


def logout() -> None:
    """Clear the cached session AND any persisted keyring entry."""
    session = _get_cached_session()
    if session is not None and session.source == "device_flow":
        _keyring_delete(session.login)
    elif session is None:
        # No cached session, but we may have a persisted one to remove.
        login = _recall_login()
        if login:
            _keyring_delete(login)
    _set_cached_session(None)


# ---------------------------------------------------------------------------
# Subprocess env injection — used by /api/workspaces/create + similar (Phase C)
# ---------------------------------------------------------------------------


def current_token_env() -> dict:
    """Return env vars to merge into spawned subprocesses so ``gh`` / Octokit /
    git over HTTPS can authenticate without writing the token to disk.

    Empty dict if no session is active. Caller pattern:

        env = os.environ | current_token_env()
        subprocess.run([...], env=env)
    """
    session = current_session()
    if session is None:
        return {}
    return {
        "GH_TOKEN": session.token,
        "GITHUB_TOKEN": session.token,
        "GH_USER": session.login,
    }


# ---------------------------------------------------------------------------
# Status payload — what `/api/auth/github/status` returns
# ---------------------------------------------------------------------------


def status_payload() -> dict:
    """Build the JSON payload for ``GET /api/auth/github/status``.

    Never includes the token itself — only ``{authenticated, login?, source?,
    scopes?}``.
    """
    session = current_session()
    if session is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "login": session.login,
        "source": session.source,
        "scopes": list(session.scopes),
    }


def list_orgs() -> dict:
    """Fetch the current user's GitHub orgs (todo #8 Phase B-extension).

    Returns ``{login: str, orgs: [{name: str, kind: "personal"|"org"}, ...]}``
    on success; ``{"error": "unauthenticated"}`` if no session.

    The user's personal namespace is the first entry (kind=``personal``);
    real orgs follow (kind=``org``). The New Workspace modal renders this
    payload as a ``<select>`` so the user picks rather than free-types.
    """
    session = current_session()
    if session is None:
        return {"error": "unauthenticated"}
    status, payload = _http_get(
        "https://api.github.com/user/orgs", token=session.token,
    )
    if status != 200 or not isinstance(payload, list):
        return {
            "error": "orgs_lookup_failed",
            "status": status,
            "detail": (payload.get("message") if isinstance(payload, dict) else None),
        }
    orgs = [{"name": session.login, "kind": "personal"}]
    for o in payload:
        if isinstance(o, dict) and o.get("login"):
            orgs.append({"name": o["login"], "kind": "org"})
    return {"login": session.login, "orgs": orgs}
