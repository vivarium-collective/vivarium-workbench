"""Unit tests for vivarium_dashboard.server._is_generated_path.

The dashboard's dirty-tree gate (`_dirty_workspace`) filters porcelain
output through this predicate. Any path that returns True is treated as
"the dashboard's own generated artifact" and skipped — so it must NOT
block the Install / commit / workstream-push actions.
"""
from vivarium_dashboard.lib.git_status import is_generated_path as _is_generated_path


def test_reports_directory_is_generated():
    assert _is_generated_path("reports/index.html") is True
    assert _is_generated_path("reports/") is True


def test_out_directory_is_generated():
    assert _is_generated_path("out/parca/cache.pkl") is True
    assert _is_generated_path("out/") is True


def test_pbg_runtime_state_is_generated():
    """v2ecoli friction #15: .pbg/ holds dashboard-authored runtime state
    (composite-runs.db, dashboard pid, viz-requests/, …). The dashboard's
    own Install action used to 409 on `?? .pbg/composite-runs.db-shm`."""
    assert _is_generated_path(".pbg/composite-runs.db-shm") is True
    assert _is_generated_path(".pbg/composite-runs.db-wal") is True
    assert _is_generated_path(".pbg/dashboard/") is True
    assert _is_generated_path(".pbg/runs/sim_abc.json") is True
    assert _is_generated_path(".pbg/") is True


def test_authored_paths_are_not_generated():
    assert _is_generated_path("workspace.yaml") is False
    assert _is_generated_path("studies/dnaa-00/study.yaml") is False
    assert _is_generated_path("scripts/lint-workspace.py") is False
    assert _is_generated_path("v2ecoli/processes/metabolism.py") is False


def test_lookalikes_are_not_treated_as_generated():
    """Guard against accidental prefix collisions — e.g. `outsider/` is not
    `out/`, and `.pbg-bak/` is not `.pbg/`."""
    assert _is_generated_path("outsider/foo.py") is False
    assert _is_generated_path(".pbg-bak/snapshot.json") is False
    assert _is_generated_path("reports-archive/2024/q1.html") is False
