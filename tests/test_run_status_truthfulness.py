"""Run-status truthfulness: a run with no progress must not read "running".

Regression (sim962, and the scaffold-placeholder run): a run showed as
"running" when nothing was actually executing -- a head process merely alive, or
a row marked "running" that had produced no output and stopped heart-beating.
The truthfulness guard that exists for this is heartbeat FRESHNESS:
``report_views._has_active_run_for_study`` only counts a run as active when its
status is "running" AND its ``heartbeat_at`` is within ``freshness_s`` (300s by
default). A run that is marked running but has a stale or absent heartbeat -- the
no-progress case -- must NOT count as active, and therefore must NOT promote a
study's effective status to "running" via
``report_views._compute_study_effective_status``.

These are pure-function tests over the spec ``runs[]`` path (the runs.db path is
tolerant/empty for a bare workspace), asserting both the freshness gate itself
and its consequence on the derived study status.
"""
from __future__ import annotations

import time

from vivarium_workbench.lib.report_views import (
    _compute_study_effective_status,
    _has_active_run_for_study,
)

_NOW = time.time()
_FRESH = _NOW - 5.0            # 5s ago -- well within the 300s window
_STALE = _NOW - 10_000.0       # ~2.7h ago -- long past the window


def _spec(runs):
    return {"runs": runs}


def test_stale_heartbeat_running_run_is_not_active(tmp_path):
    """A run still marked "running" whose last heartbeat is long past the
    freshness window is NOT actively executing -- the classic stalled/orphaned
    run that keeps its stale "running" label forever."""
    spec = _spec([{"status": "running", "heartbeat_at": _STALE}])
    assert _has_active_run_for_study(tmp_path, "s", spec) is False


def test_absent_heartbeat_running_run_is_not_active(tmp_path):
    """The sim962 / placeholder shape: a row marked "running" with NO heartbeat
    at all (never reported progress) must not count as active."""
    spec = _spec([{"status": "running"}])
    assert _has_active_run_for_study(tmp_path, "s", spec) is False


def test_non_numeric_heartbeat_running_run_is_not_active(tmp_path):
    """A malformed heartbeat is unverifiable, hence not fresh -- fail closed."""
    spec = _spec([{"status": "running", "heartbeat_at": "not-a-time"}])
    assert _has_active_run_for_study(tmp_path, "s", spec) is False


def test_fresh_heartbeat_running_run_is_active(tmp_path):
    """⛔ Positive control: a genuinely live run (running + fresh heartbeat) DOES
    count as active -- without this the tests above would pass on a helper that
    always returns False."""
    spec = _spec([{"status": "running", "heartbeat_at": _FRESH}])
    assert _has_active_run_for_study(tmp_path, "s", spec) is True


def test_completed_run_with_fresh_heartbeat_is_not_active(tmp_path):
    """Only a "running" status can be active; a terminal run is not, regardless
    of a recent heartbeat."""
    spec = _spec([{"status": "complete", "heartbeat_at": _FRESH}])
    assert _has_active_run_for_study(tmp_path, "s", spec) is False


def test_no_progress_run_does_not_promote_study_to_running(tmp_path):
    """THE CONSEQUENCE: a study in a pre-evaluation state (e.g. "build") whose
    only run is marked running but shows no fresh progress must NOT read as
    "running". Composes the freshness gate with the effective-status derivation
    exactly as the report builder does."""
    spec = _spec([{"status": "running", "heartbeat_at": _STALE}])
    active = _has_active_run_for_study(tmp_path, "s", spec)
    eff = _compute_study_effective_status("build", has_runs=True, has_active_run=active)
    assert active is False
    assert eff != "running"
    assert eff == "build"


def test_fresh_run_promotes_study_to_running(tmp_path):
    """⛔ Positive control for the composition: a study with a genuinely live run
    DOES read as "running"."""
    spec = _spec([{"status": "running", "heartbeat_at": _FRESH}])
    active = _has_active_run_for_study(tmp_path, "s", spec)
    eff = _compute_study_effective_status("build", has_runs=True, has_active_run=active)
    assert active is True
    assert eff == "running"
