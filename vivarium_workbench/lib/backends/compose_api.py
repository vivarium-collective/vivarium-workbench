"""compose-api's run lifecycle: an adapter over :mod:`remote_run_compose` (which step 1a leaves in place).

Every method calls ``remote_run_compose.<name>`` through the module attribute at call time, so a patch of that
module still takes effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, Union

from vivarium_workbench.lib import remote_run_compose

if TYPE_CHECKING:
    from vivarium_workbench.lib.backends.provider import Detached


class ComposeApiRuns:
    name = "compose-api"

    def owns(self, run_id: object) -> bool:
        return remote_run_compose.is_run_id(run_id)

    def submit(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return remote_run_compose.submit(ws_root, body)

    def land(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return remote_run_compose.land(ws_root, body)

    def status(self, run_id: str) -> tuple[dict, int]:
        return remote_run_compose.status(run_id)

    def progress(self, run_id: str) -> tuple[dict, int]:
        return remote_run_compose.progress(run_id)

    def cancel(self, run_id: str) -> tuple[dict, int]:
        return remote_run_compose.cancel(run_id)

    def trace(self, run_id: str) -> "tuple[Union[bytes, dict], int, str]":
        return remote_run_compose.trace(run_id)

    def change_visibility(self, run_id: str, visibility: str) -> tuple[dict, int]:
        """The optional :class:`backends.Visibility` method: compose-api's owner-only ``PATCH /simulations/{id}``."""
        return remote_run_compose.change_visibility(run_id, visibility)

    def run_composite(self, ws_root: Path, composite_id: str, *, dest: Path, n_steps: float,
                      overrides: "dict | None", emit_paths: "list[str] | None",
                      analysis_options: "dict | None", build_ref: "dict | None",
                      poll_interval: float, poll_timeout: float) -> Path:
        """The composite runs in the environment compose-api's registry lists for the workspace, recording
        ``emit_paths`` (when given) under its own emitter; its results zip lands in ``dest``. ``build_ref`` names an
        SMS build, which compose-api has no use for."""
        if analysis_options:
            # compose-api runs the document only; there is no server-side analysis step to hand these to.
            print(f"note: compose-api does not run declared analyses; not run: {', '.join(sorted(analysis_options))}",
                  flush=True)
        return remote_run_compose.run_composite(
            ws_root, composite_id, dest=Path(dest), n_steps=n_steps, overrides=overrides, emit_paths=emit_paths,
            poll_interval=poll_interval, poll_timeout=poll_timeout)

    def dispatch_preflight(self, ws_root: Path) -> dict:
        """Ready exactly when an environment compose-api's registry lists runs this workspace (nothing is shipped
        from git): :func:`remote_run_compose.dispatch_preflight`."""
        return remote_run_compose.dispatch_preflight(ws_root)

    def dispatch_composite(self, ws_root: Path, body: dict, *, spec_id: str, overrides: dict,
                           emit_paths: list, steps: float) -> "tuple[dict, int] | Detached":
        """The card's run goes to the detached runner (``run_composite`` sends it to the environment compose-api's
        registry lists for this workspace). No cloud image to resolve and no git preflight: the environment's image
        runs, not this checkout. A step count compose-api cannot run is refused now (its 400), and so is a workspace
        the readiness check refuses (:func:`remote_run_compose.check_environment`, with a short timeout since it runs
        inside the launch request): a named environment compose-api does not list (409 ``unknown-environment``), or
        a compose-api that cannot be asked (``_backend_error``'s answer: 502, or 401/403 for the token). Nothing is
        written and nothing is spawned for a refused launch."""
        from compose_api_client.ext import CliError

        from vivarium_workbench.lib.backends.provider import Detached

        try:
            remote_run_compose.checked_steps(steps)
            remote_run_compose.check_environment(ws_root, timeout=remote_run_compose.CHECK_TIMEOUT)
        except remote_run_compose.ComposeDispatchError as e:
            return {**e.body, "run_target": "deployment"}, e.status
        except CliError as e:
            out, status = remote_run_compose._backend_error(e)
            return {**out, "run_target": "deployment"}, status
        return Detached()


#: How long the identity request may wait for an answer: a probe's (``SmsApiClient.for_("probe")`` waits 3 s). The
#: session bounds the connection itself at 5 s.
IDENTITY_TIMEOUT = 3.0


def compose_health_identity(base_url: str) -> Optional[str]:
    """The server identity the compose-api at ``base_url`` reports: ``deployment_id`` on ``GET /health``
    (biosimulations/compose-api#543), or ``None`` when it reports none (a deployment from before that field).

    Raises when the server cannot be asked (unreachable, an error answer, the circuit breaker open); the caller
    (``records._ask``) caches the answer and remembers a failure. One anonymous attempt with a probe's timeout,
    through the same per-URL circuit breaker as every other remote call (``backend_config.link``)."""
    from compose_api_client.ext import ApiError, ComposeSession, NetworkError, RateLimitedError

    from vivarium_workbench.lib import backend_config
    from vivarium_workbench.lib.backends.records import IDENTITY_FIELD

    if not (base_url or "").strip():
        raise ValueError("no compose-api URL to ask for its identity")
    lk = backend_config.link(base_url)
    lk.check()  # a link known to be down answers at once (CircuitOpen), without a network call
    try:
        with ComposeSession(base_url, timeout=IDENTITY_TIMEOUT) as session:
            health = session.health()
    except (RateLimitedError, ApiError):
        # An answer (429, 502/503/504 are NetworkErrors in the client's hierarchy too): the server is reachable, as
        # the breaker's own probe counts any HTTP status (and remote_run_compose._backend_error reads these).
        _mark(lk.mark_up)
        raise
    except NetworkError as e:
        # Only a failed CONNECTION marks the link down (the client says "cannot reach <url>: could not connect ..."). A
        # server that is merely slow to answer /health is not down: marking it so would make the capability
        # profile refuse new runs (require_named_backend_reachable) until the breaker's half-open retry.
        if str(e).startswith("cannot reach"):
            _mark(lk.mark_down, str(e))
        raise
    _mark(lk.mark_up)
    value = health.get(IDENTITY_FIELD) if isinstance(health, dict) else None
    text = str(value).strip() if value is not None else ""
    return text or None


def _mark(update: Callable[..., None], *args: str) -> None:
    """Breaker bookkeeping: it must never fail the call it records."""
    try:
        update(*args)
    except Exception:  # noqa: BLE001
        pass
