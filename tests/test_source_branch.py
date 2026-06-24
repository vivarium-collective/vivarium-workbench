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
    _git(ws, "remote", "add", "origin", str(bare))
    _git(ws, "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base")
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
