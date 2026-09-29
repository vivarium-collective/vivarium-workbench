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

import importlib.util
import ipaddress
import json
import logging
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml

from vivarium_workbench.lib.atomic_io import atomic_write_text
from vivarium_workbench.lib.errors import APIError

log = logging.getLogger(__name__)

KEYRING_SERVICE = "vivarium-workbench-llm"
INSTALL_HINT = "chat extra not installed: pip install 'vivarium-workbench[chat]'"

PROVIDERS = ("anthropic", "openai", "google", "openai-compatible", "bedrock")
Provider = Literal["anthropic", "openai", "google", "openai-compatible", "bedrock"]
StorageMode = Literal["keyring", "memory"]
Source = Literal["keyring", "memory", "environment", "aws"]

# Providers whose key is picked up from the ambient environment when none was saved.
ENV_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

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


def chat_available() -> bool:
    """True when the optional ``[chat]`` extra (pydantic-ai) is importable."""
    return importlib.util.find_spec("pydantic_ai") is not None


def require_chat() -> None:
    """Raise the canonical 503 when the ``[chat]`` extra is missing."""
    if not chat_available():
        raise APIError(503, INSTALL_HINT)


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


def _keyring_get(provider: str) -> Credential | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        raw = kr.get_password(KEYRING_SERVICE, provider)
    except Exception as e:  # noqa: BLE001
        log.warning("keyring read failed for %s: %s", provider, mask_key(str(e)))
        return None
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return Credential(d.get("api_key"), d.get("base_url"), "keyring")
    except (ValueError, AttributeError):
        return None


def _keyring_set(provider: str, cred: Credential) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(KEYRING_SERVICE, provider,
                        json.dumps({"api_key": cred.api_key, "base_url": cred.base_url}))
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
    if provider == "bedrock":
        return Credential(source="aws") if (ambient and _aws_credentials_present()) else None
    if mode == "keyring":
        cred = _keyring_get(provider)
        if cred:
            return cred
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
    if provider == "bedrock":
        if api_key or base_url:
            raise APIError(422, "bedrock uses the server's ambient AWS credentials; "
                                "no key or base_url is accepted")
        return None, None
    if provider == "openai-compatible":
        if not base_url:
            raise APIError(422, "openai-compatible requires base_url")
        return api_key, _check_base_url(base_url, mode)
    if base_url:
        raise APIError(422, f"base_url is only accepted for openai-compatible, not {provider}")
    if not api_key:
        raise APIError(422, f"{provider} requires api_key")
    return api_key, None


def save_credential(provider: str, api_key: str | None, base_url: str | None,
                    *, mode: StorageMode, session: str | None) -> Source:
    """Store an already-validated credential; returns where it actually landed."""
    cred = Credential(api_key, base_url, "memory")
    if mode == "keyring" and _keyring_set(provider, Credential(api_key, base_url, "keyring")):
        return "keyring"
    with _LOCK:
        _MEMORY[(_scope(mode, session), provider)] = cred
    return "memory"


def delete_credential(provider: str, *, mode: StorageMode, session: str | None) -> None:
    if mode == "keyring":
        _keyring_delete(provider)
    with _LOCK:
        _MEMORY.pop((_scope(mode, session), provider), None)


# ---------------------------------------------------------------------------
# Selection (provider + model — non-secret)
# ---------------------------------------------------------------------------


def selection_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "vivarium-workbench" / "ai.yaml"


def get_selection(*, mode: StorageMode, session: str | None) -> dict[str, str] | None:
    if mode == "memory":
        with _LOCK:
            sel = _SELECTION.get(session or "")
        return dict(sel) if sel else None
    try:
        data = yaml.safe_load(selection_path().read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if isinstance(data, dict) and data.get("provider") and data.get("model"):
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
    path = selection_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, yaml.safe_dump(sel, sort_keys=True))


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def status(*, mode: StorageMode, session: str | None) -> dict[str, Any]:
    """The ``GET /api/ai/status`` body — never contains a key."""
    providers = []
    for p in PROVIDERS:
        cred = get_credential(p, mode=mode, session=session)
        providers.append({
            "id": p,
            "configured": cred is not None,
            "source": cred.source if cred else None,
            "base_url": cred.base_url if cred else None,
        })
    return {
        "available": chat_available(),
        "providers": providers,
        "selected": get_selection(mode=mode, session=session),
        "storage_mode": mode,
    }


# ---------------------------------------------------------------------------
# Model construction + live key check (needs the [chat] extra)
# ---------------------------------------------------------------------------


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
        return OpenAIChatModel(model, provider=OpenAIProvider(base_url=cred.base_url, api_key=key))
    if provider == "google":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider
        return GoogleModel(model, provider=GoogleProvider(api_key=cred.api_key))
    if provider == "bedrock":
        from pydantic_ai.models.bedrock import BedrockConverseModel
        return BedrockConverseModel(model)
    raise APIError(422, f"unknown provider '{provider}'")


async def check_key(provider: str, model: str, cred: Credential) -> None:
    """One minimal real request (a 1-token completion) to prove the key works.

    401/403 → ``APIError(401)``; 404 → ``APIError(422)`` (unknown model); any
    other failure → ``APIError(502)``. Every message is passed through
    :func:`mask_key`.
    """
    require_chat()
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelHTTPError

    secrets = (cred.api_key or "",)
    try:
        await Agent(build_model(provider, model, cred)).run(
            "ping", model_settings={"max_tokens": 1})
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
        raise APIError(502, f"{provider} check failed: {mask_key(str(e), secrets)}") from None
