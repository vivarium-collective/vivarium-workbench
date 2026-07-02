"""Tests for vivarium_workbench.lib.workspace_manifest_views — the pure,
ws_root-parameterised builders backing GET /api/workspace-manifest.

Hermetic: every test drives a tmp ws_root and monkeypatches the lib helpers
that would otherwise shell out to git / build the process registry / scan the
runs DB, so no real git repo or subprocess discovery runs.
"""

import yaml

from vivarium_workbench.lib import workspace_manifest_views as wmv
from vivarium_workbench.lib import composite_lookup


def _write_workspace(root, **extra):
    doc = {"name": "testws", "package_path": "pbg_testws",
           "description": "a test workspace"}
    doc.update(extra)
    (root / "workspace.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return doc


# ---------------------------------------------------------------------------
# Pure helpers (moved from server.py)
# ---------------------------------------------------------------------------

def test_count_viz_steps_in_state_counts_only_viz_step_addresses():
    state = {
        "metabolism": {"_type": "process", "address": "local:Metabolism"},
        "plot":       {"_type": "step", "address": "local:TimeSeriesPlot"},
        "heat":       {"_type": "step", "address": "local:HeatmapViz"},
        "clock":      {"_type": "step", "address": "local:Clock"},
        "not_a_dict": 42,
    }
    assert wmv.count_viz_steps_in_state(state) == 2
    assert wmv.count_viz_steps_in_state("nope") == 0


def test_filter_composites_noop_without_allowlist():
    recs = [{"id": "a", "module": "foreign_pkg.composites.x"}]
    # ws_data with no dashboard.registry allow-list → returns records unchanged.
    assert wmv.filter_composites(recs, {"name": "testws"}) == recs


def test_filter_composites_applies_allowlist():
    recs = [
        {"id": "own", "module": "pbg_testws.composites.x"},
        {"id": "foreign", "module": "other_pkg.composites.y"},
    ]
    ws_data = {"name": "testws",
               "dashboard": {"registry": {"include": ["pbg_testws"]}}}
    kept = wmv.filter_composites(recs, ws_data)
    kept_ids = {c["id"] for c in kept}
    assert "own" in kept_ids
    assert "foreign" not in kept_ids


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def test_workspace_section_shape(tmp_path, monkeypatch):
    _write_workspace(tmp_path)
    monkeypatch.setattr(wmv._git_status, "has_origin_remote", lambda root: True)
    sec = wmv.manifest_workspace_section(tmp_path)
    assert sec["name"] == "testws"
    assert sec["package_path"] == "pbg_testws"
    assert sec["description"] == "a test workspace"
    assert sec["has_origin"] is True
    # tmp_path is not a git repo → branch degrades to "unknown".
    assert isinstance(sec["branch"], str)


def test_health_section_reports_dirty_count(tmp_path, monkeypatch):
    porcelain = " M scripts/foo.py\n?? scratch.txt\n"
    monkeypatch.setattr(wmv._git_status, "dirty_workspace", lambda root: porcelain)
    sec = wmv.manifest_health_section(tmp_path)
    assert sec["dirty_count"] == 2
    assert any("foo.py" in p for p in sec["dirty_files"])
    assert sec["venv_present"] is False
    assert isinstance(sec["python_version"], str) and sec["python_version"]


def test_registry_section_counts_kinds(tmp_path, monkeypatch):
    fake = {
        "processes": [
            {"kind": "process"}, {"kind": "process"},
            {"kind": "step"},
            {"kind": "emitter"},
            {"kind": "visualization"},
            {"kind": None},  # → "other", not surfaced
        ],
        "types": [{"name": "t1"}, {"name": "t2"}],
    }
    monkeypatch.setattr(wmv._registry, "build_registry", lambda root, **kw: fake)
    sec = wmv.manifest_registry_section(tmp_path)
    assert sec == {
        "process_count": 2, "step_count": 1, "emitter_count": 1,
        "visualization_count": 1, "type_count": 2,
    }


def test_registry_section_degrades_to_zeros(tmp_path, monkeypatch):
    def _boom(root, **kw):
        raise RuntimeError("registry build failed")
    monkeypatch.setattr(wmv._registry, "build_registry", _boom)
    sec = wmv.manifest_registry_section(tmp_path)
    assert sec == {"process_count": 0, "step_count": 0, "emitter_count": 0,
                   "visualization_count": 0, "type_count": 0}


def test_composites_section_shape(tmp_path, monkeypatch):
    _write_workspace(tmp_path)
    fake_comps = {
        "pbg_testws.composites.demo": {
            "id": "pbg_testws.composites.demo",
            "name": "Demo",
            "kind": "spec",
            "module": "pbg_testws.composites.demo",
            "description": "a demo composite",
            "state": {"plot": {"_type": "step", "address": "local:TimeSeriesPlot"}},
        },
    }
    monkeypatch.setattr(composite_lookup, "discover_all_composites",
                        lambda root, pkg: fake_comps)
    sec = wmv.manifest_composites_section(tmp_path)
    assert len(sec) == 1
    c = sec[0]
    assert c["id"] == "pbg_testws.composites.demo"
    assert c["kind"] == "spec"
    assert c["module"] == "pbg_testws.composites.demo"
    assert c["viz_step_count"] == 1


def test_studies_section_lists_specs(tmp_path, monkeypatch):
    _write_workspace(tmp_path)
    study_dir = tmp_path / "investigations" / "demo"
    study_dir.mkdir(parents=True)
    (study_dir / "spec.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "demo",
        "topic": "metabolism",
        "status": "in-progress",
        "baseline": [{"name": "core", "composite": "pbg_testws.composites.demo"}],
        "variants": [],
        "interventions": [],
        "runs": [],
        "conclusions": "## Claims\nlooks promising",
    }, sort_keys=False), encoding="utf-8")
    # Avoid touching a real runs DB.
    monkeypatch.setattr(wmv._investigations_index, "_count_runs_for_study",
                        lambda root, name, spec: 3)
    sec = wmv.manifest_studies_section(tmp_path)
    assert len(sec) == 1
    s = sec[0]
    assert s["name"] == "demo"
    assert s["topic"] == "metabolism"
    assert s["status"] == "in-progress"
    assert s["n_baseline"] == 1
    assert s["n_variants"] == 0
    assert s["baseline_names"] == ["core"]
    assert s["n_runs"] == 3
    assert s["conclusions_len"] > 0


def test_skills_section_returns_list(tmp_path):
    # Best-effort read of ~/.claude/skills — assert it never raises and is a list.
    assert isinstance(wmv.manifest_skills_section(tmp_path), list)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def test_workspace_manifest_aggregates_six_sections(tmp_path, monkeypatch):
    _write_workspace(tmp_path)
    monkeypatch.setattr(wmv._git_status, "has_origin_remote", lambda root: True)
    monkeypatch.setattr(wmv._git_status, "dirty_workspace", lambda root: "")
    monkeypatch.setattr(wmv._registry, "build_registry",
                        lambda root, **kw: {"processes": [], "types": []})
    monkeypatch.setattr(composite_lookup, "discover_all_composites",
                        lambda root, pkg: {})
    out, status = wmv.workspace_manifest(tmp_path)
    assert status == 200
    assert set(out) == {"workspace", "composites", "studies",
                        "registry", "health", "skills"}
    assert out["workspace"]["name"] == "testws"
    assert isinstance(out["composites"], list)
    assert isinstance(out["studies"], list)
    assert isinstance(out["skills"], list)
    assert isinstance(out["registry"], dict)
    assert isinstance(out["health"], dict)
