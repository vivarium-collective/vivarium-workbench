"""The SMS provider (docs/backend-adapters.md §2.2, step P1c): viva-api's two backends and its answers.

* ``backends()``: ``viva-core`` (``/viva/v1`` document and composite runs) and ``sms-legacy`` (``/api/v1`` integer
  simulations), one viva-api server -- they share ``viva_health_identity``;
* ``select()``: a run of the session's own build (``.viv-build.json``) or of a pinned deployment is SMS's;
* ``recognise()``: the viva-core half of the capability probe (``server_capabilities.backend_profile``);
* ``worker_launcher()``: viva-api's hosted env-worker launcher (proxy or dial-back), when the deployment asks for one;
* ``probe_client()``: the client the capability probe reads a named backend with (``SmsApiClient``, probe class).

The core finds this module by importing it (until step P4's entry point) and reads ``PROVIDER``.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from vivarium_workbench.lib.backends.provider import Recognised

if TYPE_CHECKING:
    from vivarium_workbench.lib.backends import Backend
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
                from vivarium_workbench_sms.sms_legacy import SmsLegacyRuns
                from vivarium_workbench_sms.viva_core import VivaCoreRuns, viva_health_identity

                self._backends = (
                    Backend("viva-core", VivaCoreRuns(), identity=viva_health_identity),
                    Backend("sms-legacy", SmsLegacyRuns(), identity=viva_health_identity),  # the same viva-api
                )
            return self._backends

    def select(self, ws_root: Path) -> "Optional[Backend]":
        """SMS's claim on a NEW run, with no network call: the session's own build (``.viv-build.json``), checked
        first, or the deployment's pinned config. Read through their modules at call time (tests patch them)."""
        from vivarium_workbench.lib import run_core
        from vivarium_workbench_sms import remote_pinned

        if run_core.run_target_for(Path(ws_root)) == "deployment" or remote_pinned.is_pinned_enabled():
            return self.backends()[1]
        return None

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


PROVIDER = SmsProvider()
