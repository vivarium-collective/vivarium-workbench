"""List registered Visualization / Analysis classes for a workspace.

Extracted from ``vivarium_dashboard.server._visualization_classes_data`` so the
FastAPI seam (``api/app.py``) can call it without importing the stdlib server
module.  The single implementation is shared: ``server.py`` re-imports
``list_visualization_classes`` and keeps its old ``_visualization_classes_data``
name as a thin wrapper.
"""

from __future__ import annotations

import sys
from pathlib import Path

from vivarium_dashboard.lib.spec_norm import normalize_requirements  # noqa: F401 (re-exported)


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Ensure the workspace root is on ``sys.path`` so its package is importable."""
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def list_visualization_classes(ws_root: Path) -> dict:
    """Return ``{"classes": [...]}`` for all registered Visualization / Analysis classes.

    Mirrors ``GET /api/visualization-classes``.  Tolerates missing packages /
    ``build_core`` failures → returns empty list.  Used by ``publish.build_bundle``
    to export ``api/visualization-classes.json`` (via the ``server.py`` forwarder).

    Parameters
    ----------
    ws_root:
        Workspace root directory (must contain ``workspace.yaml``).

    Returns
    -------
    dict
        ``{"classes": [...]}`` where each entry has ``address``, ``name``,
        ``doc``, and ``kind`` keys.
    """
    import yaml

    _ws_add_to_sys_path(ws_root)

    # Build the class registry from the workspace's core module.
    try:
        ws_data = (
            yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
        )
        pkg = ws_data.get("package_path") or (
            "pbg_" + ws_data.get("name", "").replace("-", "_")
        )
        sys.path.insert(0, str(ws_root))
        core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
        core = core_module.build_core()
        registry: dict = dict(core.link_registry)
    except Exception:
        registry = {}
        ws_data = {}
        pkg = ""

    # Inject standard pbg-superpowers visualization classes.
    try:
        from pbg_superpowers.visualizations import (
            Distribution,
            Heatmap,
            ParamVsObservable,
            PhaseSpace,
            TimeSeriesPlot,
        )
        for cls in [TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap]:
            registry[cls.__name__] = cls
    except ImportError:
        pass

    # Inject workspace-local viz classes (non-pip-installed).
    try:
        from pbg_superpowers.visualization import Visualization as _VizBase
        import pkgutil as _pkgutil
        import importlib as _importlib
        _pkg_name = ws_data.get("package_path") or (
            "pbg_" + ws_data.get("name", "").replace("-", "_")
        )
        viz_pkg = _importlib.import_module(f"{_pkg_name}.visualizations")
        for _, modname, _ in _pkgutil.iter_modules(viz_pkg.__path__):
            try:
                mod = _importlib.import_module(f"{_pkg_name}.visualizations.{modname}")
                for attr_val in vars(mod).values():
                    if not isinstance(attr_val, type):
                        continue
                    if attr_val is _VizBase:
                        continue
                    if issubclass(attr_val, _VizBase):
                        registry[attr_val.__name__] = attr_val
            except Exception:
                continue
    except Exception:
        pass

    # Filter to Visualization subclasses only.
    try:
        from pbg_superpowers.visualization import Visualization as _VB
    except ImportError:
        _VB = None

    def _is_viz(cls):
        if _VB is not None and cls is _VB:
            return False
        marker = getattr(cls, "is_visualization", None)
        if callable(marker):
            try:
                if marker() is True:
                    return True
            except Exception:
                pass
        if _VB is not None:
            try:
                if isinstance(cls, type) and issubclass(cls, _VB):
                    return True
            except TypeError:
                pass
        return False

    per_cls: dict = {}
    for name, cls in registry.items():
        if not _is_viz(cls) or name == "Visualization":
            continue
        existing = per_cls.get(id(cls))
        if existing is None or len(name) < len(existing[0]):
            per_cls[id(cls)] = (name, cls)

    out = []
    for name, cls in sorted(per_cls.values(), key=lambda kv: kv[0]):
        try:
            doc = (cls.__doc__ or "").strip().split("\n", 1)[0] if cls.__doc__ else ""
        except Exception:
            doc = ""
        out.append({"address": f"local:{name}", "name": name, "doc": doc, "kind": "visualization"})

    # Append Analysis classes from v2ecoli (process-bigraph Steps).
    # Guarded import — dashboard is workspace-agnostic; if v2ecoli is not
    # installed the analysis section is simply absent.
    try:
        import v2ecoli.workflow.analyses  # noqa: F401  (import-time registration)
        from v2ecoli.workflow.analysis import ANALYSIS_REGISTRY, Analysis
        for _name, _cls in sorted(ANALYSIS_REGISTRY.items()):
            if isinstance(_cls, type) and issubclass(_cls, Analysis):
                try:
                    _doc = (_cls.__doc__ or "").strip().split("\n")[0]
                except Exception:
                    _doc = ""
                out.append({
                    "address": f"local:{_cls.__module__}.{_cls.__qualname__}",
                    "name": _name,
                    "doc": _doc,
                    "kind": "analysis",
                })
    except Exception:
        pass

    return {"classes": out}
