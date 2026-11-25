"""Sign in to compose-api from the workbench: the same login ``compose-api auth login`` makes, from a button.

The workbench signs its compose-api calls in with the login the compose-api CLI stored for its profile
(:func:`remote_run_compose._session`; docs/backend-compose-api.md, "Signing in"). This module makes that login from the
page instead of a terminal, with the client's own sign-in (``compose_api_client.auth``), on the CLI's own profile
(``COMPOSE_API_CLI_PROFILE``, else ``production``) and in the CLI's own credential store. So the CLI and the workbench
share one login: either can sign in, and either can sign out.

A sign-in waits for the person (a browser page, or a code entered on another device) for up to the client's five
minutes, so it runs on a thread of its own, with an event loop of its own, and the page polls :func:`status`. The
client reads and writes the OS credential store synchronously, and a keychain can stop to ask the person, so none of a
sign-in or a sign-out runs on the server's event loop. One sign-in runs at a
time, and the client's own login lock also keeps it from racing a CLI sign-in. Only the client's progress messages
(``notify``, which it never gives a token, code verifier or secret) reach the page: the tokens go from Auth0 to the
credential store inside the client, never through this API.

Sign-in is offered only where it would sign the calls in: the ``compose-api-signin`` extra installed, a compose-api
backend named, ``COMPOSE_API_TOKEN`` unset (it would take precedence), and the CLI profile's ``api_base_url`` that
backend. Otherwise :func:`status` says why, in the words of :func:`remote_run_compose._sign_in_advice`.
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from typing import Any

#: How the client reaches the person (``SignInMethod``): ``auto`` decides from the server's environment (a device code
#: over SSH or without a display), ``browser`` opens the system browser with the loopback callback, and ``device``
#: shows a code to enter on any device (a remote or headless server).
METHODS = ("auto", "browser", "device")

_LOCK = threading.Lock()
_FLOW: "dict[str, Any] | None" = None


def _flow_view_locked() -> "dict | None":
    """The current or last sign-in, as the page sees it (never the thread). The caller holds ``_LOCK``."""
    if _FLOW is None:
        return None
    return {k: (list(v) if k == "notes" else v) for k, v in _FLOW.items() if k != "thread"}


def _flow_view() -> "dict | None":
    with _LOCK:
        return _flow_view_locked()


def _target() -> "tuple[Any, tuple[dict, int] | None]":
    """The CLI profile a sign-in here would sign the calls in with, or why there is none (a 409 answer)."""
    from vivarium_workbench.lib import remote_run_compose as rrc
    from vivarium_workbench.lib.backend_config import explicit_backend_base_url

    def refuse(reason: str, message: str) -> "tuple[None, tuple[dict, int]]":
        return None, ({"available": False, "reason": reason, "message": message}, 409)

    if rrc._signed_in_session() is None:
        return refuse("no-extra", f"install vivarium-workbench[{rrc.SIGNIN_EXTRA}] to sign in to compose-api here")
    base = explicit_backend_base_url()
    if base is None:
        return refuse("no-backend", "no compose-api backend is named (serve --backend-base-url)")
    if os.environ.get(rrc.TOKEN_ENV_VAR, "").strip():
        return refuse("token", f"{rrc.TOKEN_ENV_VAR} is set, and it signs every call in ahead of a login; "
                               "unset it to sign in here")
    from compose_api_client.auth import load_profile
    from compose_api_client.ext import ConfigError

    try:
        settings = load_profile().settings
    except ConfigError as e:
        return refuse("config", f"the compose-api CLI's configuration is unusable: {e}")
    if not settings.matches_url(base):
        return refuse("other-profile", rrc._sign_in_advice())
    if settings.auth0_client_id is None:
        return refuse("cannot-sign-in", f"the compose-api CLI's profile {settings.profile!r} has no sign-in "
                                        "(no auth0_client_id)")
    return settings, None


def status() -> "tuple[dict, int]":
    """Whether sign-in is offered here and whether the calls are signed in now, plus the current sign-in.

    ``signed_in`` is what the next compose-api call would carry, read as the call would read it
    (:func:`remote_run_compose._carries_credential`): a stored login that is about to expire is renewed, as it would
    be. A credential store that cannot be read is not signed in, with the client's reason in ``error``. While a
    sign-in waits, ``signed_in`` is ``None`` (not read; see the comment below).
    """
    from vivarium_workbench.lib import remote_run_compose as rrc

    settings, refusal = _target()
    if refusal is not None:
        return {**refusal[0], "signed_in": False, "flow": _flow_view()}, 200
    from compose_api_client.ext import CliError

    body: dict = {"available": True, "profile": settings.profile, "api_base_url": settings.api_base_url}
    with _LOCK:
        waiting = _waiting_locked()
    if waiting:
        # Not read while a sign-in waits: reading may renew the stored login, and a renewal during the wait makes the
        # client discard the new sign-in when it is committed ("Session changed while signing in").
        body["signed_in"] = None
    else:
        try:
            body["signed_in"] = rrc._carries_credential(rrc._session(rrc.CHECK_TIMEOUT))
        except CliError as e:
            body.update(signed_in=False, error=str(e))
    return {**body, "flow": _flow_view()}, 200


def _prepare(settings: Any, method: str, notify: Any) -> "tuple[Any, Any]":
    """What ``compose-api auth login`` does before it waits for the person: the request, the method resolved once,
    and the credential store opened for writing (a store that would refuse the save fails here, before any browser
    opens). Blocking (the store may ask the OS keychain): it runs on the route's worker thread (a sync route)."""
    from compose_api_client.auth import storage
    from compose_api_client.auth.models import SignInMethod, SignInRequest
    from compose_api_client.auth.signin import connection_for, resolve

    settings.require_client_id()
    request = SignInRequest(signup=False, provider=None, method=SignInMethod(method), persistent=True)
    request = resolve(settings, request, notify=notify)
    connection_for(settings, request)
    return request, storage.open_persistent_store(settings, check=True, writable=True)


def _run(flow: dict, settings: Any, request: Any, store: Any, notify: Any) -> None:
    """A sign-in, on a thread and an event loop of its own: what ``compose-api auth login`` does once it has resolved
    the request. Wait for the person (``persistent_sign_in``, which saves the session), then have the API confirm who
    it is (``confirm_identity``): the CLI counts a login done only after all three. The client reads and writes the OS
    credential store synchronously inside both, and a keychain can stop to ask the person, so none of it runs on the
    server's event loop. The flow always ends ``ok`` or ``error``."""
    from compose_api_client.auth import signin
    from compose_api_client.auth.identity import confirm_identity

    async def sign_in_and_confirm() -> None:
        await signin.persistent_sign_in(settings, request, store, notify=notify)
        notify(f"Signed in. Confirming the session with the Compose API at {settings.api_base_url}...")
        await confirm_identity(settings, store, after_sign_in=True)

    try:
        asyncio.run(sign_in_and_confirm())
    except BaseException as e:  # noqa: BLE001 -- any failure ends this sign-in; the page shows the client's message
        with _LOCK:
            flow.update(state="error", error=str(e) or type(e).__name__)
        if not isinstance(e, Exception):
            raise
        return
    with _LOCK:
        flow["state"] = "ok"
        flow["notes"].append(f"Signed in to {settings.api_base_url} (compose-api CLI profile {settings.profile!r}).")


def _waiting_locked() -> bool:
    """A sign-in is still waiting for the person: preparing (no thread yet) or its thread alive. The caller holds
    ``_LOCK``."""
    if _FLOW is None or _FLOW["state"] != "running":
        return False
    thread = _FLOW.get("thread")
    return thread is None or thread.is_alive()


def start(body: "dict | None") -> "tuple[dict, int]":
    """Start a sign-in (``{"method": "auto" | "browser" | "device"}``, default ``auto``). 202 with the sign-in to poll;
    400 for another method; 409 when sign-in is not offered here, one is already waiting for the person, or the
    client refuses before waiting (an unusable credential store, a method that cannot work here)."""
    global _FLOW
    method = str((body or {}).get("method") or "auto")
    if method not in METHODS:
        return {"error": f"method must be one of {', '.join(METHODS)}, not {method!r}"}, 400
    settings, refusal = _target()
    if refusal is not None:
        return refusal
    with _LOCK:
        if _waiting_locked():
            return {"error": "a sign-in is already waiting for you", "flow": _flow_view_locked()}, 409
        flow: dict = {"id": uuid.uuid4().hex, "method": method, "state": "running", "notes": [], "error": None}
        _FLOW = flow

    def notify(text: str) -> None:
        with _LOCK:
            flow["notes"].append(str(text))

    try:
        request, store = _prepare(settings, method, notify)
    except BaseException as e:  # noqa: BLE001 -- never leave a sign-in "running" with nothing behind it
        with _LOCK:
            flow.update(state="error", error=str(e) or type(e).__name__)
        if not isinstance(e, Exception):
            raise
        return {"error": str(e) or type(e).__name__, "flow": _flow_view()}, 409
    thread = threading.Thread(target=_run, args=(flow, settings, request, store, notify), name="compose-api-sign-in",
                              daemon=True)
    with _LOCK:
        flow["thread"] = thread
    thread.start()
    return {"flow": _flow_view()}, 202


def logout() -> "tuple[dict, int]":
    """Sign out as ``compose-api auth logout`` does: the stored login is erased (for the CLI too) and its refresh token
    revoked with Auth0 when Auth0 can be reached (``revocation`` says whether it was). 200
    ``{signed_in: false, revocation}``; 409 when sign-in is not offered here or the store refuses. Runs on the route's
    worker thread (a sync route), on an event loop of its own: the store calls block."""
    global _FLOW
    settings, refusal = _target()
    if refusal is not None:
        return refusal
    from compose_api_client.auth import AuthSession, storage
    from compose_api_client.auth.oauth import Auth0OAuthClient
    from compose_api_client.ext import CliError

    async def end() -> Any:
        # Not parsed first, as the CLI's logout: a record too damaged to read must still be removable.
        session = AuthSession(settings, storage.open_persistent_store(settings, check=False, writable=False))
        async with Auth0OAuthClient(settings) as client:
            return await session.logout(client)

    try:
        result = asyncio.run(end())
    except CliError as e:
        return {"error": str(e)}, 409
    with _LOCK:
        if not _waiting_locked():
            _FLOW = None
    return {"signed_in": False, "revocation": result.revocation}, 200
