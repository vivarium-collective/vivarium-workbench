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

from vivarium_workbench.lib.sms_api_client import (
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
