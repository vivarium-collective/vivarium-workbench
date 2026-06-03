"""Standalone runner for ``/api/investigation-render-viz`` — subprocess path.

Why this exists
---------------

The dashboard serves the HTTP API from its own venv.  Workspaces have
their own venvs that ship the scientific stack (wholecell, viva_munk,
EcoliWCM types, …).  When the dashboard's in-process render path tried
to ``import {pkg}.core`` directly, it transitively pulled in the
workspace's heavy deps and crashed with ``No module named 'wholecell'``
for v2ecoli-style workspaces.

This module is the *workspace-side* render runner.  The HTTP handler
writes a request JSON, spawns ``uv run --directory <workspace> python
-m vivarium_dashboard.lib.run_render_viz <request_path>``, and reads
the response JSON back.  Inside this subprocess the workspace's venv
is active, so workspace-specific imports succeed.

Honors the producer/consumer split established by todo #22:

* Workspace declares ``core_bootstrap: pbg_<wsname>.core:build_core``
  in its ``study.yaml``.  The runner imports + calls it.
* If absent, falls back to ``{pkg}.core.build_core()`` where ``pkg``
  comes from ``workspace.yaml:package_path``.
* If neither resolves, returns an informative error in the response —
  no silent fallback to default cores that would render meaningless
  charts.

I/O contract
------------

Request file (``<workspace>/.pbg/render-viz/<request_id>.req.json``)::

    {
        "request_id":      "<uuid4>",
        "study_name":      "colonies-01-hpc-readiness",
        "spec_path":       "/abs/path/to/study.yaml",
        "inv_dir":         "/abs/path/to/studies/colonies-01-hpc-readiness",
        "workspace":       "/abs/path/to/v2ecoli",
        "pkg":             "v2ecoli",
        "core_bootstrap":  "pbg_v2ecoli.core:build_core"   // optional
    }

Response file (``<workspace>/.pbg/render-viz/<request_id>.resp.json``)::

    {
        "ok":                true,
        "n_visualizations":  3,
        "viz_paths":         ["/abs/path/to/viz/x.html", ...],
        "error":             null
    }

On exceptions the runner writes a response with ``ok: false`` and a
non-null ``error`` string.  The process exit code is **always 0** on
controlled errors so the calling HTTP handler can rely on response-file
content rather than exit-code parsing.  The exit code is non-zero only
for uncontrolled crashes (e.g. before the runner's exception handler
takes over), which the caller then surfaces as 500.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import traceback
from pathlib import Path


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write ``payload`` to ``path`` atomically (write-then-rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    os.replace(tmp, path)


def _build_core(core_bootstrap: str | None, pkg: str | None):
    """Resolve a process_bigraph Core for the workspace.

    Resolution order:

    1. ``core_bootstrap`` dotted path (``mod:fn`` or ``mod.fn``) — the
       workspace-declared bootstrap (todo #22).
    2. ``{pkg}.core.build_core()`` — pbg-template fallback convention.

    Returns ``(core, source)`` where ``source`` describes which path
    resolved.  Raises ``RuntimeError`` (with a clear message) when
    neither resolves.
    """
    if core_bootstrap:
        if ":" in core_bootstrap:
            mod_path, fn_name = core_bootstrap.rsplit(":", 1)
        else:
            mod_path, fn_name = core_bootstrap.rsplit(".", 1)
        try:
            mod = importlib.import_module(mod_path)
        except ImportError as exc:
            raise RuntimeError(
                f"core_bootstrap module not importable: {mod_path!r} ({exc})"
            ) from exc
        fn = getattr(mod, fn_name, None)
        if fn is None:
            raise RuntimeError(
                f"core_bootstrap target not found: {mod_path}:{fn_name}"
            )
        return fn(), f"core_bootstrap={core_bootstrap}"

    if pkg:
        try:
            core_mod = importlib.import_module(f"{pkg}.core")
        except ImportError as exc:
            raise RuntimeError(
                f"workspace package's core module not importable: "
                f"{pkg}.core ({exc}).  Declare 'core_bootstrap' in study.yaml "
                f"to point at the right bootstrap function (e.g. "
                f"'pbg_{pkg}.core:build_core'), or implement "
                f"'{pkg}.core.build_core()'."
            ) from exc
        build_core = getattr(core_mod, "build_core", None)
        if build_core is None:
            raise RuntimeError(
                f"{pkg}.core has no build_core() function.  Declare "
                f"'core_bootstrap' in study.yaml to point elsewhere."
            )
        return build_core(), f"{pkg}.core.build_core"

    raise RuntimeError(
        "no core_bootstrap declared and no workspace package_path resolved; "
        "cannot build a process_bigraph Core for the render"
    )


def _register_default_viz_types(registry: dict) -> int:
    """Register pbg_superpowers' canonical visualization classes into ``registry``.

    Returns the count of classes successfully registered.  Missing
    ``pbg_superpowers`` is not fatal — the workspace may ship its own
    viz classes via ``core_bootstrap``.
    """
    try:
        from pbg_superpowers.visualizations import (
            TimeSeriesPlot, ParamVsObservable, Distribution, PhaseSpace, Heatmap,
        )
    except ImportError:
        return 0
    registry["TimeSeriesPlot"] = TimeSeriesPlot
    registry["ParamVsObservable"] = ParamVsObservable
    registry["Distribution"] = Distribution
    registry["PhaseSpace"] = PhaseSpace
    registry["Heatmap"] = Heatmap
    return 5


def render(request: dict) -> dict:
    """Render every viz in the spec; return the response payload.

    Pure function over a request dict.  Unit-testable without
    subprocess machinery.
    """
    study_name = request["study_name"]
    spec_path = Path(request["spec_path"])
    inv_dir = Path(request["inv_dir"])
    workspace = Path(request["workspace"])
    pkg = request.get("pkg")
    core_bootstrap = request.get("core_bootstrap")

    # Ensure the workspace itself is importable (its own package may
    # not be installed in the venv if the workspace uses path-mode
    # installs).  Mirrors the dashboard's _ws_add_to_sys_path.
    if str(workspace) not in sys.path:
        sys.path.insert(0, str(workspace))

    # Load spec via the dashboard's own validator so the runner agrees
    # with the API surface on what a spec looks like.
    from vivarium_dashboard.lib.investigations import (
        load_spec, render_visualizations, InvestigationSpecError,
    )
    try:
        spec = load_spec(spec_path)
    except InvestigationSpecError as exc:
        return {"ok": False, "error": f"spec error: {exc}",
                "n_visualizations": 0, "viz_paths": []}

    # Build the core via the spec-declared bootstrap (or the pbg-template
    # convention fallback).  Errors propagate as a structured response.
    try:
        core, core_source = _build_core(core_bootstrap, pkg)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc),
                "n_visualizations": 0, "viz_paths": []}
    except Exception as exc:
        # Workspace-specific import-time crash (e.g. dill unpickle of
        # ParCa cache going wrong) — preserve the type name + message
        # so the dashboard can surface it informatively.
        return {"ok": False,
                "error": f"core build failed: {type(exc).__name__}: {exc}",
                "n_visualizations": 0, "viz_paths": []}

    registry = dict(core.link_registry)
    _register_default_viz_types(registry)

    from process_bigraph import Composite

    def build_and_run(viz_doc, registry_arg):
        composite = Composite({"state": viz_doc}, core=core)
        composite.run(1)
        state = composite.state
        html = state.get("output_store")
        if isinstance(html, dict):
            html = html.get("value") or html.get("_value") or ""
        return html if isinstance(html, str) else ""

    try:
        viz_paths = render_visualizations(
            spec, inv_dir, study_name,
            core_registry=registry, build_and_run=build_and_run,
        )
    except Exception as exc:
        return {"ok": False,
                "error": f"render failed: {type(exc).__name__}: {exc}",
                "n_visualizations": 0, "viz_paths": [],
                "traceback": traceback.format_exc()}

    return {
        "ok": True,
        "error": None,
        "n_visualizations": len(viz_paths),
        "viz_paths": [str(p) for p in viz_paths],
        "core_source": core_source,
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point: read request file, call ``render``, write response file."""
    parser = argparse.ArgumentParser(
        prog="vivarium_dashboard.lib.run_render_viz",
        description="Subprocess runner for /api/investigation-render-viz.",
    )
    parser.add_argument("request_path", help="path to <request_id>.req.json")
    args = parser.parse_args(argv)

    req_path = Path(args.request_path)
    if not req_path.is_file():
        print(f"render-viz runner: request file not found: {req_path}",
              file=sys.stderr)
        return 2

    try:
        request = json.loads(req_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"render-viz runner: malformed request file: {exc}",
              file=sys.stderr)
        return 2

    # The response sits alongside the request, suffix swapped.
    resp_path = req_path.with_name(req_path.name.replace(".req.json", ".resp.json"))

    try:
        response = render(request)
    except Exception as exc:
        # Catch-all so the calling HTTP handler always gets a
        # readable response file even if something below renders'
        # own try/except slipped through.
        response = {
            "ok": False,
            "error": f"runner crashed: {type(exc).__name__}: {exc}",
            "n_visualizations": 0,
            "viz_paths": [],
            "traceback": traceback.format_exc(),
        }

    _atomic_write_json(resp_path, response)
    return 0  # Even on render failure: exit 0 + ok:false in response.


if __name__ == "__main__":
    sys.exit(main())
