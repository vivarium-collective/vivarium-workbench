"""Feature-detect what a viva-api deployment can actually do — dual-engine W4/Q5.

Spec: ``docs/dual-engine-comparison.md`` §5.3 (rollout rule 2) + §5.6 (Q5).
viva-api #262 added ``GET /core/v1/capabilities`` → ``{version, capabilities:
[str, ...]}`` where each entry means "this deployment, right now, can genuinely
serve this" (code present AND configured AND wired). The consumer contract, per
that endpoint's own docstring:

* branch on **membership** in ``capabilities`` — **never on version** (a
  deployment can run an image from an unmerged branch that no version ordering
  describes: the 2026-08-19 production incident);
* an **absent** name means "not available here", never "unknown";
* **unrecognised** names are ignored.

A deployment that predates the endpoint 404s — mapped here to "advertises
nothing" (an empty set), which by the contract above reads as "nothing beyond
the pre-capabilities baseline is available". A network failure is NOT mapped to
absence: an unreachable service must surface as unreachable, not as
"unsupported" (those demand different fixes from the user).

Dispatch paths call :func:`require_capabilities` so an old deployment yields a
clear "this viva-api doesn't support X yet" — never a half-dispatched run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vivarium_workbench.lib.errors import APIError
from vivarium_workbench_sms.sms_api_client import (
    CAPABILITY_VIVA_V1_COMPOSITES as CAPABILITY_VIVA_V1_COMPOSITES,
    CAPABILITY_VIVA_V1_COMPOSITES_DOCUMENTS as CAPABILITY_VIVA_V1_COMPOSITES_DOCUMENTS,
    CAPABILITY_VIVA_V1_SURFACE as CAPABILITY_VIVA_V1_SURFACE,
    CAPABILITY_VIVA_V1_WORKERS as CAPABILITY_VIVA_V1_WORKERS,
    SmsApiClient,
    SmsApiError,
)

# Capability names the workbench branches on. Mirrored from viva-api's
# CAPABILITY_REGISTRY — the strings themselves are the public, stable API
# (lower-case kebab-case slugs); these constants exist so workbench call sites
# can't typo them.
CAPABILITY_CONTAINER_JOBS = "container-jobs"
CAPABILITY_DUAL_ENGINE_COMPARISON = "dual-engine-comparison"
CAPABILITY_CHAIN_DISPATCH = "chain-dispatch"
CAPABILITY_CHAIN_PROGRESS = "chain-progress"
# GET /api/v1/simulations/{id}/trace + GET /viva/v1/composites/{id}/trace answer a
# Chrome Trace Event JSON document (the workbench's "Open in Perfetto").
CAPABILITY_VIVA_V1_TRACE = "viva-v1-trace"
# GET /viva/v1/datasets (filter by source/owner/kind/attributes) + /datasets/{id}/content
# (one registered file's bytes through the API, no storage credentials).
CAPABILITY_VIVA_V1_DATASETS = "viva-v1-datasets"
# The /viva/v1 path switches (CAPABILITY_VIVA_V1_SURFACE, CAPABILITY_VIVA_V1_WORKERS)
# are defined beside the resolver that reads them, in sms_api_client, and
# re-exported above so every name the workbench branches on is importable here.

# The version string reported for a deployment that predates the endpoint —
# for error messages and logs only (never branched on, like any version).
_PRE_CAPABILITIES_VERSION = "pre-capabilities (endpoint absent)"


@dataclass(frozen=True)
class ServerCapabilities:
    """A deployment's advertisement: what it can serve, and (for humans) its version."""

    version: str
    capabilities: frozenset = field(default_factory=frozenset)

    def supports(self, *names: str) -> bool:
        """True when EVERY named capability is advertised."""
        return all(n in self.capabilities for n in names)


class CapabilityUnsupportedError(RuntimeError):
    """The deployment does not advertise a required capability.

    The §5.3 contract: a clear "this service doesn't support X yet" naming the
    missing capabilities and the server's version (for the bug report) — never
    a half-dispatched run.
    """

    def __init__(self, missing: "list[str]", version: str) -> None:
        super().__init__(
            "this viva-api deployment does not support: "
            + ", ".join(sorted(missing))
            + f" (server version: {version}) — upgrade/redeploy viva-api, or use a "
            "deployment that advertises "
            + ("this capability" if len(missing) == 1 else "these capabilities")
        )
        self.missing = sorted(missing)
        self.version = version


def fetch_capabilities(client: SmsApiClient) -> ServerCapabilities:
    """The deployment's advertisement, honestly degraded.

    * endpoint answers → its ``{version, capabilities}`` verbatim;
    * **404** (deployment predates the endpoint) → an EMPTY advertisement —
      by the endpoint's own contract, absent means "not available here";
    * any other failure (unreachable, 5xx) → the ``SmsApiError`` propagates:
      "can't reach the service" must never be reported as "unsupported".
    """
    try:
        raw = client.capabilities() or {}
    except SmsApiError as e:
        if e.status == 404:
            return ServerCapabilities(
                version=_PRE_CAPABILITIES_VERSION, capabilities=frozenset()
            )
        raise
    names = raw.get("capabilities") or []
    return ServerCapabilities(
        version=str(raw.get("version") or "unknown"),
        capabilities=frozenset(str(n) for n in names),
    )


def require_capabilities(client: SmsApiClient, *names: str) -> ServerCapabilities:
    """Gate a dispatch path on the deployment advertising every ``name``.

    Returns the fetched :class:`ServerCapabilities` on success (so callers can
    log the version / branch on further names without a second round-trip).
    Raises :class:`CapabilityUnsupportedError` naming every missing capability,
    or lets the transport ``SmsApiError`` propagate when the service is
    unreachable (a different problem needing a different fix).
    """
    caps = fetch_capabilities(client)
    missing = [n for n in names if n not in caps.capabilities]
    if missing:
        raise CapabilityUnsupportedError(missing, caps.version)
    return caps


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


def backend_profile(client: SmsApiClient) -> dict:
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
    out: dict = {"reachable": True, "version": None, "capabilities": [], "services": {},
                 "dispatch": DISPATCH_LEGACY, "error": None}
    try:
        caps = fetch_capabilities(client)
    except SmsApiError as e:
        out.update(reachable=False, error=str(e))
        return out
    out["version"] = caps.version
    out["capabilities"] = sorted(caps.capabilities)
    if not caps.supports(CAPABILITY_VIVA_V1_COMPOSITES):
        try:
            version, operations = client.openapi_operations()
        except SmsApiError as e:
            if e.status is None or e.status >= 500:
                # The capabilities route answered 404, so something is there, but it cannot say what it is
                # (a refused connection, a timeout, a 5xx): not a legacy backend that is up.
                out.update(reachable=False, error=str(e))
                return out
            version, operations = None, frozenset()  # a 4xx: this backend has no OpenAPI document
        except ValueError:  # an /openapi.json that is not JSON is not compose-api
            version, operations = None, frozenset()
        if COMPOSE_OPERATIONS <= operations:
            out["dispatch"] = DISPATCH_COMPOSE
            out["version"] = version or out["version"]
        return out
    try:
        out["services"] = dict(client.health_v1().get("services") or {})
    except SmsApiError:
        out["services"] = {}
    if caps.supports(CAPABILITY_VIVA_V1_COMPOSITES_DOCUMENTS):
        out["dispatch"] = DISPATCH_DOCUMENT
    return out


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

    from vivarium_workbench_sms.sms_api_client import explicit_backend_base_url

    base = explicit_backend_base_url()
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
    profile = backend_profile(SmsApiClient.for_("probe", base))
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
    from vivarium_workbench_sms.sms_api_client import explicit_backend_base_url

    base = explicit_backend_base_url()
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
