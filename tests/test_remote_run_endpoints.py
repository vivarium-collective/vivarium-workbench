import importlib
import os


def test_sms_api_base_default_and_override(monkeypatch):
    server = importlib.import_module("vivarium_dashboard.server")
    monkeypatch.delenv("SMS_API_BASE", raising=False)
    assert server._sms_api_base() == "http://localhost:8080"
    monkeypatch.setenv("SMS_API_BASE", "http://localhost:9000")
    assert server._sms_api_base() == "http://localhost:9000"


def test_normalize_repo_url_strips_git_suffix():
    server = importlib.import_module("vivarium_dashboard.server")
    # sms-api simulator/upload 500s on a .git-suffixed URL
    assert server._normalize_repo_url("https://github.com/x/v2ecoli.git") == "https://github.com/x/v2ecoli"
    assert server._normalize_repo_url("  https://github.com/x/v2ecoli  ") == "https://github.com/x/v2ecoli"
    assert server._normalize_repo_url("https://github.com/x/v2ecoli") == "https://github.com/x/v2ecoli"


def test_remote_run_start_requires_login(monkeypatch):
    server = importlib.import_module("vivarium_dashboard.server")
    from vivarium_dashboard.lib import github_auth

    monkeypatch.setattr(github_auth, "current_session", lambda: None)

    captured = {}

    class _H:
        _json = lambda self, data, code: captured.update(data=data, code=code)
        _post_remote_run_start = server.Handler._post_remote_run_start

    _H()._post_remote_run_start({"study": "s"})
    assert captured["code"] == 401
