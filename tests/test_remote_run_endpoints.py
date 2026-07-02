def test_sms_api_base_default_and_override(monkeypatch):
    from vivarium_dashboard.lib import workspace_deps_views as wdv
    monkeypatch.delenv("SMS_API_BASE", raising=False)
    assert wdv._sms_api_base() == "http://localhost:8080"
    monkeypatch.setenv("SMS_API_BASE", "http://localhost:9000")
    assert wdv._sms_api_base() == "http://localhost:9000"


def test_normalize_repo_url_strips_git_suffix():
    from vivarium_dashboard.lib import source_build_views as sbv
    # sms-api simulator/upload 500s on a .git-suffixed URL
    assert sbv._normalize_repo_url("https://github.com/x/v2ecoli.git") == "https://github.com/x/v2ecoli"
    assert sbv._normalize_repo_url("  https://github.com/x/v2ecoli  ") == "https://github.com/x/v2ecoli"
    assert sbv._normalize_repo_url("https://github.com/x/v2ecoli") == "https://github.com/x/v2ecoli"


def test_remote_run_start_requires_login(monkeypatch, tmp_path):
    from vivarium_dashboard.lib import remote_run_views
    from vivarium_dashboard.lib import github_auth

    monkeypatch.setattr(github_auth, "current_session", lambda: None)

    body, code = remote_run_views.remote_run_start(tmp_path, {"study": "s"})
    assert code == 401
