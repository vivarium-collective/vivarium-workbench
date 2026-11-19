"""The workbench's remote backends, one adapter each (docs/backend-adapters.md §2).

Step 1a names the adapters that already exist informally and routes the remote-run views' calls through them:

* :data:`COMPOSE` -- compose-api (:mod:`remote_run_compose`), run ids ``compose-<n>``;
* :data:`VIVA_CORE` -- viva-core's ``/viva/v1`` run surface (:mod:`remote_run_viva_v1`), string run ids;
* :data:`SMS_LEGACY` -- the frozen SMS ``/api/v1`` path (:mod:`.sms_legacy`), integer simulation ids.

Step 1b puts the choice of backend for a workspace's NEW run in one place: :func:`named`, :func:`select` and
:func:`active` (§1; from P1c they consult the provider, §2.2). The six older predicates (``remote_pinned.resolve_run_target`` / ``uses_*_dispatch``,
``server_capabilities.*_dispatch_active``) remain, as wrappers or primitives, because tests patch them.

An existing run is routed by its stored record (:func:`for_existing`, step 1c-2); :func:`for_run`'s id-form rule
is only the fallback for a run with no record (one listed live from the configured server, or stored before step
1c-1).
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
    def submit(self, ws_root: Path, body: dict) -> tuple[dict, int]: ...
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


# --- which backend runs a workspace's new run (step 1b) ---------------------------------------------------------
#
# Every primitive is read through its module at call time (tests patch them there), and the imports are lazy:
# remote_pinned and server_capabilities sit below this package and must not import it at load time.


def named() -> "Backend | None":
    """The backend the operator NAMED (``serve --backend-base-url``), if its profile runs documents: compose-api
    or viva-core. ``None`` when none is named, or the named one is unreachable or offers only the legacy surface.
    Never raises; reads the cached profile once."""
    from vivarium_workbench.lib import server_capabilities as sc

    profile = sc.explicit_backend_profile()
    if not profile:
        return None
    if profile["dispatch"] == sc.DISPATCH_COMPOSE:
        return COMPOSE
    if profile["dispatch"] == sc.DISPATCH_DOCUMENT:
        return VIVA_CORE
    return None


def select(ws_root: "Path | str") -> "Backend | None":
    """Where a new run of ``ws_root`` goes, without refusing anything. ``None`` means "run locally".

    1. The session's own build (``.viv-build.json``) or the deployment's pinned config: SMS, with no network call.
       The session build is checked first and short-circuits the pin.
    2. Otherwise the named backend, when it runs documents (:func:`named`).
    """
    from vivarium_workbench.lib import remote_pinned, run_core

    if run_core.run_target_for(Path(ws_root)) == "deployment" or remote_pinned.is_pinned_enabled():
        return SMS_LEGACY
    return named()


def active(ws_root: "Path | str") -> "Backend | None":
    """:func:`select`, plus the wb#1630 rule: a backend the operator named that cannot be reached is refused
    (``APIError`` 502), never replaced by a local run. ``None`` still means "run locally"."""
    from vivarium_workbench.lib import server_capabilities as sc

    chosen = select(ws_root)
    if chosen is None:
        sc.require_named_backend_reachable()
    return chosen


# --- an EXISTING run: the backend its record names (step 1c-2) ---------------------------------------------------

def by_kind(kind: str) -> Backend:
    """The adapter for a record's ``kind``."""
    return {COMPOSE.name: COMPOSE, VIVA_CORE.name: VIVA_CORE, SMS_LEGACY.name: SMS_LEGACY}[kind]


def resolve(ws_root: "Path | str", run_id: object, *, deployment: "str | None" = None,
            study: "str | None" = None):
    """``records.resolve_existing`` with the caller-facing arguments of :func:`for_existing` (a study's slug, a
    deployment name from a request), returning the full ``Resolution`` (its ``via`` says whether a record decided)."""
    from vivarium_workbench.lib import study_spec
    from vivarium_workbench.lib.backends import records

    study_db = None
    if isinstance(study, str) and study.strip():
        try:
            study_db = study_spec.study_dir(Path(ws_root), study.strip()) / "runs.db"
        except Exception:  # noqa: BLE001 -- an unknown study narrows nothing; the whole workspace is searched
            study_db = None
    # Only a non-blank string is a deployment name (a JSON body may carry anything).
    where = deployment.strip() if isinstance(deployment, str) and deployment.strip() else None
    return records.resolve_existing(Path(ws_root), run_id, deployment=where, study_db=study_db)


def for_existing(ws_root: "Path | str", run_id: object, *, deployment: "str | None" = None,
                 study: "str | None" = None) -> "Backend | tuple[dict, int] | None":
    """Which backend answers for the existing run ``run_id`` (docs/backend-adapters.md §2.3, step 1c).

    The run's stored record decides (``records.resolve_existing``): a ``Backend``, a ``(body, 409)`` refusal when
    the run belongs to another backend, or ``None`` when there is no id (the caller's own "id required" answer).
    ``study`` narrows the lookup to that study's runs DB first; ``deployment`` is the caller's row's.
    """
    res = resolve(ws_root, run_id, deployment=deployment, study=study)
    if res.refusal is not None:
        return res.refusal
    return by_kind(res.kind) if res.kind else None
