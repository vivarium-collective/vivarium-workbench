"""The SMS provider (docs/backend-adapters.md §2.2, step P1c): viva-api's two backends and its answers.

* ``backends()``: ``viva-core`` (``/viva/v1`` document and composite runs) and ``sms-legacy`` (``/api/v1`` integer
  simulations, with the ``artifacts`` / ``analyses`` facets of ``vivarium_workbench_sms/analyses.py`` and the
  ``listing`` facet of ``vivarium_workbench_sms/remote_simulations.py``), one viva-api server -- they share
  ``viva_health_identity``;
* ``select()``: a run of the session's own build (``.viv-build.json``) or of a pinned deployment is SMS's;
* ``session_build()``: the session's own build, read from its ``.viv-build.json`` stamp (``remote_pinned``);
* ``recognise()``: the viva-core half of the capability probe (``server_capabilities.backend_profile``);
* ``worker_launcher()``: viva-api's hosted env-worker launcher (proxy or dial-back), when the deployment asks for one;
* ``probe_client()``: the client the capability probe reads a named backend with (``SmsApiClient``, probe class);
* ``cli()``: its subcommands (``run-remote``, ``vivarium_workbench_sms/cli.py``);
* ``router()``: the routes only SMS serves (the legacy pipeline, the thin client's build phase and the source builds,
  ``vivarium_workbench_sms/routes.py``).
* ``payload_types()``: its wire models, for the core's TypeScript generator (``vivarium_workbench_sms/models.py``).

The core finds this module by importing it (until step P4's entry point) and reads ``PROVIDER``.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from vivarium_workbench.lib.backends.provider import PayloadTypes, ProviderRouter, Recognised, SessionBuild

if TYPE_CHECKING:
    from vivarium_workbench.lib.backends import Backend
    from vivarium_workbench.lib.backends.provider import Command
    from vivarium_workbench.lib.env_worker_launcher import WorkerLauncher
    from vivarium_workbench_sms.sms_api_client import SmsApiClient


class SmsProvider:
    name = "sms"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._backends: "Optional[tuple[Backend, Backend]]" = None

    def backends(self) -> "tuple[Backend, Backend]":
        """``(viva-core, sms-legacy)``, built once: the same objects on every call."""
        with self._lock:
            if self._backends is None:
                from vivarium_workbench.lib.backends import Backend
                from vivarium_workbench_sms.analyses import SmsAnalyses, SmsArtifacts
                from vivarium_workbench_sms.remote_simulations import SmsListing
                from vivarium_workbench_sms.sms_legacy import SmsLegacyBuilds, SmsLegacyRuns, SmsLegacyUnrouted
                from vivarium_workbench_sms.viva_core import VivaCoreRuns, viva_health_identity

                self._backends = (
                    Backend("viva-core", VivaCoreRuns(), identity=viva_health_identity),
                    Backend("sms-legacy", SmsLegacyRuns(), identity=viva_health_identity,  # the same viva-api
                            unrouted=SmsLegacyUnrouted(), artifacts=SmsArtifacts(), analyses=SmsAnalyses(),
                            builds=SmsLegacyBuilds(), listing=SmsListing()),
                )
            return self._backends

    def select(self, ws_root: Path) -> "Optional[Backend]":
        """SMS's claim on a NEW run, with no network call: the session's own build (``.viv-build.json``), checked
        first, or the deployment's pinned config. Read through their modules at call time (tests patch them)."""
        from vivarium_workbench_sms import remote_pinned

        if self.session_build(Path(ws_root)) is not None or remote_pinned.is_pinned_enabled():
            return self.backends()[1]
        return None

    def session_build(self, ws_root: Path) -> "Optional[SessionBuild]":
        """The session's own build (``remote_pinned.session_build``: its ``.viv-build.json`` stamp), or ``None``. Read
        through the module at call time (tests patch it). No network call; never raises."""
        from vivarium_workbench_sms import remote_pinned

        return remote_pinned.session_build(Path(ws_root))

    def recognise(self, client: object) -> Recognised:
        """The viva-core half of ``server_capabilities.backend_profile``, moved verbatim (step P1c): the capability
        list (membership, never version) and, when the run surface is present, the health services; dispatch
        ``viva-v1-document`` when the backend runs documents. Never raises: a backend that cannot be asked is
        ``reachable: False`` and ``stop``."""
        from vivarium_workbench.lib import server_capabilities as sc
        from vivarium_workbench_sms import sms_api_client as sac
        from vivarium_workbench_sms.sms_api_client import SmsApiError

        out: dict = {"reachable": True, "version": None, "capabilities": [], "services": {},
                     "dispatch": sc.DISPATCH_LEGACY, "error": None}
        try:
            caps = sac.fetch_capabilities(client)  # type: ignore[arg-type]
        except SmsApiError as e:
            out.update(reachable=False, error=str(e))
            return Recognised(out, has_composites=False, stop=True)
        out["version"] = caps.version
        out["capabilities"] = sorted(caps.capabilities)
        if not caps.supports(sac.CAPABILITY_VIVA_V1_COMPOSITES):
            return Recognised(out, has_composites=False, stop=False)
        try:
            out["services"] = dict(client.health_v1().get("services") or {})  # type: ignore[attr-defined]
        except SmsApiError:
            out["services"] = {}
        if caps.supports(sac.CAPABILITY_VIVA_V1_COMPOSITES_DOCUMENTS):
            out["dispatch"] = sc.DISPATCH_DOCUMENT
        return Recognised(out, has_composites=True, stop=False)

    def worker_launcher(self) -> "Optional[WorkerLauncher]":
        """viva-api's hosted launcher for this deployment (``ENV_WORKER_PROXY_BASE``, else
        ``ENV_WORKER_ADVERTISE_HOST``), or ``None`` (step P2). No network call."""
        from vivarium_workbench_sms.env_workers import hosted_launcher  # lazy, like backends()

        return hosted_launcher()

    def probe_client(self, base_url: str) -> "SmsApiClient":
        """``SmsApiClient.for_("probe", base_url)``: one attempt, 3 s, through the breaker (step P2). Read from its
        module at call time (tests patch ``SmsApiClient.for_``)."""
        from vivarium_workbench_sms import sms_api_client as sac

        return sac.SmsApiClient.for_("probe", base_url)

    def cli(self) -> "tuple[Command, ...]":
        """``run-remote`` (``vivarium_workbench_sms/cli.py``, step P2), built fresh on every call."""
        from vivarium_workbench_sms import cli as _cli  # lazy, like backends()

        return _cli.commands()

    def router(self) -> ProviderRouter:
        """``remote-run-start``, ``-status``, ``-build`` and ``-pinned-build`` (``vivarium_workbench_sms/routes.py``,
        step P2 Runs a2), and ``/api/source/builds``, ``build-remote`` and ``switch-build`` (Builds B3), built fresh
        on every call. Imported here, lazily, so FastAPI and the route models load only in a process that builds an
        app."""
        from vivarium_workbench_sms import routes  # lazy, like backends()

        return ProviderRouter(routes.build_router(), routes.READONLY_ALLOWED, ai_excluded=routes.AI_EXCLUDED)

    def payload_types(self) -> PayloadTypes:
        """Its wire models (``vivarium_workbench_sms/models.py``, step P3 M1), for ``lib/generate_ts.py``: the legacy
        pipeline's job and the payloads of its routes. Imported here, lazily, like ``router()``."""
        from vivarium_workbench_sms import models

        return PayloadTypes(
            aliases={"RemoteJobStatus": models.RemoteJobStatus},
            models=[
                models.RemoteRunStep,
                models.RemoteRunJob,
                models.SourceBuilds,
                models.BuildRemoteResponse,
                models.RemoteRunStartResponse,
            ],
        )


PROVIDER = SmsProvider()
