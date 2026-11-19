"""The workbench's remote backends, one adapter each (docs/backend-adapters.md §2).

Step 1a names the adapters that already exist informally and routes the remote-run views' calls through them:

* :data:`COMPOSE` -- compose-api (:mod:`remote_run_compose`), run ids ``compose-<n>``;
* :data:`VIVA_CORE` -- viva-core's ``/viva/v1`` run surface (:mod:`remote_run_viva_v1`), string run ids;
* :data:`SMS_LEGACY` -- the frozen SMS ``/api/v1`` path (:mod:`.sms_legacy`), integer simulation ids.

The views still choose an adapter with their own per-key rules (pinned by ``tests/test_backends_routing.py``);
:func:`for_run` states the id-form rule those rules approximate. Making every view route through it is a later,
deliberate behaviour change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Union

from vivarium_workbench.lib.backends.compose_api import ComposeApiRuns
from vivarium_workbench.lib.backends.sms_legacy import SmsLegacyRuns
from vivarium_workbench.lib.backends.viva_core import VivaCoreRuns



class RunFacet(Protocol):
    """A backend's run lifecycle, in the views' existing ``(body, status)`` shapes."""

    name: str

    def owns(self, run_id: object) -> bool: ...
    def land(self, ws_root: Path, body: dict) -> tuple[dict, int]: ...
    def status(self, run_id: str) -> tuple[dict, int]: ...
    def progress(self, run_id: str) -> tuple[dict, int]: ...
    def cancel(self, run_id: str) -> tuple[dict, int]: ...
    def trace(self, run_id: str) -> "tuple[Union[bytes, dict], int, str]": ...


@dataclass(frozen=True)
class Backend:
    """One backend. Only the run facet exists so far (§2.1's other facets come with later steps)."""

    name: str
    runs: RunFacet


COMPOSE = Backend("compose-api", ComposeApiRuns())
VIVA_CORE = Backend("viva-core", VivaCoreRuns())
SMS_LEGACY = Backend("sms-legacy", SmsLegacyRuns())

_ALL = (COMPOSE, VIVA_CORE, SMS_LEGACY)


def for_run(run_id: object) -> "Backend | None":
    """The backend that owns ``run_id`` by its form, or ``None`` for an empty id. At most one adapter owns an id."""
    for backend in _ALL:
        if backend.runs.owns(run_id):
            return backend
    return None
