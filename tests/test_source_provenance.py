"""Source-provenance (repo@commit) for the Runs table.

Covers the always-filled guarantee end to end:
  * git_repo_identity / URL parsing / commit-URL building,
  * build_run_manifest recording repo + remote in code_version,
  * save_metadata auto-building a manifest when a caller passes ``workspace``
    but no manifest (the remote-landing / ad-hoc path),
  * backfill_source_provenance stamping legacy manifest-less rows,
  * list_simulations + build_simulations_data surfacing ``source_ref`` on
    every row (manifest-accurate where present, inferred workspace HEAD else).
"""
import json
import subprocess

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import simulations_index as si


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _make_repo(path, remote="https://github.com/vivarium-collective/demo.git"):
    path.mkdir(parents=True, exist_ok=True)
    _git("init", cwd=path)
    _git("config", "user.email", "t@t", cwd=path)
    _git("config", "user.name", "t", cwd=path)
    if remote:
        _git("remote", "add", "origin", remote, cwd=path)
    (path / "README.md").write_text("x")
    _git("add", "-A", cwd=path)
    _git("commit", "-m", "init", cwd=path)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


# --- pure helpers ----------------------------------------------------------

@pytest.mark.parametrize("url,name", [
    ("git@github.com:vivarium-collective/v2ecoli.git", "v2ecoli"),
    ("https://github.com/vivarium-collective/v2ecoli", "v2ecoli"),
    ("https://github.com/vivarium-collective/v2ecoli.git", "v2ecoli"),
    ("ssh://git@github.com/org/repo.git", "repo"),
    ("", None),
    (None, None),
])
def test_remote_url_to_repo_name(url, name):
    assert cr._remote_url_to_repo_name(url) == name


def test_commit_url_github_only():
    gh = "git@github.com:org/repo.git"
    assert si._commit_url(gh, "deadbeef") == "https://github.com/org/repo/commit/deadbeef"
    assert si._commit_url("https://github.com/org/repo", "abc") == \
        "https://github.com/org/repo/commit/abc"
    # non-GitHub or missing pieces -> no link (UI shows plain sha)
    assert si._commit_url("https://gitlab.com/org/repo", "abc") is None
    assert si._commit_url(None, "abc") is None
    assert si._commit_url("git@github.com:org/repo.git", None) is None


def test_git_repo_identity(tmp_path):
    sha = _make_repo(tmp_path / "ws")
    ident = cr.git_repo_identity(tmp_path / "ws")
    assert ident["git_sha"] == sha
    assert ident["repo"] == "demo"           # from the remote basename
    assert ident["remote_url"].endswith("demo.git")


def test_git_repo_identity_no_remote_falls_back_to_dirname(tmp_path):
    _make_repo(tmp_path / "myws", remote=None)
    ident = cr.git_repo_identity(tmp_path / "myws")
    assert ident["repo"] == "myws"            # dir name when no remote
    assert ident["remote_url"] is None
    assert ident["git_sha"]


def test_git_repo_identity_non_repo(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    ident = cr.git_repo_identity(d)
    assert ident["git_sha"] is None
    assert ident["repo"] == "plain"           # still names the dir
    assert ident["dirty"] is None             # unknown — not a git repo


def test_git_repo_identity_reports_dirty(tmp_path):
    ws = tmp_path / "ws"
    _make_repo(ws)
    # Clean right after commit.
    assert cr.git_repo_identity(ws)["dirty"] is False
    # An uncommitted (untracked) file — the #1618 bug 10 case: a study dir that
    # was never committed means HEAD doesn't capture what would run.
    (ws / "studies").mkdir()
    (ws / "studies" / "s1.yaml").write_text("name: s1\n")
    assert cr.git_repo_identity(ws)["dirty"] is True


def test_manifest_code_version_records_dirty(tmp_path):
    ws = tmp_path / "ws"
    _make_repo(ws)
    (ws / "uncommitted.txt").write_text("x")  # dirty the tree
    m = cr.build_run_manifest(
        spec_id="pkg.demo", params={}, n_steps=1, emitter=None,
        emit_paths=[], runtime={}, origin="local", ws_root=ws,
    )
    assert m["code_version"]["git_dirty"] is True


def test_source_from_manifest_surfaces_dirty():
    src = si._source_from_manifest(json.dumps({"code_version": {
        "git_sha": "a" * 40, "repo": "demo", "git_dirty": True}}))
    assert src["dirty"] is True
    # Legacy manifest without the key → unknown (None), never a false "clean".
    legacy = si._source_from_manifest(json.dumps({"code_version": {
        "git_sha": "a" * 40, "repo": "demo"}}))
    assert legacy["dirty"] is None


# --- manifest records repo + commit ----------------------------------------

def test_manifest_code_version_has_repo(tmp_path):
    sha = _make_repo(tmp_path / "ws")
    m = cr.build_run_manifest(
        spec_id="pkg.demo", params={}, n_steps=1, emitter=None,
        emit_paths=[], runtime={}, origin="local", ws_root=tmp_path / "ws",
    )
    cv = m["code_version"]
    assert cv["git_sha"] == sha
    assert cv["repo"] == "demo"
    assert cv["remote_url"].endswith("demo.git")


# --- save_metadata auto-builds a manifest when given a workspace -----------

def test_save_metadata_autobuilds_manifest(tmp_path):
    _make_repo(tmp_path / "ws")
    conn = cr.connect(tmp_path / "ws" / "runs.db")
    cr.save_metadata(conn, spec_id="pkg.demo", run_id="pkg.demo__1__aa",
                     params={"seed": 1}, label="x", started_at=1.0, n_steps=3,
                     workspace=tmp_path / "ws")
    row = cr.query_run_meta(conn, run_id="pkg.demo__1__aa")
    manifest = json.loads(row["manifest_json"])
    assert manifest["code_version"]["repo"] == "demo"
    assert manifest["code_version"]["git_sha"]


def test_save_metadata_no_workspace_no_manifest(tmp_path):
    conn = cr.connect(tmp_path / "runs.db")
    cr.save_metadata(conn, spec_id="pkg.demo", run_id="pkg.demo__1__bb",
                     params={}, label="x", started_at=1.0, n_steps=3)
    row = cr.query_run_meta(conn, run_id="pkg.demo__1__bb")
    assert row["manifest_json"] is None  # no workspace -> nothing to stamp


# --- source_from_manifest --------------------------------------------------

def test_source_from_manifest_shapes():
    assert si._source_from_manifest(None) is None
    assert si._source_from_manifest("not json") is None
    assert si._source_from_manifest(json.dumps({"code_version": {}})) is None
    src = si._source_from_manifest(json.dumps({"code_version": {
        "git_sha": "abcdef1234567890", "repo": "demo",
        "remote_url": "https://github.com/org/demo.git", "package": "pkg"}}))
    assert src["repo"] == "demo"
    assert src["commit_short"] == "abcdef1"
    assert src["commit_url"] == "https://github.com/org/demo/commit/abcdef1234567890"
    assert src["inferred"] is False
    # a backfilled manifest is flagged inferred
    bf = si._source_from_manifest(json.dumps({"code_version": {
        "git_sha": "a" * 12, "repo": "demo", "backfilled": True}}))
    assert bf["inferred"] is True


# --- end-to-end over a workspace of runs -----------------------------------

def _workspace_with_run(tmp_path):
    """A minimal workspace: git repo + one manifest-less runs_meta row."""
    ws = tmp_path / "ws"
    _make_repo(ws)
    (ws / "workspace.yaml").write_text("name: demo\n")
    study_dir = ws / "studies" / "s1"
    study_dir.mkdir(parents=True)
    conn = cr.connect(study_dir / "runs.db")
    # Simulate a legacy recording path: params, but NO manifest.
    conn.execute(
        "INSERT INTO runs_meta (run_id, spec_id, label, params_json, "
        "started_at, status, n_steps, progress_step) "
        "VALUES (?,?,?,?,?,?,?,0)",
        ("legacy__1__aa", "pkg.demo", "legacy", json.dumps({"seed": 1}),
         1.0, "completed", 5),
    )
    conn.commit()
    conn.close()
    return ws


def test_backfill_stamps_legacy_rows(tmp_path):
    ws = _workspace_with_run(tmp_path)
    n = si.backfill_source_provenance(ws)
    assert n == 1
    conn = cr.connect(ws / "studies" / "s1" / "runs.db")
    row = cr.query_run_meta(conn, run_id="legacy__1__aa")
    manifest = json.loads(row["manifest_json"])
    assert manifest["code_version"]["repo"] == "demo"
    assert manifest["code_version"]["backfilled"] is True
    # idempotent: a second pass stamps nothing new
    assert si.backfill_source_provenance(ws) == 0


def test_list_simulations_always_fills_source_ref(tmp_path):
    ws = _workspace_with_run(tmp_path)
    rows = si.list_simulations(ws)
    assert rows, "expected the legacy run to be discovered"
    for r in rows:
        assert r.get("source_ref"), f"row {r.get('run_id')} missing source_ref"
        assert r["source_ref"]["repo"] == "demo"


def test_build_simulations_data_carries_source_ref(tmp_path):
    ws = _workspace_with_run(tmp_path)
    si.backfill_source_provenance(ws)
    data = si.build_simulations_data(ws)
    sims = data["simulations"]
    assert sims
    assert all(s.get("source_ref") for s in sims)
    # the backfilled run's manifest source is non-... it is inferred (backfilled)
    got = [s for s in sims if s["run_id"] == "legacy__1__aa"]
    assert got and got[0]["source_ref"]["repo"] == "demo"
