"""A study card's ``n_runs`` includes its study-tagged remote (GovCloud) runs.

Remote runs live only in the Simulations index (their store is an ``s3://`` uri,
so they have no local runs.db / study.yaml entry). ``_count_runs_for_study`` folds
a ``remote_count`` into its max, and ``_remote_study_run_counts`` supplies it —
gated on the workspace's ``remote_run_study_map`` so a workspace without one pays
no sms-api fetch.
"""
import time
from pathlib import Path

from vivarium_workbench.lib.investigations_index import (
    _count_runs_for_study,
    _remote_study_run_counts,
)


def test_count_is_max_of_db_spec_and_remote(tmp_path):
    # no runs.db present -> db_count 0; spec + remote fold into the max.
    assert _count_runs_for_study(tmp_path, "s", {"runs": [1, 2]}, remote_count=5) == 5
    assert _count_runs_for_study(tmp_path, "s", {"runs": [1, 2, 3]}, remote_count=2) == 3
    assert _count_runs_for_study(tmp_path, "s", None, remote_count=0) == 0
    assert _count_runs_for_study(tmp_path, "s", {"runs": []}, remote_count=0) == 0


def test_remote_counts_skip_fetch_without_a_study_map(tmp_path):
    # A workspace that hasn't opted into remote-run association must NOT make the
    # (potentially slow) sms-api round-trip: no map => {} immediately.
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    t0 = time.time()
    counts = _remote_study_run_counts(tmp_path)
    assert counts == {}
    assert time.time() - t0 < 1.0  # no network


def test_remote_counts_empty_when_no_workspace_yaml(tmp_path):
    assert _remote_study_run_counts(tmp_path) == {}
