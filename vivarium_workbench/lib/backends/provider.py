"""Backend providers: what a plugin registers (docs/backend-adapters.md §2.2, step P1c).

A provider brings backends and answers the questions only it can answer: whether a NEW run is its own (a session
build, a pinned deployment) and what the backend at a named URL is (its half of the capability probe). The core
asks; it never imports a plugin's adapters itself.

Providers are found through the ``vivarium_workbench.backends`` entry-point group (step P4a): each entry point names
a provider object whose ``name`` equals the entry point's name and whose ``api_version`` equals
:data:`PROVIDER_API_VERSION`. The core never imports a plugin by name (the import-linter contract has no exceptions).

* An entry point whose top-level package is not installed (``ModuleNotFoundError`` naming it, or only a namespace
  left by a ``__pycache__`` directory: the metadata outlived the code, as in the core-only CI job) is skipped: the
  plugin is absent. A missing submodule of a present package is a broken plugin.
* Anything else that fails to load raises: an ``ImportError`` (a missing dependency inside the plugin, a missing
  object), a :class:`ProviderVersionError`, a name mismatch, two entry points with one name; and, when the
  registry is built (``backends.config.check_config`` does it at every process entry), a
  :class:`ProviderConflictError`. Other exceptions from the plugin's import (``SyntaxError``, ``RuntimeError``)
  propagate unchanged. An installed plugin must never look absent; the CLI and the server report these as "the
  backend plugin failed to load" and exit.
* The in-tree plugin's package present with no entry point naming it is a stale install (an editable install made
  before P4a) and raises (until step P4b, which splits the plugin into its own distribution).

``VIVARIUM_WORKBENCH_BACKEND_PROVIDERS=none`` turns providers off -- the core then ignores every provider when
selecting, probing and routing (step P3's core-only CI job uses it), whether or not one is installed.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Protocol

if TYPE_CHECKING:
    import argparse

    from fastapi import APIRouter
    from pydantic import BaseModel

    from vivarium_workbench.lib.backend_config import OpenApiProbe
    from vivarium_workbench.lib.backends import Backend
    from vivarium_workbench.lib.env_worker_launcher import WorkerLauncher

#: Set to ``none`` to turn every provider off (step P3's core-only CI job).
PROVIDERS_ENV = "VIVARIUM_WORKBENCH_BACKEND_PROVIDERS"
#: The entry-point group a provider registers under (``[project.entry-points."vivarium_workbench.backends"]``).
ENTRY_POINT_GROUP = "vivarium_workbench.backends"
#: The provider interface's major version. Bump it only when the Protocol changes incompatibly; a provider declares
#: the version it targets as a literal ``api_version`` and is refused at start-up when the two differ.
PROVIDER_API_VERSION = 1
#: The in-tree plugin's package, named only by the stale-install check (removed at step P4b).
_PLUGIN = "vivarium_workbench_sms"


class ProviderVersionError(ImportError):
    """A provider targets another provider-interface version than this workbench offers. An ``ImportError``, so the
    existing "the backend plugin failed to load" reporters print it and exit."""


class ProviderConflictError(ImportError):
    """The active providers' backends conflict (two of one kind, one named like the core's compose-api, two unrouted
    facets). Raised when the registry is built, which ``backends.config.check_config`` does at every process entry; an
    ``ImportError``, so the same reporters print it and exit."""


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
class SessionBuild:
    """This workspace is a backend's build, checked out for this session (``Provider.session_build``, step P2 Builds).

    The fields are what the build's record says, raw (``None`` where it says nothing); ``readable`` is False when the
    record exists but cannot be read, and then every other field is ``None``."""

    readable: bool
    id: "int | str | None"  # the backend's build id, as recorded (SMS: the simulator id)
    repo_url: "str | None"
    commit: "str | None"
    branch: "str | None"


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


@dataclass(frozen=True)
class ProviderRouter:
    """Routes only a provider serves (``Provider.router``). ``create_app`` adds ``router``'s routes to the app, flat,
    just before the SPA catch-all (``api.app._include_provider_routers``): a path and method the app already serves is
    refused, and so is a ``readonly_allowed`` or ``ai_excluded`` path that is not one of ``router``'s own routes.
    ``readonly_allowed`` is the POST/DELETE paths a read-only deployment keeps (GETs are always kept); ``ai_excluded``
    is the paths the built-in AI must never see or call (they push to git; ``lib/ai_tools.excluded_paths`` adds them to
    its own ``EXCLUDED_PATHS``)."""

    router: "APIRouter"
    readonly_allowed: frozenset[str]
    ai_excluded: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Detached:
    """``runs.dispatch_composite``'s answer when the card's run goes through the detached deployment runner
    (``run_runner`` -> ``remote_run.run_remote`` -> ``runs.run_composite``; step P2 Builds B2). ``build_ref`` is
    written into the run request and carried unread to ``run_composite``."""

    build_ref: "dict | None" = None


@dataclass(frozen=True)
class PayloadTypes:
    """A provider's wire models, for the core's TypeScript generator (``Provider.payload_types``, step P3 M1).
    ``lib/generate_ts.py`` emits them after the core's own types: ``aliases`` are the named ``Literal`` types to keep by
    name (TypeScript alias name -> the ``Literal``), ``models`` the interfaces, in output order."""

    aliases: "dict[str, object]"
    models: "list[type[BaseModel]]"


class Provider(Protocol):
    """What a backend plugin registers. Only the methods a step needs exist; later steps add theirs (§2.1)."""

    name: str
    #: The :data:`PROVIDER_API_VERSION` it targets, written as a literal (never an import of the constant).
    api_version: int

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

    # -- P2 Builds --
    def session_build(self, ws_root: Path) -> "Optional[SessionBuild]":
        """The build ``ws_root`` is a checkout of, when this provider made it, else ``None``. No network call; never
        raises (an unreadable record is ``SessionBuild(readable=False, ...)``)."""
        ...

    # -- P2 CLI --
    def cli(self) -> "tuple[Command, ...]":
        """Its subcommands. Built on every call (the CLI calls it once per process), never cached, so a test can patch
        a handler on the provider's module."""
        ...

    # -- P2 Runs a --
    def router(self) -> "Optional[ProviderRouter]":
        """Routes only this provider serves, or ``None``. Built on every call (``create_app`` calls it once per app),
        so FastAPI and the route models load only in a process that builds an app, never in an env worker."""
        ...

    # -- P3 M1 --
    def payload_types(self) -> "Optional[PayloadTypes]":
        """Its own wire models (the routes it serves), for ``lib/generate_ts.py``, or ``None``. Built on every call, so
        the models load only in a process that generates the types."""
        ...

    # -- P3 F0 --
    def features(self) -> frozenset[str]:
        """The UI features this provider adds that no facet implies (``backends.features.process_features``; the facet
        features come from its backends' facets). Features only hide optional controls; nothing that decides where a
        run goes reads them. No network call; never raises."""
        ...

    # -- P3 C1 (read through lib/backends/config.py) --
    def check_config(self, named: Optional[str]) -> None:
        """Its half of the one-backend rule: refuse (``backend_config.BackendConfigError``) its own settings when they
        name another backend than ``named`` (the URL the operator named, already validated; ``None``: none named).
        Reads the environment only; no network call."""
        ...

    def base_url(self) -> Optional[str]:
        """The URL its backends are asked at: the named one, else its own setting or default, or ``None`` when it
        has none. Reads the environment only; no network call; never raises."""
        ...

    def configured(self) -> bool:
        """Whether one of its own settings names a backend (the operator opted in), as opposed to its default URL.
        Reads the environment only."""
        ...

    def deployment_default(self) -> Optional[str]:
        """Its deployment name when no name is configured and no backend is named (``backend_config.deployment_name``),
        or ``None``. No network call."""
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


def _entry_points() -> "list[importlib.metadata.EntryPoint]":
    """The group's entry points, sorted by name (a seam for tests)."""
    return sorted(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: (ep.name, ep.value))


def _real_package(name: str) -> bool:
    """Whether ``name`` is importable as a real package (not a namespace left behind by a stray ``__pycache__``
    directory, not hidden by a finder)."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return False
    return spec is not None and spec.origin not in (None, "namespace") and spec.submodule_search_locations is not None


def _plugin_installed() -> bool:
    """Whether the in-tree plugin's package is installed (a real package). A seam for tests."""
    return _real_package(_PLUGIN)


def _absent(e: ModuleNotFoundError, module: str) -> bool:
    """``e`` says the entry point's distribution is not installed: its TOP-LEVEL package cannot be found (or is only
    a namespace, e.g. a leftover ``__pycache__`` directory). A missing submodule of a real, present package is a
    broken plugin, not an absent one."""
    if not e.name:
        return False
    top = module.split(".", 1)[0]
    if e.name == top:
        return True
    within = module == e.name or module.startswith(e.name + ".")
    return within and e.name.startswith(top + ".") and not _real_package(top)


def _dist_name(ep: "importlib.metadata.EntryPoint") -> str:
    return getattr(ep.dist, "name", None) or ep.value


def _load() -> "tuple[Provider, ...]":
    if (os.environ.get(PROVIDERS_ENV) or "").strip().lower() == "none":
        return ()
    eps = _entry_points()
    # Two entry points with one name: refused before anything loads (a stale distribution beside its successor would
    # otherwise hide behind the identity dedupe below).
    values: "dict[str, set[str]]" = {}
    for ep in eps:
        values.setdefault(ep.name, set()).add(ep.value)
    clashing = sorted(name for name, vals in values.items() if len(vals) > 1)
    if clashing:
        detail = "; ".join(f"{n}: {sorted(values[n])}" for n in clashing)
        raise ImportError(f"more than one {ENTRY_POINT_GROUP} entry point has the same name ({detail})")
    if not eps and _plugin_installed():
        raise ImportError(
            f"{_PLUGIN} is installed but no {ENTRY_POINT_GROUP} entry point names it: a stale install; "
            "re-run `uv sync` (or `pip install -e .`)")
    found: "list[Provider]" = []
    seen: "set[tuple[str, str]]" = set()
    for ep in eps:
        if (ep.name, ep.value) in seen:  # the same distribution visible twice
            continue
        seen.add((ep.name, ep.value))
        try:
            obj = ep.load()
        except ModuleNotFoundError as e:
            if _absent(e, ep.module):
                continue
            raise
        except AttributeError as e:
            raise ImportError(f"{ENTRY_POINT_GROUP} entry point {ep.name!r} ({ep.value}): {e}") from e
        version = getattr(obj, "api_version", None)
        if version != PROVIDER_API_VERSION:
            raise ProviderVersionError(
                f"the backend provider {ep.name!r} from {_dist_name(ep)} targets provider interface version "
                f"{version!r}, but this workbench offers version {PROVIDER_API_VERSION}; install matching versions")
        if getattr(obj, "name", None) != ep.name:
            raise ImportError(
                f"{ENTRY_POINT_GROUP} entry point {ep.name!r} ({ep.value}) loads a provider named "
                f"{getattr(obj, 'name', None)!r}")
        if any(p is obj for p in found):
            continue
        found.append(obj)
    return tuple(found)


def reset() -> None:
    """Forget the providers (tests; a changed ``PROVIDERS_ENV``). ``backends.reset_registry`` also forgets what was
    built from them."""
    global _providers
    with _lock:
        _providers = None
