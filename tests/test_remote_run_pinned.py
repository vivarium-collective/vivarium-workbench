"""Tests for pinned-build remote runs (lib.remote_pinned + the pinned paths in
lib.remote_run_views).

Pinned mode lets the demo submit sims against the latest **built** simulator for
a configured repo@branch with NO git push, NO login, NO local-repo access. These
tests never touch a real network/git/auth — every external is monkeypatched.
"""

from __future__ import annotations

import pytest

from vivarium_workbench.lib import remote_pinned as rp
from vivarium_workbench.lib import remote_run_views as rrv


# Two built main entries (newest = id 69) plus a non-matching branch/repo, in the
# shape sms-api's /core/v1/simulator/versions returns.
_VERSIONS = {
    "versions": [
        {
            "git_repo_url": "https://github.com/vivarium-collective/v2ecoli",
            "git_branch": "main",
            "git_commit_hash": "648fd4c",
            "database_id": 64,
            "created_at": "2026-06-24T00:20:17",
        },
        {
            "git_repo_url": "https://github.com/vivarium-collective/v2ecoli",
            "git_branch": "main",
            "git_commit_hash": "70b5ec3",
            "database_id": 69,
            "created_at": "2026-07-06T20:09:52",
        },
        {
            "git_repo_url": "https://github.com/vivarium-collective/v2ecoli",
            "git_branch": "feat/x",
            "git_commit_hash": "deadbee",
            "database_id": 70,
            "created_at": "2026-07-07T00:00:00",
        },
        {
            "git_repo_url": "https://github.com/other/repo",
            "git_branch": "main",
            "git_commit_hash": "aaaaaaa",
            "database_id": 99,
            "created_at": "2026-07-08T00:00:00",
        },
    ]
}


class _FakeClient:
    def __init__(self, versions=None) -> None:
        self._versions = versions if versions is not None else _VERSIONS

    def list_simulators(self):
        return self._versions


# --------------------------------------------------------------------------- #
# pinned_config
# --------------------------------------------------------------------------- #


def test_pinned_config_off_by_default(monkeypatch):
    monkeypatch.delenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", raising=False)
    monkeypatch.delenv("VIVARIUM_DASHBOARD_REMOTE_PINNED", raising=False)
    assert rp.pinned_config() is None
    assert rp.is_pinned_enabled() is False


def test_pinned_config_requires_repo_url(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "1")
    monkeypatch.delenv("VIVARIUM_WORKBENCH_REMOTE_REPO_URL", raising=False)
    monkeypatch.delenv("VIVARIUM_DASHBOARD_REMOTE_REPO_URL", raising=False)
    assert rp.pinned_config() is None  # enabled but no repo → still off


def test_pinned_config_on_defaults_branch_main(monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_PINNED", "true")
    monkeypatch.setenv(
        "VIVARIUM_WORKBENCH_REMOTE_REPO_URL",
        "https://github.com/vivarium-collective/v2ecoli",
    )
    monkeypatch.delenv("VIVARIUM_WORKBENCH_REMOTE_BRANCH", raising=False)
    monkeypatch.delenv("VIVARIUM_DASHBOARD_REMOTE_BRANCH", raising=False)
    cfg = rp.pinned_config()
    assert cfg is not None
    assert cfg.branch == "main"
    assert rp.is_pinned_enabled() is True


# --------------------------------------------------------------------------- #
# _normalize_repo / resolve_pinned_build
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "a,b",
    [
        (
            "https://github.com/vivarium-collective/v2ecoli.git",
            "https://github.com/vivarium-collective/v2ecoli",
        ),
        ("https://github.com/Org/Repo/", "https://github.com/org/repo"),
    ],
)
def test_normalize_repo_strips_git_slash_case(a, b):
    assert rp._normalize_repo(a) == rp._normalize_repo(b)


def test_resolve_pinned_build_picks_latest_built_main_and_normalizes_git():
    # Query with the ``.git`` form — must still match the bare-URL builds.
    out = rp.resolve_pinned_build(
        _FakeClient(), "https://github.com/vivarium-collective/v2ecoli.git", "main"
    )
    assert out["simulator_id"] == 69  # newest created_at, NOT the feat/x id 70
    assert out["commit"] == "70b5ec3"
    assert out["branch"] == "main"


def test_resolve_pinned_build_raises_when_no_match():
    with pytest.raises(rp.NoPinnedBuildError):
        rp.resolve_pinned_build(
            _FakeClient(), "https://github.com/nobody/nothing", "main"
        )


# --------------------------------------------------------------------------- #
# remote_run_pinned_build_start
# --------------------------------------------------------------------------- #


def test_pinned_build_start_409_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(rrv.remote_pinned, "pinned_config", lambda: None)
    body, status = rrv.remote_run_pinned_build_start(tmp_path, {"study": "s"})
    assert status == 409
    assert "not enabled" in body["error"]


def test_pinned_build_start_returns_built_phase(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rrv.remote_pinned,
        "pinned_config",
        lambda: rp.PinnedConfig(
            repo_url="https://github.com/vivarium-collective/v2ecoli", branch="main"
        ),
    )
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: _FakeClient())
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    body, status = rrv.remote_run_pinned_build_start(tmp_path, {"study": "s"})
    assert status == 202
    assert body["phase"] == "built"
    assert body["simulator_id"] == 69
    assert body["pinned"] is True


# --------------------------------------------------------------------------- #
# submit/land gate relaxed under pinned mode
# --------------------------------------------------------------------------- #


def test_submit_gate_allows_pinned_without_session(monkeypatch, tmp_path):
    # No GitHub session, but pinned mode on → gate passes (fails later on the
    # missing study, NOT on 401).
    monkeypatch.setattr(rrv.github_auth, "current_session", lambda: None)
    monkeypatch.setattr(rrv.remote_pinned, "is_pinned_enabled", lambda: True)
    _body, status = rrv.remote_run_submit(tmp_path, {"study": "", "simulator_id": 69})
    assert status != 401
    assert status == 400  # study is required


def test_submit_gate_401_when_no_session_and_not_pinned(monkeypatch, tmp_path):
    monkeypatch.setattr(rrv.github_auth, "current_session", lambda: None)
    monkeypatch.setattr(rrv.remote_pinned, "is_pinned_enabled", lambda: False)
    _body, status = rrv.remote_run_submit(tmp_path, {"study": "s", "simulator_id": 69})
    assert status == 401


# --------------------------------------------------------------------------- #
# remote_run_config
# --------------------------------------------------------------------------- #


def test_remote_run_config_off(monkeypatch, tmp_path):
    monkeypatch.setattr(rrv.remote_pinned, "pinned_config", lambda: None)
    monkeypatch.setattr(rrv.remote_pinned, "remote_deployment_name", lambda: "smscdk")
    body, status = rrv.remote_run_config(tmp_path)
    assert status == 200
    assert body == {"pinned": False, "deployment": "smscdk"}


def test_remote_run_config_on_resolves_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rrv.remote_pinned,
        "pinned_config",
        lambda: rp.PinnedConfig(
            repo_url="https://github.com/vivarium-collective/v2ecoli", branch="main"
        ),
    )
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: _FakeClient())
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    body, status = rrv.remote_run_config(tmp_path)
    assert status == 200
    assert body["pinned"] is True
    assert body["commit"] == "70b5ec3"
    assert body["simulator_id"] == 69


def test_remote_run_config_on_degrades_on_missing_build(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rrv.remote_pinned,
        "pinned_config",
        lambda: rp.PinnedConfig(
            repo_url="https://github.com/nobody/nothing", branch="main"
        ),
    )
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: _FakeClient())
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    body, status = rrv.remote_run_config(tmp_path)
    assert status == 200
    assert body["pinned"] is True
    assert "build_error" in body


# --------------------------------------------------------------------------- #
# session's own switched build takes priority over the static pin
# --------------------------------------------------------------------------- #


def _stamp_build_meta(ws_root, **overrides):
    import json

    meta = {
        "simulator_id": 54,
        "repo": "sms-ecoli",
        "branch": "main",
        "commit": "d02c6a7",
        "repo_url": "https://github.com/CovertLabEcoli/sms-ecoli",
    }
    meta.update(overrides)
    (ws_root / ".viv-build.json").write_text(json.dumps(meta))


def test_resolved_from_session_build_absent_when_not_a_remote_build(tmp_path):
    assert rp.resolved_from_session_build(tmp_path) is None


def test_resolved_from_session_build_reads_stamp(tmp_path):
    _stamp_build_meta(tmp_path)
    out = rp.resolved_from_session_build(tmp_path)
    assert out == {
        "simulator_id": 54,
        "commit": "d02c6a7",
        "branch": "main",
        "repo_url": "https://github.com/CovertLabEcoli/sms-ecoli",
    }


def test_pinned_build_start_prefers_session_build_over_static_pin(
    monkeypatch, tmp_path
):
    # Static pin says v2ecoli; session has switched to sms-ecoli. The session
    # binding must win -- this is exactly the bug: pinned dispatch silently
    # ignoring which workspace the user actually selected.
    monkeypatch.setattr(
        rrv.remote_pinned,
        "pinned_config",
        lambda: rp.PinnedConfig(
            repo_url="https://github.com/vivarium-collective/v2ecoli", branch="main"
        ),
    )
    _stamp_build_meta(tmp_path)
    body, status = rrv.remote_run_pinned_build_start(tmp_path, {"study": "s"})
    assert status == 202
    assert body["simulator_id"] == 54
    assert body["commit"] == "d02c6a7"


def test_remote_run_config_prefers_session_build_over_static_pin(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rrv.remote_pinned,
        "pinned_config",
        lambda: rp.PinnedConfig(
            repo_url="https://github.com/vivarium-collective/v2ecoli", branch="main"
        ),
    )
    monkeypatch.setattr(rrv.remote_pinned, "remote_deployment_name", lambda: "smscdk")
    _stamp_build_meta(tmp_path)
    body, status = rrv.remote_run_config(tmp_path)
    assert status == 200
    assert body["repo_url"] == "https://github.com/CovertLabEcoli/sms-ecoli"
    assert body["simulator_id"] == 54


def test_pinned_build_start_falls_back_to_static_pin_when_unswitched(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        rrv.remote_pinned,
        "pinned_config",
        lambda: rp.PinnedConfig(
            repo_url="https://github.com/vivarium-collective/v2ecoli", branch="main"
        ),
    )
    monkeypatch.setattr(rrv, "SmsApiClient", lambda base=None: _FakeClient())
    monkeypatch.setattr(rrv, "_sms_api_base", lambda: "http://sms.local")
    body, status = rrv.remote_run_pinned_build_start(tmp_path, {"study": "s"})
    assert status == 202
    assert body["simulator_id"] == 69


def test_remote_deployment_name_default(monkeypatch):
    import vivarium_workbench.lib.remote_pinned as rp_mod

    monkeypatch.setattr(rp_mod, "get_env", lambda k, d="": d)
    assert rp_mod.remote_deployment_name() == "smsvpctest"


def test_remote_deployment_name_from_env(monkeypatch):
    import vivarium_workbench.lib.remote_pinned as rp_mod

    monkeypatch.setattr(
        rp_mod, "get_env", lambda k, d="": "smscdk" if k == "REMOTE_DEPLOYMENT" else d
    )
    assert rp_mod.remote_deployment_name() == "smscdk"


# --------------------------------------------------------------------------- #
# resolve_run_target (item 18) — the ONE local-vs-deployment resolution every
# run entrypoint (composite_test_run, run_study_baseline, run_study_variant)
# now calls, so none of them can drift apart again.
# --------------------------------------------------------------------------- #


def test_resolve_run_target_local_when_neither_condition_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "is_pinned_enabled", lambda: False)
    assert rp.resolve_run_target(tmp_path) == "local"


def test_resolve_run_target_deployment_when_pinned_with_no_session_build(
    tmp_path, monkeypatch
):
    """The exact item-18 bug condition: pinned deployment, but this
    workspace has no .viv-build.json (composite_test_run's own inline check
    already got this right; run_study_baseline/run_study_variant didn't)."""
    assert not (tmp_path / ".viv-build.json").exists()
    monkeypatch.setattr(rp, "is_pinned_enabled", lambda: True)
    assert rp.resolve_run_target(tmp_path) == "deployment"


def test_resolve_run_target_deployment_when_session_build_with_pin_off(
    tmp_path, monkeypatch
):
    """A materialized session build (.viv-build.json) alone is sufficient —
    unchanged from run_core.run_target_for's pre-existing behavior."""
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    monkeypatch.setattr(rp, "is_pinned_enabled", lambda: False)
    assert rp.resolve_run_target(tmp_path) == "deployment"


def test_resolve_run_target_deployment_when_both_conditions_hold(tmp_path, monkeypatch):
    """Session build AND deployment pin both present — still resolves
    cleanly to "deployment" (no conflict, no crash)."""
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')
    monkeypatch.setattr(rp, "is_pinned_enabled", lambda: True)
    assert rp.resolve_run_target(tmp_path) == "deployment"


def test_resolve_run_target_short_circuits_before_checking_pin(tmp_path, monkeypatch):
    """A materialized session build resolves "deployment" WITHOUT even
    calling is_pinned_enabled() — the env-var check is pure overhead once
    the .viv-build.json check alone already answers "deployment"."""
    (tmp_path / ".viv-build.json").write_text('{"simulator_id": 66}')

    def _boom():
        raise AssertionError("is_pinned_enabled must not be called here")

    monkeypatch.setattr(rp, "is_pinned_enabled", _boom)
    assert rp.resolve_run_target(tmp_path) == "deployment"
