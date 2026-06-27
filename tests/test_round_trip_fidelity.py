# tests/test_round_trip_fidelity.py
import subprocess
from pathlib import Path

import pytest

from vivarium_dashboard.lib.provenance_manifest import build_manifest, lockfile_hash
from vivarium_dashboard.lib import sync_workspace as sw


def _make_origin(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "workspace.yaml").write_text("name: demo\npackage: demo\n")
    (path / "uv.lock").write_text("lock-contents-v1\n")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(path)], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin",
                    "https://github.com/vivarium-collective/demo.git"], check=True, env=env)
    for a in (["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, env=env, capture_output=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def _stub_externals(monkeypatch):
    monkeypatch.setattr(sw.sync_materialize, "run_uv_sync", lambda ws, **k: ({"ok": True}, 200))
    monkeypatch.setattr(sw, "_catalog_add", lambda p, name=None, package=None: {"path": str(p)})


def test_round_trip_preserves_commit_and_lockfile(tmp_path, _stub_externals):
    """Same commit + same lockfile after sync — the fidelity contract."""
    origin = tmp_path / "origin"
    sha = _make_origin(origin)
    # Emit the manifest from the source state, but point repo at the local clone source.
    manifest = build_manifest(origin)
    manifest["repo"] = f"file://{origin}"   # build_manifest reads origin URL; clone needs a reachable source
    dest = tmp_path / "local"

    body, status = sw.sync_from_manifest(manifest, dest)
    assert status == 200, body

    synced_head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                                 capture_output=True, text=True).stdout.strip()
    assert synced_head == sha                                 # exact commit
    assert lockfile_hash(dest) == manifest["lockfile"]        # exact lockfile


def test_lockfile_drift_is_rejected(tmp_path, _stub_externals):
    """If the source lockfile changed after the manifest was emitted, sync refuses."""
    origin = tmp_path / "origin"
    sha = _make_origin(origin)
    manifest = build_manifest(origin)
    manifest["repo"] = f"file://{origin}"
    # Tamper: change the manifest's pinned hash to simulate drift.
    manifest["lockfile"] = "uv.lock@0000deadbeef"
    body, status = sw.sync_from_manifest(manifest, tmp_path / "local")
    assert status == 409


def test_manifest_carries_build_inputs(tmp_path):
    """The manifest exposes repo+branch+commit — the inputs build-via-sms-api needs.
    Guards the 'one manifest, both directions' invariant."""
    origin = tmp_path / "origin"
    _make_origin(origin)
    m = build_manifest(origin)
    # build-remote consumes repo+branch; switch-build pins commit. All present:
    assert m["repo"] and m["branch"] and m["commit"]
    assert set(["repo", "branch", "commit"]).issubset(m.keys())
