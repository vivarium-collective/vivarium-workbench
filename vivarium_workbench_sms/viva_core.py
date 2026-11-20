"""viva-core's ``/viva/v1`` run lifecycle: an adapter over :mod:`remote_run_viva_v1` (left in place by step 1a).

Every method calls ``remote_run_viva_v1.<name>`` through the module attribute at call time, so a patch of that
module still takes effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Union

from vivarium_workbench.lib import remote_run_compose
from vivarium_workbench_sms import remote_run_viva_v1

if TYPE_CHECKING:
    from vivarium_workbench.lib.backends.provider import Detached


class VivaCoreRuns:
    name = "viva-core"

    def owns(self, run_id: object) -> bool:
        # remote_run_viva_v1.is_run_id also matches compose-api ids; ownership is exclusive.
        return remote_run_viva_v1.is_run_id(run_id) and not remote_run_compose.is_run_id(run_id)

    def submit(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return remote_run_viva_v1.submit(ws_root, body)

    def land(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return remote_run_viva_v1.land(ws_root, body)

    def status(self, run_id: str) -> tuple[dict, int]:
        return remote_run_viva_v1.status(run_id)

    def progress(self, run_id: str) -> tuple[dict, int]:
        return remote_run_viva_v1.progress(run_id)

    def cancel(self, run_id: str) -> tuple[dict, int]:
        return remote_run_viva_v1.cancel(run_id)

    def trace(self, run_id: str) -> "tuple[Union[bytes, dict], int, str]":
        # remote_trace.fetch_trace still serves viva-core traces itself; it moves here in step 4.
        raise NotImplementedError("viva-core traces are served by remote_trace.fetch_trace until step 4")

    def run_composite(self, ws_root: Path, composite_id: str, *, dest: Path, n_steps: float,
                      overrides: "dict | None", emit_paths: "list[str] | None",
                      analysis_options: "dict | None", build_ref: "dict | None",
                      poll_interval: float, poll_timeout: float) -> Path:
        """A ``/viva/v1/composites`` document run of the composite (:func:`deployment_run.run`, step P2 Runs b)."""
        from vivarium_workbench_sms import deployment_run  # lazy: loading the registry (env workers) skips it

        return deployment_run.run(ws_root, composite_id, dest=dest, n_steps=n_steps, overrides=overrides,
                                  poll_interval=poll_interval, poll_timeout=poll_timeout,
                                  analysis_options=analysis_options, build_ref=build_ref, emit_paths=emit_paths, via_viva_v1=True)

    def dispatch_preflight(self, ws_root: Path) -> dict:
        """The named backend runs the workspace's composites as documents: there is no cloud image to resolve, but a
        named-environment run ships the workspace from git. Moved verbatim from ``/api/remote-dispatch-preflight``
        (step P2 Builds B2): the git preflight always runs (Finding F: the card skips it for an ``id:``
        environment)."""
        from vivarium_workbench.lib import remote_run

        pf = remote_run.remote_dispatch_preflight(ws_root)
        pf["target"] = "deployment"
        pf["dispatch"] = "viva-v1-document"
        return pf

    def dispatch_composite(self, ws_root: Path, body: dict, *, spec_id: str, overrides: dict,
                           emit_paths: list, steps: float) -> "tuple[dict, int] | Detached":
        """The card's run goes to the detached runner as a document (``run_composite`` -> ``POST
        /viva/v1/composites``): no cloud image to resolve. A named environment installs the workspace FROM GIT, so
        the clean+pushed preflight still applies. Moved verbatim from ``composite_test_run`` (step P2 Builds B2)."""
        from vivarium_workbench.lib import remote_run
        from vivarium_workbench.lib.backends.provider import Detached

        pf = remote_run.remote_dispatch_preflight(ws_root) if "name" in remote_run_viva_v1.backend_environment() else {"ok": True}
        if not pf.get("ok"):
            return {"error": pf.get("message", "workspace not ready to run remotely"),
                    "reason": pf.get("reason"), "preflight": pf, "run_target": "deployment"}, 409
        return Detached()


def viva_health_identity(base_url: str) -> "str | None":
    """The server identity a viva-core (viva-api) at ``base_url`` reports: ``deployment_id`` on ``GET /viva/v1/health``
    (viva-api#1055 ask F), or ``None`` when it reports none. Raises when the server cannot be asked; the caller
    (``records._ask``) caches the answer and remembers a failure. Through the circuit breaker, with the probe policy."""
    from vivarium_workbench.lib.backends.records import IDENTITY_FIELD
    from vivarium_workbench_sms.sms_api_client import SmsApiClient

    health = SmsApiClient.for_("probe", base_url).health_v1() or {}
    value = health.get(IDENTITY_FIELD)
    return str(value).strip() if value not in (None, "") else None
