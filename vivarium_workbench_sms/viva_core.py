"""viva-core's ``/viva/v1`` run lifecycle: an adapter over :mod:`remote_run_viva_v1` (left in place by step 1a).

Every method calls ``remote_run_viva_v1.<name>`` through the module attribute at call time, so a patch of that
module still takes effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from vivarium_workbench.lib import remote_run_compose
from vivarium_workbench_sms import remote_run_viva_v1


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


def viva_health_identity(base_url: str) -> "str | None":
    """The server identity a viva-core (viva-api) at ``base_url`` reports: ``deployment_id`` on ``GET /viva/v1/health``
    (viva-api#1055 ask F), or ``None`` when it reports none. Raises when the server cannot be asked; the caller
    (``records._ask``) caches the answer and remembers a failure. Through the circuit breaker, with the probe policy."""
    from vivarium_workbench.lib.backends.records import IDENTITY_FIELD
    from vivarium_workbench_sms.sms_api_client import SmsApiClient

    health = SmsApiClient.for_("probe", base_url).health_v1() or {}
    value = health.get(IDENTITY_FIELD)
    return str(value).strip() if value not in (None, "") else None
