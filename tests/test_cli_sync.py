import json
import subprocess
from pathlib import Path

from vivarium_dashboard import cli


def _make_origin(path: Path) -> tuple[str, str]:
    path.mkdir(parents=True, exist_ok=True)
    (path / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (path / "uv.lock").write_text("lock-contents-v1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    for a in (["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, env=env, capture_output=True)
    sha = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    return f"file://{path}", sha


def test_cmd_sync_from_manifest_file(tmp_path, monkeypatch):
    url, sha = _make_origin(tmp_path / "origin")
    from vivarium_dashboard.lib.provenance_manifest import lockfile_hash
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": lockfile_hash(tmp_path / "origin"), "results": {"runs": []}}
    mfile = tmp_path / "manifest.json"
    mfile.write_text(json.dumps(manifest))
    # avoid a real uv sync + real catalog write
    import vivarium_dashboard.lib.sync_workspace as sw
    monkeypatch.setattr(sw.sync_materialize, "run_uv_sync", lambda ws, **k: ({"ok": True}, 200))
    monkeypatch.setattr(sw, "_catalog_add", lambda p, name=None, package=None: {"path": str(p)})

    dest = tmp_path / "local"
    rc = cli.main(["sync", str(mfile), "--dest", str(dest)])
    assert rc == 0
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head == sha


def test_cmd_sync_reports_failure_rc(tmp_path, monkeypatch):
    url, sha = _make_origin(tmp_path / "origin")
    manifest = {"repo": url, "commit": sha, "branch": "main", "workspace": "demo",
                "lockfile": "uv.lock@deadbeefcafe", "results": {"runs": []}}
    mfile = tmp_path / "m.json"
    mfile.write_text(json.dumps(manifest))
    rc = cli.main(["sync", str(mfile), "--dest", str(tmp_path / "local")])
    assert rc == 1  # lockfile mismatch -> non-zero exit
