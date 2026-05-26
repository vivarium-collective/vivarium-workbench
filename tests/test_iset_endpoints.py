"""Unit tests for iset (investigation-set) endpoint helpers.

Covers:
- compute_investigation_status: pure status-derivation function.
- _post_iset_create_for_test: POST /api/iset-create body handler.
- _build_iset_summary_for_test / _build_iset_detail_for_test: GET handlers
  pulled out to functions that don't require an HTTP handler instance.
"""
from pathlib import Path

import pytest
import yaml

from vivarium_dashboard.server import (
    compute_investigation_status,
    _post_iset_create_for_test,
    _post_iset_clone_for_test,
    _build_iset_summary_for_test,
    _build_iset_detail_for_test,
)


# ---------------------------------------------------------------------------
# Part 1: compute_investigation_status — pure derivation rules
# ---------------------------------------------------------------------------


def test_compute_status_failed_when_any_child_failed():
    assert compute_investigation_status(["planned", "failed", "complete"]) == "failed"


def test_compute_status_failed_when_any_child_invalid():
    assert compute_investigation_status(["running", "invalid"]) == "failed"


def test_compute_status_complete_when_all_children_complete():
    assert compute_investigation_status(["complete", "complete", "ran"]) == "complete"


def test_compute_status_complete_when_all_children_ran():
    assert compute_investigation_status(["ran", "ran"]) == "complete"


def test_compute_status_running_when_any_child_running():
    assert compute_investigation_status(["planned", "running", "planned"]) == "running"


def test_compute_status_running_when_child_implementing():
    assert compute_investigation_status(["planned", "implementing"]) == "running"


def test_compute_status_running_when_child_runnable():
    assert compute_investigation_status(["planned", "runnable"]) == "running"


def test_compute_status_running_when_child_analyzing():
    assert compute_investigation_status(["planned", "analyzing"]) == "running"


def test_compute_status_running_when_child_has_accumulated_runs():
    # No child is in a 'running' status but at least one has runs accumulated
    # → still treat as 'running' per the derivation rules.
    statuses = ["planned", "planned"]
    has_runs = [False, True]
    assert compute_investigation_status(statuses, has_runs=has_runs) == "running"


def test_compute_status_in_progress_when_some_but_not_all_complete():
    assert compute_investigation_status(["complete", "planned"]) == "in_progress"
    assert compute_investigation_status(["ran", "planning"]) == "in_progress"


def test_compute_status_planning_when_all_planned():
    assert compute_investigation_status(["planned", "planned"]) == "planning"


def test_compute_status_planning_when_mix_planned_planning():
    assert compute_investigation_status(["planned", "planning"]) == "planning"


def test_compute_status_planning_when_empty():
    assert compute_investigation_status([]) == "planning"


def test_compute_status_unknown_status_treated_as_planning():
    # Garbage / unrecognized status values fall through to the 'else' bucket.
    assert compute_investigation_status(["weird-thing"]) == "planning"


# ---------------------------------------------------------------------------
# Part 2: _post_iset_create_for_test — POST /api/iset-create handler
# ---------------------------------------------------------------------------


@pytest.fixture
def _ws(tmp_path):
    """Workspace with empty investigations/ + studies/ dirs."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: ws\ncreated: \"2026-05-16\"\nplugin_version: 0.6.1\npackage_path: pkg\n"
    )
    (ws / "investigations").mkdir()
    (ws / "studies").mkdir()
    return ws


def test_iset_create_success_writes_yaml(_ws):
    body = {"name": "my-investigation", "overview": "Why this matters"}
    resp, code = _post_iset_create_for_test(_ws, body)
    assert code == 200, resp
    p = _ws / "investigations" / "my-investigation" / "investigation.yaml"
    assert p.is_file()
    spec = yaml.safe_load(p.read_text())
    assert spec["name"] == "my-investigation"
    assert spec["description"].strip() == "Why this matters"
    assert spec["status"] == "planning"
    assert spec["studies"] == []
    # v2 scaffold: schema_version bumped to 2 to surface the narrative spine
    # (executive / scientific_argument / biological_story / at_a_glance /
    # glossary / guidelines) as commented placeholders.
    assert spec["schema_version"] == 2
    # The text body includes the narrative-spine TODO comments.
    text = p.read_text()
    assert "executive:" in text
    assert "biological_story:" in text
    assert "scientific_argument:" in text


def test_iset_create_returns_detail_shape(_ws):
    body = {"name": "foo", "overview": "bar"}
    resp, code = _post_iset_create_for_test(_ws, body)
    assert code == 200, resp
    # Should match the shape returned by GET /api/iset/<name>.
    assert resp["name"] == "foo"
    assert resp["status"] == "planning"
    assert resp["effective_status"] == "planning"
    assert resp["studies"] == []


def test_iset_create_with_parent_studies(_ws):
    body = {"name": "child", "parent_studies": ["study-a", "study-b"]}
    resp, code = _post_iset_create_for_test(_ws, body)
    assert code == 200, resp
    spec = yaml.safe_load(
        (_ws / "investigations" / "child" / "investigation.yaml").read_text()
    )
    # v2 scaffold routes the `parent_studies` body param into `studies:` (the
    # actual schema field). The original endpoint wrote a top-level
    # `parent_studies:` field that did not exist in investigation.schema.json.
    assert spec["studies"] == ["study-a", "study-b"]


def test_iset_create_rejects_bad_slug_uppercase(_ws):
    resp, code = _post_iset_create_for_test(_ws, {"name": "BadName"})
    assert code == 400
    assert "error" in resp


def test_iset_create_rejects_bad_slug_underscores(_ws):
    # The spec says kebab-case only: ^[a-z0-9][a-z0-9-]*$, no underscores.
    resp, code = _post_iset_create_for_test(_ws, {"name": "with_underscore"})
    assert code == 400


def test_iset_create_rejects_bad_slug_leading_dash(_ws):
    resp, code = _post_iset_create_for_test(_ws, {"name": "-leading"})
    assert code == 400


def test_iset_create_rejects_empty_name(_ws):
    resp, code = _post_iset_create_for_test(_ws, {})
    assert code == 400


def test_iset_create_conflict_when_already_exists(_ws):
    body = {"name": "dup"}
    _post_iset_create_for_test(_ws, body)
    resp, code = _post_iset_create_for_test(_ws, body)
    assert code == 409
    assert "exists" in resp["error"].lower()


# ---------------------------------------------------------------------------
# Part 3: _post_iset_clone_for_test — POST /api/iset-clone handler
# ---------------------------------------------------------------------------


_STUB_CLONE_SCRIPT = """\
#!/usr/bin/env python3
'''Minimal stub for tests: copies investigation.yaml + bumps the name.'''
import argparse, json, sys, yaml
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--source', required=True)
p.add_argument('--target', required=True)
p.add_argument('--source-root', required=True, type=Path)
p.add_argument('--target-root', required=True, type=Path)
p.add_argument('--source-prefix', default=None)
p.add_argument('--target-prefix', default=None)
p.add_argument('--json', action='store_true')
a = p.parse_args()
src = a.source_root / 'investigations' / a.source / 'investigation.yaml'
dst_dir = a.target_root / 'investigations' / a.target
dst_dir.mkdir(parents=True, exist_ok=False)
spec = yaml.safe_load(src.read_text())
spec['name'] = a.target
(dst_dir / 'investigation.yaml').write_text(yaml.safe_dump(spec, sort_keys=False))
if a.json:
    print(json.dumps({'source': a.source, 'target': a.target, 'studies_remapped': {}}))
"""


def _seed_clone_script(ws: Path) -> None:
    """Drop a minimal stub clone script + a source investigation into the workspace fixture."""
    (ws / "scripts").mkdir(exist_ok=True)
    (ws / "scripts" / "clone_investigation.py").write_text(_STUB_CLONE_SCRIPT)
    _post_iset_create_for_test(ws, {"name": "src-inv", "overview": "source"})


def test_iset_clone_rejects_missing_source(_ws):
    resp, code = _post_iset_clone_for_test(_ws, {"target": "x"})
    assert code == 400
    assert "source and target are required" in resp["error"]


def test_iset_clone_rejects_missing_target(_ws):
    resp, code = _post_iset_clone_for_test(_ws, {"source": "x"})
    assert code == 400


def test_iset_clone_rejects_bad_slug(_ws):
    resp, code = _post_iset_clone_for_test(_ws, {"source": "Bad", "target": "ok"})
    assert code == 400
    assert "kebab-case" in resp["error"]


def test_iset_clone_rejects_same_name(_ws):
    resp, code = _post_iset_clone_for_test(_ws, {"source": "foo", "target": "foo"})
    assert code == 400
    assert "differ" in resp["error"]


def test_iset_clone_404_when_source_missing(_ws):
    resp, code = _post_iset_clone_for_test(_ws, {"source": "nope", "target": "new-one"})
    assert code == 404
    assert "nope" in resp["error"]


def test_iset_clone_501_when_script_missing(_ws):
    _post_iset_create_for_test(_ws, {"name": "src-inv"})
    resp, code = _post_iset_clone_for_test(_ws, {"source": "src-inv", "target": "dst-inv"})
    assert code == 501
    assert "clone_investigation.py" in resp["error"]


def test_iset_clone_409_when_target_exists(_ws):
    _seed_clone_script(_ws)
    _post_iset_create_for_test(_ws, {"name": "dst-inv"})
    resp, code = _post_iset_clone_for_test(_ws, {"source": "src-inv", "target": "dst-inv"})
    assert code == 409


def test_iset_clone_success_invokes_script(_ws):
    _seed_clone_script(_ws)
    resp, code = _post_iset_clone_for_test(_ws, {"source": "src-inv", "target": "dst-inv"})
    assert code == 200, resp
    assert (_ws / "investigations" / "dst-inv" / "investigation.yaml").is_file()
    assert resp["name"] == "dst-inv"
    assert "clone_summary" in resp
    assert resp["clone_summary"]["target"] == "dst-inv"


def test_iset_clone_passes_target_prefix_to_script(_ws):
    _seed_clone_script(_ws)
    resp, code = _post_iset_clone_for_test(
        _ws,
        {"source": "src-inv", "target": "dst-inv", "target_prefix": "demo"},
    )
    # Stub doesn't act on the prefix but the dashboard must accept + forward it.
    assert code == 200, resp


def test_iset_create_atomic_no_partial_on_error(_ws):
    # Confirm we don't leave behind a half-written file: the only file created
    # should be the final investigation.yaml, no .tmp sibling.
    _post_iset_create_for_test(_ws, {"name": "ok"})
    inv_dir = _ws / "investigations" / "ok"
    files = sorted(p.name for p in inv_dir.iterdir())
    assert files == ["investigation.yaml"]


# ---------------------------------------------------------------------------
# Part 3: _build_iset_summary_for_test / _build_iset_detail_for_test —
# verify effective_status surfaces correctly on the GET endpoints.
# ---------------------------------------------------------------------------


def _write_iset(ws, name, **fields):
    p = ws / "investigations" / name / "investigation.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "title": fields.pop("title", name), **fields}
    p.write_text(yaml.safe_dump(data, sort_keys=False))


def _write_study(ws, name, status):
    p = ws / "studies" / name / "study.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(
        {"schema_version": 3, "name": name, "status": status,
         "baseline": [{"composite": "x", "name": "b"}]},
        sort_keys=False,
    ))


def test_iset_summary_effective_status_running(_ws):
    _write_iset(_ws, "inv", status="planning", studies=["s1", "s2"])
    _write_study(_ws, "s1", "planned")
    _write_study(_ws, "s2", "running")
    out = _build_iset_summary_for_test(_ws)
    items = [i for i in out if i["name"] == "inv"]
    assert len(items) == 1
    assert items[0]["status"] == "planning"   # author intent
    assert items[0]["effective_status"] == "running"


def test_iset_summary_effective_status_planning_matches_author(_ws):
    _write_iset(_ws, "inv", status="planning", studies=["s1"])
    _write_study(_ws, "s1", "planned")
    out = _build_iset_summary_for_test(_ws)
    items = [i for i in out if i["name"] == "inv"]
    assert items[0]["effective_status"] == "planning"


def test_iset_summary_effective_status_failed(_ws):
    _write_iset(_ws, "inv", status="planning", studies=["s1", "s2"])
    _write_study(_ws, "s1", "complete")
    _write_study(_ws, "s2", "failed")
    out = _build_iset_summary_for_test(_ws)
    items = [i for i in out if i["name"] == "inv"]
    assert items[0]["effective_status"] == "failed"


def test_iset_detail_includes_effective_status(_ws):
    _write_iset(_ws, "inv", status="planning", studies=["s1"])
    _write_study(_ws, "s1", "complete")
    resp, code = _build_iset_detail_for_test(_ws, "inv")
    assert code == 200, resp
    assert resp["effective_status"] == "complete"
    assert resp["status"] == "planning"


# ---------------------------------------------------------------------------
# Part 4: Pass A multi-axis status — round-trip through iset detail
# ---------------------------------------------------------------------------


def _write_study_full(ws, name, **fields):
    """Write a study with arbitrary extra fields (multi-axis status etc.)."""
    p = ws / "studies" / name / "study.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 3,
        "name": name,
        "baseline": [{"composite": "x", "name": "b"}],
        **fields,
    }
    p.write_text(yaml.safe_dump(data, sort_keys=False))


def test_iset_detail_multiaxis_status_round_trip(_ws):
    """All six Pass A axes set on a study must surface on the iset detail."""
    _write_iset(_ws, "inv", status="planning", studies=["s1"])
    _write_study_full(
        _ws,
        "s1",
        status="ran",
        design_status="approved",
        implementation_status="complete",
        simulation_status="ran",
        evaluation_status="evaluated",
        gate_status="passed",
        expert_review_status="approved",
    )
    resp, code = _build_iset_detail_for_test(_ws, "inv")
    assert code == 200, resp
    s = resp["studies"][0]
    assert s["name"] == "s1"
    assert s["design_status"] == "approved"
    assert s["implementation_status"] == "complete"
    assert s["simulation_status"] == "ran"
    assert s["evaluation_status"] == "evaluated"
    assert s["gate_status"] == "passed"
    assert s["expert_review_status"] == "approved"


def test_iset_detail_multiaxis_status_absent_is_none(_ws):
    """Studies that don't set any multi-axis fields must round-trip as None
    on every axis (not raise KeyError, not omit the keys)."""
    _write_iset(_ws, "inv", status="planning", studies=["s1"])
    _write_study(_ws, "s1", "planned")  # no axis fields set
    resp, code = _build_iset_detail_for_test(_ws, "inv")
    assert code == 200, resp
    s = resp["studies"][0]
    for axis in (
        "design_status", "implementation_status", "simulation_status",
        "evaluation_status", "gate_status", "expert_review_status",
    ):
        assert s[axis] is None, f"{axis} should be None for legacy study, got {s[axis]!r}"


def test_iset_detail_multiaxis_status_partial(_ws):
    """Setting only some axes returns the set ones and leaves the rest None."""
    _write_iset(_ws, "inv", status="planning", studies=["s1"])
    _write_study_full(
        _ws, "s1", status="ran",
        gate_status="needs_calibration",
        simulation_status="ran",
    )
    resp, code = _build_iset_detail_for_test(_ws, "inv")
    assert code == 200, resp
    s = resp["studies"][0]
    assert s["gate_status"] == "needs_calibration"
    assert s["simulation_status"] == "ran"
    assert s["design_status"] is None
    assert s["implementation_status"] is None
    assert s["evaluation_status"] is None
    assert s["expert_review_status"] is None
