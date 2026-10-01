"""Route-level containment for two routes whose file references are chosen by the request.

``tests/test_traversal_sweep.py`` walks the whole API with escaping values; these pin the *legitimate* half — a
reference inside the workspace still works — and the refusals the sweep's generic payloads do not reach
(a control directory inside the workspace, a wrong file type, a cwd-relative name).
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from vivarium_workbench.api import app as appmod
from vivarium_workbench.lib import _root


@pytest.fixture
def ws_client(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "studies" / "s1").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("schema_version: 2\nname: cont\n")
    (ws / "studies" / "s1" / "study.yaml").write_text("schema_version: 3\nname: s1\n")
    (ws / "cfg.json").write_text(json.dumps({"a": 1}))
    (ws / ".pbg").mkdir()
    (ws / ".pbg" / "state.json").write_text(json.dumps({"secret": "control-file"}))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)  # a cwd-relative write would land here, not in the repo or home
    saved = _root.get_workspace_root()
    _root.set_workspace_root(ws)
    app = appmod.create_app()
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    yield TestClient(app, raise_server_exceptions=False), ws, cwd
    _root._WS_ROOT = saved
    _root._WS_PATHS = None


def test_a_config_file_inside_the_workspace_is_still_returned(ws_client):
    client, _ws, _cwd = ws_client
    r = client.get("/api/study-config-file", params={"study": "s1", "ref": "cfg.json"})
    assert r.status_code == 200 and r.json()["content"] == {"a": 1}


@pytest.mark.parametrize("ref", [".pbg/state.json", ".PBG/state.json", ".git/config.json", "cfg.txt", "studies/s1/study.yaml"])
def test_a_config_ref_cannot_reach_control_files_or_other_file_types(ws_client, ref):
    client, _ws, _cwd = ws_client
    r = client.get("/api/study-config-file", params={"study": "s1", "ref": ref})
    assert r.status_code == 400
    assert "control-file" not in r.text


def test_save_run_as_variant_still_reads_a_database_inside_the_workspace(ws_client):
    client, ws, _cwd = ws_client
    db = ws / "extra" / "runs.db"
    db.parent.mkdir()
    sqlite3.connect(db).close()
    r = client.post("/api/save-run-as-variant", json={
        "run_id": "nope", "study": "s1", "variant_name": "v", "source_db": "extra/runs.db"})
    assert r.status_code == 404  # accepted the reference, then reported the missing run


@pytest.mark.parametrize("source_db", [
    "../outside.db", "{tmp}/outside.db", ".git/x.db", ".env", "notes.txt", "victim", "extra/runs.sqlite",
])
def test_save_run_as_variant_refuses_other_databases_and_creates_nothing(ws_client, tmp_path, source_db):
    client, ws, cwd = ws_client
    source_db = source_db.replace("{tmp}", str(tmp_path))
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    r = client.post("/api/save-run-as-variant", json={
        "run_id": "r", "study": "s1", "variant_name": "v", "source_db": source_db})
    assert r.status_code == 400
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before  # nothing created, incl. in cwd
    assert not any(cwd.iterdir())


def test_cleanup_stale_only_removes_server_files_inside_a_real_workspace(tmp_path, monkeypatch):
    """The path is caller-supplied. Server state files are deleted only under a directory that is a workspace;
    for anything else the registry entry is dropped and no file is touched. Only the user-level registry is
    stubbed (it lives in the real config dir); the deletion gate is exercised for real."""
    from vivarium_workbench.lib import workspaces_mutations as wm

    monkeypatch.setattr(wm.workspace_catalog, "find_running", lambda p: None)
    monkeypatch.setattr(wm.workspace_catalog, "unregister_server", lambda p: None)

    def state_dir(root):
        d = root / ".pbg" / "server"
        d.mkdir(parents=True)
        (d / "server-info").write_text("{}")
        (d / "server.pid").write_text("1")
        return d

    not_a_ws = tmp_path / "plain-dir"
    keep = state_dir(not_a_ws)
    assert wm.workspaces_cleanup_stale({"path": str(not_a_ws)}) == ({"ok": True}, 200)
    assert (keep / "server-info").exists() and (keep / "server.pid").exists()

    real_ws = tmp_path / "real-ws"
    gone = state_dir(real_ws)
    (real_ws / "workspace.yaml").write_text("name: x\n")
    assert wm.workspaces_cleanup_stale({"path": str(real_ws)}) == ({"ok": True}, 200)
    assert not (gone / "server-info").exists() and not (gone / "server.pid").exists()


# --- display labels are not identifiers -------------------------------------------------------------------
# `name` / `spec_id` / `mode` are free text on many routes (an observable named after its store path, a spec
# id like "pkg/stem"). Only where a value is joined onto a directory is it held to a plain name.

@pytest.mark.parametrize("method, url, body", [
    ("post", "/api/observable", {"name": "agents/0/mass", "store_path": "agents/0/mass"}),
    ("get", "/api/visualization-status?name=plots/growth.html", None),
    ("get", "/api/composite-runs?spec_id=pkg/stem", None),
])
def test_a_label_containing_a_slash_is_not_refused_as_an_identifier(ws_client, method, url, body):
    client, _ws, _cwd = ws_client
    r = client.post(url, json=body) if method == "post" else client.get(url)
    assert "plain name" not in r.text, r.text


@pytest.mark.parametrize("method, url, body", [
    ("get", "/api/investigation-hypotheses?name=../x", None),
    ("post", "/api/investigation-run", {"name": "../x"}),
])
def test_a_name_that_is_joined_onto_the_studies_directory_must_be_plain(ws_client, method, url, body):
    client, _ws, _cwd = ws_client
    r = client.post(url, json=body) if method == "post" else client.get(url)
    assert r.status_code == 400 and "plain name" in r.text, (r.status_code, r.text)


def test_investigation_members_that_are_paths_are_refused_before_they_are_stored(ws_client):
    """`parent_studies` is stored and joined onto the studies directory by later requests."""
    client, ws, _cwd = ws_client
    r = client.post("/api/investigation-create", json={"name": "inv1", "parent_studies": ["../../outside", "/etc"]})
    assert r.status_code == 400 and "plain name" in r.text, (r.status_code, r.text)
    assert not (ws / "investigations" / "inv1").exists()
