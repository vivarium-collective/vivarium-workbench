"""Tests for vivarium_workbench.lib.study_crud_mutations (Batch 19).

Three sections:
1. Direct lib builder tests — fixture workspace, assert file mutations and
   (dict, status) returns including 400/404/409 error paths.
2. Server shim parity — assert server._post_study_X_for_test(ws, body)
   delegates to the lib builder (same result).
3. FastAPI route tests — client.post(...) → assert mutation + 200/4xx.
"""
from __future__ import annotations

import sqlite3

import pytest
import yaml
from fastapi.testclient import TestClient

from vivarium_workbench.lib import study_crud_mutations as scm
from vivarium_workbench.api.app import create_app, get_workspace


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path):
    """Workspace with one v3 study (s1) under studies/."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "workspace.yaml").write_text(
        'schema_version: 2\nname: ws\ncreated: "2026-05-14"\n'
        'plugin_version: 0.6.1\npackage_path: pkg\n'
    )
    sd = ws_root / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "s1",
        "baseline": [{"name": "core", "composite": "pkg.composites.foo", "params": {}}],
        "variants": [
            {"name": "fast", "base_composite": "core", "parameter_overrides": {"k": 1}},
        ],
        "runs": [], "visualizations": [], "interventions": [],
        "comparisons": [],
    }))
    return ws_root


@pytest.fixture
def ws_layout_remap(tmp_path):
    """Workspace whose `layout:` remaps studies/ under workspace/ — the
    v2ecoli/sms-ecoli convention (studies: workspace/studies)."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "workspace.yaml").write_text(
        'schema_version: 2\nname: ws\ncreated: "2026-05-14"\n'
        'plugin_version: 0.6.1\npackage_path: pkg\n'
        'layout:\n  studies: workspace/studies\n'
    )
    sd = ws_root / "workspace" / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "s1",
        "baseline": [{"name": "core", "composite": "pkg.composites.foo", "params": {}}],
        "variants": [], "runs": [], "visualizations": [], "interventions": [],
        "comparisons": [],
    }))
    return ws_root


def _read_spec(ws_root):
    return yaml.safe_load((ws_root / "studies" / "s1" / "study.yaml").read_text())


def _seed_run(ws_root, run_id):
    """Insert one row into runs.db and append to study.yaml.runs."""
    from vivarium_workbench.lib.composite_runs import connect
    sd = ws_root / "studies" / "s1"
    db = sd / "runs.db"
    conn = connect(db)
    conn.execute(
        "INSERT INTO runs_meta (run_id, spec_id, label, params_json, "
        "started_at, status) VALUES (?,?,?,?,?,?)",
        (run_id, "pkg.foo", "lbl", "{}", 1.0, "completed"),
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history "
        "(simulation_id TEXT, step INTEGER, global_time REAL, state TEXT, "
        "PRIMARY KEY (simulation_id, step))"
    )
    conn.execute("INSERT INTO history VALUES (?,?,?,?)", (run_id, 0, 0.0, "{}"))
    conn.commit()
    conn.close()
    sf = sd / "study.yaml"
    spec = yaml.safe_load(sf.read_text())
    spec.setdefault("runs", []).append(
        {"run_id": run_id, "variant": None, "label": "lbl", "status": "completed"})
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))


# ---------------------------------------------------------------------------
# 1. Direct lib builder tests
# ---------------------------------------------------------------------------


class TestStudyVariantAdd:
    def test_happy(self, ws):
        resp, code = scm.study_variant_add(ws, {
            "study": "s1", "name": "slow", "base_composite": "core",
        })
        assert code == 200
        assert resp == {"ok": True, "name": "slow"}
        spec = _read_spec(ws)
        names = [v["name"] for v in spec["variants"]]
        assert "slow" in names

    def test_missing_study_400(self, ws):
        resp, code = scm.study_variant_add(ws, {"name": "v1", "base_composite": "core"})
        assert code == 400
        assert "study" in resp["error"].lower() or "variant" in resp["error"].lower()

    def test_missing_base_composite_400(self, ws):
        resp, code = scm.study_variant_add(ws, {"study": "s1", "name": "v1"})
        assert code == 400
        assert "base_composite" in resp["error"]

    def test_base_composite_not_in_baseline_404(self, ws):
        resp, code = scm.study_variant_add(ws, {
            "study": "s1", "name": "v1", "base_composite": "ghost",
        })
        assert code == 404

    def test_duplicate_variant_409(self, ws):
        resp, code = scm.study_variant_add(ws, {
            "study": "s1", "name": "fast", "base_composite": "core",
        })
        assert code == 409

    def test_study_not_found_404(self, ws):
        resp, code = scm.study_variant_add(ws, {
            "study": "no-such", "name": "v1", "base_composite": "core",
        })
        assert code == 404


class TestStudyVariantDelete:
    def test_happy(self, ws):
        resp, code = scm.study_variant_delete(ws, {"study": "s1", "variant": "fast"})
        assert code == 200
        assert resp == {"ok": True}
        spec = _read_spec(ws)
        assert all(v["name"] != "fast" for v in spec.get("variants", []))

    def test_missing_variant_400(self, ws):
        resp, code = scm.study_variant_delete(ws, {"study": "s1"})
        assert code == 400

    def test_unknown_variant_404(self, ws):
        resp, code = scm.study_variant_delete(ws, {"study": "s1", "variant": "ghost"})
        assert code == 404

    def test_study_not_found_404(self, ws):
        resp, code = scm.study_variant_delete(ws, {"study": "nope", "variant": "fast"})
        assert code == 404


class TestStudyVariantSetParams:
    def test_happy(self, ws):
        resp, code = scm.study_variant_set_params(ws, {
            "study": "s1", "variant": "fast",
            "parameter_overrides": {"k": 99, "n": 5},
        })
        assert code == 200
        spec = _read_spec(ws)
        v = next(v for v in spec["variants"] if v["name"] == "fast")
        assert v["parameter_overrides"] == {"k": 99, "n": 5}

    def test_missing_overrides_400(self, ws):
        resp, code = scm.study_variant_set_params(ws, {"study": "s1", "variant": "fast"})
        assert code == 400

    def test_overrides_not_dict_400(self, ws):
        resp, code = scm.study_variant_set_params(ws, {
            "study": "s1", "variant": "fast", "parameter_overrides": "bad",
        })
        assert code == 400

    def test_unknown_variant_404(self, ws):
        resp, code = scm.study_variant_set_params(ws, {
            "study": "s1", "variant": "ghost", "parameter_overrides": {},
        })
        assert code == 404


class TestStudyBaselineAdd:
    def test_happy(self, ws):
        resp, code = scm.study_baseline_add(ws, {
            "study": "s1", "name": "alt", "composite": "pkg.composites.bar",
        })
        assert code == 200
        assert resp == {"ok": True, "name": "alt"}
        spec = _read_spec(ws)
        assert any(b["name"] == "alt" for b in spec["baseline"])

    def test_missing_composite_400(self, ws):
        resp, code = scm.study_baseline_add(ws, {"study": "s1", "name": "alt"})
        assert code == 400
        assert "composite" in resp["error"].lower()

    def test_duplicate_400(self, ws):
        resp, code = scm.study_baseline_add(ws, {
            "study": "s1", "name": "core", "composite": "pkg.composites.other",
        })
        assert code == 409

    def test_missing_study_400(self, ws):
        resp, code = scm.study_baseline_add(ws, {
            "name": "alt", "composite": "pkg.composites.bar",
        })
        assert code == 400

    def test_honors_workspace_layout_remap(self, ws_layout_remap):
        """A workspace declaring `layout: {studies: workspace/studies}` (the
        v2ecoli/sms-ecoli convention) must still resolve — not 404 because the
        builder hardcoded ws_root/'studies' instead of WorkspacePaths."""
        resp, code = scm.study_baseline_add(ws_layout_remap, {
            "study": "s1", "name": "alt", "composite": "pkg.composites.bar",
        })
        assert code == 200
        spec = yaml.safe_load(
            (ws_layout_remap / "workspace" / "studies" / "s1" / "study.yaml").read_text()
        )
        assert any(b["name"] == "alt" for b in spec["baseline"])


class TestStudyBaselineRemove:
    def test_happy(self, ws):
        # First add a second baseline entry so removal doesn't empty it
        scm.study_baseline_add(ws, {
            "study": "s1", "name": "alt", "composite": "pkg.composites.bar",
        })
        resp, code = scm.study_baseline_remove(ws, {"study": "s1", "name": "alt"})
        assert code == 200
        spec = _read_spec(ws)
        assert all(b["name"] != "alt" for b in spec["baseline"])

    def test_unknown_entry_404(self, ws):
        resp, code = scm.study_baseline_remove(ws, {"study": "s1", "name": "ghost"})
        assert code == 404

    def test_variant_dependency_409(self, ws):
        # "fast" variant references "core" baseline
        resp, code = scm.study_baseline_remove(ws, {"study": "s1", "name": "core"})
        assert code == 409
        assert "fast" in resp.get("error", "") or "fast" in str(resp.get("dependents", []))

    def test_would_empty_400(self, ws):
        # Remove the variant first so 409 doesn't fire, then try to empty baseline
        scm.study_variant_delete(ws, {"study": "s1", "variant": "fast"})
        resp, code = scm.study_baseline_remove(ws, {"study": "s1", "name": "core"})
        assert code == 400
        assert "empty" in resp["error"].lower()


@pytest.fixture
def ws_v4(tmp_path):
    """Workspace with one v4 conditions-based study (item 80/83 regression):
    baseline is only ever defined under conditions.baseline — never a real,
    on-disk baseline[] key. The frontend/API response sees a baseline[] entry
    only because investigations._project_v4_redesign_to_legacy_view
    synthesizes it in-memory, purely for display; it is never persisted."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "workspace.yaml").write_text(
        'schema_version: 2\nname: ws\ncreated: "2026-05-14"\n'
        'plugin_version: 0.6.1\npackage_path: pkg\n'
    )
    sd = ws_root / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "s1",
        "conditions": {
            "baseline": {
                "composite": "pkg.composites.foo",
                "params": {"n_seeds": 10, "n_generations": 5},
            },
        },
        "runs": [], "visualizations": [], "interventions": [],
        "comparisons": [],
    }))
    return ws_root


class TestV4BaselineGuard:
    """Regression coverage for item 80/83's live-found bug: editing a v4
    conditions-based study's baseline params via the add-then-remove save
    flow silently created a disconnected, orphaned baseline[] entry (the
    add succeeded against an empty on-disk list; the follow-up remove then
    404'd trying to delete an entry — the one the frontend displayed — that
    had only ever existed as a synthesized, never-persisted view). Both
    halves must now refuse cleanly instead."""

    def test_add_refuses_on_unmaterialized_v4_baseline(self, ws_v4):
        resp, code = scm.study_baseline_add(ws_v4, {
            "study": "s1", "name": "s1-baseline-abc123",
            "composite": "pkg.composites.foo", "params": {"n_seeds": 20},
        })
        assert code == 422
        assert "conditions.baseline" in resp["error"]
        # Must NOT have created a disconnected baseline[] key.
        spec = yaml.safe_load((ws_v4 / "studies" / "s1" / "study.yaml").read_text())
        assert "baseline" not in spec
        assert spec["conditions"]["baseline"]["params"] == {"n_seeds": 10, "n_generations": 5}

    def test_remove_refuses_on_unmaterialized_v4_baseline(self, ws_v4):
        resp, code = scm.study_baseline_remove(ws_v4, {"study": "s1", "name": "s1"})
        assert code == 422
        assert "conditions.baseline" in resp["error"]

    def test_v4_study_with_already_materialized_baseline_is_unaffected(self, ws_v4):
        """A v4 study that already has a REAL, persisted baseline[] (e.g. an
        older study from before this guard existed, or one a maintainer
        deliberately materialized) must keep working exactly as a v3 study
        does — the guard only fires when there is nothing real to edit."""
        sf = ws_v4 / "studies" / "s1" / "study.yaml"
        spec = yaml.safe_load(sf.read_text())
        spec["baseline"] = [{"name": "s1", "composite": "pkg.composites.foo", "params": {}}]
        sf.write_text(yaml.safe_dump(spec, sort_keys=False))

        resp, code = scm.study_baseline_add(ws_v4, {
            "study": "s1", "name": "alt", "composite": "pkg.composites.bar",
        })
        assert code == 200
        resp, code = scm.study_baseline_remove(ws_v4, {"study": "s1", "name": "alt"})
        assert code == 200


class TestStudyInterventionAdd:
    def test_happy(self, ws):
        resp, code = scm.study_intervention_add(ws, {
            "study": "s1", "name": "heat-shock", "description": "+10C",
        })
        assert code == 200
        assert resp == {"ok": True, "name": "heat-shock"}
        spec = _read_spec(ws)
        assert any(i["name"] == "heat-shock" for i in spec.get("interventions", []))

    def test_default_empty_description(self, ws):
        scm.study_intervention_add(ws, {"study": "s1", "name": "x"})
        spec = _read_spec(ws)
        i = next(i for i in spec["interventions"] if i["name"] == "x")
        assert i["description"] == ""

    def test_missing_name_400(self, ws):
        resp, code = scm.study_intervention_add(ws, {"study": "s1"})
        assert code == 400

    def test_duplicate_409(self, ws):
        scm.study_intervention_add(ws, {"study": "s1", "name": "x"})
        resp, code = scm.study_intervention_add(ws, {"study": "s1", "name": "x"})
        assert code == 409


class TestStudyInterventionUpdate:
    def test_happy(self, ws):
        scm.study_intervention_add(ws, {"study": "s1", "name": "x", "description": "old"})
        resp, code = scm.study_intervention_update(ws, {
            "study": "s1", "name": "x", "description": "new",
        })
        assert code == 200
        spec = _read_spec(ws)
        i = next(i for i in spec["interventions"] if i["name"] == "x")
        assert i["description"] == "new"

    def test_unknown_404(self, ws):
        resp, code = scm.study_intervention_update(ws, {
            "study": "s1", "name": "ghost", "description": "x",
        })
        assert code == 404


class TestStudyInterventionDelete:
    def test_happy(self, ws):
        scm.study_intervention_add(ws, {"study": "s1", "name": "x"})
        scm.study_intervention_add(ws, {"study": "s1", "name": "y"})
        resp, code = scm.study_intervention_delete(ws, {"study": "s1", "name": "x"})
        assert code == 200
        spec = _read_spec(ws)
        assert [i["name"] for i in spec["interventions"]] == ["y"]

    def test_unknown_404(self, ws):
        resp, code = scm.study_intervention_delete(ws, {"study": "s1", "name": "ghost"})
        assert code == 404


class TestStudyRunDelete:
    def test_happy(self, ws):
        _seed_run(ws, "r1")
        _seed_run(ws, "r2")
        resp, code = scm.study_run_delete(ws, {"study": "s1", "run_id": "r1"})
        assert code == 200
        conn = sqlite3.connect(str(ws / "studies" / "s1" / "runs.db"))
        meta_ids = [r[0] for r in conn.execute("SELECT run_id FROM runs_meta")]
        conn.close()
        assert meta_ids == ["r2"]
        spec = _read_spec(ws)
        assert [r["run_id"] for r in spec["runs"]] == ["r2"]

    def test_missing_run_id_400(self, ws):
        resp, code = scm.study_run_delete(ws, {"study": "s1"})
        assert code == 400

    def test_study_not_found_404(self, ws):
        resp, code = scm.study_run_delete(ws, {"study": "no-such", "run_id": "r1"})
        assert code == 404


class TestStudyRunsClear:
    def test_happy(self, ws):
        _seed_run(ws, "r1")
        _seed_run(ws, "r2")
        resp, code = scm.study_runs_clear(ws, {"study": "s1"})
        assert code == 200
        conn = sqlite3.connect(str(ws / "studies" / "s1" / "runs.db"))
        n = conn.execute("SELECT COUNT(*) FROM runs_meta").fetchone()[0]
        conn.close()
        assert n == 0
        spec = _read_spec(ws)
        assert spec["runs"] == []

    def test_missing_study_400(self, ws):
        resp, code = scm.study_runs_clear(ws, {})
        assert code == 400

    def test_study_not_found_404(self, ws):
        resp, code = scm.study_runs_clear(ws, {"study": "no-such"})
        assert code == 404


class TestStudyComparisonAdd:
    def test_happy(self, ws):
        resp, code = scm.study_comparison_add(ws, {
            "study": "s1", "run_ids": ["r1", "r2"],
        })
        assert code == 200
        assert "name" in resp
        spec = _read_spec(ws)
        assert len(spec["comparisons"]) == 1
        assert spec["comparisons"][0]["run_ids"] == ["r1", "r2"]

    def test_custom_name(self, ws):
        resp, code = scm.study_comparison_add(ws, {
            "study": "s1", "run_ids": ["r1", "r2"], "name": "my-cmp",
        })
        # Note: _study_name_from_body tries "name" first as study id.
        # Since body["name"]="my-cmp" is non-empty, _study_name_from_body
        # returns "my-cmp" as the study — this is the documented legacy
        # behavior when callers send "name" for comparison label.
        # The study lookup fails → 404 (not a regression; matches seam).
        assert code == 404

    def test_too_few_run_ids_400(self, ws):
        resp, code = scm.study_comparison_add(ws, {"study": "s1", "run_ids": ["only-one"]})
        assert code == 400

    def test_missing_study_400(self, ws):
        resp, code = scm.study_comparison_add(ws, {"run_ids": ["r1", "r2"]})
        assert code == 400

    def test_study_not_found_404(self, ws):
        resp, code = scm.study_comparison_add(ws, {"study": "nope", "run_ids": ["r1", "r2"]})
        assert code == 404
