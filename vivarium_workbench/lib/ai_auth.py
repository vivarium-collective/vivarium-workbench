"""LLM-provider credentials + model selection for the built-in chat.

Mirrors :mod:`vivarium_workbench.lib.github_auth`: the optional ``keyring``
import pattern, a ``mask_*`` scrubber, and best-effort degradation instead of
crashing. See ``docs/ai-chat.md``.

Where a key lives depends on how the server is bound (``storage_mode``):

* **keyring** — a loopback bind (``127.0.0.1`` / ``localhost`` / ``::1``): the
  OS keyring under service ``vivarium-workbench-llm`` (falls back to process
  memory when no usable keyring backend exists).
* **memory** — any other bind (hosted pod, ``0.0.0.0``): process memory only,
  scoped per ``X-VW-Session`` key, never written to disk or the keyring. The
  model selection is per-session memory too (a shared pod's home dir is not a
  private place to persist anything per user).

Keys are never returned by any route, and every string that may carry one goes
through :func:`mask_key` before it is logged or surfaced.
"""
from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml

# pydantic-ai prints a banner (with an ad) on first import; check_key/build_model
# import it lazily from request handlers, so silence it here, before any of them.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from vivarium_workbench.lib import claude_cli as _claude  # noqa: E402
from vivarium_workbench.lib import csrf as _csrf  # noqa: E402
from vivarium_workbench.lib.atomic_io import atomic_write_text  # noqa: E402
from vivarium_workbench.lib.errors import APIError

log = logging.getLogger(__name__)

KEYRING_SERVICE = "vivarium-workbench-llm"
INSTALL_HINT = "chat extra not installed: pip install 'vivarium-workbench[chat]'"

# Order follows marimo's AI Providers tab (OpenAI, Anthropic, Google, Ollama, OpenCode Go,
# Bedrock, then the generic OpenAI-compatible entry). ``claude-code`` is not in marimo's set: it is
# the user's own signed-in `claude` CLI (lib/claude_cli.py) — no key, loopback-only.
PROVIDERS = ("openai", "anthropic", "google", "ollama", "opencode", "bedrock", "claude-code", "openai-compatible")
Provider = Literal["openai", "anthropic", "google", "ollama", "opencode", "bedrock", "claude-code", "openai-compatible"]

# Ollama: marimo's placeholder base URL; no API key.
OLLAMA_DEFAULT = "http://localhost:11434/v1"
# OpenCode Go: an OpenAI-compatible gateway (marimo lists it as "OpenCode Go"); key required,
# base URL fixed. Its /models list is public, which powers the model dropdown.
OPENCODE_BASE = "https://opencode.ai/zen/go/v1"
StorageMode = Literal["keyring", "memory"]
Source = Literal["keyring", "memory", "environment", "aws", "config", "cli"]

# Providers with no secret to store: only an endpoint. It goes in ai.yaml, never the keychain —
# a URL is not a secret, and a keychain read is what makes macOS ask "python wants to use
# confidential information".
KEYLESS_ENDPOINT_PROVIDERS = ("ollama",)

# Providers whose key is picked up from the ambient environment when none was saved.
ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

LOCAL_HOSTS = _csrf.LOOPBACK_HOSTS

# NAT64 prefixes embed an arbitrary IPv4 (incl. 169.254.169.254) in an IPv6 address.
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))

# Shapes of provider secrets, plus any bearer token. Exact stored values are
# scrubbed as well (see mask_key's ``secrets`` argument).
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{20,}|Bearer\s+[A-Za-z0-9._\-]{16,})"
)


@dataclass(frozen=True)
class Credential:
    api_key: str | None = None
    base_url: str | None = None
    source: Source = "memory"


# ---------------------------------------------------------------------------
# Availability + masking
# ---------------------------------------------------------------------------


CHAT_ENV = "VIVARIUM_WORKBENCH_CHAT"
_OFF = frozenset({"0", "false", "no", "off"})
_ON = frozenset({"1", "true", "yes", "on"})
_DEFAULT_ON = False      # fail closed: a launch path that never calls configure_default (e.g. bare uvicorn) is shared


def default_enabled_for_bind(host: str, *, proxied: bool) -> bool:
    """On by default only where the server is private to this machine: a loopback bind that is not proxied."""
    from vivarium_workbench.lib import csrf
    return csrf.is_loopback_host(host) and not proxied


def configure_default(enabled: bool) -> None:
    """Set what an unset ``VIVARIUM_WORKBENCH_CHAT`` means (``serve`` calls it at start-up; until then it is off)."""
    global _DEFAULT_ON
    _DEFAULT_ON = bool(enabled)


def unavailable_reason() -> str | None:
    """Why the chat cannot be used right now, or ``None`` when it can."""
    if importlib.util.find_spec("pydantic_ai") is None:
        return INSTALL_HINT
    raw = (os.environ.get(CHAT_ENV) or "").strip().lower()
    if raw in _OFF:
        return f"The chat is switched off ({CHAT_ENV}={raw})."
    if raw in _ON or _DEFAULT_ON:
        return None
    return (f"The chat is off by default on a server that is not private to this machine; "
            f"start it with {CHAT_ENV}=1 to enable it.")


def chat_available() -> bool:
    """True when the ``[chat]`` extra is importable and the chat has not been switched off."""
    return unavailable_reason() is None


def require_chat() -> None:
    """Raise the canonical 503 when the chat is unavailable (extra missing, or switched off)."""
    reason = unavailable_reason()
    if reason is not None:
        raise APIError(503, reason)


def mask_key(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Replace any key-shaped token (and every exact ``secrets`` value) in
    ``text`` with ``<redacted>``. Idempotent; safe on already-masked text."""
    for s in secrets:
        if s:
            text = text.replace(s, "<redacted>")
    return _SECRET_RE.sub("<redacted>", text)


# ---------------------------------------------------------------------------
# Storage mode
# ---------------------------------------------------------------------------


def storage_mode(bind_host: str | None, *, proxied: bool = False) -> StorageMode:
    """``keyring`` only for a loopback bind that is NOT behind a proxy/base path.

    Fail closed: an unknown bind (``None`` — e.g. the app imported by another
    ASGI runner) and any proxied deployment (``--trust-proxy``, ``--allowed-origin``,
    ``--base-path`` — a loopback bind behind a local reverse proxy serves many
    users) get per-session memory, never the machine keyring.
    """
    return "keyring" if (bind_host in LOCAL_HOSTS and not proxied) else "memory"


def server_credentials_allowed() -> bool:
    """Operator opt-in (``VIVARIUM_WORKBENCH_CHAT_ALLOW_SERVER_CREDENTIALS=1``) to let
    a *hosted* server's own env/AWS credentials serve every visitor's chat."""
    from vivarium_workbench.lib.env_compat import get_env
    return (get_env("CHAT_ALLOW_SERVER_CREDENTIALS", "") or "").strip().lower() in ("1", "true", "yes")


_LOCK = Lock()
# (scope, provider) -> Credential. ``scope`` is the session key in memory mode
# and "" in keyring mode (single-user machine; used only when the keyring is
# unusable).
_MEMORY: dict[tuple[str, str], Credential] = {}
# scope -> {"provider": ..., "model": ...} — memory-mode selection.
_SELECTION: dict[str, dict[str, str]] = {}


def _scope(mode: StorageMode, session: str | None) -> str:
    return "" if mode == "keyring" else (session or "")


def _keyring():
    try:
        import keyring
        return keyring
    except Exception:  # noqa: BLE001 — a broken keyring must degrade, not crash
        return None


# One keychain read per provider per process (until it is saved/removed), and a failed or
# refused read is not retried for a minute: the UI asks for status several times per page load,
# and on macOS each read of an item created by another program prompts the user.
_KR_CACHE: dict[str, Credential | None] = {}
_KR_FAILED: dict[str, float] = {}
KR_RETRY_S = 60.0


def _keyring_get(provider: str) -> Credential | None:
    with _LOCK:
        if provider in _KR_CACHE:
            return _KR_CACHE[provider]
        if time.monotonic() - _KR_FAILED.get(provider, -KR_RETRY_S) < KR_RETRY_S:
            return None
    kr = _keyring()
    if kr is None:
        return None
    try:
        raw = kr.get_password(KEYRING_SERVICE, provider)
    except Exception as e:  # noqa: BLE001
        log.warning("keyring read failed for %s: %s", provider, mask_key(str(e)))
        with _LOCK:
            _KR_FAILED[provider] = time.monotonic()
        return None
    cred: Credential | None = None
    if raw:
        try:
            d = json.loads(raw)
            cred = Credential(d.get("api_key"), d.get("base_url"), "keyring")
        except (ValueError, AttributeError):
            cred = None
    with _LOCK:
        _KR_CACHE[provider] = cred
    return cred


def _keyring_forget(provider: str) -> None:
    with _LOCK:
        _KR_CACHE.pop(provider, None)
        _KR_FAILED.pop(provider, None)


def _keyring_set(provider: str, cred: Credential) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(KEYRING_SERVICE, provider,
                        json.dumps({"api_key": cred.api_key, "base_url": cred.base_url}))
        _keyring_forget(provider)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("keyring write failed for %s: %s", provider,
                    mask_key(str(e), (cred.api_key or "",)))
        return False


def _keyring_delete(provider: str) -> None:
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(KEYRING_SERVICE, provider)
    except Exception:  # noqa: BLE001 — absent entries are not an error
        pass
    _keyring_forget(provider)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def _aws_credentials_present() -> bool:
    try:
        import boto3
        return boto3.Session().get_credentials() is not None
    except Exception:  # noqa: BLE001
        return False


def get_credential(provider: str, *, mode: StorageMode, session: str | None) -> Credential | None:
    """The credential a chat turn should use: saved first, then (loopback servers,
    or hosted ones whose operator opted in) the server's environment / AWS role.
    A hosted server never lends its own credentials to anonymous sessions by default."""
    ambient = mode == "keyring" or server_credentials_allowed()
    if provider == _claude.PROVIDER:
        # Never lent on a shared server (one login would serve every visitor), and nothing is stored here:
        # "configured" only means the machine's own `claude` says it is signed in.
        return Credential(source="cli") if (mode == "keyring" and _claude.logged_in()) else None
    if provider == "bedrock":
        return Credential(source="aws") if (ambient and _aws_credentials_present()) else None
    if mode == "keyring":
        cfg = _read_cfg()
        # Only providers this app itself saved to the keychain are looked up there: probing every
        # provider on every status call would touch the keychain (and, on macOS, prompt) for nothing.
        if provider in (cfg.get("keyring") or []):
            cred = _keyring_get(provider)
            if cred:
                return cred
        url = (cfg.get("endpoints") or {}).get(provider)
        if url and provider in KEYLESS_ENDPOINT_PROVIDERS:
            return Credential(None, str(url), "config")
    with _LOCK:
        cred = _MEMORY.get((_scope(mode, session), provider))
    if cred:
        return cred
    env = ENV_KEYS.get(provider)
    if ambient and env and os.environ.get(env):
        return Credential(api_key=os.environ[env], source="environment")
    return None


def _check_base_url(url: str, mode: StorageMode) -> str:
    """Validate a user-supplied endpoint. On a hosted (memory-mode) server the
    server itself makes the request, so restrict it to public https hosts —
    otherwise this field is an SSRF primitive against the pod's network."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise APIError(422, "base_url must be an http(s) URL")
    try:
        port = parts.port
    except ValueError:
        raise APIError(422, "base_url has an invalid port") from None
    if mode == "memory":
        if parts.scheme != "https":
            raise APIError(422, "base_url must be https on a hosted server")
        try:
            infos = socket.getaddrinfo(parts.hostname, port or 443, proto=socket.IPPROTO_TCP)
        except OSError as e:
            raise APIError(422, f"base_url host does not resolve: {e}") from e
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global or any(ip in n for n in _NAT64):
                raise APIError(422, "base_url must resolve to a public address on a hosted server")
    return url.strip().rstrip("/")


def validate_request(provider: str, api_key: str | None, base_url: str | None,
                     *, mode: StorageMode) -> tuple[str | None, str | None]:
    """Normalise + validate a save request; returns ``(api_key, base_url)``."""
    if provider not in PROVIDERS:
        raise APIError(422, f"unknown provider '{provider}'", providers=list(PROVIDERS))
    api_key = (api_key or "").strip() or None
    if provider == _claude.PROVIDER:
        if mode != "keyring":
            raise APIError(422, "claude-code is only available on a local (loopback) server")
        if api_key or base_url:
            raise APIError(422, "claude-code takes no key or base_url: sign in with `claude auth login` in a terminal")
        return None, None
    if provider == "bedrock":
        if api_key or base_url:
            raise APIError(422, "bedrock uses the server's ambient AWS credentials; "
                                "no key or base_url is accepted")
        return None, None
    if provider == "openai-compatible":
        if not base_url:
            raise APIError(422, "openai-compatible requires base_url")
        return api_key, _check_base_url(base_url, mode)
    if provider == "ollama":
        # Local model server: no key, default base URL (loopback http is fine on a local
        # bind; a hosted server may only reach a public https endpoint).
        return None, _check_base_url(base_url or OLLAMA_DEFAULT, mode)
    if provider == "opencode":
        if base_url:
            raise APIError(422, "opencode uses a fixed base URL; base_url is not accepted")
        if not api_key:
            raise APIError(422, "opencode requires api_key")
        return api_key, None
    if base_url:
        raise APIError(422, f"base_url is only accepted for openai-compatible, not {provider}")
    if not api_key:
        raise APIError(422, f"{provider} requires api_key")
    return api_key, None


def save_credential(provider: str, api_key: str | None, base_url: str | None,
                    *, mode: StorageMode, session: str | None) -> Source:
    """Store an already-validated credential; returns where it actually landed."""
    cred = Credential(api_key, base_url, "memory")
    if mode == "keyring" and api_key is None and provider in KEYLESS_ENDPOINT_PROVIDERS:
        _update_cfg(lambda c: c.setdefault("endpoints", {}).__setitem__(provider, base_url))
        return "config"
    if mode == "keyring" and _keyring_set(provider, Credential(api_key, base_url, "keyring")):
        _update_cfg(lambda c: c.__setitem__("keyring", sorted({*(c.get("keyring") or []), provider})))
        return "keyring"
    with _LOCK:
        _MEMORY[(_scope(mode, session), provider)] = cred
    return "memory"


def delete_credential(provider: str, *, mode: StorageMode, session: str | None) -> None:
    if mode == "keyring":
        cfg = _read_cfg()
        if provider in (cfg.get("keyring") or []):
            _keyring_delete(provider)
        if provider in (cfg.get("keyring") or []) or provider in (cfg.get("endpoints") or {}):
            def _drop(c: dict) -> None:
                c["keyring"] = [x for x in (c.get("keyring") or []) if x != provider]
                (c.get("endpoints") or {}).pop(provider, None)
            _update_cfg(_drop)
    with _LOCK:
        _MEMORY.pop((_scope(mode, session), provider), None)


# ---------------------------------------------------------------------------
# Selection (provider + model — non-secret)
# ---------------------------------------------------------------------------


def selection_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "vivarium-workbench" / "ai.yaml"


_CFG_LOCK = Lock()


def _read_cfg() -> dict[str, Any]:
    try:
        data = yaml.safe_load(selection_path().read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _update_cfg(mutate) -> None:
    """Read-modify-write ``ai.yaml`` (non-secret: selection, keyless endpoints, which providers
    have a keychain entry) so unrelated keys survive."""
    with _CFG_LOCK:
        cfg = _read_cfg()
        mutate(cfg)
        path = selection_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, yaml.safe_dump(cfg, sort_keys=True))


def get_selection(*, mode: StorageMode, session: str | None) -> dict[str, str] | None:
    if mode == "memory":
        with _LOCK:
            sel = _SELECTION.get(session or "")
        return dict(sel) if sel else None
    data = _read_cfg()
    if data.get("provider") and data.get("model"):
        return {"provider": str(data["provider"]), "model": str(data["model"])}
    return None


def set_selection(provider: str, model: str, *, mode: StorageMode, session: str | None) -> None:
    if provider not in PROVIDERS:
        raise APIError(422, f"unknown provider '{provider}'", providers=list(PROVIDERS))
    if not model.strip():
        raise APIError(422, "model is required")
    sel = {"provider": provider, "model": model.strip()}
    if mode == "memory":
        with _LOCK:
            _SELECTION[session or ""] = sel
        return
    _update_cfg(lambda c: c.update(sel))


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def status(*, mode: StorageMode, session: str | None) -> dict[str, Any]:
    """The ``GET /api/ai/status`` body — never contains a key."""
    providers = []
    off = unavailable_reason() is not None      # switched off: do not read the keyring (an OS prompt) for a dead feature
    for p in PROVIDERS:
        cred = None if off else get_credential(p, mode=mode, session=session)
        providers.append({
            "id": p,
            "configured": cred is not None,
            "source": cred.source if cred else None,
            "base_url": cred.base_url if cred else None,
        })
    return {
        "available": chat_available(),
        "reason": unavailable_reason(),
        "providers": providers,
        "selected": None if off else get_selection(mode=mode, session=session),
        "storage_mode": mode,
    }


# ---------------------------------------------------------------------------
# Model construction + live key check (needs the [chat] extra)
# ---------------------------------------------------------------------------


# Headers an OpenAI-style provider call may carry. Anything else is dropped before it leaves the process: the SDKs
# fall back to the *server's* environment (``OPENAI_ORG_ID``, ``OPENAI_PROJECT_ID``, ``OPENAI_CUSTOM_HEADERS``,
# ``OLLAMA_API_KEY`` …) and, with a user-named ``base_url``, that would send the operator's values to an endpoint the
# user chose. ``x-stainless-*`` is the SDK's own (non-secret) client telemetry.
_EGRESS_HEADERS = frozenset({"host", "authorization", "content-type", "content-length", "accept", "accept-encoding",
                             "connection", "user-agent", "idempotency-key"})


async def _strip_foreign_headers(request) -> None:
    for name in [h for h in request.headers if h.lower() not in _EGRESS_HEADERS and not h.lower().startswith("x-stainless-")]:
        del request.headers[name]


def _egress_client():
    """An ``httpx`` client for a provider call that sends nothing but the allow-listed headers."""
    import httpx
    from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT
    return httpx.AsyncClient(timeout=httpx.Timeout(DEFAULT_HTTP_TIMEOUT, connect=5),
                             event_hooks={"request": [_strip_foreign_headers]})


def build_model(provider: str, model: str, cred: Credential):
    """A pydantic-ai ``Model`` for ``provider``/``model`` using ``cred``."""
    require_chat()
    if provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        return AnthropicModel(model, provider=AnthropicProvider(api_key=cred.api_key))
    if provider in ("openai", "openai-compatible"):
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        # An openai-compatible endpoint (Ollama, vLLM) may need no key, but the
        # SDK refuses an empty one — a placeholder is the documented convention.
        key = cred.api_key or ("unused" if provider == "openai-compatible" else None)
        return OpenAIChatModel(model, provider=OpenAIProvider(base_url=cred.base_url, api_key=key, http_client=_egress_client()))
    if provider == "ollama":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.ollama import OllamaProvider
        # An explicit placeholder key (else the provider sends the server's OLLAMA_API_KEY to the user's URL) and an
        # egress client that drops any header the SDK took from the server's environment.
        return OpenAIChatModel(model, provider=OllamaProvider(
            base_url=cred.base_url or OLLAMA_DEFAULT, api_key="ollama", http_client=_egress_client()))
    if provider == "opencode":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        return OpenAIChatModel(model, provider=OpenAIProvider(base_url=OPENCODE_BASE, api_key=cred.api_key or "unused", http_client=_egress_client()))
    if provider == "google":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider
        return GoogleModel(model, provider=GoogleProvider(api_key=cred.api_key))
    if provider == "bedrock":
        from pydantic_ai.models.bedrock import BedrockConverseModel
        return BedrockConverseModel(model)
    if provider == _claude.PROVIDER:
        raise APIError(422, "claude-code runs its own loop (lib/ai_claude_code.py); it is not a pydantic-ai model")
    raise APIError(422, f"unknown provider '{provider}'")


#: Total seconds a key check may take. Without it a server that accepts the connection and never answers holds the
#: request (and its worker) open for as long as the peer likes.
CHECK_KEY_TIMEOUT = 20.0


async def check_key(provider: str, model: str, cred: Credential) -> None:
    """One minimal real request (a 1-token completion) to prove the key works.

    401/403 → ``APIError(401)``; 404 → ``APIError(422)`` (unknown model); any
    other failure → ``APIError(502)``. Every message is passed through
    :func:`mask_key`.
    """
    require_chat()
    if provider == _claude.PROVIDER:
        try:
            async with asyncio.timeout(CHECK_KEY_TIMEOUT):
                await _claude.check(model)
        except TimeoutError:
            raise APIError(504, f"claude did not answer within {CHECK_KEY_TIMEOUT:g} s") from None
        except _claude.ClaudeCliError as e:
            msg = mask_key(str(e))
            raise APIError(401 if "not signed in" in msg else 422 if "model" in msg.lower() else 502, msg) from None
        return
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelHTTPError

    secrets = (cred.api_key or "",)
    try:
        async with asyncio.timeout(CHECK_KEY_TIMEOUT):
            await Agent(build_model(provider, model, cred)).run(
                "ping", model_settings={"max_tokens": 1})
    except TimeoutError:
        raise APIError(504, f"{provider} did not answer within {CHECK_KEY_TIMEOUT:g} s") from None
    except ModelHTTPError as e:
        msg = mask_key(str(e), secrets)
        if e.status_code in (401, 403):
            raise APIError(401, f"{provider} rejected the credentials: {msg}") from None
        if e.status_code == 404:
            raise APIError(422, f"{provider} does not know model '{model}': {msg}") from None
        raise APIError(502, f"{provider} check failed ({e.status_code}): {msg}") from None
    except APIError:
        raise
    except Exception as e:  # noqa: BLE001 — network/SDK errors: surface, masked
        msg = mask_key(str(e), secrets)
        if provider in ("ollama", "openai-compatible") and "onnect" in msg:
            where = cred.base_url or "the configured endpoint"
            hint = "is Ollama running? start it with `ollama serve`" if provider == "ollama" else "is the server running?"
            msg = f"could not connect to {where} — {hint} ({msg})"
        raise APIError(502, f"{provider} check failed: {msg}") from None


# ---------------------------------------------------------------------------
# Installed Ollama models (the model dropdown shows what the user actually has)
# ---------------------------------------------------------------------------

MAX_OLLAMA_MODELS = 200
_MAX_TAGS_BYTES = 1_000_000


async def list_ollama_models(*, base_url: str | None, mode: StorageMode, session: str | None) -> dict[str, Any]:
    """The models installed in an Ollama server (its ``/api/tags``). Same SSRF rules as a save
    (loopback ``http`` only on a local bind, public ``https`` on a hosted one), no redirects, no key
    sent, response size capped. Ollama has no model catalogue to consult otherwise: marimo's static
    list names models a given machine may never have pulled."""
    require_chat()
    import asyncio

    import httpx

    saved = await asyncio.to_thread(get_credential, "ollama", mode=mode, session=session)
    raw = base_url or (saved.base_url if saved else None) or OLLAMA_DEFAULT
    base = await asyncio.to_thread(_check_base_url, raw, mode)      # DNS + blocking: off the event loop
    root = base[:-3] if base.endswith("/v1") else base
    url = root.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0), follow_redirects=False) as c:
            async with c.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise APIError(502, f"{url} answered {resp.status_code}")
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) > _MAX_TAGS_BYTES:
                        raise APIError(502, f"{url} answered with too much data")
    except httpx.HTTPError as e:
        raise APIError(502, f"could not reach Ollama at {root} — is it running? (`ollama serve`) ({e})") from None
    try:
        names = [m.get("name") for m in json.loads(body).get("models", [])]
    except (ValueError, AttributeError):
        raise APIError(502, f"{url} did not return a model list") from None
    models = sorted({n[:200] for n in names if isinstance(n, str) and n})[:MAX_OLLAMA_MODELS]
    return {"models": models, "source": url}
