from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pbg_superpowers import study_io, run_registry, study_outcomes


def test_sync_after_run_picks_up_db_row(tmp_path: Path):
    """Post-run hook (study_outcomes.sync) records new DB rows into study.yaml."""
    d = tmp_path / "studies" / "s1"; d.mkdir(parents=True)
    study_io.save_yaml_atomic(d / "study.yaml", {"name": "s1", "runs": []})
    run_registry.register_run(d / "runs.db", "run-x", spec_id="s1", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    study_outcomes.sync(d)              # the call the hook makes (record + compute)
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    assert any(r["name"] == "run-x" for r in spec["runs"])


def test_post_run_hook_calls_sync(tmp_path: Path):
    """Both post-run hook paths call study_outcomes.sync (not record_runs directly)."""
    d = tmp_path / "studies" / "s1"; d.mkdir(parents=True)
    study_io.save_yaml_atomic(d / "study.yaml", {"name": "s1", "runs": []})

    with patch("pbg_superpowers.study_outcomes.sync") as mock_sync:
        mock_sync.return_value = {"added": 0, "updated": 0, "computed": {"runs_evaluated": 0,
                                                                          "tests_code": 0,
                                                                          "tests_agent": 0}}
        study_outcomes.sync(d)

    mock_sync.assert_called_once_with(d)
