"""Method-class routing at the pool — §2A.8 workstream 8 step 1 (corrected).

The first version of step 1 pinned job-class methods to a LOCAL subprocess even
on a hosted deployment. That inverted the rule `env_worker_launcher` states —
"selected by deployment topology, not preference" — by making transport a
per-method choice, and on a hosted deployment it could not work at all: the
workbench pod's interpreter cannot import the workspace package, and the bridge
that let a venv-less build borrow the base workspace's venv died when the base
became a scaffold.

These tests pin the corrected property: **transport follows the deployment, for
every method**, and a session's context supplies both halves of its environment
(its dir on the PVC, the image its build stamp names). What separates a study
from a catalog query is SCALE, which dispatches to viva-api — a different axis
entirely, not a different launcher.
"""
import re
from pathlib import Path

import pytest

from vivarium_workbench.lib.env_worker_pool import WorkerPool
from vivarium_workbench.lib.env_worker_routing import JOB_CLASS_METHODS, is_job_class


class _FakeWorker:
    def __init__(self, kind):
        self.kind = kind

    def alive(self):
        return True

    def call(self, method, params=None, *, timeout=None):
        return {"served_by": self.kind, "method": method}

    def close(self):
        pass


class _FakeLauncher:
    def __init__(self, kind):
        self.kind = kind
        self.launched = 0

    def env_key(self, workspace):
        return f"{self.kind}:{workspace}"

    def launch(self, workspace, *, interpreter, timeout):
        self.launched += 1
        return _FakeWorker(self.kind)


@pytest.fixture
def routed(monkeypatch):
    """A pool with per-method routing and both launchers faked."""
    local, remote = _FakeLauncher("local"), _FakeLauncher("remote")
    monkeypatch.setattr(
        "vivarium_workbench.lib.env_worker_launcher.LocalWorkerLauncher", lambda: local)
    monkeypatch.setattr(
        "vivarium_workbench.lib.env_worker_launcher.default_launcher", lambda: remote)
    return WorkerPool(), local, remote


# --- the property worth having ----------------------------------------------

@pytest.mark.parametrize("method", sorted(JOB_CLASS_METHODS))
def test_job_class_methods_run_in_the_deployments_own_environment(routed, tmp_path, method):
    """Job-class work runs in the active context's environment, like everything
    else. On a hosted deployment that is the build's own image — the same image
    the simulation itself runs in, and strictly more correct than a subprocess in
    the workbench pod, which cannot import the workspace package at all."""
    pool, local, remote = routed
    r = pool.call(tmp_path, method, interpreter="/usr/bin/python3")
    assert r["served_by"] == "remote"
    assert local.launched == 0


@pytest.mark.parametrize("method", ["registry_catalog", "discover_composites",
                                    "attach_process_docs", "list_generators"])
def test_interactive_methods_follow_the_deployment(routed, tmp_path, method):
    pool, local, remote = routed
    r = pool.call(tmp_path, method, interpreter="/usr/bin/python3")
    assert r["served_by"] == "remote"


def test_one_workspace_holds_one_worker_across_method_classes(routed, tmp_path):
    """The corrected routing means a study and a catalog query share the context's
    single environment, instead of splitting it into two transports. The pool key
    still carries `kind`, so a deployment that genuinely runs both remains
    representable — nothing here routes to two at once any more."""
    pool, local, remote = routed
    assert pool.call(tmp_path, "registry_catalog", interpreter="/x")["served_by"] == "remote"
    assert pool.call(tmp_path, "run_study", interpreter="/x")["served_by"] == "remote"
    assert pool.size() == 1
    assert (local.launched, remote.launched) == (0, 1)


def test_discard_evicts_every_kind(routed, tmp_path):
    """A session switch must not leave a worker of any kind behind — still true
    when a pool holds entries from an explicit launcher alongside the deployment's."""
    pool, local, remote = routed
    pool.call(tmp_path, "registry_catalog", interpreter="/x")
    pool._acquire(str(tmp_path), "/x", local)  # simulate a local entry for the same ws
    assert pool.size() == 2
    pool.discard(tmp_path, interpreter="/x")
    assert pool.size() == 0


def test_an_explicit_launcher_still_overrides_everything(tmp_path):
    """Tests and single-transport callers keep the old behaviour."""
    only = _FakeLauncher("local")
    pool = WorkerPool(launcher=only)
    for m in ("registry_catalog", "run_study"):
        assert pool.call(tmp_path, m, interpreter="/x")["served_by"] == "local"


# --- drift guard -------------------------------------------------------------

def test_every_declared_capability_is_accounted_for():
    """A newly added HEAVY method must fail this test rather than silently
    inheriting the interactive path."""
    src = (Path(__file__).resolve().parent.parent
           / "vivarium_workbench" / "env_worker.py").read_text()
    caps = {c.strip().strip('"') for c in
            re.search(r"_CAPABILITIES = \[(.*?)\]", src, re.S).group(1).replace("\n", "").split(",")
            if c.strip()}
    assert JOB_CLASS_METHODS <= caps, JOB_CLASS_METHODS - caps
    # Documented as interactive on purpose (§2A.7: "Viz straddles ... don't
    # pre-design"), so their presence here is a decision, not an oversight.
    for viz in ("render_viz_doc", "viz_preview", "validate_generated_visualization"):
        assert viz in caps and not is_job_class(viz)


# --- the venv-less workspace -------------------------------------------------

def test_interactive_call_survives_a_workspace_with_no_venv(routed, tmp_path, monkeypatch):
    """The blocker for a minimal seed workspace (#937's supposed dependency).

    A hosted workspace with no `.venv` makes `resolve_interpreter` RAISE under
    REQUIRE_WORKSPACE_VENV. That must not reach an interactive call: the remote
    worker runs the simulator image's own Python and never wanted an interpreter
    from this filesystem. The pool used to resolve one before choosing a launcher,
    so the strict guard fired on the path that needed it least.
    """
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable

    def _no_venv(ws):
        raise EnvWorkerUnavailable(f"workspace has no .venv: {ws}")

    monkeypatch.setattr(
        "vivarium_workbench.lib.env_resolver.resolve_interpreter", _no_venv)
    pool, local, remote = routed
    assert pool.call(tmp_path, "registry_catalog")["served_by"] == "remote"


def test_the_launcher_names_the_environment_not_the_workspace(routed, tmp_path):
    """Pool keys come from `env_key`, so the environment is named by whatever the
    deployment's launcher says it is — an image on hosted, an interpreter on a
    laptop — never by asking the workspace for something that transport ignores."""
    pool, local, remote = routed
    pool.call(tmp_path, "registry_catalog")
    pool.call(tmp_path, "run_study")
    assert {k[1] for k in pool._entries} == {f"remote:{tmp_path}"}


def test_discard_without_an_interpreter_evicts_everything_for_the_workspace(routed, tmp_path):
    """Callers don't know the keys — the launchers mint them. Guessing one (the
    old `sys.executable` default) matched nothing and left workers pinned to the
    previous session."""
    pool, local, remote = routed
    pool.call(tmp_path, "registry_catalog")
    pool._acquire(str(tmp_path), "/other", local)
    assert pool.size() == 2
    pool.discard(tmp_path)
    assert pool.size() == 0


def test_job_class_survives_a_venvless_workspace_on_a_hosted_deployment(
        routed, tmp_path, monkeypatch):
    """The concrete breakage the correction removes.

    On a hosted deployment nothing has a `.venv`: the slim image ships none, the
    base workspace is a scaffold, and `materialize_build` extracts a tarball
    without provisioning one. Pinning job-class to a local subprocess therefore
    made every study, analysis and process run raise "workspace has no .venv" —
    advice pointing at the one thing §2A.8 deliberately removed.

    Routing by topology never asks the question, so the call goes through.
    """
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable

    def _no_venv(ws):
        raise EnvWorkerUnavailable(f"workspace has no .venv: {ws}")

    monkeypatch.setattr(
        "vivarium_workbench.lib.env_resolver.resolve_interpreter", _no_venv)
    pool, local, remote = routed
    for method in sorted(JOB_CLASS_METHODS):
        assert pool.call(tmp_path, method)["served_by"] == "remote"


def test_no_scale_warning_on_a_laptop(tmp_path, caplog, monkeypatch):
    """"Sized for interaction" is a property of a hosted worker POD. A laptop
    subprocess has no such ceiling and running a study there is the ordinary path,
    so warning would tell the user to dispatch work that has nowhere better to go.
    Caught by actually running the workbench locally, not by a unit test."""
    local = _FakeLauncher("local")
    monkeypatch.setattr(
        "vivarium_workbench.lib.env_worker_launcher.default_launcher", lambda: local)
    pool = WorkerPool()
    with caplog.at_level("WARNING"):
        pool.call(tmp_path, "run_study")
    assert not [r for r in caplog.records if "dispatch to viva-api" in r.getMessage()]


def test_scale_is_a_separate_axis_from_transport(routed, tmp_path, caplog):
    """`is_job_class` no longer picks a transport; it marks the calls a scale
    precheck will inspect (step 2). Until that exists the gap must be visible,
    and warned once per (method, workspace) rather than on every call."""
    pool, local, remote = routed
    with caplog.at_level("WARNING"):
        pool.call(tmp_path, "run_study")
        pool.call(tmp_path, "run_study")
        pool.call(tmp_path, "registry_catalog")
    warnings = [r for r in caplog.records if "dispatch to viva-api" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "run_study" in warnings[0].getMessage()


# --- run entrypoints honor the run target (step 2a) --------------------------

def test_run_process_is_not_job_class():
    """`env_worker._run_process` builds one class, fills its ports and runs a
    SINGLE `update()`, degrading to {ok: False, stage, error} rather than raising.
    That is the Composite Explorer's "try this process" probe, not a job. It was
    classified on the `run_` prefix rather than on what it does."""
    from vivarium_workbench.lib.env_worker_routing import is_job_class
    assert not is_job_class("run_process")


def test_run_entrypoints_are_narrower_than_job_class():
    """`resolve_run_target` governs run ENTRYPOINTS. Post-run analyses are
    job-class (heavy) but are not entrypoints — gating them on the run target
    would stop post-run analysis outright on any pinned deployment."""
    from vivarium_workbench.lib.env_worker_routing import (
        JOB_CLASS_METHODS, RUN_ENTRYPOINT_METHODS, is_run_entrypoint)
    assert RUN_ENTRYPOINT_METHODS < JOB_CLASS_METHODS
    assert is_run_entrypoint("run_study")
    for m in ("run_study_analyses", "run_investigation_analysis"):
        assert not is_run_entrypoint(m)


def test_study_step_refuses_to_run_a_deployment_target_in_a_worker(tmp_path, monkeypatch):
    """The step-2a defect: a user picks a remote build (or the deployment pins
    remote runs), and the investigation composite runs the study in a worker
    anyway — silently ignoring the choice they made."""
    from vivarium_workbench.lib import investigation_steps

    monkeypatch.setattr(
        "vivarium_workbench.lib.remote_pinned.resolve_run_target", lambda p: "deployment")

    def _boom(*a, **k):
        raise AssertionError("must not reach the env worker on a deployment target")

    monkeypatch.setattr("vivarium_workbench.lib.env_worker_pool.get_pool", _boom)
    with pytest.raises(RuntimeError, match="must not run in an env worker"):
        investigation_steps._run_study_hook(str(tmp_path), "some-study")


def test_study_step_still_uses_the_worker_on_a_local_target(tmp_path, monkeypatch):
    """The local path is unchanged — this gate must not cost a laptop its runs."""
    from vivarium_workbench.lib import investigation_steps

    monkeypatch.setattr(
        "vivarium_workbench.lib.remote_pinned.resolve_run_target", lambda p: "local")

    class _Pool:
        def call(self, ws, method, params=None):
            return {"ran": method}

    monkeypatch.setattr("vivarium_workbench.lib.env_worker_pool.get_pool", lambda: _Pool())
    assert investigation_steps._run_study_hook(str(tmp_path), "s")["ran"] == "run_study"
