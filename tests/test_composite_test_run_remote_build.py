"""SP-D2: remote-build workspace → dispatches to deployment (was SP-A's 409 guard).

A workspace carrying a ``.viv-build.json`` stamp has been materialised from a
remote build; ``run_core.run_target_for`` resolves it to the ``deployment``
target. Under SP-A this returned 409 (deployment execution unbuilt). SP-D2 BUILDS
that path: ``composite_test_run`` now accepts (202) and stamps ``target:
"deployment"`` into the run-request, so the detached runner dispatches to sms-api
``/compose/v1`` instead of running locally.
"""
from __future__ import annotations

import json


def test_composite_test_run_on_remote_build_dispatches(tmp_path, monkeypatch):
    from vivarium_workbench.lib import composite_test_run_views as v
    from vivarium_workbench.lib import run_registry

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: remote-ws\n", encoding="utf-8")
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')

    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    monkeypatch.setattr(run_registry, "spawn_detached", lambda *a, **k: 4242)
    # This test covers dispatch ROUTING, not the git preflight — the tmp workspace
    # isn't a pushed git repo, so mock the preflight as ready (its own behaviour is
    # covered by test_composite_test_run_remote_unpushed_returns_409 below).
    from vivarium_workbench.lib import remote_run
    monkeypatch.setattr(remote_run, "remote_dispatch_preflight",
                        lambda ws: {"ok": True, "reason": "ok"})

    body, status = v.composite_test_run(
        tmp_path, {"id": "pkg.composites.x", "overrides": {}, "steps": 7})

    assert status == 202
    assert body["status"] == "running"

    # The run-request carries the deployment target so run_runner.execute dispatches remotely.
    run_dir = tmp_path / ".pbg" / "runs" / body["run_id"]
    req = json.loads((run_dir / "request.json").read_text())
    assert req["target"] == "deployment"
    assert req["steps"] == 7


def test_composite_test_run_remote_unpushed_returns_409(tmp_path, monkeypatch):
    """A deployment-target dispatch on a workspace that isn't git-clean+pushed
    returns a clean 409 (the preflight) with an actionable message, and spawns
    NO detached run — instead of the runner tracing out on git_pip_url."""
    from vivarium_workbench.lib import composite_test_run_views as v
    from vivarium_workbench.lib import run_registry, remote_run

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: remote-ws\n", encoding="utf-8")
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    spawned = []
    monkeypatch.setattr(run_registry, "spawn_detached",
                        lambda *a, **k: (spawned.append(1), 4242)[1])
    monkeypatch.setattr(remote_run, "remote_dispatch_preflight",
                        lambda ws: {"ok": False, "reason": "unpushed",
                                    "message": "Workspace commit isn't pushed — push it first."})

    body, status = v.composite_test_run(tmp_path, {"id": "pkg.composites.x", "steps": 7})

    assert status == 409
    assert body["reason"] == "unpushed"
    assert "push" in body["error"].lower()
    assert not spawned  # no detached run spawned


def test_execute_remote_forwards_overrides_to_run_remote(tmp_path, monkeypatch):
    """The remote-dispatch path must apply the run form's parameter overrides —
    previously they were dropped, so a UI Run used the composite DEFAULTS (e.g.
    batch_baseline's 4 cells x 3600s) regardless of what the user set."""
    from vivarium_workbench.lib import run_runner
    from vivarium_workbench.lib import remote_run
    from vivarium_workbench.lib import composite_runs as cr

    req = run_runner.RunRequest(
        run_id="r1", spec_id="pkg.composites.batch", pkg="pkg", workspace=tmp_path,
        overrides={"n_seeds": 1, "max_duration": 60.0}, steps=3, emit_paths=[],
        db_file=str(tmp_path / "runs.db"), log_path="log", target="deployment")

    captured = {}

    def fake_run_remote(ws, spec_id, dest=None, n_steps=1, overrides=None):
        captured.update(overrides=overrides, spec_id=spec_id, n_steps=n_steps)

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(remote_run, "run_remote", fake_run_remote)
    monkeypatch.setattr(cr, "connect", lambda db: _Conn())
    monkeypatch.setattr(cr, "complete_metadata", lambda *a, **k: None)

    rc = run_runner._execute_remote(req, tmp_path)
    assert rc == 0
    assert captured["overrides"] == {"n_seeds": 1, "max_duration": 60.0}
    assert captured["n_steps"] == 3


def test_composite_test_run_explicit_cloud_build_runs_without_push(tmp_path, monkeypatch):
    """A Cloud run against a SELECTED build (run_target='deployment' + build)
    dispatches to deployment and stamps build_ref into the run-request WITHOUT
    any git preflight — so it works on an un-pushed/dirty workspace (the whole
    point: run build N's committed code, not the local tree)."""
    from vivarium_workbench.lib import composite_test_run_views as v
    from vivarium_workbench.lib import run_registry, remote_run

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    monkeypatch.setattr(run_registry, "spawn_detached", lambda *a, **k: 4242)
    # The preflight MUST NOT be consulted on the explicit-build path — fail loudly
    # if it is, to prove a Cloud-against-build run never requires a local push.
    def _boom(*a, **k):  # pragma: no cover - only hit on regression
        raise AssertionError("git preflight must be skipped for explicit build runs")
    monkeypatch.setattr(remote_run, "remote_dispatch_preflight", _boom)

    body = {"id": "pkg.composites.x", "steps": 7, "run_target": "deployment",
            "build": {"simulator_id": 211,
                      "repo_url": "https://github.com/CovertLabEcoli/sms-ecoli.git",
                      "commit": "33ecd77"}}
    resp, status = v.composite_test_run(tmp_path, body)
    assert status == 202, resp
    assert resp["status"] == "running"

    req = json.loads((tmp_path / ".pbg" / "runs" / resp["run_id"] / "request.json").read_text())
    assert req["target"] == "deployment"
    assert req["build_ref"] == {"simulator_id": 211,
                                "repo_url": "https://github.com/CovertLabEcoli/sms-ecoli.git",
                                "commit": "33ecd77"}


def test_composite_test_run_cloud_no_build_returns_409(tmp_path, monkeypatch):
    """run_target='deployment' but NO build resolved → clean 409 (never silently
    fall back to local — that silent-local behaviour is the confusion we remove)."""
    from vivarium_workbench.lib import composite_test_run_views as v
    from vivarium_workbench.lib import run_registry

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    spawned = []
    monkeypatch.setattr(run_registry, "spawn_detached",
                        lambda *a, **k: (spawned.append(1), 1)[1])

    resp, status = v.composite_test_run(tmp_path, {"id": "pkg.composites.x", "run_target": "deployment"})
    assert status == 409
    assert resp["reason"] == "no-build"
    assert not spawned


def test_execute_remote_forwards_build_ref_to_run_remote(tmp_path, monkeypatch):
    """_execute_remote forwards RunRequest.build_ref into run_remote so the
    dispatch installs the selected build's commit (not the local tree)."""
    from vivarium_workbench.lib import run_runner, remote_run
    from vivarium_workbench.lib import composite_runs as cr

    bref = {"simulator_id": 211, "repo_url": "https://github.com/x/sms-ecoli.git", "commit": "33ecd77"}
    req = run_runner.RunRequest(
        run_id="r1", spec_id="pkg.composites.x", pkg="pkg", workspace=tmp_path,
        overrides={}, steps=3, emit_paths=[], db_file=str(tmp_path / "runs.db"),
        log_path="log", target="deployment", build_ref=bref)
    captured = {}

    def fake_run_remote(ws, spec_id, dest=None, n_steps=1, overrides=None, build_ref=None, **k):
        captured.update(build_ref=build_ref)

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(remote_run, "run_remote", fake_run_remote)
    monkeypatch.setattr(cr, "connect", lambda db: _Conn())
    monkeypatch.setattr(cr, "complete_metadata", lambda *a, **k: None)

    run_runner._execute_remote(req, tmp_path)
    assert captured["build_ref"] == bref
