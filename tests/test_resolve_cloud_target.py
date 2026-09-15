"""c1 (#1107): resolve_cloud_target is the ONE typed Cloud-vs-local dispatch
decision — a CloudTarget (image dispatch), None (local run), or a (dict, 409)
with an actionable actions[] list. These tests pin every branch, including the
frozen-``PinnedConfig`` path that used to raise ``AttributeError`` → 500.
"""
from __future__ import annotations

import pytest

from vivarium_workbench.lib import composite_test_run_views as v
from vivarium_workbench.lib import remote_pinned


def test_explicit_build_with_simulator_id_returns_cloud_target(tmp_path):
    body = {"run_target": "deployment",
            "build": {"simulator_id": 211,
                      "repo_url": "https://github.com/x/sms-ecoli.git",
                      "commit": "33ecd77aabbcc"}}
    t = v.resolve_cloud_target(tmp_path, body)
    assert isinstance(t, v.CloudTarget)
    assert t.simulator_id == 211
    assert t.source == "explicit"
    assert t.repo_url.endswith("sms-ecoli.git")
    assert t.commit == "33ecd77aabbcc"


def test_explicit_build_without_simulator_id_returns_409_actions(tmp_path):
    # A build with commit + repo_url but NO simulator_id used to fall through to
    # the dead compose path; now it's an actionable dead-end.
    body = {"run_target": "deployment",
            "build": {"repo_url": "https://github.com/x/sms-ecoli.git",
                      "commit": "33ecd77aabbcc"}}
    t = v.resolve_cloud_target(tmp_path, body)
    assert isinstance(t, tuple)
    payload, status = t
    assert status == 409
    assert payload["reason"] == "no-build"
    assert payload["run_target"] == "deployment"
    labels = [a["label"] for a in payload["actions"]]
    assert "Build on cloud" in labels
    assert "Switch Environment to Local" in labels
    # The Build-on-cloud action carries the commit so the Builds panel pre-fills.
    build_action = next(a for a in payload["actions"] if a["label"] == "Build on cloud")
    assert build_action["commit"] == "33ecd77aabbcc"


def test_explicit_deployment_no_build_returns_409(tmp_path):
    t = v.resolve_cloud_target(tmp_path, {"run_target": "deployment"})
    assert isinstance(t, tuple)
    payload, status = t
    assert status == 409
    assert payload["reason"] == "no-build"


def test_local_workspace_returns_none(tmp_path, monkeypatch):
    # No explicit Cloud request and the workspace resolves to local.
    monkeypatch.setattr(remote_pinned, "resolve_run_target", lambda ws: "local")
    assert v.resolve_cloud_target(tmp_path, {}) is None


def test_session_build_resolves_to_cloud_target(tmp_path, monkeypatch):
    monkeypatch.setattr(remote_pinned, "resolve_run_target", lambda ws: "deployment")
    monkeypatch.setattr(
        remote_pinned, "resolved_from_session_build",
        lambda ws: {"simulator_id": 124, "repo_url": "r", "commit": "c", "branch": "b"})
    t = v.resolve_cloud_target(tmp_path, {})
    assert isinstance(t, v.CloudTarget)
    assert t.simulator_id == 124
    assert t.source == "session-build"


def test_pinned_workspace_resolves_to_cloud_target_frozen_dataclass(tmp_path, monkeypatch):
    """The frozen-``PinnedConfig`` path: pinned mode ON, no session build, a build
    resolves. pinned_config() returns a FROZEN dataclass — the code must read
    ``.repo_url`` as an attribute (never ``.get(...)``, which raised AttributeError
    → 500). Exercised through the real resolve_pinned_simulator_id."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL",
                       "https://github.com/x/sms-ecoli.git")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_BRANCH", "main")
    # No session build, so it falls to the deployment-wide pin.
    monkeypatch.setattr(remote_pinned, "resolved_from_session_build", lambda ws: None)
    # Stub only the network hop; the frozen PinnedConfig flows through for real.
    monkeypatch.setattr(
        remote_pinned, "resolve_pinned_build",
        lambda client, repo, branch: {"simulator_id": 333, "commit": "abc",
                                       "branch": branch, "repo_url": repo})

    t = v.resolve_cloud_target(tmp_path, {})
    assert isinstance(t, v.CloudTarget)          # NOT an AttributeError / 500
    assert t.simulator_id == 333
    assert t.source == "pinned"
    assert t.repo_url.endswith("sms-ecoli.git")  # read off the frozen dataclass


def test_pinned_workspace_no_build_returns_409_not_500(tmp_path, monkeypatch):
    """Pinned mode ON but NO build exists → the actionable 409, never a 500."""
    monkeypatch.setattr(remote_pinned, "resolve_run_target", lambda ws: "deployment")
    monkeypatch.setattr(remote_pinned, "resolved_from_session_build", lambda ws: None)
    monkeypatch.setattr(remote_pinned, "resolve_pinned_simulator_id",
                        lambda client, ws: None)
    t = v.resolve_cloud_target(tmp_path, {})
    assert isinstance(t, tuple)
    payload, status = t
    assert status == 409
    assert payload["reason"] == "no-build"
    assert [a["label"] for a in payload["actions"]] == [
        "Build on cloud", "Switch Environment to Local"]


def test_compose_dispatch_gated_off_by_default(monkeypatch):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH", raising=False)
    assert v._compose_dispatch_allowed() is False


def test_compose_dispatch_allowed_when_flag_set(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH", "1")
    assert v._compose_dispatch_allowed() is True


def test_pinned_no_build_end_to_end_is_409_not_500(tmp_path, monkeypatch):
    """The frozen-dataclass bug end to end: a pinned workspace with no build must
    surface as a clean 409 with actions[] from composite_test_run — never a 500."""
    from vivarium_workbench.lib import run_registry
    (tmp_path / ".pbg").mkdir()
    (tmp_path / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    monkeypatch.delenv("VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH", raising=False)
    monkeypatch.setattr(run_registry, "count_running", lambda db_file: 0)
    spawned = []
    monkeypatch.setattr(run_registry, "spawn_detached",
                        lambda *a, **k: (spawned.append(1), 1)[1])
    monkeypatch.setattr(remote_pinned, "resolve_run_target", lambda ws: "deployment")
    monkeypatch.setattr(remote_pinned, "resolved_from_session_build", lambda ws: None)
    monkeypatch.setattr(remote_pinned, "resolve_pinned_simulator_id",
                        lambda client, ws: None)

    resp, status = v.composite_test_run(tmp_path, {"id": "pkg.composites.x"})
    assert status == 409
    assert resp["reason"] == "no-build"
    assert resp["actions"]
    assert not spawned
