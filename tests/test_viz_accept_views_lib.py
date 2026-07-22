"""Tests for lib.viz_accept_views — visualization-accept finalize builder.

Covers the pure builder ``visualization_accept(ws_root, body)``:
  - 400 (no name)
  - 404 (generated file missing)
  - 500 (generated file fails to import — syntactically broken module)
  - 500 (workspace build_core() raises)
  - 500 (class_name not discoverable in the generated module)
  - 200 happy path (clean module + build_core + matching is_visualization() class)
  - clear_registry_cache() is invoked on the file-present paths

Plus FastAPI route tests (happy 200 + 400 + 404 + OpenAPI presence).

Hermetic: each test builds a real ``pkg/visualizations/<snake>.py`` + ``core.py``
+ ``workspace.yaml`` under a tmp ws_root so the import / build_core / class-walk
run for real.  An autouse fixture snapshots ``sys.path`` / ``sys.modules`` and
restores them so generated workspace modules never leak between tests or into the
real workspace.

Note: the 2 pre-existing failures in test_visualization_endpoints.py are on
origin/main and are NOT regressions from this batch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vivarium_workbench.lib import viz_accept_views as vav
from vivarium_workbench.api.app import create_app, get_workspace


# ---------------------------------------------------------------------------
# Hermetic sys.path / sys.modules isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_sys(monkeypatch):
    """Snapshot sys.path / sys.modules, restore after — drop any pbg_* modules
    or tmp paths the builder inserts so the import never leaks between tests."""
    orig_path = list(sys.path)
    orig_modules = set(sys.modules)
    yield
    sys.path[:] = orig_path
    for mod in list(sys.modules):
        if mod not in orig_modules:
            sys.modules.pop(mod, None)


# ---------------------------------------------------------------------------
# Workspace builders
# ---------------------------------------------------------------------------

_CLEAN_MODULE = """\
class MyViz:
    @staticmethod
    def is_visualization():
        return True
"""

_BROKEN_MODULE = "def (this is not valid python\n"

_CLEAN_CORE = """\
def build_core():
    return {"ok": True}
"""

_FAILING_CORE = """\
def build_core():
    raise RuntimeError("build_core boom")
"""


def _make_ws(
    base: Path,
    *,
    name: str,
    snake: str = "my_viz",
    module_src: str = _CLEAN_MODULE,
    core_src: str = _CLEAN_CORE,
    write_module: bool = True,
) -> Path:
    """Build a minimal, importable workspace under ``base/<name>``.

    A distinct ``name`` per test gives a distinct ``pbg_<name>`` package so the
    in-process import never collides with another test's module.
    """
    pkg = f"pbg_{name}"
    w = base / name
    (w / pkg / "visualizations").mkdir(parents=True)
    (w / "workspace.yaml").write_text(
        f"name: {name}\npackage_path: {pkg}\n", encoding="utf-8"
    )
    (w / pkg / "__init__.py").write_text("", encoding="utf-8")
    (w / pkg / "visualizations" / "__init__.py").write_text("", encoding="utf-8")
    (w / pkg / "core.py").write_text(core_src, encoding="utf-8")
    if write_module:
        (w / pkg / "visualizations" / f"{snake}.py").write_text(
            module_src, encoding="utf-8"
        )
    return w


@pytest.fixture
def spy_clear(monkeypatch):
    """Replace clear_registry_cache with a recorder; assert it is invoked."""
    calls = {"n": 0}

    def _rec() -> None:
        calls["n"] += 1

    monkeypatch.setattr(vav, "clear_registry_cache", _rec)
    return calls


# ---------------------------------------------------------------------------
# Builder: visualization_accept
# ---------------------------------------------------------------------------


class TestVisualizationAccept:
    def test_400_missing_name(self, tmp_path: Path, spy_clear) -> None:
        w = _make_ws(tmp_path, name="ws400")
        resp, code = vav.visualization_accept(w, {})
        assert code == 400
        assert resp == {"error": "name is required"}
        # 400 returns before the cache is touched.
        assert spy_clear["n"] == 0

    def test_400_blank_name(self, tmp_path: Path, spy_clear) -> None:
        w = _make_ws(tmp_path, name="wsblank")
        resp, code = vav.visualization_accept(w, {"name": "   "})
        assert code == 400
        assert resp["error"] == "name is required"

    def test_404_file_missing(self, tmp_path: Path, spy_clear) -> None:
        w = _make_ws(tmp_path, name="ws404", write_module=False)
        resp, code = vav.visualization_accept(w, {"name": "my-viz"})
        assert code == 404
        assert resp == {
            "error": "generated file not found at pbg_ws404/visualizations/my_viz.py"
        }
        # 404 returns before the cache is touched.
        assert spy_clear["n"] == 0

    def test_500_import_fails(self, tmp_path: Path, spy_clear) -> None:
        w = _make_ws(tmp_path, name="wsimp", module_src=_BROKEN_MODULE)
        resp, code = vav.visualization_accept(w, {"name": "my-viz"})
        assert code == 500
        assert resp["error"].startswith("generated file failed to import: ")
        assert "SyntaxError" in resp["error"]
        # cache cleared before the import attempt
        assert spy_clear["n"] == 1

    def test_500_build_core_fails(self, tmp_path: Path, spy_clear) -> None:
        w = _make_ws(tmp_path, name="wsbuild", core_src=_FAILING_CORE)
        resp, code = vav.visualization_accept(w, {"name": "my-viz"})
        assert code == 500
        assert resp["error"].startswith(
            "workspace build_core() failed after importing the generated file: "
        )
        assert "RuntimeError" in resp["error"]
        assert "build_core boom" in resp["error"]
        assert spy_clear["n"] == 1

    def test_500_class_not_found(self, tmp_path: Path, spy_clear) -> None:
        w = _make_ws(tmp_path, name="wscls")
        resp, code = vav.visualization_accept(
            w, {"name": "my-viz", "class_name": "NotThere"}
        )
        assert code == 500
        assert resp["error"] == (
            "class 'NotThere' not found in generated file after import; "
            "check the @as_visualization name= argument matches"
        )
        assert spy_clear["n"] == 1

    def test_200_happy_with_class(self, tmp_path: Path, spy_clear) -> None:
        w = _make_ws(tmp_path, name="wsok1")
        resp, code = vav.visualization_accept(
            w, {"name": "my-viz", "class_name": "MyViz"}
        )
        assert code == 200, resp
        assert resp == {"ok": True}
        assert spy_clear["n"] == 1
        # The verify (import + build_core) now runs in the env worker, so the
        # generated module is imported THERE — never into this HTTP process.
        assert "pbg_wsok1.visualizations.my_viz" not in sys.modules

    def test_200_happy_without_class(self, tmp_path: Path, spy_clear) -> None:
        # No class_name → the class-walk is skipped; import + build_core only.
        w = _make_ws(tmp_path, name="wsok2")
        resp, code = vav.visualization_accept(w, {"name": "my-viz"})
        assert code == 200, resp
        assert resp == {"ok": True}
        assert spy_clear["n"] == 1

    def test_name_snake_normalisation(self, tmp_path: Path, spy_clear) -> None:
        # "My-Viz" → snake "my_viz"; the file at that snake path is found.
        w = _make_ws(tmp_path, name="wssnake", snake="my_viz")
        resp, code = vav.visualization_accept(w, {"name": "My-Viz"})
        assert code == 200, resp


# ---------------------------------------------------------------------------
# FastAPI route: POST /api/visualization-accept
# ---------------------------------------------------------------------------


def _client(ws: Path) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


class TestVisualizationAcceptRoute:
    def test_happy_path_200(self, tmp_path: Path) -> None:
        w = _make_ws(tmp_path, name="rtok")
        client = _client(w)
        resp = client.post(
            "/api/visualization-accept",
            json={"name": "my-viz", "class_name": "MyViz"},
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json() == {"ok": True}

    def test_400_missing_name(self, tmp_path: Path) -> None:
        w = _make_ws(tmp_path, name="rt400")
        client = _client(w)
        resp = client.post("/api/visualization-accept", json={})
        assert resp.status_code == 400
        assert resp.json()["error"] == "name is required"

    def test_404_file_missing(self, tmp_path: Path) -> None:
        w = _make_ws(tmp_path, name="rt404", write_module=False)
        client = _client(w)
        resp = client.post("/api/visualization-accept", json={"name": "absent"})
        assert resp.status_code == 404
        assert "generated file not found" in resp.json()["error"]

    def test_in_openapi(self, tmp_path: Path) -> None:
        w = _make_ws(tmp_path, name="rtoa")
        client = _client(w)
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/visualization-accept" in paths
        assert "post" in paths["/api/visualization-accept"]
