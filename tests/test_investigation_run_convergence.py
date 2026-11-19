"""Plan §A5 — the "Run" button converges onto run_jobs for v3 investigations.

The two orchestrators this plan set out to merge read DIFFERENT spec shapes,
which is why they never merged on their own:

    /api/investigation-run            investigations/<n>/spec.yaml       (v2)
    /api/investigation-run-unblocked  investigations/<n>/investigation.yaml (v3)

`vwb migrate-investigations` is the one-way v2 -> v3 rewrite, and the real
v2ecoli build carries 11 investigations, all investigation.yaml, zero spec.yaml.
So convergence is a delegation for v3, and v2 keeps its synchronous behaviour.
"""
from __future__ import annotations

import yaml

from vivarium_workbench.lib import investigation_run_views as irv
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: conv-ws\npackage_path: pkg\n")
    ws = WorkspacePaths.load(tmp_path)
    ws.investigations.mkdir(parents=True, exist_ok=True)
    ws.studies.mkdir(parents=True, exist_ok=True)
    return ws


def _v3(tmp_path, members=("a",)):
    ws = _ws(tmp_path)
    d = ws.investigations / "inv"
    d.mkdir(parents=True, exist_ok=True)
    (d / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "inv", "members": list(members)}))
    for m in members:
        sd = ws.studies / m
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "name": m,
            "conditions": {"baseline": {"composite": "pkg.composites.cell"}},
        }))
    return ws


def _v2(tmp_path):
    ws = _ws(tmp_path)
    d = ws.investigations / "inv"
    d.mkdir(parents=True, exist_ok=True)
    (d / "spec.yaml").write_text(yaml.safe_dump({
        "name": "inv", "composite": "pkg.composites.cell",
        "simulations": [{"name": "s1", "params": {}}],
    }))
    return ws


def test_v3_investigation_delegates_and_returns_202_with_a_job_id(tmp_path, monkeypatch):
    """The convergence itself: the Run button now answers with the SAME async
    contract Run-unblocked has, instead of blocking through every simulation."""
    _v3(tmp_path)
    seen = {}

    def _fake(ws_root, body):
        seen["body"] = body
        return {"job_id": "j-123", "items": [{"study": "a"}]}, 202

    monkeypatch.setattr(
        "vivarium_workbench.lib.run_unblocked_views.investigation_run_unblocked", _fake)
    body, code = irv.investigation_run(tmp_path, {"name": "inv"})
    assert seen, "the async lib function was never reached"
    assert seen["body"] == {"investigation": "inv"}
    assert code == 202, body
    assert body["job_id"] == "j-123"


def test_v3_on_a_deployment_target_no_longer_refuses(tmp_path, monkeypatch):
    """A1 made this route 409 on a deployment target because it could not honour
    one. Delegation CAN, so the refusal must not fire for v3 — otherwise A5
    would have converged the button onto a path it then blocks."""
    _v3(tmp_path)
    monkeypatch.setattr(
        "vivarium_workbench_sms.remote_pinned.resolve_run_target",
        lambda p: "deployment")
    monkeypatch.setattr(
        "vivarium_workbench.lib.run_unblocked_views.investigation_run_unblocked",
        lambda ws, b: ({"job_id": "j-9"}, 202))
    body, code = irv.investigation_run(tmp_path, {"name": "inv"})
    assert code == 202, body


def test_v2_spec_still_refuses_a_deployment_target(tmp_path, monkeypatch):
    """v2 has no studies to delegate to, so it keeps A1's refusal. Inventing a
    v2->studies translation here would be a migration wearing a run button."""
    _v2(tmp_path)
    monkeypatch.setattr(
        "vivarium_workbench_sms.remote_pinned.resolve_run_target",
        lambda p: "deployment")
    body, code = irv.investigation_run(tmp_path, {"name": "inv"})
    assert code == 409, body
    assert body["run_target"] == "deployment"


def test_v2_spec_does_not_delegate(tmp_path, monkeypatch):
    """The discriminator is the spec shape, not the presence of the file."""
    _v2(tmp_path)
    called = []
    monkeypatch.setattr(
        "vivarium_workbench.lib.run_unblocked_views.investigation_run_unblocked",
        lambda ws, b: called.append(b) or ({"job_id": "x"}, 202))
    monkeypatch.setattr(
        "vivarium_workbench_sms.remote_pinned.resolve_run_target", lambda p: "local")
    irv.investigation_run(tmp_path, {"name": "inv"})
    assert called == [], "a v2 spec must not be handed to the v3 orchestrator"


def test_a_dir_with_both_shapes_keeps_the_v2_path(tmp_path, monkeypatch):
    """Mid-migration a directory can hold both. The v2 loader is the one that
    understands `spec.yaml`, so it wins — delegating would silently run a
    DIFFERENT set of simulations than the spec the user is looking at."""
    ws = _v3(tmp_path)
    (ws.investigations / "inv" / "spec.yaml").write_text(yaml.safe_dump({
        "name": "inv", "composite": "pkg.composites.cell", "simulations": []}))
    called = []
    monkeypatch.setattr(
        "vivarium_workbench.lib.run_unblocked_views.investigation_run_unblocked",
        lambda ws_, b: called.append(b) or ({"job_id": "x"}, 202))
    monkeypatch.setattr(
        "vivarium_workbench_sms.remote_pinned.resolve_run_target", lambda p: "local")
    irv.investigation_run(tmp_path, {"name": "inv"})
    assert called == [], "spec.yaml present must keep the v2 path"


def test_name_is_still_required(tmp_path):
    _v3(tmp_path)
    body, code = irv.investigation_run(tmp_path, {})
    assert code == 400 and "name is required" in body["error"]
