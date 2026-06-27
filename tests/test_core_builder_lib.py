"""Tests for ``lib.core_builder`` (in-process workspace core build).

Hermetic: each test writes a tiny fake ``<pkg>/core.py`` into a tmp ws_root that
defines ``build_core()`` returning an object with a ``link_registry`` dict — so
``build_core_for_pkg`` / ``build_viz_registry`` exercise the real
``sys.path.insert`` + ``__import__`` path WITHOUT a real process-bigraph core.
The 5 ``pbg_superpowers`` Visualization classes are registered when importable
and tolerated when not (the bare link-registry copy is all we assert on).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vivarium_dashboard.lib import core_builder


def _write_fake_pkg(ws_root: Path, pkg: str, *, reg_keys: tuple[str, ...] = (),
                    broken: bool = False) -> None:
    """Write ``<ws>/<pkg>/core.py`` with a ``build_core()`` factory.

    ``reg_keys`` names the classes registered into the core's ``link_registry``
    (each mapped to a distinct class object defined inside the generated
    module).
    """
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


@pytest.fixture(autouse=True)
def _clean_import_state():
    """Drop any fake pkg modules between tests so a fresh import resolves."""
    saved_path = list(sys.path)
    saved_mods = {k: v for k, v in sys.modules.items()
                  if k.startswith("pkg_fake")}
    yield
    sys.path[:] = saved_path
    for k in list(sys.modules):
        if k.startswith("pkg_fake"):
            sys.modules.pop(k, None)
    sys.modules.update(saved_mods)


def test_build_core_for_pkg_returns_core(tmp_path):
    _write_fake_pkg(tmp_path, "pkg_fake_a", reg_keys=("X",))
    core = core_builder.build_core_for_pkg(tmp_path, "pkg_fake_a")
    assert isinstance(core.link_registry, dict)
    assert "X" in core.link_registry
    # ws_root was inserted on sys.path so the workspace pkg resolves.
    assert str(tmp_path) in sys.path


def test_build_viz_registry_returns_core_and_registry(tmp_path):
    _write_fake_pkg(tmp_path, "pkg_fake_b", reg_keys=("MyViz",))
    core, registry = core_builder.build_viz_registry(tmp_path, "pkg_fake_b")
    assert isinstance(registry, dict)
    # The workspace link_registry entry survives into the returned dict.
    assert registry["MyViz"] is core.link_registry["MyViz"]
    # registry is a COPY, not the live link_registry object.
    assert registry is not core.link_registry
    # The 5 pbg viz classes are registered IF pbg_superpowers is importable;
    # tolerate its absence (then only the bare link-registry entry is present).
    try:
        import pbg_superpowers.visualizations  # noqa: F401
        for cls_name in ("TimeSeriesPlot", "ParamVsObservable", "Distribution",
                         "PhaseSpace", "Heatmap"):
            assert cls_name in registry
    except ImportError:
        assert set(registry) == {"MyViz"}


# (the remaining tests below)


def test_build_core_for_pkg_raises_on_broken_core(tmp_path):
    _write_fake_pkg(tmp_path, "pkg_fake_c", broken=True)
    with pytest.raises(RuntimeError, match="core build boom"):
        core_builder.build_core_for_pkg(tmp_path, "pkg_fake_c")


def test_build_viz_registry_propagates_broken_core(tmp_path):
    """``build_viz_registry`` does not swallow a core-build failure (the caller
    maps it to a 500)."""
    _write_fake_pkg(tmp_path, "pkg_fake_d", broken=True)
    with pytest.raises(RuntimeError, match="core build boom"):
        core_builder.build_viz_registry(tmp_path, "pkg_fake_d")
