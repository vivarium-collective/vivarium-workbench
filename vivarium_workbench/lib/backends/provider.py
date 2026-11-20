"""Backend providers: what a plugin registers (docs/backend-adapters.md §2.2, step P1c).

A provider brings backends and answers the questions only it can answer: whether a NEW run is its own (a session
build, a pinned deployment) and what the backend at a named URL is (its half of the capability probe). The core
asks; it never imports a plugin's adapters itself.

Until step P4 the one provider is the in-tree SMS plugin, found by importing ``vivarium_workbench_sms.provider``
(the single core->plugin edge the import-linter ratchet keeps for it); P4 replaces that import with an entry point.

``VIVARIUM_WORKBENCH_BACKEND_PROVIDERS=none`` turns providers off -- the core then ignores SMS when selecting,
probing and routing (step P3's core-only CI job uses it). That is not "plugin absent": the package is still
installed, and the core cannot be imported without it until step P2 finishes.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Protocol

if TYPE_CHECKING:
    import argparse

    from vivarium_workbench.lib.backend_config import OpenApiProbe
    from vivarium_workbench.lib.backends import Backend
    from vivarium_workbench.lib.env_worker_launcher import WorkerLauncher

#: Set to ``none`` to turn every provider off (step P3's core-only CI job).
PROVIDERS_ENV = "VIVARIUM_WORKBENCH_BACKEND_PROVIDERS"
_PLUGIN = "vivarium_workbench_sms"


@dataclass(frozen=True)
class Recognised:
    """A provider's half of the capability probe (``server_capabilities.backend_profile``).

    ``profile`` has the profile's usual fields; ``has_composites`` says the capability list carries
    ``viva-v1-composites`` (the core asks compose-api's OpenAPI only when it does not, exactly as before);
    ``stop`` says the backend could not be asked, so the profile (``reachable: False``) is the answer."""

    profile: dict
    has_composites: bool
    stop: bool


@dataclass(frozen=True)
class Command:
    """A subcommand a provider adds to ``vivarium-workbench`` (``Provider.cli``). The core owns argparse: it adds the
    subparser, ``configure`` fills in its arguments and ``run`` is its handler (exit code). ``uses_backend`` puts it
    with ``cli.BACKEND_COMMANDS``: the one-backend check and the hosted-worker check run before it."""

    name: str
    help: str
    configure: "Callable[[argparse.ArgumentParser], None]"
    run: "Callable[[argparse.Namespace], int]"
    uses_backend: bool


class Provider(Protocol):
    """What a backend plugin registers. Only the methods a step needs exist; later steps add theirs (§2.1)."""

    name: str

    def backends(self) -> "tuple[Backend, ...]":
        """Its backends, built once: the same objects on every call."""
        ...

    def select(self, ws_root: Path) -> "Optional[Backend]":
        """Its claim on a NEW run of ``ws_root`` (no network call), or ``None``."""
        ...

    def recognise(self, client: object) -> Recognised:
        """Its half of the capability probe of the backend ``client`` talks to. Never raises."""
        ...

    # -- P2 Workers --
    def worker_launcher(self) -> "Optional[WorkerLauncher]":
        """The launcher for this deployment's hosted env workers, read from the provider's own deployment config, or
        ``None`` when this deployment configures none. Called once per pool (the pool caches it); no network call."""
        ...

    # -- P2 Client --
    def probe_client(self, base_url: str) -> "OpenApiProbe":
        """The client its ``recognise`` reads ``base_url`` with. It must also answer ``openapi_operations()`` (the
        core's compose-api detection runs on the same client, after ``recognise``). No network call."""
        ...

    # -- P2 CLI --
    def cli(self) -> "tuple[Command, ...]":
        """Its subcommands. Built on every call (the CLI calls it once per process), never cached, so a test can patch
        a handler on the provider's module."""
        ...


_lock = threading.RLock()
_providers: "Optional[tuple[Provider, ...]]" = None


def providers() -> "tuple[Provider, ...]":
    """The active providers (cached). Empty when turned off, or when the plugin package is not installed. A plugin
    that is installed but broken (a missing dependency inside it, an error at import) raises: it must not look
    absent."""
    global _providers
    with _lock:
        if _providers is None:
            _providers = _load()
        return _providers


def _load() -> "tuple[Provider, ...]":
    if (os.environ.get(PROVIDERS_ENV) or "").strip().lower() == "none":
        return ()
    try:
        from vivarium_workbench_sms import provider as sms_provider
    except ModuleNotFoundError as e:
        if e.name in (_PLUGIN, f"{_PLUGIN}.provider"):
            return ()
        raise
    return (sms_provider.PROVIDER,)


def reset() -> None:
    """Forget the providers (tests; a changed ``PROVIDERS_ENV``). ``backends.reset_registry`` also forgets what was
    built from them."""
    global _providers
    with _lock:
        _providers = None
