"""Visualization-rendering core machinery (viz-core extraction).

Pure, ``ws_root``-parameterized helpers for building a workspace core and
resolving / demoing ``pbg_superpowers`` Visualization classes, lifted verbatim
from the ``server.Handler`` instance methods so the (later) visualization-preview
port can reach them without an ``import server``.

The four functions + the ``BUILTIN_VIZ_DEMOS`` constant were moved byte-identical
from ``server.py`` (modulo the ``WORKSPACE`` global → explicit ``ws_root`` arg,
the inlined ``_ws_add_to_sys_path`` body, and the intra-closure call rewires).
``server.py`` keeps 1-line instance-method shims delegating here with
``WORKSPACE`` threaded as ``ws_root``, so the live path stays byte-identical.

This module does NOT import ``server`` (no ``lib → server`` edge).

NOTE: ``build_workspace_core`` overlaps ``lib.core_builder.build_core_for_pkg``
but has a DIFFERENT contract — it *swallows* any exception and returns
``(None, {})`` rather than raising.  That contract is reproduced here verbatim;
do not swap in ``core_builder``'s raising behavior.
"""

from __future__ import annotations

import sys

import yaml


# Synthetic demo states for the 5 built-in pbg-superpowers Visualization
# classes. Each key is the class's short name; value is a state dict that
# matches the class's declared inputs(). Used when previewing a viz without
# real run data, or as a fallback when investigation data is incompatible.
BUILTIN_VIZ_DEMOS: dict[str, dict] = {
    "TimeSeriesPlot": {
        # Three runs in a sweep — list-of-lists triggers the multi-run
        # branch in TimeSeriesPlot.update(), and Plotly auto-shows the
        # legend once there's more than one named trace.
        "observable": [
            [1.0, 1.4, 2.1, 3.0, 4.2, 5.7, 7.1, 8.0, 8.3, 8.4],
            [2.0, 2.6, 3.5, 4.6, 5.9, 7.3, 8.5, 9.1, 9.3, 9.3],
            [0.5, 0.7, 1.1, 1.7, 2.5, 3.5, 4.6, 5.5, 6.1, 6.4],
        ],
        "time": [
            [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5],
            [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5],
            [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5],
        ],
        "_run_labels": ["rate=1.0", "rate=2.0", "rate=0.5"],
    },
    "ParamVsObservable": {
        "sweep_param_values": [0.1, 0.5, 1.0, 2.0, 5.0],
        "reduced_observable": [3.0, 7.5, 12.0, 17.5, 21.0],
    },
    "Distribution": {
        "samples": [
            10.0, 10.3, 10.1, 10.6, 10.4, 10.2, 10.5, 10.9, 10.7, 10.4,
            10.8, 10.3, 10.5, 11.0, 10.6, 10.2, 10.4, 10.7, 10.5, 10.8,
        ],
    },
    "PhaseSpace": {
        "x": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0],
        "y": [0.0, 0.8, 1.5, 1.8, 1.5, 0.8, 0.0, -0.8, -1.5, -0.8],
    },
    "Heatmap": {
        "x_params": [0.1, 0.5, 1.0, 2.0, 5.0],
        "y_params": [10.0, 20.0, 30.0],
        "z_values": [
            [1.0, 2.0, 3.0, 4.0, 5.0],
            [2.0, 4.0, 6.0, 8.0, 10.0],
            [3.0, 6.0, 9.0, 12.0, 15.0],
        ],
    },
}


def build_workspace_core(ws_root):
    """Build the workspace's process-bigraph core and return (core, registry_dict).
    On failure, returns (None, {})."""
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)
    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        sys.path.insert(0, str(ws_root))
        core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
        core = core_module.build_core()
        return core, dict(core.link_registry)
    except Exception:
        return None, {}


def add_workspace_viz_classes(ws_root, registry: dict) -> dict:
    """Walk <workspace_pkg>.visualizations.* and inject local Visualization
    subclasses into ``registry`` (so non-pip-installed workspace classes
    are reachable). Returns the mutated registry."""
    try:
        from pbg_superpowers.visualization import Visualization as _VizBase
    except ImportError:
        return registry
    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        import pkgutil, importlib
        viz_pkg = importlib.import_module(f"{pkg}.visualizations")
        for _, modname, _ in pkgutil.iter_modules(viz_pkg.__path__):
            try:
                mod = importlib.import_module(f"{pkg}.visualizations.{modname}")
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
    return registry


def resolve_viz_class(ws_root, address: str):
    """Resolve a 'local:<Name>' address (or bare class name) to the class
    object. Accepts both short names (e.g. ``TimeSeriesPlot``) and the
    fully-qualified module path that ``bigraph_schema.discover_packages``
    emits. Returns (class_obj, short_name) or (None, None) if not found.
    """
    class_key = address.split(":", 1)[1] if ":" in address else address
    core, registry = build_workspace_core(ws_root)
    try:
        from pbg_superpowers.visualizations import (
            TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap,
        )
        for cls in [TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap]:
            registry[cls.__name__] = cls
    except ImportError:
        pass
    add_workspace_viz_classes(ws_root, registry)

    short = class_key.rsplit(".", 1)[-1]
    for key in (class_key, short):
        cls = registry.get(key)
        if cls is not None:
            return cls, short
    return None, None


def demo_state_for(cls, class_key: str) -> dict:
    """Return a synthetic state dict for previewing a class.

    Priority: cls.demo() classmethod (user-provided) → built-in demo map →
    empty dict.
    """
    if hasattr(cls, "demo") and callable(getattr(cls, "demo")):
        try:
            state = cls.demo()
            if isinstance(state, dict):
                return state
        except Exception:
            pass
    return dict(BUILTIN_VIZ_DEMOS.get(class_key, {}))
