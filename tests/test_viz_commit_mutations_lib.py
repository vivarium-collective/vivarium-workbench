"""Tests for lib.viz_commit_mutations — visualization commit pure builders.

Covers (per builder):
  - Happy path: workspace.yaml mutation + (dict, 200) return.
  - Every 400/404/409 validation path.
  - Behavioral commit-path tests: drive the REAL server._post_* handler with
    server._active_branch_action monkeypatched to a recorder, asserting:
      (a) _active_branch_action IS called with the exact commit_msg,
      (b) validation 400/404 returns BEFORE the wrapper is ever called,
      (c) the inner action() re-raises on a lib non-200.

Route tests (FastAPI via TestClient):
  - Happy path per route.
  - Key error paths per route.
  - Each route appears in the OpenAPI schema.

Note: the 2 pre-existing failures in test_visualization_endpoints.py are on
origin/main and are NOT regressions from this batch.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from vivarium_dashboard.lib import viz_commit_mutations as vcm
from vivarium_dashboard.api.app import create_app, get_workspace


# ---------------------------------------------------------------------------
# Shared workspace fixture
# ---------------------------------------------------------------------------

_WS_YAML = """\
name: myws
package_path: pbg_myws
observables: []
visualizations: []
simulations: []
"""


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Minimal workspace with empty observables/visualizations/simulations."""
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text(_WS_YAML, encoding="utf-8")
    return w


@pytest.fixture
def client(ws: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


# ---------------------------------------------------------------------------
# observable_add
# ---------------------------------------------------------------------------


class TestObservableAdd:
    def test_happy_path_appends_entry(self, ws: Path) -> None:
        resp, code = vcm.observable_add(ws, {"name": "obs1", "store_path": "agents.0.obs1"})
        assert code == 200, resp
        assert resp["ok"] is True
        data = yaml.safe_load((ws / "workspace.yaml").read_text())
        obs = data["observables"]
        assert len(obs) == 1
        assert obs[0]["name"] == "obs1"
        assert obs[0]["store_path"] == "agents.0.obs1"

    def test_happy_path_with_units_and_description(self, ws: Path) -> None:
        resp, code = vcm.observable_add(ws, {
            "name": "obs2",
            "store_path": "agents.0.obs2",
            "units": "mM",
            "description": "A test observable",
        })
        assert code == 200, resp
        data = yaml.safe_load((ws / "workspace.yaml").read_text())
        entry = data["observables"][0]
        assert entry["units"] == "mM"
        assert entry["description"] == "A test observable"

    def test_units_none_excluded(self, ws: Path) -> None:
        vcm.observable_add(ws, {"name": "obs3", "store_path": "p", "units": ""})
        data = yaml.safe_load((ws / "workspace.yaml").read_text())
        assert "units" not in data["observables"][0]

    def test_400_missing_name(self, ws: Path) -> None:
        resp, code = vcm.observable_add(ws, {"store_path": "x"})
        assert code == 400
        assert "name and store_path are required" in resp["error"]

    def test_400_missing_store_path(self, ws: Path) -> None:
        resp, code = vcm.observable_add(ws, {"name": "obs"})
        assert code == 400
        assert "name and store_path are required" in resp["error"]

    def test_400_both_missing(self, ws: Path) -> None:
        resp, code = vcm.observable_add(ws, {})
        assert code == 400

    def test_409_duplicate_name(self, ws: Path) -> None:
        vcm.observable_add(ws, {"name": "dup", "store_path": "p"})
        resp, code = vcm.observable_add(ws, {"name": "dup", "store_path": "q"})
        assert code == 409
        assert "already registered" in resp["error"]

    def test_multiple_observables_appended(self, ws: Path) -> None:
        vcm.observable_add(ws, {"name": "a", "store_path": "x"})
        vcm.observable_add(ws, {"name": "b", "store_path": "y"})
        data = yaml.safe_load((ws / "workspace.yaml").read_text())
        names = [o["name"] for o in data["observables"]]
        assert names == ["a", "b"]


# ---------------------------------------------------------------------------
# visualization_add
# ---------------------------------------------------------------------------


class TestVisualizationAdd:
    def test_happy_path_description_only(self, ws: Path) -> None:
        resp, code = vcm.visualization_add(ws, {"name": "my-viz", "description": "A nice plot"})
        assert code == 200, resp
        assert resp["ok"] is True
        data = yaml.safe_load((ws / "workspace.yaml").read_text())
        vizes = data["visualizations"]
        assert len(vizes) == 1
        assert vizes[0]["name"] == "my-viz"
        assert vizes[0]["description"] == "A nice plot"

    def test_happy_path_structured(self, ws: Path) -> None:
        # First add an observable so the structured path passes ref check.
        vcm.observable_add(ws, {"name": "free-dnaA", "store_path": "agents.0.dnaa"})
        resp, code = vcm.visualization_add(ws, {
            "name": "dnaA-plot",
            "type": "time-series",
            "observables": ["free-dnaA"],
        })
        assert code == 200, resp
        data = yaml.safe_load((ws / "workspace.yaml").read_text())
        v = data["visualizations"][0]
        assert v["type"] == "time-series"
        assert v["observables"] == ["free-dnaA"]

    def test_400_missing_name(self, ws: Path) -> None:
        resp, code = vcm.visualization_add(ws, {})
        assert code == 400
        assert "name is required" in resp["error"]

    def test_400_name_invalid_chars(self, ws: Path) -> None:
        resp, code = vcm.visualization_add(ws, {"name": "bad name"})
        assert code == 400
        assert "name must match" in resp["error"]

    def test_400_type_required_when_observables(self, ws: Path) -> None:
        resp, code = vcm.visualization_add(ws, {
            "name": "v",
            "observables": ["x"],
        })
        assert code == 400
        assert "type is required when observables are specified" in resp["error"]

    def test_400_invalid_type(self, ws: Path) -> None:
        resp, code = vcm.visualization_add(ws, {
            "name": "v",
            "type": "pie-chart",
            "observables": ["x"],
        })
        assert code == 400
        assert "type must be one of" in resp["error"]

    def test_400_observables_non_empty_list(self, ws: Path) -> None:
        resp, code = vcm.visualization_add(ws, {
            "name": "v",
            "type": "time-series",
            "observables": [],
        })
        assert code == 400
        assert "observables must be a non-empty list" in resp["error"]

    def test_400_unregistered_observable_reference(self, ws: Path) -> None:
        resp, code = vcm.visualization_add(ws, {
            "name": "v",
            "type": "time-series",
            "observables": ["ghost-obs"],
        })
        assert code == 400
        assert "not registered" in resp["error"]

    def test_400_unregistered_simulation(self, ws: Path) -> None:
        resp, code = vcm.visualization_add(ws, {
            "name": "v",
            "simulation": "ghost-sim",
        })
        assert code == 400
        assert "not registered" in resp["error"]

    def test_409_duplicate_name(self, ws: Path) -> None:
        vcm.visualization_add(ws, {"name": "dup"})
        resp, code = vcm.visualization_add(ws, {"name": "dup"})
        assert code == 409
        assert "already registered" in resp["error"]

    def test_config_preserved(self, ws: Path) -> None:
        vcm.visualization_add(ws, {"name": "v", "config": {"color": "red"}})
        data = yaml.safe_load((ws / "workspace.yaml").read_text())
        assert data["visualizations"][0]["config"] == {"color": "red"}

    def test_validation_order_name_then_regex_then_type_then_observables(self, ws: Path) -> None:
        """Validate that the error ordering matches the handler: name → regex → type → obs."""
        # 1. name missing
        r, c = vcm.visualization_add(ws, {})
        assert c == 400 and "name is required" in r["error"]
        # 2. regex
        r, c = vcm.visualization_add(ws, {"name": "bad name"})
        assert c == 400 and "name must match" in r["error"]
        # 3. type required when observables given
        r, c = vcm.visualization_add(ws, {"name": "ok", "observables": ["x"]})
        assert c == 400 and "type is required" in r["error"]
        # 4. type must be one of
        r, c = vcm.visualization_add(ws, {"name": "ok", "type": "bogus", "observables": ["x"]})
        assert c == 400 and "type must be one of" in r["error"]
        # 5. observables must be non-empty
        r, c = vcm.visualization_add(ws, {"name": "ok", "type": "time-series", "observables": []})
        assert c == 400 and "observables must be a non-empty list" in r["error"]


# ---------------------------------------------------------------------------
# visualization_commit_batch
# ---------------------------------------------------------------------------


class TestVisualizationCommitBatch:
    def _seed_staged(self, ws: Path, names: list[str]) -> None:
        staged = ws / ".pbg" / "visualizations-staged"
        staged.mkdir(parents=True, exist_ok=True)
        for n in names:
            (staged / f"{n}.py").write_text(f"# {n}\n")
        # Create workspace.yaml with package_path
        pass  # ws fixture already has workspace.yaml

    def test_happy_path_commits_all(self, ws: Path) -> None:
        self._seed_staged(ws, ["viz-a", "viz-b"])
        resp, code = vcm.visualization_commit_batch(ws, {})
        assert code == 200, resp
        assert resp["ok"] is True
        assert set(resp["committed"]) == {"viz-a", "viz-b"}
        target = ws / "pbg_myws" / "visualizations"
        assert (target / "viz-a.py").is_file()
        assert (target / "viz-b.py").is_file()
        # Staged files removed
        assert not (ws / ".pbg" / "visualizations-staged" / "viz-a.py").exists()

    def test_happy_path_commits_subset(self, ws: Path) -> None:
        self._seed_staged(ws, ["viz-a", "viz-b", "viz-c"])
        resp, code = vcm.visualization_commit_batch(ws, {"names": ["viz-a", "viz-c"]})
        assert code == 200, resp
        assert set(resp["committed"]) == {"viz-a", "viz-c"}
        # viz-b still staged
        assert (ws / ".pbg" / "visualizations-staged" / "viz-b.py").exists()

    def test_creates_init_py(self, ws: Path) -> None:
        self._seed_staged(ws, ["viz-x"])
        vcm.visualization_commit_batch(ws, {})
        assert (ws / "pbg_myws" / "visualizations" / "__init__.py").is_file()

    def test_404_no_staged_dir(self, ws: Path) -> None:
        resp, code = vcm.visualization_commit_batch(ws, {})
        assert code == 404
        assert "no staged visualizations" in resp["error"]

    def test_404_no_match_on_names(self, ws: Path) -> None:
        self._seed_staged(ws, ["viz-real"])
        resp, code = vcm.visualization_commit_batch(ws, {"names": ["ghost"]})
        assert code == 404
        assert "no staged visualizations match" in resp["error"]

    def test_404_empty_staged_dir(self, ws: Path) -> None:
        staged = ws / ".pbg" / "visualizations-staged"
        staged.mkdir(parents=True)
        resp, code = vcm.visualization_commit_batch(ws, {})
        assert code == 404
        assert "no staged visualizations match" in resp["error"]


# ---------------------------------------------------------------------------
# Behavioral commit-path tests (drive real server.Handler.* shims)
# ---------------------------------------------------------------------------


def _make_handler_and_capture():
    """Return (handler_instance, captured_dict).

    The handler's _json is replaced with a recorder that stores (resp, code)
    and returns resp so the method's return value is inspectable.
    """
    import vivarium_dashboard.server as _srv
    handler = object.__new__(_srv.Handler)
    captured: dict[str, Any] = {}

    def _capture_json(resp, code):
        captured["resp"] = resp
        captured["code"] = code
        return resp

    handler._json = _capture_json  # type: ignore[method-assign]
    return handler, captured


class TestObservableCommitPath:
    """Behavioral tests for server._post_observable shim."""

    def test_shim_calls_active_branch_action_with_correct_commit_msg(
        self, ws: Path, monkeypatch: Any
    ) -> None:
        """_active_branch_action is called with f"feat(setup): add observable '{name}'"."""
        import vivarium_dashboard.server as _srv

        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        calls: dict[str, Any] = {}
        sentinel = {"branch": "feat/x", "commit": "abc1234", "message": "m"}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()  # run it so the lib mutation fires
            return dict(sentinel), 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)

        handler, captured = _make_handler_and_capture()
        handler._post_observable({"name": "obs-test", "store_path": "p.q"})

        assert "commit_msg" in calls, "_active_branch_action was NOT called"
        assert calls["commit_msg"] == "feat(setup): add observable 'obs-test'"
        assert captured["code"] == 200

    def test_400_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        """Missing name/store_path → 400 BEFORE _active_branch_action is called."""
        import vivarium_dashboard.server as _srv

        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 400")

        monkeypatch.setattr(_srv, "_active_branch_action", _boom)

        handler, captured = _make_handler_and_capture()
        handler._post_observable({"name": "obs"})  # missing store_path
        assert captured["code"] == 400
        assert "name and store_path are required" in captured["resp"]["error"]

    def test_action_reraises_on_lib_non_200(self, ws: Path, monkeypatch: Any) -> None:
        """action() re-raises when the lib builder returns non-200."""
        import vivarium_dashboard.server as _srv

        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _lib_fails(ws_root: Path, body: dict) -> "tuple[dict, int]":
            return {"error": "boom"}, 500

        monkeypatch.setattr(_srv._viz_commit_mut, "observable_add", _lib_fails)

        captured_action: dict[str, Any] = {}

        def _record_and_run(commit_message, action_fn):
            try:
                action_fn()
            except Exception as exc:
                captured_action["raised"] = exc
                return {"error": str(exc)}, 500
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _record_and_run)

        handler, captured = _make_handler_and_capture()
        handler._post_observable({"name": "x", "store_path": "p"})

        assert "raised" in captured_action, "action() swallowed the lib non-200"
        assert captured["code"] == 500


class TestVisualizationServerCommitPath:
    """Behavioral tests for server._post_visualization shim."""

    def test_shim_calls_active_branch_action_with_correct_commit_msg(
        self, ws: Path, monkeypatch: Any
    ) -> None:
        """_active_branch_action is called with f"feat(setup): add visualization '{name}'"."""
        import vivarium_dashboard.server as _srv

        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        calls: dict[str, Any] = {}
        sentinel = {"branch": "feat/x", "commit": "abc1234", "message": "m"}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()
            return dict(sentinel), 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)

        handler, captured = _make_handler_and_capture()
        handler._post_visualization({"name": "my-viz"})

        assert "commit_msg" in calls, "_active_branch_action was NOT called"
        assert calls["commit_msg"] == "feat(setup): add visualization 'my-viz'"
        assert captured["code"] == 200

    def test_400_missing_name_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        """Missing name → 400 BEFORE _active_branch_action is called."""
        import vivarium_dashboard.server as _srv

        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 400")

        monkeypatch.setattr(_srv, "_active_branch_action", _boom)

        handler, captured = _make_handler_and_capture()
        handler._post_visualization({"name": ""})
        assert captured["code"] == 400
        assert "name is required" in captured["resp"]["error"]

    def test_400_bad_name_regex_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        """Name with spaces → 400 BEFORE _active_branch_action is called."""
        import vivarium_dashboard.server as _srv

        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 400")

        monkeypatch.setattr(_srv, "_active_branch_action", _boom)

        handler, captured = _make_handler_and_capture()
        handler._post_visualization({"name": "bad name"})
        assert captured["code"] == 400
        assert "name must match" in captured["resp"]["error"]

    def test_action_reraises_on_lib_non_200(self, ws: Path, monkeypatch: Any) -> None:
        """action() re-raises when the lib builder returns non-200."""
        import vivarium_dashboard.server as _srv

        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _lib_fails(ws_root: Path, body: dict) -> "tuple[dict, int]":
            return {"error": "boom"}, 500

        monkeypatch.setattr(_srv._viz_commit_mut, "visualization_add", _lib_fails)

        captured_action: dict[str, Any] = {}

        def _record_and_run(commit_message, action_fn):
            try:
                action_fn()
            except Exception as exc:
                captured_action["raised"] = exc
                return {"error": str(exc)}, 500
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _record_and_run)

        handler, captured = _make_handler_and_capture()
        handler._post_visualization({"name": "v"})

        assert "raised" in captured_action, "action() swallowed the lib non-200"
        assert captured["code"] == 500


class TestVisualizationCommitBatchServerCommitPath:
    """Behavioral tests for server._post_visualization_commit_batch shim."""

    def _seed_staged(self, ws: Path, names: list[str]) -> None:
        staged = ws / ".pbg" / "visualizations-staged"
        staged.mkdir(parents=True, exist_ok=True)
        for n in names:
            (staged / f"{n}.py").write_text(f"# {n}\n")

    def test_shim_calls_active_branch_action_with_correct_commit_msg_single(
        self, ws: Path, monkeypatch: Any
    ) -> None:
        """Single viz → commit_msg = f"feat(viz): commit {name}"."""
        import vivarium_dashboard.server as _srv

        self._seed_staged(ws, ["my-viz"])
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        calls: dict[str, Any] = {}
        sentinel = {"branch": "feat/x", "commit": "abc1234", "message": "m"}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()
            return dict(sentinel), 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)

        handler, captured = _make_handler_and_capture()
        handler._post_visualization_commit_batch({})

        assert "commit_msg" in calls, "_active_branch_action was NOT called"
        assert calls["commit_msg"] == "feat(viz): commit my-viz"

    def test_shim_calls_active_branch_action_with_correct_commit_msg_multi(
        self, ws: Path, monkeypatch: Any
    ) -> None:
        """Multiple vizzes → commit_msg = f"feat(viz): commit N visualization(s): ..."."""
        import vivarium_dashboard.server as _srv

        self._seed_staged(ws, ["viz-a", "viz-b"])
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        calls: dict[str, Any] = {}
        sentinel = {"branch": "feat/x", "commit": "abc1234", "message": "m"}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()
            return dict(sentinel), 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)

        handler, captured = _make_handler_and_capture()
        handler._post_visualization_commit_batch({})

        assert "commit_msg" in calls
        assert "2 visualization(s)" in calls["commit_msg"]
        assert "viz-a" in calls["commit_msg"]
        assert "viz-b" in calls["commit_msg"]

    def test_404_no_staged_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        """No staged dir → 404 BEFORE _active_branch_action is called."""
        import vivarium_dashboard.server as _srv

        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 404")

        monkeypatch.setattr(_srv, "_active_branch_action", _boom)

        handler, captured = _make_handler_and_capture()
        handler._post_visualization_commit_batch({})
        assert captured["code"] == 404
        assert "no staged visualizations" in captured["resp"]["error"]

    def test_action_reraises_on_lib_non_200(self, ws: Path, monkeypatch: Any) -> None:
        """action() re-raises when the lib builder returns non-200."""
        import vivarium_dashboard.server as _srv

        self._seed_staged(ws, ["viz-x"])
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _lib_fails(ws_root: Path, body: dict) -> "tuple[dict, int]":
            return {"error": "boom"}, 500

        monkeypatch.setattr(_srv._viz_commit_mut, "visualization_commit_batch", _lib_fails)

        captured_action: dict[str, Any] = {}

        def _record_and_run(commit_message, action_fn):
            try:
                action_fn()
            except Exception as exc:
                captured_action["raised"] = exc
                return {"error": str(exc)}, 500
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _record_and_run)

        handler, captured = _make_handler_and_capture()
        handler._post_visualization_commit_batch({})

        assert "raised" in captured_action, "action() swallowed the lib non-200"
        assert captured["code"] == 500


# ---------------------------------------------------------------------------
# FastAPI route tests
# ---------------------------------------------------------------------------


class TestObservableAddRoute:
    def test_happy_path(self, client: TestClient, ws: Path) -> None:
        resp = client.post("/api/observable", json={"name": "obs1", "store_path": "a.b"})
        assert resp.status_code == 200, resp.json()
        assert resp.json()["ok"] is True
        data = yaml.safe_load((ws / "workspace.yaml").read_text())
        assert data["observables"][0]["name"] == "obs1"

    def test_400_missing_fields(self, client: TestClient) -> None:
        resp = client.post("/api/observable", json={"name": "obs"})
        assert resp.status_code == 400
        assert "name and store_path are required" in resp.json().get("error", "")

    def test_409_duplicate(self, client: TestClient) -> None:
        client.post("/api/observable", json={"name": "dup", "store_path": "p"})
        resp = client.post("/api/observable", json={"name": "dup", "store_path": "q"})
        assert resp.status_code == 409

    def test_observable_add_in_openapi(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/observable" in paths
        assert "post" in paths["/api/observable"]


class TestVisualizationAddRoute:
    def test_happy_path(self, client: TestClient, ws: Path) -> None:
        resp = client.post("/api/visualization", json={"name": "my-viz", "description": "D"})
        assert resp.status_code == 200, resp.json()
        assert resp.json()["ok"] is True
        data = yaml.safe_load((ws / "workspace.yaml").read_text())
        assert data["visualizations"][0]["name"] == "my-viz"

    def test_400_missing_name(self, client: TestClient) -> None:
        resp = client.post("/api/visualization", json={})
        assert resp.status_code == 400
        assert "name is required" in resp.json().get("error", "")

    def test_400_type_required_when_observables(self, client: TestClient) -> None:
        resp = client.post("/api/visualization", json={"name": "v", "observables": ["x"]})
        assert resp.status_code == 400
        assert "type is required" in resp.json().get("error", "")

    def test_400_invalid_type(self, client: TestClient) -> None:
        resp = client.post("/api/visualization", json={
            "name": "v", "type": "scatter", "observables": ["x"],
        })
        assert resp.status_code == 400
        assert "type must be one of" in resp.json().get("error", "")

    def test_visualization_add_in_openapi(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/visualization" in paths
        assert "post" in paths["/api/visualization"]


class TestVisualizationCommitBatchRoute:
    def _seed_staged(self, ws: Path, names: list[str]) -> None:
        staged = ws / ".pbg" / "visualizations-staged"
        staged.mkdir(parents=True, exist_ok=True)
        for n in names:
            (staged / f"{n}.py").write_text(f"# {n}\n")

    def test_happy_path(self, client: TestClient, ws: Path) -> None:
        self._seed_staged(ws, ["viz-x"])
        resp = client.post("/api/visualization-commit-batch", json={})
        assert resp.status_code == 200, resp.json()
        data = resp.json()
        assert data["ok"] is True
        assert "viz-x" in data["committed"]

    def test_404_no_staged(self, client: TestClient) -> None:
        resp = client.post("/api/visualization-commit-batch", json={})
        assert resp.status_code == 404
        assert "no staged visualizations" in resp.json().get("error", "")

    def test_visualization_commit_batch_in_openapi(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/visualization-commit-batch" in paths
        assert "post" in paths["/api/visualization-commit-batch"]
