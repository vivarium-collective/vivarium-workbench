"""Unit tests for iset (investigation-set) endpoint helpers.

Covers:
- compute_investigation_status: pure status-derivation function.
- _post_iset_create_for_test: POST /api/investigation-create body handler.
- _build_iset_summary_for_test / _build_iset_detail_for_test: GET handlers
  pulled out to functions that don't require an HTTP handler instance.
"""
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib.investigation_status import (
    compute_investigation_status,
    build_iset_summary,
    study_run_slugs,
)
from vivarium_workbench.lib.report_views import (
    _compute_study_effective_status as compute_study_effective_status,
    build_iset_detail,
)
from vivarium_workbench.lib.scaffold_mutations import (
    investigation_create as _post_iset_create_for_test,
    iset_clone as _post_iset_clone_for_test,
)


def _build_iset_summary_for_test(ws):
    run_slugs = study_run_slugs(ws)

    def _has_runs(slug, spec):
        return slug in run_slugs or bool((spec or {}).get("runs"))

    return build_iset_summary(ws, study_has_runs=_has_runs)


def _build_iset_detail_for_test(ws, name):
    detail = build_iset_detail(ws, name)
    if detail is None:
        return {"error": f"investigation '{name}' not found"}, 404
    return detail, 200


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


def test_compute_status_complete_when_all_children_evaluated():
    # 'evaluated' (and 'decided') are terminal lifecycle states
    # (Simulate -> Evaluate -> Decide), so an all-evaluated investigation reads
    # complete rather than falling through to in_progress. Regression: the badge
    # previously showed in_progress for finished investigations like
    # surrogate-modeling / colonies / ketchup whose studies terminate at
    # 'evaluated'.
    assert compute_investigation_status(["evaluated", "evaluated"]) == "complete"
    assert compute_investigation_status(["ran", "evaluated", "decided"]) == "complete"


def test_compute_status_in_progress_when_evaluated_mixed_with_planned():
    # A genuinely-unfinished investigation (some evaluated, some still planned)
    # must stay in_progress, not flip to complete.
    assert compute_investigation_status(
        ["evaluated", "evaluated", "planned"]) == "in_progress"


def test_compute_status_running_when_any_child_running():
    assert compute_investigation_status(["planned", "running", "planned"]) == "running"


def test_compute_status_running_when_child_implementing():
    assert compute_investigation_status(["planned", "implementing"]) == "running"


def test_compute_status_running_when_child_runnable():
    assert compute_investigation_status(["planned", "runnable"]) == "running"


def test_compute_status_running_when_child_analyzing():
    assert compute_investigation_status(["planned", "analyzing"]) == "running"


def test_compute_status_in_progress_when_child_has_accumulated_runs():
    # No child is in a 'running' status but at least one has runs accumulated
    # → 'in_progress'. Accumulated (completed) run history is NOT active
    # execution, so it no longer maps to 'running'.
    statuses = ["planned", "planned"]
    has_runs = [False, True]
    assert compute_investigation_status(statuses, has_runs=has_runs) == "in_progress"


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
# Part 1b: compute_study_effective_status — per-study derivation
# ---------------------------------------------------------------------------


def test_study_effective_failed_when_status_failed():
    assert compute_study_effective_status("failed") == "failed"


def test_study_effective_failed_when_status_invalid():
    assert compute_study_effective_status("invalid") == "failed"


def test_study_effective_complete_when_status_complete():
    assert compute_study_effective_status("complete") == "complete"
    assert compute_study_effective_status("ran") == "complete"


def test_study_effective_running_when_status_running():
    assert compute_study_effective_status("running") == "running"
    assert compute_study_effective_status("implementing") == "running"
    assert compute_study_effective_status("runnable") == "running"
    assert compute_study_effective_status("analyzing") == "running"


def test_study_effective_running_only_when_active_run():
    # 'running' means a run is genuinely in flight, NOT merely that the study
    # has accumulated run history. A planned study with finished runs reads as
    # 'planned'; only an active run (has_active_run) makes it 'running'.
    assert compute_study_effective_status("planned", has_runs=True) == "planned"
    assert (
        compute_study_effective_status("planned", has_runs=True, has_active_run=True)
        == "running"
    )


def test_study_effective_planned_when_planned_no_runs():
    assert compute_study_effective_status("planned") == "planned"
    assert compute_study_effective_status("planning") == "planned"


def test_study_effective_reflects_declared_when_empty_or_unknown():
    # Empty / None normalize to 'planned'; any other declared status is
    # reflected verbatim (e.g. 'in_progress', 'characterization-complete')
    # rather than being flattened to 'planned'.
    assert compute_study_effective_status("") == "planned"
    assert compute_study_effective_status(None) == "planned"
    assert compute_study_effective_status("weird-thing") == "weird-thing"


def test_study_effective_failed_beats_runs():
    # A failed study with runs on disk is still failed, not running.
    assert compute_study_effective_status("failed", has_runs=True) == "failed"


# ---------------------------------------------------------------------------
# Part 2: _post_iset_create_for_test — POST /api/investigation-create handler
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
    # Should match the shape returned by GET /api/investigation/<name>.
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
# Part 3: _post_iset_clone_for_test — POST /api/investigation-clone handler
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


# ---------------------------------------------------------------------------
# Part 4b: discovery_implications passthrough — alternate hypotheses,
# mechanism-update proposals, and the richer followup_study_proposals must
# surface on the iset detail so the study view + report can render them.
# ---------------------------------------------------------------------------


def test_iset_detail_surfaces_discovery_implications(_ws):
    _write_iset(_ws, "inv", status="planning", studies=["s1"])
    di = {
        "resolved_uncertainties": ["init timing now bounded"],
        "remaining_uncertainties": ["dnaA cooperativity"],
        "alternate_hypotheses": [
            {"id": "alt-1", "statement": "Titration sets timing",
             "why_plausible": "matches replication data"},
        ],
        "mechanism_update_proposals": [
            {"mechanism_node_or_edge": "dnaA->oriC",
             "update_type": "strengthen", "rationale": "consistent",
             "requires_expert_approval": True},
        ],
        "followup_study_proposals": [
            {"id": "fup-1", "title": "Sweep dnaA copy number",
             "study_type": "parameter_sweep", "source_trigger": "low_confidence",
             "expected_information_gain": "high",
             "proposed_experiment": "vary copy number 0.5x-2x"},
        ],
    }
    _write_study_full(_ws, "s1", status="ran", discovery_implications=di)
    resp, code = _build_iset_detail_for_test(_ws, "inv")
    assert code == 200, resp
    s = resp["studies"][0]
    assert s["discovery_implications"] == di
    assert s["discovery_implications"]["followup_study_proposals"][0]["id"] == "fup-1"


def test_iset_detail_discovery_implications_absent_is_empty_dict(_ws):
    _write_iset(_ws, "inv", status="planning", studies=["s1"])
    _write_study(_ws, "s1", "planned")
    resp, code = _build_iset_detail_for_test(_ws, "inv")
    assert code == 200, resp
    assert resp["studies"][0]["discovery_implications"] == {}


# ---------------------------------------------------------------------------
# Part 5: schema-drift defense — non-list acceptance_criteria / expert_docs
# must degrade gracefully, not break report generation.
#
# Backstory: an investigation author grouped acceptance_criteria into
# {investigation_terminal: [...], per_study_gating: [...]} — semantically
# reasonable, but the dashboard's walkthrough renderer expects a list and
# crashed with "(iset.acceptance_criteria || []).map is not a function".
# The fix moved the grouping into a per-entry `gating:` tag and added
# `_coerce_list_field` on the server so any future non-list value degrades
# to [] with a single stderr warning rather than 500-ing the endpoint.
# ---------------------------------------------------------------------------

from vivarium_workbench.lib.report_views import _coerce_list_field


def test_coerce_list_field_passes_through_list():
    assert _coerce_list_field({"x": [1, 2, 3]}, "x") == [1, 2, 3]


def test_coerce_list_field_returns_empty_for_missing():
    assert _coerce_list_field({}, "missing") == []


def test_coerce_list_field_returns_empty_for_explicit_none():
    assert _coerce_list_field({"x": None}, "x") == []


def test_coerce_list_field_coerces_dict_to_empty(capsys):
    """The bug-class fix: a dict where a list was expected does NOT raise;
    it degrades to [] and prints a stderr warning naming the field."""
    out = _coerce_list_field({"acceptance_criteria": {"a": [1]}},
                              "acceptance_criteria", source="my.yaml")
    assert out == []
    captured = capsys.readouterr()
    assert "acceptance_criteria" in captured.err
    assert "my.yaml" in captured.err
    assert "dict" in captured.err


def test_coerce_list_field_coerces_string_to_empty(capsys):
    """Other non-list types also degrade rather than raise."""
    out = _coerce_list_field({"x": "oops"}, "x", source="s.yaml")
    assert out == []
    err = capsys.readouterr().err
    assert "expected list" in err
    assert "str" in err


def test_iset_detail_with_dict_acceptance_criteria_does_not_500(_ws, capsys):
    """End-to-end: a grouped (dict-shaped) acceptance_criteria field on an
    investigation.yaml must NOT 500 the /api/investigation/<name> endpoint. It must
    return 200 with acceptance_criteria coerced to []."""
    _write_iset(
        _ws, "inv", status="planning", studies=[],
        # Grouped shape — the original chris-feedback integration attempt
        # that broke the renderer.
        acceptance_criteria={
            "investigation_terminal": [{"study": "a", "behavior": "b"}],
            "per_study_gating":       [{"study": "c", "behavior": "d"}],
        },
    )
    resp, code = _build_iset_detail_for_test(_ws, "inv")
    assert code == 200, resp
    assert resp["acceptance_criteria"] == []
    err = capsys.readouterr().err
    assert "acceptance_criteria" in err
    assert "dict" in err


def test_iset_detail_with_list_acceptance_criteria_passes_through(_ws):
    """Sanity: a list-shaped acceptance_criteria flows through unchanged."""
    crits = [
        {"study": "s1", "behavior": "b1", "gating": "investigation_terminal"},
        {"study": "s1", "behavior": "b2", "gating": "per_study"},
    ]
    _write_iset(
        _ws, "inv", status="planning", studies=[],
        acceptance_criteria=crits,
    )
    resp, code = _build_iset_detail_for_test(_ws, "inv")
    assert code == 200, resp
    assert resp["acceptance_criteria"] == crits


def test_registry_imports_meta_dict_and_list_forms():
    """_registry_imports_meta returns per-repo metadata from workspace.yaml
    imports (both dict and list shapes), sorted by name."""
    from vivarium_workbench.lib.registry import _registry_imports_meta

    dict_form = {"imports": {
        "pbg_ketchup": {"source": "https://github.com/x/pbg-ketchup",
                        "ref": "main", "description": "KETCHUP estimators"},
        "pbg_copasi": {"source": "https://github.com/x/pbg-copasi"},
    }}
    out = _registry_imports_meta(dict_form)
    assert [e["name"] for e in out] == ["pbg_copasi", "pbg_ketchup"]  # sorted
    ket = next(e for e in out if e["name"] == "pbg_ketchup")
    assert ket["package"] == "pbg_ketchup"
    assert ket["source"] == "https://github.com/x/pbg-ketchup"
    assert ket["ref"] == "main"
    assert ket["description"] == "KETCHUP estimators"

    list_form = {"imports": [
        {"name": "pbg-torch", "package": "pbg_torch",
         "source": "https://github.com/x/pbg-torch"},
        "viva-munk",
    ]}
    out2 = _registry_imports_meta(list_form)
    pkgs = {e["package"] for e in out2}
    assert "pbg_torch" in pkgs
    assert "viva_munk" in pkgs  # bare-string entry, dashes normalized


def test_registry_imports_meta_empty():
    from vivarium_workbench.lib.registry import _registry_imports_meta
    assert _registry_imports_meta({}) == []
    assert _registry_imports_meta(None) == []
