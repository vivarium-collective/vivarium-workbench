"""Tests for lib.investigation_viz_mutations — investigation viz POST builders.

Covers (per builder):
  - add_viz: happy path (spec ``visualizations`` append + augmented
    ``(dict, status)`` return) + every 400/404/409 path; the private
    ``_apply_add_viz`` RAISES on a duplicate (the live ``_active_branch_action``
    path's 500), while the public ``add_viz`` returns the precise 409.
  - render_viz: 400 (name required) + 404 (not found) validation paths with a
    real on-disk spec; the heavy core-build/render path is exercised by
    stubbing ``render_visualizations`` and the workspace ``<pkg>.core`` module.

Behavioral commit-path test for add-viz: drive the REAL
``server._post_investigation_add_viz`` with ``server._active_branch_action``
monkeypatched to a recorder — asserting the exact commit_msg,
validation-before-wrapper, and the post-wrapper ``ok``/``investigation``/
``viz_name`` augmentation.  For render-viz (no wrapper) a plain delegation test:
monkeypatch the lib ``render_viz`` to a sentinel and assert the server shim
returns it.  NOT inspect.getsource.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest
import yaml

from vivarium_dashboard.lib import investigation_viz_mutations as ivm


_INV = "demo"


def _make_ws(tmp_path: Path) -> Path:
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text(
        "schema_version: 3\nname: testws\npackage_path: pbg_testws\n",
        encoding="utf-8",
    )
    return w


def _make_inv(ws: Path, spec: dict) -> Path:
    inv = ws / "investigations" / _INV
    inv.mkdir(parents=True, exist_ok=True)
    (inv / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return inv


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return _make_ws(tmp_path)


def _read(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# A minimal spec that passes lib.investigations.load_spec (v2 variants shape).
_VALID_SPEC = {
    "name": _INV,
    "baseline": "b",
    "variants": [{"name": "b", "source": "pbg_testws.composites.b"}],
    "visualizations": [],
}


# ---------------------------------------------------------------------------
# add_viz
# ---------------------------------------------------------------------------


class TestAddViz:
    def test_happy(self, ws: Path) -> None:
        inv = _make_inv(ws, {"name": _INV})
        resp, code = ivm.add_viz(ws, {
            "investigation": _INV, "name": "my-plot",
            "address": "local:TimeSeriesPlot", "config": {"x": "time"},
        })
        assert code == 200, resp
        assert resp == {"ok": True, "investigation": _INV, "viz_name": "my-plot"}
        spec = _read(inv / "spec.yaml")
        assert spec["visualizations"] == [{
            "name": "my-plot", "address": "local:TimeSeriesPlot",
            "config": {"x": "time"},
        }]

    def test_happy_default_config(self, ws: Path) -> None:
        inv = _make_inv(ws, {"name": _INV})
        resp, code = ivm.add_viz(ws, {
            "investigation": _INV, "name": "p", "address": "local:X",
        })
        assert code == 200, resp
        spec = _read(inv / "spec.yaml")
        assert spec["visualizations"][0]["config"] == {}

    def test_400_missing_fields(self, ws: Path) -> None:
        resp, code = ivm.add_viz(ws, {"investigation": _INV, "name": "p"})
        assert code == 400
        assert resp["error"] == "investigation, name, address required"

    def test_400_bad_viz_name(self, ws: Path) -> None:
        _make_inv(ws, {"name": _INV})
        resp, code = ivm.add_viz(ws, {
            "investigation": _INV, "name": "bad name!", "address": "local:X",
        })
        assert code == 400
        assert resp["error"] == "viz name must match [a-zA-Z0-9_-]+"

    def test_404_investigation(self, ws: Path) -> None:
        resp, code = ivm.add_viz(ws, {
            "investigation": "ghost", "name": "p", "address": "local:X",
        })
        assert code == 404
        assert resp["error"] == "investigation 'ghost' not found"

    def test_409_duplicate(self, ws: Path) -> None:
        _make_inv(ws, {"name": _INV, "visualizations": [
            {"name": "p", "address": "local:X", "config": {}},
        ]})
        resp, code = ivm.add_viz(ws, {
            "investigation": _INV, "name": "p", "address": "local:Y",
        })
        assert code == 409
        assert resp["error"] == "visualization 'p' already exists in spec"

    def test_apply_raises_on_duplicate(self, ws: Path) -> None:
        # The private helper raises (live path → 500 via _active_branch_action).
        inv = _make_inv(ws, {"name": _INV, "visualizations": [
            {"name": "p", "address": "local:X", "config": {}},
        ]})
        with pytest.raises(RuntimeError, match="already exists in spec"):
            ivm._apply_add_viz(
                ws, spec_path=inv / "spec.yaml",
                viz_name="p", address="local:Y", viz_config={},
            )


# ---------------------------------------------------------------------------
# render_viz
# ---------------------------------------------------------------------------


class TestRenderViz:
    def test_400_name_required(self, ws: Path) -> None:
        resp, code = ivm.render_viz(ws, {"name": ""})
        assert code == 400
        assert resp["error"] == "name is required"

    def test_404_not_found(self, ws: Path) -> None:
        resp, code = ivm.render_viz(ws, {"name": "ghost"})
        assert code == 404
        assert resp["error"] == "investigation 'ghost' not found"

    def test_happy_stubbed(self, ws: Path, monkeypatch: Any) -> None:
        inv = _make_inv(ws, dict(_VALID_SPEC))

        # Stub the workspace <pkg>.core module so build_core() succeeds without
        # importing a real workspace package.
        fake_core = types.ModuleType("pbg_testws.core")
        fake_pkg = types.ModuleType("pbg_testws")

        class _Core:
            link_registry = {"existing": object()}

        fake_core.build_core = lambda: _Core()  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pbg_testws", fake_pkg)
        monkeypatch.setitem(sys.modules, "pbg_testws.core", fake_core)

        from vivarium_dashboard.lib import investigations as _inv_lib
        monkeypatch.setattr(
            _inv_lib, "render_visualizations",
            lambda spec, inv_dir, name, **kw: [Path("a.html"), Path("b.html")],
        )

        resp, code = ivm.render_viz(ws, {"name": _INV})
        assert code == 200, resp
        assert resp["ok"] is True
        assert resp["investigation"] == _INV
        assert resp["n_visualizations"] == 2
        assert resp["viz_paths"] == ["a.html", "b.html"]

    def test_500_build_core_fails(self, ws: Path, monkeypatch: Any) -> None:
        _make_inv(ws, dict(_VALID_SPEC))
        fake_core = types.ModuleType("pbg_testws.core")

        def _boom():
            raise RuntimeError("core kaput")

        fake_core.build_core = _boom  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pbg_testws", types.ModuleType("pbg_testws"))
        monkeypatch.setitem(sys.modules, "pbg_testws.core", fake_core)
        resp, code = ivm.render_viz(ws, {"name": _INV})
        assert code == 500
        assert "failed to build core" in resp["error"]


# The commit-path (``server._active_branch_action`` wrapper) and render-viz
# delegation tests that drove the retired ``server.Handler`` were removed with
# the server.py retirement. The lib builders' return shapes (including the
# ``{ok, investigation, viz_name}`` augmentation) are covered by TestAddViz /
# TestRenderViz above; the FastAPI seam is covered in tests/test_api_app.py.
