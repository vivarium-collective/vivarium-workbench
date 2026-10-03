"""Builders for the ``/api/ai/*`` routes (provider credentials + model choice).

Thin orchestration over :mod:`vivarium_workbench.lib.ai_auth`; the routes in
``api/app.py`` resolve the storage mode and session key and delegate here.
Nothing returned from this module ever contains an API key.
"""
from __future__ import annotations

import asyncio
from typing import Any

from vivarium_workbench.lib import ai_auth
from vivarium_workbench.lib.ai_auth import StorageMode
from vivarium_workbench.lib.errors import APIError
from vivarium_workbench.lib.models import AiCredentialsRequest, AiSelectRequest


def ai_status(mode: StorageMode, session: str | None) -> dict[str, Any]:
    """``GET /api/ai/status`` — 200 even without the extra (``available: false``
    lets the UI show the install hint instead of an error)."""
    return ai_auth.status(mode=mode, session=session)


async def ai_save_credentials(body: AiCredentialsRequest, mode: StorageMode,
                              session: str | None) -> dict[str, Any]:
    """``POST /api/ai/credentials`` — validate, prove the key with one real
    1-token request, store it, and select the provider/model. The key is only
    stored after the provider accepted it."""
    ai_auth.require_chat()
    # Everything that can block — keychain reads (macOS may show a prompt and wait for the user),
    # DNS resolution for the hosted SSRF check, file writes — runs off the event loop, so a slow
    # answer can't freeze every other request this server is handling.
    off = asyncio.to_thread
    raw_key = body.api_key
    if body.provider == "opencode" and not (raw_key or "").strip():
        # fixed base URL => re-saving without retyping the key can't send it anywhere new
        prior = await off(ai_auth.get_credential, "opencode", mode=mode, session=session)
        if prior and prior.source in ("keyring", "memory"):
            raw_key = prior.api_key
    api_key, base_url = await off(
        ai_auth.validate_request, body.provider, raw_key, body.base_url, mode=mode)
    # Re-saving an openai-compatible endpoint without retyping the key keeps the
    # saved key (the form never shows it) instead of silently dropping it.
    # ONLY for the same endpoint: sending the saved key to a different base_url
    # would hand it to whoever runs that host.
    if body.provider == "openai-compatible" and api_key is None:
        existing = await off(ai_auth.get_credential, body.provider, mode=mode, session=session)
        if existing and existing.source in ("keyring", "memory") and existing.base_url == base_url:
            api_key = existing.api_key
    cred = ai_auth.Credential(api_key, base_url, "memory")
    if body.provider == "bedrock":
        got = await off(ai_auth.get_credential, "bedrock", mode=mode, session=session)
        if got is None:
            raise APIError(422, "no ambient AWS credentials found on the server")
        cred = got
    await ai_auth.check_key(body.provider, body.model, cred)
    # Nothing is stored for bedrock (ambient AWS credentials) or claude-code (the machine's own `claude` login).
    source: str = {"bedrock": "aws", "claude-code": "cli"}.get(body.provider, "")
    if not source:
        source = await off(ai_auth.save_credential, body.provider, api_key, base_url, mode=mode, session=session)
    await off(ai_auth.set_selection, body.provider, body.model, mode=mode, session=session)
    return {"ok": True, "provider": body.provider, "model": body.model, "source": source}


def ai_delete_credentials(provider: str, mode: StorageMode, session: str | None) -> dict[str, Any]:
    """``DELETE /api/ai/credentials/{provider}`` — forget a saved key."""
    if provider not in ai_auth.PROVIDERS:
        raise APIError(422, f"unknown provider '{provider}'", providers=list(ai_auth.PROVIDERS))
    ai_auth.delete_credential(provider, mode=mode, session=session)
    return {"ok": True, "provider": provider}


def ai_select(body: AiSelectRequest, mode: StorageMode, session: str | None) -> dict[str, Any]:
    """``POST /api/ai/select`` — switch provider/model for an already-configured provider."""
    ai_auth.require_chat()
    if ai_auth.get_credential(body.provider, mode=mode, session=session) is None:
        raise APIError(409, f"{body.provider} has no credentials yet — save them first")
    ai_auth.set_selection(body.provider, body.model, mode=mode, session=session)
    return {"ok": True, "provider": body.provider, "model": body.model}


def ai_capabilities(app: Any) -> dict[str, Any]:
    """``GET /api/ai/capabilities`` — what the assistant can reach (Capabilities popover)."""
    from vivarium_workbench.lib import ai_tools
    ai_auth.require_chat()
    return ai_tools.capabilities(app)


async def ai_ollama_models(base_url: str | None, mode: StorageMode, session: str | None) -> dict[str, Any]:
    """``POST /api/ai/ollama-models`` — the models installed in the user's Ollama (feeds the model dropdown)."""
    return await ai_auth.list_ollama_models(base_url=base_url, mode=mode, session=session)
