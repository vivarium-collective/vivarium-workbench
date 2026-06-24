import subprocess
from pathlib import Path
from vivarium_dashboard import server


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_git_branch_commit_resolves(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "checkout", "-q", "-b", "feat/x")
    _git(tmp_path, "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "m")
    branch, commit = server._git_branch_commit(str(tmp_path))
    assert branch == "feat/x"
    assert len(commit) >= 4 and commit.isalnum()


def test_git_branch_commit_non_git(tmp_path):
    assert server._git_branch_commit(str(tmp_path)) == ("", "")


def test_branch_source_js_present_and_wired():
    from pathlib import Path
    from vivarium_dashboard import server
    js = (Path(server.__file__).parent / "static" / "branch-source.js").read_text()
    for needle in ("/api/workspaces", "/api/source/builds", "/api/source/switch",
                   "/api/source/switch-build", "/api/workspaces/forget",
                   "viv-bs-switch", "Local", "Remote"):
        assert needle in js, needle


def test_branch_source_mounted_in_github_page():
    from pathlib import Path
    from vivarium_dashboard import server
    tpl = (Path(server.__file__).parent / "templates" / "index.html.j2").read_text()
    assert 'id="viv-branch-source"' in tpl
    assert "assets/branch-source.js" in tpl


def test_chip_is_display_only_and_no_source_switch_js():
    from pathlib import Path
    from vivarium_dashboard import server
    tpl = (Path(server.__file__).parent / "templates" / "index.html.j2").read_text()
    # The chip block keeps the source label but is no longer a button/dropdown trigger.
    assert "assets/source-switch.js" not in tpl
    assert 'id="viv-source-switch-trigger"' not in tpl


def test_branch_push_commits_and_pushes(tmp_path, monkeypatch):
    from vivarium_dashboard import server
    # bare remote + working clone on a named branch with a dirty tree
    bare = tmp_path / "remote.git"; _git(tmp_path, "init", "-q", "--bare", str(bare))
    ws = tmp_path / "ws"; ws.mkdir()
    _git(ws, "init", "-q"); _git(ws, "checkout", "-q", "-b", "feat/x")
    _git(ws, "config", "user.email", "t@t.t"); _git(ws, "config", "user.name", "t")
    _git(ws, "remote", "add", "origin", str(bare))
    _git(ws, "commit", "-q", "--allow-empty", "-m", "base")
    (ws / "f.txt").write_text("hi")
    monkeypatch.setattr(server, "WORKSPACE", ws)
    captured = {}

    class H:
        def _json(self, obj, code): captured.update(obj=obj, code=code)

    server.Handler._post_branch_push(H(), {"message": "add f"})
    assert captured["code"] == 200 and captured["obj"]["pushed"] is True
    log = subprocess.run(["git", "-C", str(ws), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "add f" in log


def test_branch_push_non_git_409(tmp_path, monkeypatch):
    from vivarium_dashboard import server
    monkeypatch.setattr(server, "WORKSPACE", tmp_path)  # not a git repo
    captured = {}

    class H:
        def _json(self, obj, code): captured.update(obj=obj, code=code)

    server.Handler._post_branch_push(H(), {"message": "x"})
    assert captured["code"] == 409


def test_register_simulator_posts_upload(monkeypatch):
    from vivarium_dashboard.lib import sms_api_client as sac
    seen = {}
    monkeypatch.setattr(sac.SmsApiClient, "_post",
                        lambda self, path, params=None, json_body=None: seen.update(path=path, body=json_body) or {"database_id": 99})
    out = sac.SmsApiClient("http://x").register_simulator("https://github.com/o/r", "main", "abc1234")
    assert out["database_id"] == 99
    assert seen["path"] == "/core/v1/simulator/upload"
    assert seen["body"]["git_branch"] == "main" and seen["body"]["git_commit_hash"] == "abc1234"


def test_build_remote_endpoint(monkeypatch):
    from vivarium_dashboard import server
    from vivarium_dashboard.lib import sms_api_client as sac
    monkeypatch.setattr(sac.SmsApiClient, "latest_simulator",
                        lambda self, repo, branch: {"git_commit_hash": "deadbee"})
    monkeypatch.setattr(sac.SmsApiClient, "register_simulator",
                        lambda self, repo, branch, commit: {"database_id": 64, "git_commit_hash": commit})
    captured = {}

    class H:
        def _json(self, obj, code): captured.update(obj=obj, code=code)

    server.Handler._post_source_build_remote(H(), {"repo": "https://github.com/o/v2ecoli", "branch": "main"})
    assert captured["code"] == 200
    assert captured["obj"]["simulator_id"] == 64 and captured["obj"]["commit"] == "deadbee"


def test_build_remote_missing_args_400(monkeypatch):
    from vivarium_dashboard import server
    captured = {}

    class H:
        def _json(self, obj, code): captured.update(obj=obj, code=code)

    server.Handler._post_source_build_remote(H(), {"repo": ""})
    assert captured["code"] == 400
