"""How an env worker comes into being — the `EnvironmentResolver`'s one seam.

The pool (``env_worker_pool``) owns *when* a worker is created and reused; this
module owns *how*. Which way a deployment uses is **not a per-call choice**:

* **Local** — spawn a subprocess over a ``socketpair`` against the workspace's own
  interpreter (``LocalWorkerLauncher``, here). What a laptop does, because a
  laptop has no cluster.
* **Hosted** — run the simulator's prebuilt image as a worker in its own pod. What
  a hosted deployment does, because hosted never builds an environment
  (REFACTOR-PLAN §2A.8). Those launchers belong to a backend provider
  (``Provider.worker_launcher()``; the SMS plugin's ``env_workers``, step P2), not
  to the core.

**Selected by deployment topology, not preference** (§2A.8). One wiring decision
at the composition root (§5C.4) — ``default_launcher()`` — so there is nothing to
cherry-pick per call site. A deployment that asks for hosted workers
(``HOSTED_WORKER_ENV``) when no provider supplies them is REFUSED, at startup and
at launch; it never quietly falls back to a local subprocess.

Every launcher produces a worker speaking the identical protocol; everything above
the connection is shared (spec §2).
"""
from __future__ import annotations

import logging
import os
from typing import Protocol

from vivarium_workbench.lib.env_compat import NEW_PREFIX, OLD_PREFIX, get_env
from vivarium_workbench.lib.env_worker_client import EnvWorker

logger = logging.getLogger(__name__)

#: Env vars (suffixes, read with ``env_compat.get_env``, so the ``VIVARIUM_WORKBENCH_`` and the deprecated
#: ``VIVARIUM_DASHBOARD_`` prefixes both count) by which a deployment asks for HOSTED env workers. Listed in the core
#: only so the core can refuse when nobody supplies them; their meaning belongs to the provider. Blank counts as unset.
HOSTED_WORKER_ENV = ("ENV_WORKER_PROXY_BASE", "ENV_WORKER_ADVERTISE_HOST")


class WorkerLauncher(Protocol):
    """Create one worker for a workspace. The pool decides when; this decides how."""

    #: Distinguishes workers in the pool's key. A local and a remote worker for
    #: the same (workspace, interpreter) are different environments and must not
    #: share a pool entry.
    kind: str

    def env_key(self, workspace: str) -> str:
        """Identify the environment this launcher would give the workspace.

        The pool keys warm workers on it, so it must distinguish environments that
        differ and match ones that don't. It is the launcher's to answer because
        only the launcher knows what the environment IS: a local worker's is the
        interpreter it spawns, a remote worker's is the image it runs — and asking
        the workspace for an interpreter it will never use is what broke the
        venv-less hosted workspace (a strict-mode resolve raised before the remote
        launcher, which ignores interpreters, was ever chosen).
        """
        ...

    def launch(self, workspace: str, *, interpreter: str | None, timeout: float) -> EnvWorker:
        ...


class LocalWorkerLauncher:
    """Spawn a subprocess against the workspace's interpreter (today's behavior)."""

    kind = "local"

    def env_key(self, workspace: str) -> str:
        from vivarium_workbench.lib import env_resolver
        return env_resolver.resolve_interpreter(workspace)

    def launch(self, workspace: str, *, interpreter: str | None, timeout: float) -> EnvWorker:
        return EnvWorker(workspace, interpreter=interpreter, timeout=timeout)


def hosted_workers_requested() -> list[str]:
    """The full names of the ``HOSTED_WORKER_ENV`` variables this deployment sets (non-blank), each spelled as it is
    set (the deprecated ``VIVARIUM_DASHBOARD_`` name when that is the one ``get_env`` reads)."""
    return [(NEW_PREFIX if NEW_PREFIX + name in os.environ else OLD_PREFIX) + name
            for name in HOSTED_WORKER_ENV if (get_env(name, "") or "").strip()]


def default_launcher() -> WorkerLauncher:
    """The composition root (§5C.4): one decision, made from deployment config.

    The first provider that supplies a hosted launcher wins (provider order, as in ``backends.select``); local
    otherwise. Deliberately not a per-call or per-workspace switch. Raises ``BackendConfigError`` when the deployment
    asks for hosted workers and no provider supplies them -- never Local -- and when an installed provider is broken.
    """
    from vivarium_workbench.lib.backends import provider as _provider  # lazy: no import cost for local tools

    for p in _provider.providers():  # raises for a broken plugin (step P1c), never "absent"
        launcher = p.worker_launcher()
        if launcher is not None:
            return launcher
    asked = hosted_workers_requested()
    if asked:
        from vivarium_workbench.lib.backend_config import BackendConfigError

        why = (f"providers are turned off by {_provider.PROVIDERS_ENV}=none"
               if (os.environ.get(_provider.PROVIDERS_ENV) or "").strip().lower() == "none"
               else "no installed provider supplies hosted env workers (is the SMS plugin installed?)")
        names = " and ".join(asked)
        raise BackendConfigError(
            f"{names} {'is' if len(asked) == 1 else 'are'} set, so this deployment runs env workers on a hosted "
            f"backend, but no backend provider supplies them ({why}). Unset {'it' if len(asked) == 1 else 'them'} "
            "to run env workers as local subprocesses, or enable the provider."
        )
    return LocalWorkerLauncher()


def check_worker_config() -> None:
    """Startup: build the launcher once and discard it (no network call), so the same code that decides at launch
    decides at startup and the two cannot drift. Raises ``BackendConfigError``."""
    default_launcher()

