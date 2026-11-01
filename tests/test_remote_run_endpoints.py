def test_sms_api_base_default_and_override(monkeypatch):
    from vivarium_workbench.lib import workspace_deps_views as wdv
    # "Unconfigured" means BOTH names unset — VIVA_API_BASE is read first, and
    # conftest's _isolate_viva_api_base now sets both for the run, so deleting
    # only the legacy alias would leave the canonical name pointing elsewhere.
    monkeypatch.delenv("VIVA_API_BASE", raising=False)
    monkeypatch.delenv("SMS_API_BASE", raising=False)
    assert wdv._sms_api_base() == "http://localhost:8080"
    monkeypatch.setenv("SMS_API_BASE", "http://localhost:9000")
    assert wdv._sms_api_base() == "http://localhost:9000"


def test_normalize_repo_url_strips_git_suffix():
    from vivarium_workbench.lib import source_build_views as sbv
    # sms-api simulator/upload 500s on a .git-suffixed URL
    assert sbv._normalize_repo_url("https://github.com/x/v2ecoli.git") == "https://github.com/x/v2ecoli"
    assert sbv._normalize_repo_url("  https://github.com/x/v2ecoli  ") == "https://github.com/x/v2ecoli"
    assert sbv._normalize_repo_url("https://github.com/x/v2ecoli") == "https://github.com/x/v2ecoli"


def test_remote_run_start_requires_login(monkeypatch, tmp_path):
    from vivarium_workbench.lib import remote_run_views
    from vivarium_workbench.lib import github_auth

    monkeypatch.setattr(github_auth, "current_session", lambda: None)

    body, code = remote_run_views.remote_run_start(tmp_path, {"study": "s"})
    assert code == 401


def test_test_suite_cannot_reach_a_live_deployment(
    inproc_client, tmp_path, _isolate_viva_api_base
):
    """The isolation itself, asserted — otherwise it can regress silently and
    the only symptom is tests quietly talking to real infrastructure again.

    Checks the property where it actually matters: inside the SPAWNED server
    subprocess, which is the process that resolves builds and dispatches runs.
    Its base must be conftest's closed port, never the localhost:8080 default
    that a developer's SSM tunnel to sms-api-stanford-test occupies.

    Takes the expected value by REQUESTING the autouse fixture (which yields
    it) rather than importing the module constant. `from tests.conftest import
    ...` works locally but not in CI, where a dependency ships its own
    top-level `tests` package and the import resolves to
    site-packages/tests/__init__.py -> ModuleNotFoundError: No module named
    'nose'. The fixture is the same value with no import-path ambiguity."""
    import json

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: isolation-ws\n")
    (ws / ".pbg").mkdir()
    client = inproc_client(ws)
    res = client.get("/api/source/remote-health")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["base_url"] == _isolate_viva_api_base, json.dumps(body)
    assert body["reachable"] is False
