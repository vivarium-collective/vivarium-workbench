"""Unit tests for lib.investigation_status (extracted from server.py)."""

import yaml

from vivarium_dashboard.lib import investigation_status as inv


def test_compute_investigation_status_rules():
    c = inv.compute_investigation_status
    assert c([]) == "planning"
    assert c(["planning"]) == "planning"
    assert c(["failed"]) == "failed"            # rule 1 wins
    assert c(["complete", "failed"]) == "failed"
    assert c(["complete", "ran"]) == "complete"  # all done
    assert c(["evaluated", "decided"]) == "complete"
    assert c(["running"]) == "running"
    assert c(["complete", "planning"]) == "in_progress"  # some done, not all
    # injected runs flip an all-planning investigation to in_progress (rule 4)
    assert c(["planning"], has_runs=[True]) == "in_progress"


def _make_ws(tmp_path):
    """A minimal workspace: one investigation referencing one study."""
    (tmp_path / "investigations" / "inv-a").mkdir(parents=True)
    (tmp_path / "investigations" / "inv-a" / "investigation.yaml").write_text(
        yaml.safe_dump({
            "name": "inv-a", "title": "Inv A", "status": "active",
            "description": "d", "question": "q", "hypothesis": "h",
            "studies": ["study-1"],
        })
    )
    (tmp_path / "studies" / "study-1").mkdir(parents=True)
    (tmp_path / "studies" / "study-1" / "study.yaml").write_text(
        yaml.safe_dump({"status": "complete"})
    )
    return tmp_path


def test_build_iset_summary_shape(tmp_path):
    ws = _make_ws(tmp_path)
    out = inv.build_iset_summary(ws, study_has_runs=lambda s, spec: False)
    assert len(out) == 1
    e = out[0]
    assert e["name"] == "inv-a"
    assert e["title"] == "Inv A"
    assert e["n_studies"] == 1
    assert e["studies"] == ["study-1"]
    assert e["effective_status"] == "complete"   # one study, status complete
    assert e["lifecycle"] == "wip"               # tmp dir is not a git repo
    assert e["current"] is False                 # no matching git branch


def test_build_iset_summary_injects_runs(tmp_path):
    """A planning study with injected has_runs=True rolls up to in_progress."""
    ws = tmp_path
    (ws / "investigations" / "inv-b").mkdir(parents=True)
    (ws / "investigations" / "inv-b" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "inv-b", "studies": ["s-plan"]})
    )
    (ws / "studies" / "s-plan").mkdir(parents=True)
    (ws / "studies" / "s-plan" / "study.yaml").write_text(yaml.safe_dump({"status": "planning"}))

    no_runs = inv.build_iset_summary(ws, study_has_runs=lambda s, spec: False)
    has_runs = inv.build_iset_summary(ws, study_has_runs=lambda s, spec: True)
    assert no_runs[0]["effective_status"] == "planning"
    assert has_runs[0]["effective_status"] == "in_progress"


def test_build_iset_summary_parse_error(tmp_path):
    """A malformed investigation.yaml yields the minimal {name, error} entry."""
    (tmp_path / "investigations" / "inv-bad").mkdir(parents=True)
    (tmp_path / "investigations" / "inv-bad" / "investigation.yaml").write_text("a: [unterminated")
    out = inv.build_iset_summary(tmp_path, study_has_runs=lambda s, spec: False)
    assert out[0]["name"] == "inv-bad"
    assert "error" in out[0]


def test_iter_iset_dirs_empty(tmp_path):
    assert list(inv.iter_iset_dirs(tmp_path)) == []


def test_study_run_slugs_empty(tmp_path):
    assert inv.study_run_slugs(tmp_path) == set()


def test_server_shim_delegates(tmp_path):
    """server._build_iset_summary_for_test still works (delegates to lib) and
    matches the lib builder with an equivalent runs-presence check."""
    from vivarium_dashboard import server

    ws = _make_ws(tmp_path)
    via_server = server._build_iset_summary_for_test(ws)
    via_lib = inv.build_iset_summary(
        ws, study_has_runs=lambda s, spec: server._count_runs_for_study(s, spec) > 0
    )
    assert via_server == via_lib
    assert via_server[0]["name"] == "inv-a"
