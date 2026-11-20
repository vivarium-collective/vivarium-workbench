"""Hardening tests for the remote-run status poll loop (``_poll_until_terminal``).

Covers the two robustness gaps that matter for external users (Chris et al.):
- a **stuck** remote run must not hang the poller forever (wall-clock deadline);
- a single **transient** blip must not fail an otherwise-healthy multi-hour run
  (bounded consecutive-error retry).
"""
import pytest

from vivarium_workbench_sms import deployment_run
from vivarium_workbench_sms.deployment_run import _MAX_CONSECUTIVE_POLL_ERRORS, _poll_until_terminal
from vivarium_workbench_sms.sms_api_client import SmsApiError

#: The poll loop is the SMS plugin's (``deployment_run``): every test here needs it (step P3, K2b).
pytestmark = pytest.mark.plugin


class _ScriptedClient:
    """A fake SmsApiClient whose ``compose_status`` replays a scripted sequence.

    Each item is either a status dict (returned) or an Exception (raised).
    """

    base_url = "http://sms-api.test"

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def compose_status(self, sim_id):
        self.calls += 1
        item = self._script.pop(0) if self._script else {"status": "running"}
        if isinstance(item, Exception):
            raise item
        return item


class _FakeTime:
    """Deterministic monotonic clock; ``sleep`` advances it (min 1s/step)."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(float(seconds), 1.0)


@pytest.fixture
def fake_time(monkeypatch):
    ft = _FakeTime()
    monkeypatch.setattr(deployment_run, "time", ft)  # only deployment_run sees the fake clock
    return ft


def test_poll_survives_transient_blips(fake_time):
    # two transient errors (< the tolerated max), then completed — must NOT fail.
    client = _ScriptedClient([
        SmsApiError("blip 1"),
        SmsApiError("blip 2"),
        {"status": "running"},
        {"status": "completed", "sim_id": 7},
    ])
    status, data = _poll_until_terminal(client, 7, poll_interval=1.0, poll_timeout=0)
    assert status == "completed"
    assert data["sim_id"] == 7


def test_poll_raises_on_persistent_polling_failure(fake_time):
    # more than the tolerated consecutive errors -> RuntimeError naming the endpoint.
    client = _ScriptedClient([SmsApiError("down")] * (_MAX_CONSECUTIVE_POLL_ERRORS + 2))
    with pytest.raises(RuntimeError, match="reachable"):
        _poll_until_terminal(client, 7, poll_interval=1.0, poll_timeout=0)


def test_poll_recovers_error_streak_before_the_cap(fake_time):
    # exactly the max consecutive errors, then success -> survives (boundary).
    script = [SmsApiError("blip")] * _MAX_CONSECUTIVE_POLL_ERRORS + [{"status": "completed"}]
    client = _ScriptedClient(script)
    status, _ = _poll_until_terminal(client, 7, poll_interval=1.0, poll_timeout=0)
    assert status == "completed"


def test_poll_times_out_on_stuck_run(fake_time):
    # never terminal -> TimeoutError once the wall-clock deadline passes.
    client = _ScriptedClient([{"status": "running"}])  # then defaults to running
    with pytest.raises(TimeoutError, match="did not reach a terminal state"):
        _poll_until_terminal(client, 7, poll_interval=2.0, poll_timeout=5.0)


def test_poll_returns_terminal_failed_without_raising(fake_time):
    # a terminal "failed" is returned (the caller decides how to surface it).
    client = _ScriptedClient([{"status": "running"}, {"status": "failed", "error": "boom"}])
    status, data = _poll_until_terminal(client, 7, poll_interval=1.0, poll_timeout=0)
    assert status == "failed"
    assert data["error"] == "boom"
