"""launch_into_study — explicit replay inputs + manifest stamping (Task 3).

Hermetic: stubs run_core.invoke_run and the internal _launch_run_and_flush
seam so no real subprocess/flush runs; asserts launch_into_study resolves the
study's own runs.db, forwards the explicit spec_id/params/n_steps to
invoke_run, and builds + forwards a manifest carrying emitter/emit_paths/
runtime through to the (stubbed) launch+flush helper.
"""
from vivarium_workbench.lib import study_runs


def test_launch_into_study_explicit_inputs_and_manifest(tmp_path, monkeypatch):
    # A study dir must already exist for _resolve_study_dir's flat fallback
    # to resolve to studies/<name> (it falls back to investigations/<name>
    # when neither exists).
    (tmp_path / "studies" / "s1").mkdir(parents=True)

    seen = {}

    def fake_invoke_run(ws_root, *, spec_id, config, db_path, label, n_steps, seed=None,
                        target=None):
        seen.update(spec_id=spec_id, config=config, db_path=db_path, n_steps=n_steps,
                    seed=seed, target=target)
        class P:
            pass
        return P()

    monkeypatch.setattr(study_runs.run_core, "invoke_run", fake_invoke_run)

    manifests = []
    flush_kwargs = []
    monkeypatch.setattr(
        study_runs, "_launch_run_and_flush",
        lambda *a, **k: (flush_kwargs.append(k) or manifests.append(k.get("manifest")) or
                         ({"run_id": "r-new", "status": "running"}, 200)),
        raising=False,
    )

    resp, status = study_runs.launch_into_study(
        tmp_path, "s1", "some.composite", {"seed": 3}, 50,
        emitter="parquet", emit_paths=["bulk"], runtime={"emitter": "parquet"})

    assert "studies/s1/runs.db" in seen["db_path"].replace("\\", "/")
    assert seen["spec_id"] == "some.composite" and seen["config"].get("seed") == 3
    # item 18: launch_into_study now resolves + threads the target explicitly
    # (remote_pinned.resolve_run_target) rather than leaving invoke_run to
    # fall back to its own .viv-build.json-only check — a plain tmp_path
    # workspace (no .viv-build.json, pinned mode off) resolves "local".
    assert seen["target"] == "local"
    # reproducible-rerun-spine Task 4: seed falls back to params["seed"] when
    # no explicit seed= kwarg is passed — first-class seed, not just a plain
    # generator param.
    assert seen["seed"] == 3
    assert status == 200 and resp["run_id"]
    m = manifests[-1]
    assert m and m["emitter"] == "parquet" and m["emit_paths"] == ["bulk"]
    assert m["spec_id"] == "some.composite" and m["params"].get("seed") == 3
    assert m["origin"] == "study" and m["study"] == "s1"
    assert m["seed"] == 3
    # reran_from defaults to None when the caller (here, a direct
    # launch_into_study call, not a rerun) doesn't pass one.
    assert flush_kwargs[-1].get("reran_from") is None


def test_launch_into_study_explicit_seed_wins_over_params(tmp_path, monkeypatch):
    """An explicit seed= kwarg (as rerun.run_rerun passes from the ORIGINAL
    run's recorded manifest) overrides whatever happens to be in params —
    the first-class seed is the source of truth for a reproduce, not a
    same-named params key that could (in principle) differ."""
    (tmp_path / "studies" / "s1").mkdir(parents=True)

    def fake_invoke_run(ws_root, *, spec_id, config, db_path, label, n_steps, seed=None,
                        target=None):
        return type("P", (), {"seed": seed})()

    monkeypatch.setattr(study_runs.run_core, "invoke_run", fake_invoke_run)

    manifests = []
    monkeypatch.setattr(
        study_runs, "_launch_run_and_flush",
        lambda *a, **k: (manifests.append(k.get("manifest")) or ({}, 200)),
        raising=False,
    )

    study_runs.launch_into_study(
        tmp_path, "s1", "some.composite", {"seed": 999}, 5, seed=7)

    assert manifests[-1]["seed"] == 7


def test_launch_into_study_threads_reran_from(tmp_path, monkeypatch):
    """rerun.run_rerun passes reran_from=<original run_id> — launch_into_study
    must forward it to _launch_run_and_flush so the completion tail
    (composite_subprocess.run_composite_subprocess) can call
    verify_reproduction once this run's own result_fingerprint is stored."""
    (tmp_path / "studies" / "s1").mkdir(parents=True)

    def fake_invoke_run(ws_root, *, spec_id, config, db_path, label, n_steps, seed=None,
                        target=None):
        return type("P", (), {})()

    monkeypatch.setattr(study_runs.run_core, "invoke_run", fake_invoke_run)

    flush_kwargs = []
    monkeypatch.setattr(
        study_runs, "_launch_run_and_flush",
        lambda *a, **k: (flush_kwargs.append(k) or ({}, 200)),
        raising=False,
    )

    study_runs.launch_into_study(
        tmp_path, "s1", "some.composite", {}, 5, reran_from="orig-run-1")

    assert flush_kwargs[-1]["reran_from"] == "orig-run-1"


def test_launch_into_study_remote_build_guard_409(tmp_path, monkeypatch):
    """A remote-build workspace (.viv-build.json) rejects before any flush —
    mirrors run_study_baseline's existing 409 guard."""
    (tmp_path / "studies" / "s1").mkdir(parents=True)
    (tmp_path / ".viv-build.json").write_text("{}")

    called = []
    monkeypatch.setattr(
        study_runs, "_launch_run_and_flush",
        lambda *a, **k: called.append(1) or ({}, 200),
        raising=False,
    )

    resp, status = study_runs.launch_into_study(
        tmp_path, "s1", "some.composite", {}, 5)
    assert status == 409
    assert not called


def test_launch_into_study_pinned_deployment_guard_409(tmp_path, monkeypatch):
    """Item 18: a deployment-wide pin (VIVARIUM_WORKBENCH_REMOTE_PINNED), with
    NO .viv-build.json in this workspace, must ALSO reject before any flush —
    previously only the .viv-build.json case was caught here (invoke_run's own
    run_target_for fallback), so a pinned deployment with a plain workspace
    silently fell through to a local subprocess. Mirrors
    test_launch_into_study_remote_build_guard_409 but for the pin condition."""
    from vivarium_workbench.lib import remote_pinned
    (tmp_path / "studies" / "s1").mkdir(parents=True)
    assert not (tmp_path / ".viv-build.json").exists()  # the pin alone must be sufficient
    monkeypatch.setattr(remote_pinned, "is_pinned_enabled", lambda: True)

    called = []
    monkeypatch.setattr(
        study_runs, "_launch_run_and_flush",
        lambda *a, **k: called.append(1) or ({}, 200),
        raising=False,
    )

    resp, status = study_runs.launch_into_study(
        tmp_path, "s1", "some.composite", {}, 5)
    assert status == 409
    assert not called
