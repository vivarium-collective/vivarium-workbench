"""The remote-backend configuration, core plus providers (docs/backend-adapters.md §2.3, step P3 C1).

``backend_config`` holds the core's half (the named URL, the neutral deployment default) and may not ask providers
(an import-linter contract). This module adds each active provider's half, read through ``Provider.check_config`` /
``base_url`` / ``configured`` / ``deployment_default``: a provider's own settings (the SMS plugin's ``VIVA_API_BASE``
/ ``SMS_API_BASE`` aliases), its default URL and its default deployment name. Core callers read the configuration
here, never through ``backend_config`` directly.

With providers off, or the plugin absent, only the named URL counts: a provider alias that is set is ignored. At
server start-up (``serve``, ``startup.serve_fastapi``, ``python -m vivarium_workbench.api``; step P4b2)
:func:`check_unclaimed_backend_env` refuses it when no provider is installed, and :func:`stray_backend_env` names it
for a warning when providers are turned off (``VIVARIUM_WORKBENCH_BACKEND_PROVIDERS=none``). Other processes (the
``run`` / ``rerun`` / ``run-composite`` children on a workspace interpreter, env workers) inherit the aliases and
never refuse them.

Everything reads the environment and the provider registry only: no network call. A provider that is installed but
broken raises (``ImportError``) as the registry does; the process entry points catch it.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from vivarium_workbench.lib.backends.provider import Provider


def _providers() -> "tuple[Provider, ...]":
    from vivarium_workbench.lib.backends import provider as _provider

    return _provider.providers()


def check_config(named: Optional[str] = None) -> None:
    """The one-backend rule, at process entry: the named URL (``named``, the flag's value before it reaches the
    environment, else ``VIVARIUM_WORKBENCH_BACKEND_BASE_URL``) is valid (``backend_config.named_backend``), and every
    active provider's own settings agree with it (``Provider.check_config``). Raises
    ``backend_config.BackendConfigError``; a broken provider raises ``ImportError``. Also builds the backend registry
    (cheap: no network call), so conflicting providers (``ProviderConflictError``, an ``ImportError``: two backends
    of one kind, one named ``compose-api``) refuse at start-up rather than on the first request."""
    from vivarium_workbench.lib import backend_config

    want = backend_config.named_backend(named)
    for p in _providers():
        p.check_config(want)
    from vivarium_workbench.lib import backends

    backends._registry()


def configured_base_url() -> Optional[str]:
    """The URL a remote backend is asked at: the named one, else the first active provider's ``base_url()`` (the SMS
    plugin: its alias, else its tunnel default), else ``None`` (nothing to ask). Never raises for a missing setting."""
    from vivarium_workbench.lib import backend_config

    named = backend_config.configured_base_url()
    if named:
        return named
    for p in _providers():
        url = p.base_url()
        if url:
            return url
    return None


def backend_configured() -> bool:
    """Whether the operator configured a backend at all: one is named, or an active provider's own setting is set
    (``Provider.configured``). A provider's default URL alone is not configuration."""
    from vivarium_workbench.lib import backend_config

    return bool(backend_config.explicit_backend_base_url()) or any(p.configured() for p in _providers())


def deployment_name() -> str:
    """The deployment namespace a remote run targets: ``backend_config.deployment_name``, with the first active
    provider's ``deployment_default()`` as the default (else the core's neutral one)."""
    from vivarium_workbench.lib import backend_config

    default = next((d for d in (p.deployment_default() for p in _providers()) if d), None)
    return backend_config.deployment_name(default=default)


def deployment_names_now() -> set[str]:
    """Every deployment name the current configuration could have written on a row before step 1c (the legacy guard
    in ``records.resolve_existing``): the configured name, the named URL's host, the host of each provider alias that
    is set (when a provider is active to claim it), and every active provider's ``deployment_default()`` when no
    name is configured. Best-effort only."""
    from urllib.parse import urlsplit

    from vivarium_workbench.lib import backend_config

    providers = _providers()
    names = {deployment_name()}
    for var in backend_config.BACKEND_BASE_ENV_VARS if providers else (backend_config.BACKEND_BASE_ENV,):
        host = urlsplit(os.environ.get(var) or "").hostname
        if host:
            names.add(host)
    if not (os.environ.get("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT") or "").strip():
        names.update(d for d in (p.deployment_default() for p in providers) if d)
    return names


def _providers_turned_off() -> bool:
    from vivarium_workbench.lib.backends.provider import PROVIDERS_ENV

    return (os.environ.get(PROVIDERS_ENV) or "").strip().lower() == "none"


def _set_provider_env() -> list[str]:
    """The provider settings that are set: each alias (``backend_config.BACKEND_BASE_ENV_VARS[1:]``) with a value, then
    the provider's pinned-run switch when it is truthy (under the spelling that is set)."""
    from vivarium_workbench.lib import backend_config
    from vivarium_workbench.lib.env_compat import NEW_PREFIX, OLD_PREFIX, get_env

    found = [v for v in backend_config.BACKEND_BASE_ENV_VARS[1:] if os.environ.get(v)]
    pin = backend_config.PROVIDER_PIN_ENV
    if (get_env(pin, "") or "").strip().lower() in backend_config.PROVIDER_PIN_TRUTHY:
        found.append(NEW_PREFIX + pin if NEW_PREFIX + pin in os.environ else OLD_PREFIX + pin)
    return found


def unclaimed_backend_env() -> list[str]:
    """The provider settings (the aliases, the truthy pinned-run switch) that are set while no provider is active to
    read them. Empty whenever a provider is active."""
    if _providers():
        return []
    return _set_provider_env()


def check_unclaimed_backend_env() -> None:
    """Server start-up only (step P4b2): refuse a provider setting that is set while no provider is installed to read
    it and providers are not turned off -- most likely an install (an image, a venv) built without the plugin. Raises
    ``backend_config.BackendConfigError``. Under ``VIVARIUM_WORKBENCH_BACKEND_PROVIDERS=none`` it does nothing (the
    operator chose no providers; :func:`stray_backend_env` warns instead).

    Not part of :func:`check_config`: the ``run`` / ``rerun`` / ``run-composite`` children run on a workspace
    interpreter that may not have the plugin, inherit the server's environment, and must keep working; env workers
    likewise."""
    from vivarium_workbench.lib import backend_config
    from vivarium_workbench.lib.backends.provider import PROVIDERS_ENV

    if _providers_turned_off():
        return
    names = unclaimed_backend_env()
    if not names:
        return
    one = len(names) == 1
    joined = ", ".join(names)
    raise backend_config.BackendConfigError(
        f"{joined} {'is' if one else 'are'} set, but no backend provider is installed to read "
        f"{'it' if one else 'them'}; install vivarium-workbench[vivaapi], or unset {joined} (or set "
        f"{PROVIDERS_ENV}=none to run without providers)")


def stray_backend_env() -> list[str]:
    """The provider settings that are set while providers are turned off (``VIVARIUM_WORKBENCH_BACKEND_PROVIDERS=none``):
    ignored, and named in the start-up warning. Empty otherwise: with a provider active they are read, and with none
    installed :func:`check_unclaimed_backend_env` refuses them."""
    if not _providers_turned_off():
        return []
    return unclaimed_backend_env()


def no_providers_note() -> Optional[str]:
    """The neutral start-up line for a server with no backend provider installed (and providers not turned off), so
    a user who expected remote-backend features has a breadcrumb; ``None`` otherwise (step P4b2, D4)."""
    if _providers_turned_off() or _providers():
        return None
    return "backend providers: none installed"
