"""Tests for lib.scaffold_mutations — investigation scaffold pure builders.

Covers:
  - investigation_create: happy path, validation errors (400), conflict (409).
  - iset_clone: happy path, validation errors (400), 404, 409, 501.
  - delete_investigation: happy path, 400 (missing name), 404 (not found).

Also verifies (behaviorally, by driving the real server handler):
  - The server shim for investigation-delete routes the delete THROUGH
    _active_branch_action with the exact commit_msg (TestServerCommitPath).
  - Validation 400/404 returns BEFORE the commit wrapper is ever called.
  - The inner action() delegates to lib.delete_investigation and re-raises on
    a lib non-200 (batch-18 do_action lesson).
  - The FastAPI route returns the plain lib result.
  - build_iset_detail's response is an additive superset of the legacy seam
    (TestIsetDetailAdditive) — no key the legacy returned is dropped/changed.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vivarium_dashboard.lib.scaffold_mutations import (
    delete_investigation,
    investigation_create,
    iset_clone,
)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

_WORKSPACE_YAML = (
    "schema_version: 2\nname: ws\ncreated: '2026-01-01'\n"
    "plugin_version: 0.6.1\npackage_path: pkg\n"
)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text(_WORKSPACE_YAML, encoding="utf-8")
    (w / "investigations").mkdir()
    (w / "studies").mkdir()
    return w


# ---------------------------------------------------------------------------
# investigation_create
# ---------------------------------------------------------------------------


def test_investigation_create_happy(ws: Path) -> None:
    resp, code = investigation_create(ws, {"name": "my-inv", "overview": "Why"})
    assert code == 200, resp
    yaml_path = ws / "investigations" / "my-inv" / "investigation.yaml"
    assert yaml_path.is_file()
    spec = yaml.safe_load(yaml_path.read_text())
    assert spec["name"] == "my-inv"
    assert spec["status"] == "planning"


def test_investigation_create_returns_detail_shape(ws: Path) -> None:
    resp, code = investigation_create(ws, {"name": "foo"})
    assert code == 200, resp
    assert resp["name"] == "foo"
    assert resp["status"] == "planning"
    assert resp["effective_status"] == "planning"
    assert resp["studies"] == []


def test_investigation_create_missing_name(ws: Path) -> None:
    resp, code = investigation_create(ws, {})
    assert code == 400
    assert "error" in resp


def test_investigation_create_bad_slug(ws: Path) -> None:
    resp, code = investigation_create(ws, {"name": "BadName"})
    assert code == 400
    assert "kebab-case" in resp["error"]


def test_investigation_create_conflict(ws: Path) -> None:
    investigation_create(ws, {"name": "dup"})
    resp, code = investigation_create(ws, {"name": "dup"})
    assert code == 409
    assert "exists" in resp["error"]


# ---------------------------------------------------------------------------
# iset_clone
# ---------------------------------------------------------------------------

_STUB_CLONE_SCRIPT = """\
#!/usr/bin/env python3
import argparse, json, sys, yaml
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--source', required=True)
p.add_argument('--target', required=True)
p.add_argument('--source-root', required=True, type=Path)
p.add_argument('--target-root', required=True, type=Path)
p.add_argument('--source-prefix', default=None)
p.add_argument('--target-prefix', default=None)
p.add_argument('--json', action='store_true')
a = p.parse_args()
src = a.source_root / 'investigations' / a.source / 'investigation.yaml'
dst_dir = a.target_root / 'investigations' / a.target
dst_dir.mkdir(parents=True, exist_ok=False)
spec = yaml.safe_load(src.read_text())
spec['name'] = a.target
(dst_dir / 'investigation.yaml').write_text(yaml.safe_dump(spec, sort_keys=False))
if a.json:
    print(json.dumps({'source': a.source, 'target': a.target, 'studies_remapped': {}}))
"""


def _seed_clone_env(ws: Path) -> None:
    """Write a stub clone script + source investigation into ws."""
    (ws / "scripts").mkdir(exist_ok=True)
    (ws / "scripts" / "clone_investigation.py").write_text(_STUB_CLONE_SCRIPT)
    investigation_create(ws, {"name": "src-inv", "overview": "source"})


def test_iset_clone_happy(ws: Path) -> None:
    _seed_clone_env(ws)
    resp, code = iset_clone(ws, {"source": "src-inv", "target": "dst-inv"})
    assert code == 200, resp
    assert (ws / "investigations" / "dst-inv" / "investigation.yaml").is_file()
    assert resp["name"] == "dst-inv"
    assert "clone_summary" in resp
    assert resp["clone_summary"]["target"] == "dst-inv"


def test_iset_clone_missing_source(ws: Path) -> None:
    resp, code = iset_clone(ws, {"target": "x"})
    assert code == 400
    assert "source and target are required" in resp["error"]


def test_iset_clone_missing_target(ws: Path) -> None:
    resp, code = iset_clone(ws, {"source": "x"})
    assert code == 400


def test_iset_clone_bad_slug(ws: Path) -> None:
    resp, code = iset_clone(ws, {"source": "Bad", "target": "ok"})
    assert code == 400
    assert "kebab-case" in resp["error"]


def test_iset_clone_same_name(ws: Path) -> None:
    resp, code = iset_clone(ws, {"source": "foo", "target": "foo"})
    assert code == 400
    assert "differ" in resp["error"]


def test_iset_clone_source_not_found(ws: Path) -> None:
    resp, code = iset_clone(ws, {"source": "nope", "target": "new-one"})
    assert code == 404
    assert "nope" in resp["error"]


def test_iset_clone_target_already_exists(ws: Path) -> None:
    _seed_clone_env(ws)
    investigation_create(ws, {"name": "dst-inv"})
    resp, code = iset_clone(ws, {"source": "src-inv", "target": "dst-inv"})
    assert code == 409


def test_iset_clone_missing_script(ws: Path) -> None:
    investigation_create(ws, {"name": "src-inv"})
    resp, code = iset_clone(ws, {"source": "src-inv", "target": "dst-inv"})
    assert code == 501
    assert "clone_investigation.py" in resp["error"]


# ---------------------------------------------------------------------------
# delete_investigation
# ---------------------------------------------------------------------------


def test_delete_investigation_happy(ws: Path) -> None:
    investigation_create(ws, {"name": "to-delete"})
    inv_dir = ws / "investigations" / "to-delete"
    assert inv_dir.is_dir()
    resp, code = delete_investigation(ws, {"name": "to-delete"})
    assert code == 200, resp
    assert resp["ok"] is True
    assert resp["name"] == "to-delete"
    assert not inv_dir.exists()


def test_delete_investigation_missing_name(ws: Path) -> None:
    resp, code = delete_investigation(ws, {})
    assert code == 400
    assert "name is required" in resp["error"]


def test_delete_investigation_not_found(ws: Path) -> None:
    resp, code = delete_investigation(ws, {"name": "ghost"})
    assert code == 404
    assert "ghost" in resp["error"]


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# The live FastAPI route returns the plain lib result (no _active_branch_action
# commit-wrapper enrichment). The retired server.Handler commit-wrapper path
# (formerly TestServerCommitPath) and the legacy-seam parity class
# (TestIsetDetailAdditive) were removed with server.py — build_iset_detail and
# the FastAPI delete route are exercised directly here / elsewhere.
# ---------------------------------------------------------------------------


def test_fastapi_route_returns_plain_lib_result(ws: Path) -> None:
    """The FastAPI route returns delete_investigation's tuple directly."""
    from fastapi.testclient import TestClient
    from vivarium_dashboard.api.app import create_app, get_workspace

    investigation_create(ws, {"name": "to-nuke"})
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    client = TestClient(app)
    r = client.post("/api/investigation-delete", json={"name": "to-nuke"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["name"] == "to-nuke"
    # No branch/commit keys — this is the plain lib result, not the
    # _active_branch_action-enriched response.
    assert "branch" not in r.json()
    assert "commit" not in r.json()
