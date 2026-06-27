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
# TestServerCommitPath — investigation-delete LIVE path still uses
# _active_branch_action; validation happens BEFORE the wrapper.
# ---------------------------------------------------------------------------


class TestServerCommitPath:
    """Behavioral tests of the live server ``_post_investigation_delete`` shim.

    These drive the real ``server.Handler._post_investigation_delete`` method
    (not the lib builder) with ``_active_branch_action`` monkeypatched to a
    recorder/raiser, so they actually PROVE the routing + ordering rather than
    string-matching the source.  This is the exact live-commit path that
    regressed in batch 18, so the proof must be behavioral.
    """

    def _handler(self):
        """A bare Handler instance (no socket plumbing) + a _json capture.

        ``_json`` is replaced with a recorder that captures ``(data, code)``
        and returns the data dict so the method's return value is inspectable.
        """
        from vivarium_dashboard.server import Handler  # type: ignore[attr-defined]

        handler = object.__new__(Handler)
        captured: dict = {}

        def _capture_json(data, code):
            captured["data"] = data
            captured["code"] = code
            return data

        handler._json = _capture_json  # type: ignore[attr-defined]
        return handler, captured

    def test_shim_routes_through_active_branch_action(
        self, ws: Path, monkeypatch
    ) -> None:
        """An existing investigation is deleted THROUGH _active_branch_action.

        Proves: (a) _active_branch_action IS called, (b) with the exact
        commit_msg, (c) the captured response is the wrapper's sentinel (so the
        shim genuinely returns the wrapper result, not a bypass), and (d) the
        inner action() actually invokes lib.scaffold_mutations.delete_investigation.
        """
        import vivarium_dashboard.server as _server

        investigation_create(ws, {"name": "del-me"})
        inv_dir = ws / "investigations" / "del-me"
        assert inv_dir.is_dir()

        monkeypatch.setattr(_server, "WORKSPACE", ws)

        calls: dict = {}
        sentinel = {"branch": "feat/x", "commit": "abc1234", "message": "m"}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            calls["action"] = action_fn
            # Run the action so the rmtree side-effect + lib delegation happen,
            # mirroring the real wrapper (which calls action_fn() then commits).
            action_fn()
            return dict(sentinel), 200

        monkeypatch.setattr(_server, "_active_branch_action", _recorder)

        spy: dict = {"n": 0}
        real_delete = _server._scaffold_mut.delete_investigation

        def _spy_delete(ws_root, body):
            spy["n"] += 1
            spy["args"] = (ws_root, body)
            return real_delete(ws_root, body)

        monkeypatch.setattr(
            _server._scaffold_mut, "delete_investigation", _spy_delete
        )

        handler, captured = self._handler()
        result = handler._post_investigation_delete({"name": "del-me"})

        # (a) wrapper was called, (b) with the exact commit_msg
        assert "commit_msg" in calls, "_active_branch_action was NOT called"
        assert calls["commit_msg"] == "feat(investigations): delete del-me"
        # (c) the shim returns the wrapper's sentinel (+ ok/name enrichment)
        assert captured["code"] == 200
        assert result["branch"] == "feat/x"
        assert result["commit"] == "abc1234"
        assert result["ok"] is True
        assert result["name"] == "del-me"
        # (d) inner action() delegated to the lib builder, which removed the dir
        assert spy["n"] == 1, "action() did not call lib.delete_investigation"
        assert spy["args"][0] == ws
        assert not inv_dir.exists()

    def test_missing_name_returns_400_before_wrapper(
        self, ws: Path, monkeypatch
    ) -> None:
        """No name → 400 and _active_branch_action is NEVER called."""
        import vivarium_dashboard.server as _server

        monkeypatch.setattr(_server, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 400")

        monkeypatch.setattr(_server, "_active_branch_action", _boom)

        handler, captured = self._handler()
        handler._post_investigation_delete({})
        assert captured["code"] == 400
        assert "name is required" in captured["data"]["error"]

    def test_not_found_returns_404_before_wrapper(
        self, ws: Path, monkeypatch
    ) -> None:
        """Unknown investigation → 404 and _active_branch_action is NEVER called."""
        import vivarium_dashboard.server as _server

        monkeypatch.setattr(_server, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 404")

        monkeypatch.setattr(_server, "_active_branch_action", _boom)

        handler, captured = self._handler()
        handler._post_investigation_delete({"name": "ghost"})
        assert captured["code"] == 404
        assert "ghost" in captured["data"]["error"]

    def test_action_reraises_on_lib_non_200(self, ws: Path, monkeypatch) -> None:
        """The inner action() re-raises when the lib builder returns non-200.

        Guards the batch-18 do_action lesson: a post-validation lib failure
        must propagate (so the wrapper surfaces an error) rather than being
        swallowed into a silent success.
        """
        import vivarium_dashboard.server as _server

        investigation_create(ws, {"name": "raise-me"})
        monkeypatch.setattr(_server, "WORKSPACE", ws)

        def _lib_fails(ws_root, body):
            return {"error": "boom"}, 500

        monkeypatch.setattr(
            _server._scaffold_mut, "delete_investigation", _lib_fails
        )

        captured_action: dict = {}

        def _record_and_run(commit_message, action_fn):
            captured_action["fn"] = action_fn
            try:
                action_fn()
            except Exception as exc:  # noqa: BLE001
                captured_action["raised"] = exc
                return {"error": str(exc)}, 500
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_server, "_active_branch_action", _record_and_run)

        handler, captured = self._handler()
        handler._post_investigation_delete({"name": "raise-me"})
        assert "raised" in captured_action, "action() swallowed the lib non-200"
        assert captured["code"] == 500

    def test_fastapi_route_returns_plain_lib_result(self, ws: Path) -> None:
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


# ---------------------------------------------------------------------------
# Behavior-preservation: the investigation-create/clone response is an additive
# SUPERSET of the legacy minimal seam (_build_iset_detail_for_test) — no
# legacy key dropped or changed.
# ---------------------------------------------------------------------------


class TestIsetDetailAdditive:
    """build_iset_detail must not drop/alter any key the legacy seam returned."""

    def _assert_superset(self, legacy: dict, new: dict, ctx: str) -> None:
        for k, v in legacy.items():
            assert k in new, f"{ctx}: legacy key {k!r} dropped from new response"
            if k == "studies":
                continue  # studies compared element-wise by the caller
            assert new[k] == v, (
                f"{ctx}: legacy key {k!r} changed value "
                f"({v!r} -> {new[k]!r})"
            )

    def test_no_studies_response_is_superset(self, ws: Path) -> None:
        from vivarium_dashboard.server import _build_iset_detail_for_test
        from vivarium_dashboard.lib.report_views import build_iset_detail

        investigation_create(ws, {"name": "foo", "overview": "bar"})
        legacy, code = _build_iset_detail_for_test(ws, "foo")
        assert code == 200
        new = build_iset_detail(ws, "foo")
        assert new is not None
        self._assert_superset(legacy, new, "top-level")

    def test_with_study_response_is_superset(self, ws: Path) -> None:
        from vivarium_dashboard.server import _build_iset_detail_for_test
        from vivarium_dashboard.lib.report_views import build_iset_detail

        inv = ws / "investigations" / "inv"
        inv.mkdir()
        (inv / "investigation.yaml").write_text(
            "name: inv\ntitle: Inv\nstatus: planning\nstudies:\n  - s1\n"
        )
        sd = ws / "studies" / "s1"
        sd.mkdir()
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 3, "name": "s1", "status": "complete",
            "baseline": [{"composite": "x", "name": "b"}],
            "design_status": "approved", "gate_status": "passed",
        }, sort_keys=False))

        legacy, code = _build_iset_detail_for_test(ws, "inv")
        assert code == 200
        new = build_iset_detail(ws, "inv")
        assert new is not None
        self._assert_superset(legacy, new, "top-level")

        legacy_study = legacy["studies"][0]
        new_study = {s["name"]: s for s in new["studies"]}[legacy_study["name"]]
        self._assert_superset(legacy_study, new_study, "study-level")
