"""Standalone runner for composite discovery — workspace-venv subprocess path.

Why this exists
---------------

The dashboard serves its HTTP API from its own venv.  Workspaces have
their own venvs that ship the scientific stack (wholecell, viva_munk,
EcoliWCM types, …).  ``pbg_superpowers.composite_generator.discover_generators``
correctly walks every distribution declaring ``bigraph-schema`` as a
dep, but its ``importlib.import_module(<workspace_pkg>)`` call blows
up in the dashboard's venv with ``ModuleNotFoundError: No module named
'wholecell'`` for v2ecoli-style workspaces.

This module is the *workspace-side* discovery runner.  The HTTP layer
spawns ``uv run --directory <workspace> python -m
vivarium_dashboard.lib.discover_composites_runner --response <path>``,
which runs inside the workspace's venv (so wholecell et al. resolve)
and writes a structured JSON response back.

Same producer/consumer split established for ``/api/investigation-
render-viz`` (commit 7ac6a22): the dashboard process never imports
workspace-specific packages directly.

I/O contract
------------

Invocation::

    python -m vivarium_dashboard.lib.discover_composites_runner \
        --workspace /abs/path/to/v2ecoli \
        --pkg v2ecoli \
        --response /abs/path/to/v2ecoli/.pbg/discover/<uuid>.resp.json \
        [--extra-packages foo,bar]   # optional explicit packages

Response file (atomic write — same pattern as run_render_viz)::

    {
        "ok": true,
        "error": null,
        "composites": [
            {"id": "v2ecoli.composites.colony", "kind": "spec", ...},
            {"id": "v2ecoli.composites.colony.colony", "kind": "generator",
             "parameters": {"seed": {...}, "n_cells": {...}, ...}, ...},
            ...
        ]
    }

On exceptions the runner writes ``ok: false`` with a non-null
``error``.  Exit code is **always 0** on controlled errors so the
caller can rely on response-file content rather than exit-code
parsing.  Non-zero exit signals an uncontrolled crash (e.g. before
the runner's exception handler is reached).
"""
from __future__ import annotations

import argparse
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


def discover(workspace: Path, pkg: str, extra_packages: list[str]) -> dict:
    """Run composite discovery against the workspace.  Pure function.

    Returns a response payload with ``ok`` + ``composites`` (or
    ``ok: false`` + ``error``).  Unit-testable without subprocess.
    """
    # Ensure the workspace root is importable.  The dashboard's call site
    # already activates the workspace venv via `uv run --directory`, but
    # editable installs sometimes need the workspace tree itself on
    # sys.path so submodule `from .x import y` style imports resolve.
    if str(workspace) not in sys.path:
        sys.path.insert(0, str(workspace))

    # Use the same machinery the dashboard's in-process path used to
    # call, but now from inside the workspace's venv where the package's
    # deps are importable.
    try:
        from vivarium_dashboard.lib.composite_lookup import (
            discover_workspace_composites,
            _derive_module_from_spec_id,
        )
    except ImportError as exc:
        return {
            "ok": False,
            "error": f"vivarium_dashboard not importable inside workspace venv: {exc}",
            "composites": [],
        }

    composites: list[dict] = []

    # ── 1. File-spec composites (the existing path) ───────────────────
    try:
        ws_specs = discover_workspace_composites(workspace, pkg)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"workspace spec scan failed: {type(exc).__name__}: {exc}",
            "composites": [],
            "traceback": traceback.format_exc(),
        }
    for spec_id, rec in ws_specs.items():
        rec.setdefault("id", spec_id)
        rec.setdefault("kind", "spec")
        rec.setdefault("module", _derive_module_from_spec_id(spec_id))
        # Strip private fields (prefix `_`) before serializing
        composites.append({k: v for k, v in rec.items() if not k.startswith("_")})

    # ── 2. @composite_generator-decorated builders ────────────────────
    # `extra_packages` is the workspace's top-level package + any
    # additional packages the caller explicitly listed.  The workspace's
    # pkg name is always included so `discover_generators` walks the
    # right subtree.
    targets = [pkg] + [p for p in (extra_packages or []) if p and p != pkg]
    try:
        from pbg_superpowers.composite_discovery import discover_all
    except ImportError as exc:
        return {
            "ok": False,
            "error": (
                f"pbg_superpowers not importable inside workspace venv: "
                f"{exc}.  Workspace pyproject.toml should declare "
                f"pbg-superpowers as a dep (or pull it via vivarium-dashboard)."
            ),
            "composites": composites,  # surface spec scan results anyway
        }

    try:
        merged = discover_all(extra_packages=targets)
    except Exception as exc:
        return {
            "ok": False,
            "error": (
                f"discover_all crashed: {type(exc).__name__}: {exc}"
            ),
            "composites": composites,
            "traceback": traceback.format_exc(),
        }

    seen_ids = {c.get("id") for c in composites}
    for gid, entry in merged.items():
        if entry.get("kind") != "generator":
            continue
        if gid in seen_ids:
            continue
        composites.append({
            "id": gid,
            "kind": "generator",
            "name": entry.get("name") or gid.rsplit(".", 1)[-1],
            "description": entry.get("description", ""),
            "tags": entry.get("tags") or [],
            "parameters": entry.get("parameters") or {},
            "requires": entry.get("requires") or {},
            "module": entry.get("module") or _derive_module_from_spec_id(gid),
            "default_n_steps": entry.get("default_n_steps"),
            "visualizations": list(entry.get("visualizations") or []),
            "emitters": list(entry.get("emitters") or []),
        })

    return {
        "ok": True,
        "error": None,
        "composites": composites,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vivarium_dashboard.lib.discover_composites_runner",
        description="Subprocess runner for /api/composites.",
    )
    parser.add_argument("--workspace", required=True,
                        help="absolute path to the workspace root")
    parser.add_argument("--pkg", required=True,
                        help="workspace package name (from workspace.yaml:package_path)")
    parser.add_argument("--response", required=True,
                        help="absolute path to write the response JSON")
    parser.add_argument("--extra-packages", default="",
                        help="comma-separated additional packages to discover")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace)
    response_path = Path(args.response)
    extra_packages = [
        p.strip() for p in args.extra_packages.split(",") if p.strip()
    ] if args.extra_packages else []

    # The response dir must exist; caller is responsible for creating
    # it, but defensively mkdir in case the dir was cleaned between
    # request-write and runner-spawn.
    response_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        response = discover(workspace, args.pkg, extra_packages)
    except Exception as exc:
        response = {
            "ok": False,
            "error": f"runner crashed: {type(exc).__name__}: {exc}",
            "composites": [],
            "traceback": traceback.format_exc(),
        }

    _atomic_write_json(response_path, response)
    return 0  # Controlled outcomes always exit 0 (caller reads response file).


if __name__ == "__main__":
    sys.exit(main())
