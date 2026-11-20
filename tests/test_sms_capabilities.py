"""Tests for the viva-api capability consumer in vivarium_workbench_sms/sms_api_client.py
(dual-engine W4/Q5; spec §5.3 rollout rule 2 + §5.6). It lived in lib/server_capabilities.py until
docs/backend-adapters.md step P2 (Client) moved it into the SMS plugin.

The contract under test: branch on membership never version; 404 = an EMPTY
advertisement (old deployment), NOT an error; unreachable propagates (never
reported as "unsupported"); require_capabilities names every missing capability
and the server version.
"""
from __future__ import annotations

import pytest

from vivarium_workbench_sms import sms_api_client as sac
from vivarium_workbench_sms.sms_api_client import SmsApiError

#: A pure-plugin module (it tests the SMS plugin's own code): the core-only job does not collect it
#: (docs/backend-adapters.md step P3, K2a).
pytestmark = pytest.mark.plugin


class FakeClient:
    def __init__(self, reply=None, error=None):
        self._reply, self._error = reply, error
        self.calls = 0

    def capabilities(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._reply


def test_fetch_happy_membership():
    client = FakeClient({"version": "0.9.53",
                         "capabilities": ["chain-dispatch", "container-jobs"]})
    caps = sac.fetch_capabilities(client)
    assert caps.version == "0.9.53"
    assert caps.supports(sac.CAPABILITY_CONTAINER_JOBS)
    assert not caps.supports(sac.CAPABILITY_DUAL_ENGINE_COMPARISON)
    # multi-name supports() is ALL-of
    assert caps.supports("chain-dispatch", "container-jobs")
    assert not caps.supports("chain-dispatch", "dual-engine-comparison")


def test_unrecognised_names_are_ignored_by_membership():
    caps = sac.fetch_capabilities(
        FakeClient({"version": "x", "capabilities": ["some-future-thing"]}))
    assert not caps.supports(sac.CAPABILITY_CONTAINER_JOBS)  # absent = unavailable
    assert "some-future-thing" in caps.capabilities         # carried, harmless


def test_404_is_an_empty_advertisement_not_an_error():
    client = FakeClient(error=SmsApiError("GET .../capabilities -> 404", status=404))
    caps = sac.fetch_capabilities(client)
    assert caps.capabilities == frozenset()
    assert "pre-capabilities" in caps.version


def test_unreachable_propagates_never_reads_as_unsupported():
    err = SmsApiError("GET ... failed (sms-api unreachable — is the tunnel up?)")
    with pytest.raises(SmsApiError) as ei:
        sac.fetch_capabilities(FakeClient(error=err))
    assert ei.value.status is None


def test_5xx_propagates_too():
    with pytest.raises(SmsApiError):
        sac.fetch_capabilities(FakeClient(error=SmsApiError("GET -> 500", status=500)))


def test_require_passes_and_returns_caps():
    client = FakeClient({"version": "0.9.53",
                         "capabilities": ["container-jobs", "dual-engine-comparison"]})
    caps = sac.require_capabilities(
        client, sac.CAPABILITY_CONTAINER_JOBS, sac.CAPABILITY_DUAL_ENGINE_COMPARISON)
    assert caps.version == "0.9.53"
    assert client.calls == 1  # one round-trip serves the whole gate


def test_require_names_every_missing_capability_and_version():
    client = FakeClient({"version": "0.9.48", "capabilities": ["chain-dispatch"]})
    with pytest.raises(sac.CapabilityUnsupportedError) as ei:
        sac.require_capabilities(
            client, sac.CAPABILITY_CONTAINER_JOBS, sac.CAPABILITY_DUAL_ENGINE_COMPARISON)
    e = ei.value
    assert e.missing == ["container-jobs", "dual-engine-comparison"]
    assert e.version == "0.9.48"
    msg = str(e)
    assert "does not support" in msg and "0.9.48" in msg
    assert "container-jobs" in msg and "dual-engine-comparison" in msg


def test_require_against_old_deployment_is_the_clear_error():
    """§5.3 rule 2 end-to-end: old server (404) → named refusal, no half-dispatch."""
    client = FakeClient(error=SmsApiError("-> 404", status=404))
    with pytest.raises(sac.CapabilityUnsupportedError) as ei:
        sac.require_capabilities(client, sac.CAPABILITY_DUAL_ENGINE_COMPARISON)
    assert "pre-capabilities" in ei.value.version


def test_sms_api_error_status_attribute():
    """The client's structured status (this PR's client change)."""
    assert SmsApiError("x", status=404).status == 404
    assert SmsApiError("x").status is None
