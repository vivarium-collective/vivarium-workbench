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
    # simulator_id-only stamp (no repo_url) → no session-build image resolves, so
    # this exercises the opt-in compose/git-install path, which is gated behind
    # VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH=1 (c1). Without the flag it's a 409.
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH", "1")

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
    # Opt-in compose path (no session-build image resolves from a bare stamp).
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH", "1")
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
    assert body["actions"]  # actionable buttons alongside the preflight message
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


def test_composite_test_run_explicit_cloud_build_dispatches_image(tmp_path, monkeypatch):
    """Plan B: a Cloud run against a SELECTED build (run_target='deployment' +
    build with a simulator_id) dispatches the build's PRE-BUILT IMAGE via
    run_simulation — NOT a compose git-install of the build's repo (which the
    sms-api allow-list rejects). No git preflight (never needs a local push), no
    detached compose subprocess: it returns a synthetic 'remote-sim-<id>' run_id
    the loom polls, and the run surfaces in the Simulations/Runs tab."""
    from vivarium_workbench.lib import composite_test_run_views as v
    from vivarium_workbench.lib import run_registry, remote_run
    from vivarium_workbench.lib import sms_api_client as sac

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    # The preflight MUST NOT be consulted — a Cloud-against-build run never pushes.
    def _boom(*a, **k):  # pragma: no cover - only hit on regression
        raise AssertionError("git preflight must be skipped for explicit build runs")
    monkeypatch.setattr(remote_run, "remote_dispatch_preflight", _boom)
    # The detached compose subprocess must NOT be spawned on the image path.
    def _no_spawn(*a, **k):  # pragma: no cover - only hit on regression
        raise AssertionError("image-backed Cloud run must not spawn a compose subprocess")
    monkeypatch.setattr(run_registry, "spawn_detached", _no_spawn)
    # Capture the run_simulation call (the image dispatch) and return a fake sim.
    captured = {}
    def _fake_run_simulation(self, **kw):
        captured.update(kw)
        return {"database_id": 909, "experiment_id": "sim211-exp"}
    monkeypatch.setattr(sac.SmsApiClient, "run_simulation", _fake_run_simulation)
    # This build carries the whole-cell default config (a v2ecoli-style repo), so
    # config resolution succeeds and the dispatch proceeds (#1113 fail-closed only
    # triggers when NO default is discoverable — covered by its own test below).
    monkeypatch.setattr(sac.SmsApiClient, "_get",
                        lambda self, path, params=None: {"config_filenames": ["api_simulation_default.json"]})

    body = {"id": "pkg.composites.x", "steps": 7, "run_target": "deployment",
            "build": {"simulator_id": 211,
                      "repo_url": "https://github.com/CovertLabEcoli/sms-ecoli.git",
                      "commit": "33ecd77"}}
    resp, status = v.composite_test_run(tmp_path, body)
    assert status == 202, resp
    assert resp["status"] == "running"
    assert resp["run_id"] == "remote-sim-909"
    assert resp["remote"] is True
    assert resp["simulation_id"] == 909
    # dispatched the build's image, not a git repo
    assert captured["simulator_id"] == 211
    # no compose request.json was written (image path is async via sms-api)
    assert not (tmp_path / ".pbg" / "runs" / resp["run_id"]).exists()


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


def test_remote_sim_run_status_and_trajectory_map_to_sms_api(tmp_path, monkeypatch):
    """Plan B: the loom polls /api/composite-run/remote-sim-<id>/status and
    /api/composite-run/remote-sim-<id>. Status maps the sms-api phase
    (queued/running -> running, done -> completed, failed -> failed); trajectory
    returns an empty (remote) trajectory so the loom degrades gracefully."""
    from vivarium_workbench.lib import composite_run_views as crv
    from vivarium_workbench.lib import remote_run_views as rrv

    calls = {}
    def _fake_status(params):
        calls["params"] = params
        return {"kind": "simulation", "phase": "done", "raw_status": "completed"}, 200
    monkeypatch.setattr(rrv, "remote_run_status", _fake_status)

    st, code = crv.build_composite_run_status(tmp_path, "remote-sim-909")
    assert code == 200
    assert st["run_id"] == "remote-sim-909"
    assert st["status"] == "completed"      # 'done' -> 'completed'
    assert st["remote"] is True
    assert calls["params"] == {"simulation_id": 909}

    traj, tcode = crv.build_composite_run(tmp_path, "remote-sim-909")
    assert tcode == 200
    assert traj == {"run_id": "remote-sim-909", "trajectory": [], "remote": True}


def test_composite_test_run_pinned_workspace_dispatches_image(tmp_path, monkeypatch):
    """Plan B, pinned path: a composite-card Run on a workspace pinned to a build
    (a full .viv-build.json) sends NO explicit run_target — the loom only sends it
    when the scope is toggled. resolve_run_target → 'deployment', and the run must
    still dispatch the pinned build's image (not fall to compose/preflight)."""
    from vivarium_workbench.lib import composite_test_run_views as v
    from vivarium_workbench.lib import run_registry, remote_run, remote_pinned
    from vivarium_workbench.lib import sms_api_client as sac

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    monkeypatch.setattr(run_registry, "spawn_detached",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no compose spawn")))
    # Pinned to build 124 (resolve_run_target → deployment; session build resolves).
    monkeypatch.setattr(remote_pinned, "resolve_run_target", lambda ws: "deployment")
    monkeypatch.setattr(remote_pinned, "resolved_from_session_build",
                        lambda ws: {"simulator_id": 124, "repo_url": "r", "commit": "c", "branch": "b"})
    # The compose preflight must never be consulted when a pinned build resolves.
    monkeypatch.setattr(remote_run, "remote_dispatch_preflight",
                        lambda ws: (_ for _ in ()).throw(AssertionError("preflight skipped on pinned image path")))
    captured = {}
    monkeypatch.setattr(sac.SmsApiClient, "run_simulation",
                        lambda self, **kw: (captured.update(kw), {"database_id": 777})[1])
    # Build carries the whole-cell default so config resolution succeeds (#1113).
    monkeypatch.setattr(sac.SmsApiClient, "_get",
                        lambda self, path, params=None: {"config_filenames": ["api_simulation_default.json"]})

    resp, status = v.composite_test_run(tmp_path, {"id": "pkg.composites.x", "steps": 7})
    assert status == 202, resp
    assert resp["run_id"] == "remote-sim-777"
    assert resp["remote"] is True
    assert captured["simulator_id"] == 124


def test_composite_test_run_no_config_no_default_fails_closed(tmp_path, monkeypatch):
    """#1113: a Cloud image dispatch with NO explicit config, against a build whose
    repo has no whole-cell default, must FAIL CLOSED (409 no-config-for-composite)
    — never silently fall back to the build's alphabetically-first config (which
    ran an UNRELATED simulation attributed to the requested composite). Confirmed
    live 6/6 on build #211: every ecoli_baseline card-run silently became
    fss_pathway_oe_native_oe_carina. run_simulation must NOT be called."""
    from vivarium_workbench.lib import composite_test_run_views as v
    from vivarium_workbench.lib import run_registry, remote_run
    from vivarium_workbench.lib import sms_api_client as sac

    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    monkeypatch.setattr(run_registry, "spawn_detached",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no compose spawn")))
    monkeypatch.setattr(remote_run, "remote_dispatch_preflight",
                        lambda ws: (_ for _ in ()).throw(AssertionError("preflight skipped")))
    # The sms-ecoli fork build (#211): only CD-specific configs, NO whole-cell
    # default — the exact case that used to resolve to an arbitrary cfgs[0].
    monkeypatch.setattr(sac.SmsApiClient, "_get",
                        lambda self, path, params=None: {"config_filenames":
                            ["fss_pathway_oe_native_oe_carina.json", "mecillinam_wellmixed.json"]})

    def _must_not_run(self, **kw):  # pragma: no cover - only hit on regression
        raise AssertionError("run_simulation must not be called when config fails closed")
    monkeypatch.setattr(sac.SmsApiClient, "run_simulation", _must_not_run)

    body = {"id": "v2ecoli.composites.ecoli_baseline", "steps": 7, "run_target": "deployment",
            "build": {"simulator_id": 211,
                      "repo_url": "https://github.com/CovertLabEcoli/sms-ecoli.git",
                      "commit": "33ecd77"}}
    resp, status = v.composite_test_run(tmp_path, body)
    assert status == 409, resp
    assert resp["reason"] == "no-config-for-composite"
    assert "ecoli_baseline" in resp["error"]
    assert "#211" in resp["error"]
    assert resp["spec_id"] == "v2ecoli.composites.ecoli_baseline"
    assert resp["available_configs"] == [
        "fss_pathway_oe_native_oe_carina.json", "mecillinam_wellmixed.json"]
