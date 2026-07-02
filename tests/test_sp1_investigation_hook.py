"""SP1 — the post-run study sync must also roll up the parent investigation's
computed acceptance (the ``_sync_parent_investigation`` hook).

Covers both that the wiring exists (structural) and that it fires end-to-end
through the study-sync endpoint (behavioral).
"""
from pathlib import Path

from pbg_superpowers import study_io, run_registry
from vivarium_workbench.lib import lifecycle_mutations, study_runs


INV_YAML = """\
name: inv1
executive:
  verdict_status: in-progress
acceptance_criteria:
  - study: s1
    behavior: beh-a
"""

STUDY_S1 = {
    "name": "s1",
    "behavior_tests": [{"name": "beh-a"}],
    "runs": [{"name": "r1", "status": "completed",
              "outcomes": {"beh-a": {"result": "PASS"}}}],
}


def _nested_ws(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Workspace with nested investigations/inv1/studies/s1/. Returns
    (ws_root, inv_dir, study_dir)."""
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    inv_dir = tmp_path / "investigations" / "inv1"
    study_dir = inv_dir / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(INV_YAML)
    study_io.save_yaml_atomic(study_dir / "study.yaml", STUDY_S1)
    run_registry.register_run(
        study_dir / "runs.db", "r1", spec_id="s1", status="completed",
        started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    return tmp_path, inv_dir, study_dir


def test_study_sync_endpoint_rolls_up_investigation_acceptance(tmp_path: Path):
    """Syncing a member study writes the parent investigation's
    executive.computed_acceptance on disk."""
    ws, inv_dir, _study_dir = _nested_ws(tmp_path)

    resp, code = lifecycle_mutations.study_sync_runs(ws, {"study": "s1"})
    assert code == 200

    spec = study_io.load_yaml_mapping(inv_dir / "investigation.yaml")
    exec_ = spec["executive"]
    assert "computed_acceptance" in exec_
    assert exec_["computed_verdict_status"] == "passing"
    # authored status untouched
    assert exec_["verdict_status"] == "in-progress"


def test_sync_parent_investigation_helper_best_effort(tmp_path: Path):
    """No owning investigation (flat study) → silent no-op, never raises."""
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    d = tmp_path / "studies" / "loner"
    d.mkdir(parents=True)
    study_io.save_yaml_atomic(d / "study.yaml", {"name": "loner", "runs": []})
    # Must not raise.
    lifecycle_mutations._sync_parent_investigation(tmp_path, d)


def test_hook_wired_at_all_study_sync_sites():
    """Structural: _sync_parent_investigation is invoked wherever a study syncs."""
    lm_src = Path(lifecycle_mutations.__file__).read_text()
    sr_src = Path(study_runs.__file__).read_text()
    assert "def _sync_parent_investigation" in lm_src
    # one definition (lifecycle_mutations) + three call sites (run-baseline,
    # run-variant in study_runs, sync endpoint in lifecycle_mutations)
    assert (lm_src + sr_src).count("_sync_parent_investigation(") >= 4
