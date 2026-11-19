"""Which dispatch surface a named backend gives the workbench (docs/backend-adapters.md §2.2).

The core half of the capability probe: the dispatch vocabulary (``DISPATCH_*``), compose-api detection by OpenAPI
operation ids, the brief profile cache and the rule that a named backend which cannot be asked refuses a run. The
viva-api half (``GET /viva/v1/capabilities``, the capability contract, health services) is the provider's
``recognise`` and lives in the SMS plugin (``vivarium_workbench_sms.sms_api_client``, step P2). The client the probe
reads with is the provider's ``probe_client``, or the core's plain-HTTP ``backend_config.ProbeClient`` with providers
off.
"""

from __future__ import annotations

from vivarium_workbench.lib import backend_config
from vivarium_workbench.lib.backend_config import BackendError, OpenApiProbe
from vivarium_workbench.lib.errors import APIError


# ---------------------------------------------------------------------------
# Which dispatch surface does this backend give the workbench?
# ---------------------------------------------------------------------------

#: ``dispatch`` values of :func:`backend_profile`.
DISPATCH_LEGACY = "legacy"            # /compose/v1 + /api/v1/simulations, as before
DISPATCH_DOCUMENT = "viva-v1-document"    # POST /viva/v1/composites {environment, document}
DISPATCH_COMPOSE = "compose-api"      # POST /simulation/run (compose-api, docs/backend-compose-api.md)

#: The operations that make a backend compose-api for the workbench. compose-api has no capabilities
#: route; its operation ids are its stable public contract (pbest's client methods are named for them),
#: so they are read from its OpenAPI document by membership, never by version.
COMPOSE_OPERATIONS = frozenset({
    "run-simulation", "get-simulation-status", "get-simulation-results-file", "list-environments",
    "get-environment-manifest", "list-registry-entries",
})


def backend_profile(client: OpenApiProbe) -> dict:
    """What the configured backend can do for a workspace, read live (never cached here).

    ``GET /viva/v1/capabilities`` (membership, never version) plus, when the
    run surface is present, ``GET /viva/v1/health`` (``services``: which stores
    the deployment actually wired). ``dispatch`` is the route a workspace
    composite takes:

    * ``viva-v1-document``: the backend advertises ``viva-v1-composites`` AND
      ``viva-v1-composites-documents`` -- a workspace composite is exported to a
      process-bigraph document and run by ``POST /viva/v1/composites``. This is
      the general route: a backend serving composites only BY ID (today: the one
      ``ecoli-simulation`` on SMS) cannot run an arbitrary workspace composite;
    * ``compose-api``: no ``/viva/v1`` composites surface, and ``GET /openapi.json``
      lists every operation in :data:`COMPOSE_OPERATIONS` -- the composite is
      exported and run by compose-api's ``POST /simulation/run`` in a registered
      environment (:mod:`remote_run_compose`);
    * ``legacy``: anything else (no capabilities route, older deployment, run
      surface without documents) -- the existing path, unchanged.

    Never raises: ``reachable: false`` + ``error`` when the backend cannot be
    asked, which reads as ``legacy`` so an older backend keeps the path it always
    had. A backend that cannot be asked (a refused connection or a timeout, an HTTP
    error other than a 404 on the capabilities route, a connection failure or a 5xx
    on ``/openapi.json``) is *unreachable*, not legacy: what a run on it would do is
    unknown, and :func:`require_named_backend_reachable` refuses a local run for it.
    """
    # Step P1c: the viva-core half (capabilities, health services, document dispatch) is the provider's
    # ``recognise`` (vivarium_workbench_sms/provider.py), moved verbatim. With providers off there is none: the
    # profile starts reachable and legacy, and only compose-api is detected.
    import vivarium_workbench.lib.backends.provider as _provider

    active = _provider.providers()
    if active:
        recognised = active[0].recognise(client)
        out = recognised.profile
        if recognised.stop or recognised.has_composites:
            return out
    else:
        out = {"reachable": True, "version": None, "capabilities": [], "services": {},
               "dispatch": DISPATCH_LEGACY, "error": None}
    # compose-api: asked exactly when the capability list lacks viva-v1-composites, as before step P1c.
    try:
        version, operations = client.openapi_operations()
    except BackendError as e:
        if e.status is None or e.status >= 500:
            # Something is there (the capabilities route answered 404, or -- providers off -- was not asked), but it
            # cannot say what it is (a refused connection, a timeout, a 5xx): not a legacy backend that is up.
            out.update(reachable=False, error=str(e))
            return out
        version, operations = None, frozenset()  # a 4xx: this backend has no OpenAPI document
    except ValueError:  # an /openapi.json that is not JSON is not compose-api
        version, operations = None, frozenset()
    if COMPOSE_OPERATIONS <= operations:
        out["dispatch"] = DISPATCH_COMPOSE
        out["version"] = version or out["version"]
    return out


def _probe_client(base: str) -> OpenApiProbe:
    """The client :func:`backend_profile` reads ``base`` with: the active provider's (the same provider whose
    ``recognise`` reads it), else the core's plain-HTTP :class:`backend_config.ProbeClient`, which only asks
    ``/openapi.json`` (compose-api detection)."""
    import vivarium_workbench.lib.backends.provider as _provider

    active = _provider.providers()
    return active[0].probe_client(base) if active else backend_config.ProbeClient(base)


#: How long a dispatch decision is trusted. Run entrypoints resolve the target
#: several times per request; one probe per half-minute keeps that off the wire.
_PROFILE_TTL = 30.0
#: How long a FAILED probe is trusted, counted from the end of the probe. The callers of one request share one
#: answer (a hung backend costs a probe its whole 3 s timeout: ``sms_api_client._CALL_CLASS_POLICY``), but a backend
#: that is back is read again within seconds once its circuit has closed, not half a minute later.
_PROFILE_FAILURE_TTL = 5.0
_PROFILE_CACHE: "dict[str, tuple[float, dict]]" = {}


def explicit_backend_profile() -> "dict | None":
    """The profile of the backend the operator NAMED (``--backend-base-url`` /
    ``VIVARIUM_WORKBENCH_BACKEND_BASE_URL``), cached briefly; ``None`` when they
    named none.

    The aliases (``VIVA_API_BASE`` / ``SMS_API_BASE``) deliberately do not count:
    they have always meant "a remote exists", not "run there", and every existing
    deployment of them must keep its behaviour byte for byte.
    """
    import time

    base = backend_config.explicit_backend_base_url()
    if base is None:
        return None
    hit = _PROFILE_CACHE.get(base)
    if hit and time.monotonic() - hit[0] < (_PROFILE_TTL if hit[1]["reachable"] else _PROFILE_FAILURE_TTL):
        return hit[1]
    # Through the circuit breaker (RemoteLink), as every other call is: a connection failure opens it, and while it is
    # open this answers "unreachable" without a network call, so a dead or hung backend is not asked every few
    # seconds by every request thread. The breaker's own probe (every 30 s while ``serve`` runs, on the one URL
    # ``sms_api_base()`` names at start-up) or the first call after its minute is up closes it when the backend is
    # back, and the profile is read again within ``_PROFILE_FAILURE_TTL`` after that.
    profile = backend_profile(_probe_client(base))
    _PROFILE_CACHE[base] = (time.monotonic(), profile)
    return profile


def require_named_backend_reachable() -> None:
    """Refuse (502, the canonical envelope) when the operator NAMED a backend and it cannot be asked.

    A named backend is the operator saying "run there". One that cannot be reached reads as ``legacy`` in
    :func:`backend_profile`, and legacy runs locally, so without this a run meant for the backend would start on
    the machine serving the workbench. The body is the shape ``remote_run_compose._backend_error`` answers for an
    unreachable compose-api. A named backend that IS reachable and offers only a legacy surface passes (and stays
    local); with no backend named nothing is probed.
    """
    profile = explicit_backend_profile()
    if profile is None or profile["reachable"]:
        return
    base = backend_config.explicit_backend_base_url()
    raise APIError(
        502, f"the backend named for this workbench ({base}) cannot be reached ({profile['error']}); "
             "a run is not started locally in its place",
        phase="unreachable", reachable=False, reason="backend unreachable", backend=base)


def compose_dispatch_active() -> bool:
    """True only when a backend was named explicitly AND it is compose-api. False (no network
    call at all) otherwise. Production code asks ``backends.named()`` (step 1b); this stays as a primitive
    because it is the low-level form and must not import ``backends``."""
    profile = explicit_backend_profile()
    return bool(profile and profile["dispatch"] == DISPATCH_COMPOSE)


def viva_v1_dispatch_active() -> bool:
    """True only when a backend was named explicitly AND it runs documents through
    ``POST /viva/v1/composites``. False (no network call at all) otherwise. Production code asks
    ``backends.named()`` (step 1b); this stays as a primitive and must not import ``backends``."""
    profile = explicit_backend_profile()
    return bool(profile and profile["dispatch"] == DISPATCH_DOCUMENT)
