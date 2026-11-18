"""Unit tests for the idle self-shutdown decision logic + watchdog wiring.

The time-based thread is exercised with a tiny limit and a fake ``on_idle`` so
the test stays fast and never actually signals the process.
"""
import threading
import time

from vivarium_workbench.lib import idle_shutdown as idle


def test_should_shutdown_pure():
    # idle beyond the limit → shut down
    assert idle.should_shutdown(last_activity=0.0, now=100.0, limit_s=90.0) is True
    # still within the window → stay up
    assert idle.should_shutdown(last_activity=0.0, now=50.0, limit_s=90.0) is False
    # exactly at the limit → shut down (>=)
    assert idle.should_shutdown(last_activity=0.0, now=90.0, limit_s=90.0) is True
    # limit <= 0 disables regardless of how idle we are
    assert idle.should_shutdown(last_activity=0.0, now=1e9, limit_s=0.0) is False
    assert idle.should_shutdown(last_activity=0.0, now=1e9, limit_s=-5.0) is False


def test_resolve_arg_wins_over_env_and_default():
    # explicit arg (minutes) → seconds, beats env + default
    assert idle.resolve_idle_seconds(
        30.0, host="127.0.0.1", base_path="", env={"VIVARIUM_WORKBENCH_IDLE_TIMEOUT": "5"}
    ) == 30.0 * 60
    # arg 0 explicitly disables
    assert idle.resolve_idle_seconds(0.0, host="127.0.0.1", base_path="", env={}) == 0.0


def test_resolve_env_applies_when_no_arg():
    assert idle.resolve_idle_seconds(
        None, host="127.0.0.1", base_path="", env={"VIVARIUM_WORKBENCH_IDLE_TIMEOUT": "45"}
    ) == 45.0 * 60
    # env 0 disables
    assert idle.resolve_idle_seconds(
        None, host="127.0.0.1", base_path="", env={"VIVARIUM_WORKBENCH_IDLE_TIMEOUT": "0"}
    ) == 0.0
    # malformed env falls through to the loopback default
    assert idle.resolve_idle_seconds(
        None, host="127.0.0.1", base_path="", env={"VIVARIUM_WORKBENCH_IDLE_TIMEOUT": "nonsense"}
    ) == idle.DEFAULT_IDLE_MINUTES * 60


def test_zero_config_default_is_loopback_only():
    # ephemeral loopback local server → default window on
    assert idle.resolve_idle_seconds(None, host="127.0.0.1", base_path="", env={}) == \
        idle.DEFAULT_IDLE_MINUTES * 60
    assert idle.resolve_idle_seconds(None, host="localhost", base_path="", env={}) == \
        idle.DEFAULT_IDLE_MINUTES * 60
    # a container / all-interfaces deployment stays up forever by default
    assert idle.resolve_idle_seconds(None, host="0.0.0.0", base_path="", env={}) == 0.0
    # behind a base path (proxied/ALB) → stays up forever by default
    assert idle.resolve_idle_seconds(None, host="127.0.0.1", base_path="/workbench", env={}) == 0.0
    # ...but an explicit env opt-in overrides even for a deployment
    assert idle.resolve_idle_seconds(
        None, host="0.0.0.0", base_path="/workbench",
        env={"VIVARIUM_WORKBENCH_IDLE_TIMEOUT": "120"},
    ) == 120.0 * 60


def test_touch_resets_idle():
    idle.touch()
    assert idle.seconds_idle() < 1.0


def test_watchdog_fires_after_idle_window():
    """With a tiny limit and no activity, the watchdog calls on_idle once."""
    fired = threading.Event()
    t = idle.start_idle_watchdog(
        limit_s=0.05, on_idle=fired.set, check_interval_s=0.01
    )
    assert t is not None
    assert fired.wait(timeout=2.0), "idle watchdog did not fire within the window"


def test_watchdog_disabled_returns_none():
    assert idle.start_idle_watchdog(0.0, on_idle=lambda: None) is None
    assert idle.start_idle_watchdog(-1.0, on_idle=lambda: None) is None


def test_watchdog_does_not_fire_while_touched():
    """Repeated touches keep the server alive past the bare idle window."""
    fired = threading.Event()
    idle.touch()
    idle.start_idle_watchdog(limit_s=0.2, on_idle=fired.set, check_interval_s=0.02)
    # keep touching for longer than the limit; it must NOT fire
    for _ in range(15):
        idle.touch()
        time.sleep(0.03)
    assert not fired.is_set(), "watchdog fired despite continuous activity"
