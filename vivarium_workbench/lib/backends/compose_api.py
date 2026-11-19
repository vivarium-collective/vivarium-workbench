"""compose-api's run lifecycle: an adapter over :mod:`remote_run_compose` (which step 1a leaves in place).

Every method calls ``remote_run_compose.<name>`` through the module attribute at call time, so a patch of that
module still takes effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Union

from vivarium_workbench.lib import remote_run_compose


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
    except NetworkError as e:  # nothing came back: the breaker learns of it, as from any other call
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
