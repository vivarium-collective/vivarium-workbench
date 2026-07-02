"""I1: run_study_baseline overlays request-body overrides on top of baseline params."""
import yaml
import pytest
from pathlib import Path
from dataclasses import dataclass


@pytest.fixture
def _override_ws(tmp_path, monkeypatch):
    """Minimal workspace with one v3 study; all subprocess/resolve steps mocked."""
    from vivarium_workbench.lib import (
        composite_subprocess,
        run_core,
        study_run_post,
        study_run_state,
        composite_runs as cr,
    )

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: test-ws\ncreated: '2026-01-01'\n"
        "package_path: test_pkg\n"
    )
    sd = ws / "studies" / "demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "demo", "created": "2026-01-01",
        "status": "planned", "objective": "",
        "baseline": [
            {"name": "core", "composite": "test_pkg.composites.cell",
             "params": {"n_steps": 2, "seed": 42}},
        ],
        "variants": [], "runs": [], "visualizations": [], "comparisons": [],
        "conclusion": None, "parent_studies": [], "interventions": [],
    }))

    # Fake RunPlan so invoke_run doesn't need a real composite registry.
    @dataclass
    class _FakePlan:
        run_id: str = "fake-run-001"
        spec_id: str = "test_pkg.composites.cell"
        db_path: Path = sd / "runs.db"
        config: dict = None
        label: str = "core"
        n_steps: int = None
        target: str = "local"

        def __post_init__(self):
            if self.config is None:
                self.config = {}

    captured = {}

    monkeypatch.setattr(run_core, "invoke_run", lambda *a, **kw: _FakePlan(config=kw.get("config", {})))
    monkeypatch.setattr(study_run_state, "resolve_study_baseline_state", lambda *a, **kw: ({}, None))
    monkeypatch.setattr(study_run_state, "investigation_emitter_for_study", lambda *a, **kw: None)
    monkeypatch.setattr(cr, "collect_emit_paths_from_spec", lambda *a, **kw: [])

    def _fake_subprocess(ws_root, *, overrides, **kwargs):
        captured["overrides"] = dict(overrides)
        return {"simulation_id": "fake-run-001"}, 200

    monkeypatch.setattr(composite_subprocess, "run_composite_subprocess", _fake_subprocess)
    monkeypatch.setattr(study_run_post, "render_study_visualizations", lambda *a, **kw: ([], []))
    monkeypatch.setattr(study_run_post, "run_post_run_scripts", lambda *a, **kw: ([], []))
    monkeypatch.setattr(study_run_post, "run_study_analyses", lambda *a, **kw: ([], []))

    return ws, captured


def test_run_study_baseline_overlays_body_overrides(_override_ws):
    """run_study_baseline with body {overrides: {k: 9}} must pass k=9 into
    composite_subprocess.run_composite_subprocess as overrides["k"] == 9."""
    ws, captured = _override_ws
    from vivarium_workbench.lib import study_runs

    body, status = study_runs.run_study_baseline(ws, {"study": "demo", "overrides": {"k": 9}})
    assert status == 200, body
    assert "overrides" in captured, "run_composite_subprocess was not called"
    assert captured["overrides"].get("k") == 9, (
        f"expected k=9 in subprocess overrides, got: {captured['overrides']}"
    )


def test_run_study_baseline_overlay_does_not_stomp_baseline_params(_override_ws):
    """Existing baseline params (seed=42) survive the overlay when not in body.overrides."""
    ws, captured = _override_ws
    from vivarium_workbench.lib import study_runs

    body, status = study_runs.run_study_baseline(ws, {"study": "demo", "overrides": {"k": 9}})
    assert status == 200, body
    assert captured["overrides"].get("seed") == 42, (
        f"baseline param seed=42 must survive overlay; got: {captured['overrides']}"
    )


def test_run_study_baseline_body_steps_honored(_override_ws):
    """body[\"steps\"] overrides the baseline params n_steps for the subprocess call."""
    ws, captured = _override_ws
    from vivarium_workbench.lib import study_runs

    # We can't directly capture steps from run_composite_subprocess kwargs here
    # because it's a positional-or-keyword arg; patch at the module level to capture it.
    from vivarium_workbench.lib import composite_subprocess
    steps_seen = {}

    def _capture_steps(ws_root, *, steps, **kwargs):
        steps_seen["steps"] = steps
        return {"simulation_id": "fake-run-001"}, 200

    import unittest.mock as mock
    with mock.patch.object(composite_subprocess, "run_composite_subprocess", _capture_steps):
        body, status = study_runs.run_study_baseline(ws, {"study": "demo", "steps": 77})
    assert status == 200, body
    assert steps_seen.get("steps") == 77, (
        f"body steps=77 must reach subprocess; got steps={steps_seen.get('steps')}"
    )
