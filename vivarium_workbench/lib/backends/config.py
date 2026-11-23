"""The remote-backend configuration, core plus providers (docs/backend-adapters.md §2.3, step P3 C1).

``backend_config`` holds the core's half (the named URL, the neutral deployment default) and may not ask providers
(an import-linter contract). This module adds each active provider's half, read through ``Provider.check_config`` /
``base_url`` / ``configured`` / ``deployment_default``: a provider's own settings (the SMS plugin's ``VIVA_API_BASE``
/ ``SMS_API_BASE`` aliases), its default URL and its default deployment name. Core callers read the configuration
here, never through ``backend_config`` directly.

With providers off, or the plugin absent, only the named URL counts: a provider alias that is set is ignored, and
:func:`stray_backend_env` names it for the start-up warning (step P4b refuses it).

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


def stray_backend_env() -> list[str]:
    """The provider alias env vars that are set while no provider is active to read them: ignored, and named in the
    start-up warning (step P4b refuses them). Empty whenever a provider is active."""
    from vivarium_workbench.lib import backend_config

    if _providers():
        return []
    return [v for v in backend_config.BACKEND_BASE_ENV_VARS[1:] if os.environ.get(v)]
