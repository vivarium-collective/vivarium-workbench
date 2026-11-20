"""The workbench's remote backends, one adapter each (docs/backend-adapters.md §2).

Step 1a names the adapters that already exist informally and routes the remote-run views' calls through them:

* :data:`COMPOSE` -- compose-api (:mod:`remote_run_compose`), run ids ``compose-<n>``;
* :data:`VIVA_CORE` -- viva-core's ``/viva/v1`` run surface (:mod:`remote_run_viva_v1`), string run ids;
* :data:`SMS_LEGACY` -- the frozen SMS ``/api/v1`` path (:mod:`.sms_legacy`), integer simulation ids.

Step 1b puts the choice of backend for a workspace's NEW run in one place: :func:`named`, :func:`select` and
:func:`active` (§1; from P1c they consult the provider, §2.2), and :func:`resolve_run_target` (backlog item 18)
answers "local" or "deployment" from :func:`active`. The two ``server_capabilities.*_dispatch_active`` primitives
remain, because tests patch them.

An existing run is routed by its stored record (:func:`for_existing`, step 1c-2); :func:`for_run`'s id-form rule
is only the fallback for a run with no record (one listed live from the configured server, or stored before step
1c-1).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Optional, Protocol, Union

from vivarium_workbench.lib.backends.compose_api import ComposeApiRuns, compose_health_identity



class RunFacet(Protocol):
    """A backend's run lifecycle, in the views' existing ``(body, status)`` shapes -- except :meth:`run_composite`,
    the detached deployment runner's blocking call, which keeps the shape of the code it replaced (step P2, Runs b).
    """

    name: str

    def owns(self, run_id: object) -> bool: ...
    def submit(self, ws_root: Path, body: dict) -> tuple[dict, int]: ...
    def land(self, ws_root: Path, body: dict) -> tuple[dict, int]: ...
    def status(self, run_id: str) -> tuple[dict, int]: ...
    def progress(self, run_id: str) -> tuple[dict, int]: ...
    def cancel(self, run_id: str) -> tuple[dict, int]: ...
    def trace(self, run_id: str) -> "tuple[Union[bytes, dict], int, str]": ...
    def run_composite(self, ws_root: Path, composite_id: str, *, dest: Path, n_steps: float,
                      overrides: "dict | None", emit_paths: "list[str] | None",
                      analysis_options: "dict | None", build_ref: "dict | None",
                      poll_interval: float, poll_timeout: float) -> Path:
        """Run a workspace composite on this backend to completion and save its results archive in ``dest`` (the
        detached deployment runner, ``run_runner._execute_remote``, through :func:`remote_run.run_remote`).
        Blocking. Returns the archive: a ``.zip`` of emitter history, or a ``.tar.gz`` the runner folds (analyses,
        PTools files). Raises ``RuntimeError`` naming why, ``TimeoutError`` past ``poll_timeout`` (the run may still
        be going). ``analysis_options`` (declared analyses, for a backend that runs them server-side) and
        ``build_ref`` (a build the backend's own builds named) are carried unread by the core; a backend that has no
        use for them ignores them."""
        ...


class UnroutedFacet(Protocol):
    """What a backend answers for a run-lifecycle request the core cannot route by a run's record: one that names no
    run, or (status) carries a build or analysis id beside it (step P2, Runs a1). The views' ``(body, status)``
    shapes; the backend's own checks run first, so its "not authenticated" still comes before any "id required".
    The methods are the SMS tails' own signatures. Once Builds and Analyses take the build and analysis ids, it
    answers only requests that name no id at all. At most one active backend declares it."""

    def submit(self, ws_root: Path, body: dict) -> tuple[dict, int]: ...
    def land(self, ws_root: Path, body: dict) -> tuple[dict, int]: ...
    def status(self, params: dict) -> tuple[dict, int]: ...
    def progress(self, params: dict) -> tuple[dict, int]: ...
    def cancel(self, params: dict) -> tuple[dict, int]: ...
    def run_composite(self, ws_root: Path, composite_id: str, *, dest: Path, n_steps: float,
                      overrides: "dict | None", emit_paths: "list[str] | None",
                      analysis_options: "dict | None", build_ref: "dict | None",
                      poll_interval: float, poll_timeout: float) -> Path:
        """:meth:`RunFacet.run_composite` for a deployment run no backend selected (step P2, Runs b): the opt-in
        compose path (``VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH``) with no session build, pin or named backend."""
        ...


class BatchStatus(Protocol):
    """Optional on a backend's ``runs`` facet (step P2, Runs c2): the status of several runs in ONE ask, for a backend
    with a batch route (SMS's ``/compose/v1`` batch). Returns ``{run_id: body}`` in ``runs.status``'s body shape
    (``phase``: "done" | "failed" | other; ``raw_status``; ``error``) for the ids it answered; an id it did not
    answer is absent. Raises when the backend did not answer at all. ``run_jobs.refresh_submitted`` asks it when the
    owning backend's runs facet has it (``getattr``), else ``runs.status`` per id. The inert sentinels never declare
    it (they own no run)."""

    def status_many(self, run_ids: "list[str]") -> "dict[str, dict]": ...


@dataclass(frozen=True)
class Backend:
    """One backend. Only the run facet exists so far (§2.1's other facets come with later steps).

    ``identity`` maps a base URL to the identity that server reports (``None`` when the backend reports none);
    run records store it as ``server_id`` (§2.3). Backends that share one function are one server and share its
    cached identity (viva-core and sms-legacy); each other function is its own family (compose-api).

    ``unrouted`` (optional) answers the lifecycle requests no run record routes (:class:`UnroutedFacet`)."""

    name: str
    runs: RunFacet
    identity: Optional[Callable[[str], Optional[str]]] = None
    unrouted: Optional[UnroutedFacet] = None


COMPOSE = Backend("compose-api", ComposeApiRuns(), identity=compose_health_identity)
# --- the provider registry (step P1c) ------------------------------------------------------------------------------
#
# Backends other than compose-api come from providers (``provider.providers()``; today the SMS plugin's two). Kept
# as module attributes because code compares ``chosen is backends.VIVA_CORE``: each resolves to its provider's
# backend (the same object every time) or, with providers off, to an inert sentinel that no lookup ever returns,
# so those comparisons stay false (``None`` would not: ``None is None`` is true for a local run).
if TYPE_CHECKING:
    from vivarium_workbench.lib.backends.provider import SessionBuild

    VIVA_CORE: Backend
    SMS_LEGACY: Backend
    _ALL: "tuple[Backend, ...]"

#: The attribute each provider kind is published under (``backends.VIVA_CORE``...).
_ATTRS = {"viva-core": "VIVA_CORE", "sms-legacy": "SMS_LEGACY"}
_LAZY = (*_ATTRS.values(), "_ALL")
_LAZY_LOCK = threading.RLock()
_BY_KIND: "dict[str, Backend] | None" = None


class _Inert:
    """The runs facet of a backend whose provider is not active: it owns no run and answers nothing."""

    def __init__(self, name: str) -> None:
        self.name = name

    def owns(self, run_id: object) -> bool:
        return False

    def _refuse(self, *a: object, **k: object) -> "tuple[dict, int]":
        raise RuntimeError(f"the {self.name} backend's provider is not active")

    submit = land = status = progress = cancel = trace = run_composite = _refuse  # type: ignore[assignment]


_SENTINELS = {kind: Backend(kind, _Inert(kind)) for kind in _ATTRS}  # type: ignore[arg-type]


def _registry() -> "dict[str, Backend]":
    """Every active provider's backends by kind (built once, under the lock)."""
    global _BY_KIND
    with _LAZY_LOCK:
        if _BY_KIND is None:
            import vivarium_workbench.lib.backends.provider as _provider

            found: "dict[str, Backend]" = {}
            for p in _provider.providers():
                for backend in p.backends():
                    found.setdefault(backend.name, backend)
            declaring = sorted(b.name for b in found.values() if b.unrouted is not None)
            if len(declaring) > 1:
                raise RuntimeError(f"more than one backend declares the unrouted facet: {declaring}")
            _BY_KIND = found
        return _BY_KIND


def unrouted() -> "Backend | None":
    """The active backend with an ``unrouted`` facet (at most one; the registry refuses two), or ``None`` (providers
    off). Read from the registry at every call, so :func:`reset_registry` covers it."""
    return next((b for b in _registry().values() if b.unrouted is not None), None)


def __getattr__(name: str) -> object:
    if name not in _LAZY:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    g = globals()
    with _LAZY_LOCK:
        if name not in g:
            reg = _registry()
            if name == "_ALL":
                g[name] = (COMPOSE, *reg.values())
            else:
                kind = next(k for k, attr in _ATTRS.items() if attr == name)
                g[name] = reg.get(kind, _SENTINELS[kind])
        return g[name]


def reset_registry() -> None:
    """Forget the active providers and the core's view of them (tests; a changed providers switch): the next access
    reads them again. A provider keeps the backends it built (``SmsProvider`` is a module singleton), so the same
    objects come back and ``is`` comparisons stay valid across a toggle. Also forgets cached capability profiles,
    which may name a provider's backend."""
    global _BY_KIND
    import vivarium_workbench.lib.backends.provider as _provider

    with _LAZY_LOCK:
        _provider.reset()
        _BY_KIND = None
        for name in _LAZY:
            globals().pop(name, None)
    from vivarium_workbench.lib import server_capabilities as sc

    sc._PROFILE_CACHE.clear()


def for_run(run_id: object) -> "Backend | None":
    """The backend that owns ``run_id`` by its form, or ``None`` for an empty id. At most one adapter owns an id."""
    import sys

    for backend in getattr(sys.modules[__name__], "_ALL"):
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
        # Through the registry, never the attribute: a profile cached before providers were turned off must not
        # hand out the inert viva-core sentinel.
        return _registry().get("viva-core")
    return None


def select(ws_root: "Path | str") -> "Backend | None":
    """Where a new run of ``ws_root`` goes, without refusing anything. ``None`` means "run locally".

    1. A provider's claim (step P1c): the SMS provider claims a run of the session's own build (``.viv-build.json``)
       or of the deployment's pinned config, with no network call.
    2. Otherwise the named backend, when it runs documents (:func:`named`).
    """
    import vivarium_workbench.lib.backends.provider as _provider

    for p in _provider.providers():
        claimed = p.select(Path(ws_root))
        if claimed is not None:
            return claimed
    return named()


def active(ws_root: "Path | str") -> "Backend | None":
    """:func:`select`, plus the wb#1630 rule: a backend the operator named that cannot be reached is refused
    (``APIError`` 502), never replaced by a local run. ``None`` still means "run locally"."""
    from vivarium_workbench.lib import server_capabilities as sc

    chosen = select(ws_root)
    if chosen is None:
        sc.require_named_backend_reachable()
    return chosen


def resolve_run_target(ws_root: "Path | str") -> "Literal['local', 'deployment']":
    """Item 18: THE local-vs-deployment target of a NEW run, the same for every run entrypoint (Composites tab,
    Study tab, CLI, batch worker, rerun, ...), never by which button was clicked.

    ``"deployment"`` when a backend is :func:`active` for ``ws_root`` (a provider's claim -- the session's own build
    or the deployment's pin -- or a named backend that runs documents); ``"local"`` otherwise. Raises ``APIError``
    502 (wb#1630) when the operator named a backend that cannot be reached.

    Backlog item 18 (2026-08-04): the Composites tab honoured a deployment-wide pin while the study-run path saw
    only the session build, so a pin with no session build fell through to a local subprocess. Every run entrypoint
    now resolves through this one function and threads the result into ``invoke_run(..., target=...)``.

    Deliberately NOT ``run_core.run_target_for``, which stays session-build-only (:func:`session_build`): a
    composite PREVIEW (``composite_resolve.resolve_composite_for_request``) needs the session build's id, which a
    bare deployment-wide pin does not provide.

    Callers read it through the module at call time (``backends.resolve_run_target(...)``) so tests can patch it.
    """
    return "deployment" if active(ws_root) is not None else "local"


def session_build(ws_root: "Path | str") -> "SessionBuild | None":
    """The build ``ws_root`` is a checkout of (step P2 Builds): the first provider's answer
    (``Provider.session_build``), or ``None`` -- a plain local workspace, providers off, or the plugin absent. No
    network call; never raises. The core's readers of the session build (the rail chip, the provenance manifest, the
    preview, the readouts soft-degrade, ``run_core.run_target_for``) ask here and never name the provider's record."""
    import vivarium_workbench.lib.backends.provider as _provider

    for p in _provider.providers():
        found = p.session_build(Path(ws_root))
        if found is not None:
            return found
    return None


# --- an EXISTING run: the backend its record names (step 1c-2) ---------------------------------------------------

def by_kind(kind: str) -> Backend:
    """The adapter for a record's ``kind``: compose-api, or an active provider's backend. ``KeyError`` for a kind no
    active provider serves (never an inert sentinel)."""
    if kind == COMPOSE.name:
        return COMPOSE
    return _registry()[kind]


def resolve(ws_root: "Path | str", run_id: object, *, deployment: "str | None" = None,
            study: "str | None" = None):
    """``records.resolve_existing`` with the caller-facing arguments of :func:`for_existing` (a study's slug, a
    deployment name from a request), returning the full ``Resolution`` (its ``via`` says whether a record decided)."""
    from vivarium_workbench.lib import study_spec
    from vivarium_workbench.lib.backends.records import resolve_existing  # the submodule (not via __getattr__)

    study_db = None
    if isinstance(study, str) and study.strip():
        try:
            study_db = study_spec.study_dir(Path(ws_root), study.strip()) / "runs.db"
        except Exception:  # noqa: BLE001 -- an unknown study narrows nothing; the whole workspace is searched
            study_db = None
    # Only a non-blank string is a deployment name (a JSON body may carry anything).
    where = deployment.strip() if isinstance(deployment, str) and deployment.strip() else None
    return resolve_existing(Path(ws_root), run_id, deployment=where, study_db=study_db)


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
