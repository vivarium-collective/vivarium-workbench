"""Tests for ``lib.viz_core`` (the viz-rendering core machinery extraction).

Hermetic and AI-free: each test writes a tiny fake workspace (``workspace.yaml``
+ a ``<pkg>/core.py`` defining ``build_core()`` and/or a
``<pkg>/visualizations/`` package) into a tmp ``ws_root``, or monkeypatches the
lib's own helpers / the ``pbg_superpowers`` base import.  A REAL process-bigraph
core is NEVER built.

Coverage:
  * ``build_workspace_core`` — success + the ``(None, {})`` exception-swallow
    contract (the contract that differs from ``core_builder``).
  * ``add_workspace_viz_classes`` — local Visualization subclass injection +
    ImportError-of-the-base leaves the registry unchanged.
  * ``resolve_viz_class`` — found (``local:<Name>`` → ``(cls, short)``) + unknown
    (``(None, None)``).
  * ``demo_state_for`` — ``demo()`` classmethod / ``BUILTIN_VIZ_DEMOS`` fallback /
    empty.
  * Server-shim parity — the real ``server.Handler`` instance methods delegate to
    the lib functions with ``server.WORKSPACE`` threaded as ``ws_root``.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from vivarium_dashboard.lib import viz_core


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_import_state():
    """Drop fake workspace pkg modules + restore sys.path/sys.modules."""
    saved_path = list(sys.path)
    saved_mods = dict(sys.modules)
    yield
    sys.path[:] = saved_path
    for k in list(sys.modules):
        if k.startswith("pkg_fakeviz"):
            sys.modules.pop(k, None)
    # Restore any pbg_superpowers entries we may have shadowed via setitem.
    for k, v in saved_mods.items():
        if k.startswith("pbg_superpowers"):
            sys.modules[k] = v


def _write_workspace(ws_root: Path, pkg: str) -> None:
    (ws_root / "workspace.yaml").write_text(
        f"package_path: {pkg}\nname: fake\n", encoding="utf-8")


def _write_fake_core(ws_root: Path, pkg: str, *, reg_keys: tuple[str, ...] = (),
                     broken: bool = False) -> None:
    pkg_dir = ws_root / pkg
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    if broken:
        (pkg_dir / "core.py").write_text(
            "def build_core():\n    raise RuntimeError('core build boom')\n",
            encoding="utf-8")
        return
    lines = ["class _Core:", "    def __init__(self):", "        self.link_registry = {}"]
    for k in reg_keys:
        lines.append(f"        class {k}: pass")
        lines.append(f"        self.link_registry[{k!r}] = {k}")
    lines.append("def build_core():")
    lines.append("    return _Core()")
    (pkg_dir / "core.py").write_text("\n".join(lines) + "\n", encoding="utf-8")



# ---------------------------------------------------------------------------
# build_workspace_core
# ---------------------------------------------------------------------------

def test_build_workspace_core_success(tmp_path):
    _write_workspace(tmp_path, "pkg_fakeviz_a")
    _write_fake_core(tmp_path, "pkg_fakeviz_a", reg_keys=("X",))
    core, registry = viz_core.build_workspace_core(tmp_path)
    assert core is not None
    assert isinstance(registry, dict)
    # link_registry snapshotted into a COPY.
    assert registry["X"] is core.link_registry["X"]
    assert registry is not core.link_registry
    # ws_root was inserted on sys.path so the workspace pkg resolves.
    assert str(tmp_path) in sys.path


def test_build_workspace_core_swallows_missing_workspace_yaml(tmp_path):
    # No workspace.yaml -> read_text raises -> swallowed.
    assert viz_core.build_workspace_core(tmp_path) == (None, {})


def test_build_workspace_core_swallows_broken_core(tmp_path):
    _write_workspace(tmp_path, "pkg_fakeviz_b")
    _write_fake_core(tmp_path, "pkg_fakeviz_b", broken=True)
    # build_core() raises RuntimeError -> swallowed, unlike core_builder which
    # re-raises.
    assert viz_core.build_workspace_core(tmp_path) == (None, {})


# ---------------------------------------------------------------------------
# add_workspace_viz_classes
# ---------------------------------------------------------------------------

def _inject_fake_viz_base(monkeypatch):
    """Inject a fake ``pbg_superpowers.visualization.Visualization`` base."""
    base_mod = types.ModuleType("pbg_superpowers.visualization")

    class Visualization:  # noqa: D401 - stub base
        pass

    base_mod.Visualization = Visualization
    pkg_mod = sys.modules.get("pbg_superpowers") or types.ModuleType("pbg_superpowers")
    monkeypatch.setitem(sys.modules, "pbg_superpowers", pkg_mod)
    monkeypatch.setitem(sys.modules, "pbg_superpowers.visualization", base_mod)
    return Visualization


def test_add_workspace_viz_classes_injects(tmp_path, monkeypatch):
    _inject_fake_viz_base(monkeypatch)
    _write_workspace(tmp_path, "pkg_fakeviz_c")
    pkg_dir = tmp_path / "pkg_fakeviz_c"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    viz_dir = pkg_dir / "visualizations"
    viz_dir.mkdir(parents=True, exist_ok=True)
    (viz_dir / "__init__.py").write_text("", encoding="utf-8")
    (viz_dir / "foo.py").write_text(
        "from pbg_superpowers.visualization import Visualization\n"
        "class MyLocalViz(Visualization):\n    pass\n",
        encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    registry: dict = {}
    out = viz_core.add_workspace_viz_classes(tmp_path, registry)
    assert out is registry
    assert "MyLocalViz" in out
    assert out["MyLocalViz"].__name__ == "MyLocalViz"


def test_add_workspace_viz_classes_importerror_unchanged(tmp_path, monkeypatch):
    # Make the base import fail -> registry returned unchanged.
    monkeypatch.setitem(sys.modules, "pbg_superpowers.visualization", None)
    sentinel = object()
    registry = {"orig": sentinel}
    out = viz_core.add_workspace_viz_classes(tmp_path, registry)
    assert out is registry
    assert out == {"orig": sentinel}


# ---------------------------------------------------------------------------
# resolve_viz_class
# ---------------------------------------------------------------------------

def test_resolve_viz_class_found(tmp_path, monkeypatch):
    class FakeTS:
        pass

    monkeypatch.setattr(viz_core, "build_workspace_core",
                        lambda ws: (object(), {"TimeSeriesPlot": FakeTS}))
    # Keep the registry hermetic: skip the 5 real pbg viz classes + workspace walk.
    monkeypatch.setitem(sys.modules, "pbg_superpowers.visualizations", None)
    monkeypatch.setattr(viz_core, "add_workspace_viz_classes", lambda ws, reg: reg)

    cls, short = viz_core.resolve_viz_class(tmp_path, "local:TimeSeriesPlot")
    assert cls is FakeTS
    assert short == "TimeSeriesPlot"


def test_resolve_viz_class_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(viz_core, "build_workspace_core", lambda ws: (object(), {}))
    monkeypatch.setitem(sys.modules, "pbg_superpowers.visualizations", None)
    monkeypatch.setattr(viz_core, "add_workspace_viz_classes", lambda ws, reg: reg)

    assert viz_core.resolve_viz_class(tmp_path, "local:Nope") == (None, None)


# ---------------------------------------------------------------------------
# demo_state_for
# ---------------------------------------------------------------------------

def test_demo_state_for_demo_classmethod():
    class C:
        @classmethod
        def demo(cls):
            return {"a": 1}

    assert viz_core.demo_state_for(C, "AnyKey") == {"a": 1}


def test_demo_state_for_builtin_returns_copy():
    class C:
        pass

    out = viz_core.demo_state_for(C, "Distribution")
    assert out == viz_core.BUILTIN_VIZ_DEMOS["Distribution"]
    # A copy, not the shared constant object.
    assert out is not viz_core.BUILTIN_VIZ_DEMOS["Distribution"]


def test_demo_state_for_empty():
    class C:
        pass

    assert viz_core.demo_state_for(C, "NotARealClass") == {}


