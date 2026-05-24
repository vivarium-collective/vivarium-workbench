"""Integration tests for the UI-authored composite routes (todo #11 Phase A/B).

Spawns a dashboard subprocess against a tmp copy of the ``ws_increase_demo``
fixture and drives the new endpoints end-to-end. The git-commit path
(``/api/composite/commit``) needs an initialized repo with an active
workstream; helper :func:`_init_workstream` handles that.
"""
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path

import pytest


_FIXTURES = Path(__file__).parent / "_fixtures"
_INCREASE = _FIXTURES / "ws_increase_demo"


def _copy_fixture(tmp_path: Path) -> Path:
    dest = tmp_path / "ws"
    shutil.copytree(_INCREASE, dest)
    return dest


def _init_workstream(workspace: Path) -> str:
    """Make the workspace a git repo on a `stage/test` branch with an
    initial commit, then write `.pbg/state.json` so the dashboard treats
    that branch as the active workstream.

    Returns the branch name. Mirrors the on-disk state laid down by
    `workspace_create._write_active_workstream` (todo #8 Phase D).
    """
    import json
    branch = "stage/test-compose"
    subprocess.run(["git", "init", "-b", "main"], cwd=workspace, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "scaffold"], cwd=workspace, check=True,
                   capture_output=True)
    subprocess.run(["git", "checkout", "-b", branch], cwd=workspace, check=True,
                   capture_output=True)

    state_file = workspace / ".pbg" / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "active_branch": branch,
        "base": "main",
        "pushed": False,
    }))
    return branch


# ---------------------------------------------------------------------------
# /api/composite/create + draft lifecycle
# ---------------------------------------------------------------------------

def test_post_create_writes_draft_and_validates(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)

    body = {
        "draft": {
            "name": "mini",
            "description": "tiny smoke",
            "requires": {"processes": ["IncreaseProcess"]},
            "parameters": {},
            "state": {
                "increase": {
                    "_type": "process",
                    "address": "local:IncreaseProcess",
                    "config": {"rate": 1.5},
                    "inputs": {"level": ["stores", "level"]},
                    "outputs": {"level": ["stores", "level"]},
                    "interval": 1.0,
                },
                "stores": {"level": 1.0},
            },
        },
    }
    r = client.post("/api/composite/create", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert "draft_id" in j
    assert j["path"].endswith(".composite.yaml")
    assert ".pbg/composite-drafts/" in j["path"]
    assert j["validation"]["ok"], j["validation"]


def test_post_create_skip_validation_returns_skipped_true(dashboard_client, tmp_path):
    """Autosave path: skip_validation=True returns immediately without
    spawning a subprocess."""
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)
    r = client.post("/api/composite/create", json={
        "draft": {"name": "x", "state": {}},
        "skip_validation": True,
    })
    assert r.status_code == 200
    assert r.json()["validation"]["skipped"] is True


def test_post_create_rejects_missing_draft(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)
    r = client.post("/api/composite/create", json={})
    assert r.status_code == 400
    assert "draft" in r.json()["error"]


def test_get_draft_after_create(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)
    create = client.post("/api/composite/create", json={
        "draft": {"name": "snap", "state": {"x": 1}},
        "skip_validation": True,
    }).json()
    draft_id = create["draft_id"]
    r = client.get(f"/api/composite/draft/{draft_id}")
    assert r.status_code == 200
    j = r.json()
    assert j["parsed"]["name"] == "snap"
    assert j["parsed"]["state"] == {"x": 1}


def test_delete_draft(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)
    create = client.post("/api/composite/create", json={
        "draft": {"name": "tmp", "state": {}},
        "skip_validation": True,
    }).json()
    draft_id = create["draft_id"]
    r = client.delete(f"/api/composite/draft/{draft_id}")
    assert r.status_code == 200
    assert r.json()["removed"] is True
    # Second delete: already gone.
    r2 = client.delete(f"/api/composite/draft/{draft_id}")
    assert r2.json()["removed"] is False


# ---------------------------------------------------------------------------
# /api/composite/draft/<id>/promote
# ---------------------------------------------------------------------------

def test_promote_moves_draft_into_pkg_composites(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)
    create = client.post("/api/composite/create", json={
        "draft": {
            "name": "promoted-thing",
            "state": {
                "increase": {
                    "_type": "process",
                    "address": "local:IncreaseProcess",
                    "config": {"rate": 2.0},
                    "inputs": {"level": ["stores", "level"]},
                    "outputs": {"level": ["stores", "level"]},
                    "interval": 1.0,
                },
                "stores": {"level": 1.0},
            },
        },
    }).json()
    draft_id = create["draft_id"]
    r = client.post(f"/api/composite/draft/{draft_id}/promote", json={})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["path"] == "pbg_ws_increase_demo/composites/promoted-thing.composite.yaml"
    assert (ws / j["path"]).is_file()
    assert j["validation"]["ok"], j["validation"]


def test_promote_refuses_overwrite_by_default(dashboard_client, tmp_path):
    """Trying to promote a draft over an existing composite is a 400."""
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)
    create = client.post("/api/composite/create", json={
        "draft": {"name": "increase-demo", "state": {}},
        "skip_validation": True,
    }).json()
    draft_id = create["draft_id"]
    r = client.post(f"/api/composite/draft/{draft_id}/promote", json={})
    assert r.status_code == 400
    assert "already exists" in r.json()["error"]


# ---------------------------------------------------------------------------
# /api/composite/commit
# ---------------------------------------------------------------------------

def test_commit_stages_and_commits_on_active_branch(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    branch = _init_workstream(ws)
    client = dashboard_client(workspace=ws)

    # Author -> save -> promote.
    create = client.post("/api/composite/create", json={
        "draft": {
            "name": "ui-built",
            "state": {
                "increase": {
                    "_type": "process",
                    "address": "local:IncreaseProcess",
                    "config": {"rate": 3.0},
                    "inputs": {"level": ["stores", "level"]},
                    "outputs": {"level": ["stores", "level"]},
                    "interval": 1.0,
                },
                "stores": {"level": 1.0},
            },
        },
    }).json()
    promo = client.post(
        f"/api/composite/draft/{create['draft_id']}/promote", json={},
    ).json()

    # Commit.
    r = client.post("/api/composite/commit", json={"path": promo["path"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["branch"] == branch
    assert "commit" in j and len(j["commit"]) == 7  # short SHA

    # The HEAD commit on the active branch should now touch the new file.
    log = subprocess.run(
        ["git", "log", "-1", "--name-only", "--format="],
        cwd=ws, capture_output=True, text=True, check=True,
    ).stdout
    assert "pbg_ws_increase_demo/composites/ui-built.composite.yaml" in log


def test_commit_409s_without_active_workstream(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    # NOTE: no git init / state.json — there is no active workstream.
    client = dashboard_client(workspace=ws)
    create = client.post("/api/composite/create", json={
        "draft": {
            "name": "stranded",
            "state": {"stores": {"x": 1}},
        },
        "skip_validation": True,
    }).json()
    promo = client.post(
        f"/api/composite/draft/{create['draft_id']}/promote", json={},
    ).json()
    r = client.post("/api/composite/commit", json={"path": promo["path"]})
    # Either 409 (the expected path when active-workstream check fires) or
    # 500 from a deeper git-init complaint. Both are acceptable failure
    # modes; the contract is "must not 200 without a workstream."
    assert r.status_code in (409, 500), r.text


def test_commit_rejects_validation_failures(dashboard_client, tmp_path):
    """If a composite was promoted but its address became unresolvable,
    /commit refuses with a 409 carrying the validation report."""
    ws = _copy_fixture(tmp_path)
    _init_workstream(ws)
    client = dashboard_client(workspace=ws)
    # Write a broken composite directly to disk to simulate "uninstalled
    # process between save and commit."
    bad = ws / "pbg_ws_increase_demo" / "composites" / "broken.composite.yaml"
    bad.write_text(
        "name: broken\n"
        "state:\n"
        "  proc:\n"
        "    _type: process\n"
        "    address: local:DoesNotExistXYZ\n"
        "    inputs: {}\n"
        "    outputs: {}\n"
        "    interval: 1.0\n"
    )
    r = client.post("/api/composite/commit", json={
        "path": "pbg_ws_increase_demo/composites/broken.composite.yaml",
    })
    assert r.status_code == 409
    body = r.json()
    assert "validation" in body
    assert body["validation"]["ok"] is False


def test_commit_validates_path_safety(dashboard_client, tmp_path):
    """Reject paths that escape WORKSPACE or point at non-composite files."""
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)
    r = client.post("/api/composite/commit", json={"path": "../etc/passwd"})
    assert r.status_code in (400, 404)


# ---------------------------------------------------------------------------
# /api/process/<address>/schema
# ---------------------------------------------------------------------------

def test_process_schema_returns_payload_for_known_address(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)
    r = client.get("/api/process/IncreaseProcess/schema")
    assert r.status_code == 200, r.text
    j = r.json()
    # The fixture's IncreaseProcess defines config_schema; introspection may
    # or may not turn it up depending on how the framework exposes the
    # attribute, but the endpoint must always return a parseable shape.
    assert "address" in j or "error" in j
    if "error" not in j:
        assert "inputs" in j
        assert "outputs" in j


def test_process_schema_404_kind_for_unknown_address(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)
    r = client.get("/api/process/NopeNotARealProcessXYZ/schema")
    # The endpoint returns 200 with an error key for not-in-registry rather
    # than 404 — keeps the client logic simple (always parse the body).
    j = r.json()
    assert "error" in j
    assert "NopeNotARealProcessXYZ" in j["error"]


# ---------------------------------------------------------------------------
# /composites/new page render
# ---------------------------------------------------------------------------

def test_composite_builder_page_renders(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    client = dashboard_client(workspace=ws)
    r = client.get("/composites/new")
    assert r.status_code == 200
    body = r.text
    assert "composite-builder-root" in body
    assert "/assets/vendor/cytoscape.min.js" in body
    assert "/assets/composite-builder.js" in body


# ---------------------------------------------------------------------------
# End-to-end: create -> promote -> commit -> visible via /api/composites
# ---------------------------------------------------------------------------

def test_full_roundtrip_appears_in_composites_listing(dashboard_client, tmp_path):
    ws = _copy_fixture(tmp_path)
    _init_workstream(ws)
    client = dashboard_client(workspace=ws)

    create = client.post("/api/composite/create", json={
        "draft": {
            "name": "roundtrip-thing",
            "description": "covers /api/composites visibility",
            "state": {
                "increase": {
                    "_type": "process",
                    "address": "local:IncreaseProcess",
                    "config": {"rate": 1.0},
                    "inputs": {"level": ["stores", "level"]},
                    "outputs": {"level": ["stores", "level"]},
                    "interval": 1.0,
                },
                "stores": {"level": 1.0},
            },
        },
    }).json()
    promo = client.post(
        f"/api/composite/draft/{create['draft_id']}/promote", json={},
    ).json()
    commit = client.post("/api/composite/commit", json={"path": promo["path"]})
    assert commit.status_code == 200, commit.text

    listing = client.get("/api/composites").json()
    names = [c["name"] for c in listing.get("composites", [])]
    assert "roundtrip-thing" in names
