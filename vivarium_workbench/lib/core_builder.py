"""In-process workspace core build + visualization registry assembly.

Extracted verbatim from the inline core-build section of the stdlib handler
``server.Handler._post_investigation_run`` (the ``sys.path.insert`` +
``__import__(f"{pkg}.core").build_core()`` + the 5 ``pbg_superpowers``
Visualization class registrations).  Pulled into ``lib/`` so both the
investigation-run port and (later) the viz-preview port can build the registry
without an ``import server``.

Two pure helpers, both threading the workspace root explicitly (replacing the
server ``WORKSPACE`` global):

  * ``build_core_for_pkg(ws_root, pkg)`` — import the workspace's ``<pkg>.core``
    and call ``build_core()``.  Raises on any failure (the caller maps it to a
    500); no try/except here so the original exception text reaches the handler.
  * ``build_viz_registry(ws_root, pkg)`` — build the core, snapshot its
    ``link_registry`` into a plain dict, and register the 5 default
    ``pbg_superpowers`` Visualization classes (``ImportError``-tolerant — a
    workspace without ``pbg_superpowers`` installed simply gets the bare
    link-registry).  Returns ``(core, registry)``.

Byte-identical to the handler's inline logic, just ``ws_root``-parameterised.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def build_core_for_pkg(ws_root: Path, pkg: str) -> Any:
    """Import ``<pkg>.core`` from the workspace and return ``build_core()``.

    Replicates the handler's in-process core build:
    ``sys.path.insert(0, str(WORKSPACE))`` then
    ``__import__(f"{pkg}.core", fromlist=["build_core"]).build_core()`` with the
    ``WORKSPACE`` global threaded as ``ws_root``.  Raises on failure so the
    caller can map it to an HTTP 500.
    """
    sys.path.insert(0, str(ws_root))
    core_module = __import__(f"{pkg}.core", fromlist=["build_core"])
    return core_module.build_core()


def build_viz_registry(ws_root: Path, pkg: str) -> "tuple[Any, dict]":
    """Build the workspace core + its visualization link registry.

    Returns ``(core, registry)`` where ``registry`` is a plain ``dict`` copy of
    ``core.link_registry`` augmented with the 5 default ``pbg_superpowers``
    Visualization classes (``TimeSeriesPlot``, ``ParamVsObservable``,
    ``Distribution``, ``PhaseSpace``, ``Heatmap``).  If ``pbg_superpowers`` is
    not importable the registry is just the bare link-registry copy.
    """
    core = build_core_for_pkg(ws_root, pkg)
    registry = dict(core.link_registry)

    # Also register the default Visualization classes from pbg_superpowers
    try:
        from pbg_superpowers.visualizations import (
            TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap,
        )
        registry["TimeSeriesPlot"] = TimeSeriesPlot
        registry["ParamVsObservable"] = ParamVsObservable
        registry["Distribution"] = Distribution
        registry["PhaseSpace"] = PhaseSpace
        registry["Heatmap"] = Heatmap
    except ImportError:
        pass

    return core, registry
