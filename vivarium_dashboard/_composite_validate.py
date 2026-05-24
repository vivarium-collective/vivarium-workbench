"""Subprocess entrypoint: validate a composite YAML against the workspace's
``build_core`` registry.

Run as::

    python -m vivarium_dashboard._composite_validate <path-to.composite.yaml>

cwd must be the workspace root (so that ``<pkg>.core.build_core`` is
importable). Emits exactly one line of JSON on stdout::

    {"ok": bool, "errors": [{"path": str, "kind": str, "message": str}],
     "warnings": [...]}

Errors that escape :func:`process_bigraph.Composite.__init__` are normalised
into ``errors`` with ``kind="resolve"`` (likely an unresolved address) or
``kind="construct"`` (anything else from process_bigraph). Import failures
of the workspace's ``core`` module surface as ``kind="import"``.

This is invoked by :func:`vivarium_dashboard.lib.composite_author.validate_composite`.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

import yaml


def _emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def _resolve_pkg_from_workspace() -> str | None:
    """Read ``workspace.yaml`` from cwd and derive the workspace package name."""
    ws_file = Path("workspace.yaml")
    if not ws_file.is_file():
        return None
    try:
        data = yaml.safe_load(ws_file.read_text()) or {}
    except Exception:
        return None
    pkg = data.get("package_path") or ("pbg_" + str(data.get("name", "")).replace("-", "_"))
    return pkg if pkg else None


def _load_spec(path: Path) -> dict[str, Any]:
    text = path.read_text()
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        _emit({"ok": False,
               "errors": [{"path": "", "kind": "usage",
                           "message": "expected one argument: path to composite YAML"}]})
        return 2

    path = Path(argv[1])
    if not path.is_file():
        _emit({"ok": False,
               "errors": [{"path": str(path), "kind": "not_found",
                           "message": f"composite file not found: {path}"}]})
        return 1

    # 1. Parse the YAML.
    try:
        spec = _load_spec(path)
    except Exception as e:
        _emit({"ok": False,
               "errors": [{"path": str(path), "kind": "parse",
                           "message": f"YAML parse error: {e}"}]})
        return 1

    if not isinstance(spec, dict) or "state" not in spec:
        _emit({"ok": False,
               "errors": [{"path": str(path), "kind": "shape",
                           "message": "composite must be a dict with a 'state' key"}]})
        return 1

    # 2. Import the workspace's build_core.
    pkg = _resolve_pkg_from_workspace()
    if not pkg:
        _emit({"ok": False,
               "errors": [{"path": "workspace.yaml", "kind": "workspace",
                           "message": "could not derive workspace package "
                                      "from workspace.yaml"}]})
        return 1

    try:
        # Workspace root is cwd; PYTHONPATH includes it via the venv's
        # site-packages or via an editable install. If neither, fall back to
        # injecting cwd onto sys.path.
        sys.path.insert(0, str(Path.cwd()))
        module = __import__(f"{pkg}.core", fromlist=["build_core"])
        build_core = getattr(module, "build_core")
    except Exception as e:
        _emit({"ok": False,
               "errors": [{"path": pkg + ".core", "kind": "import",
                           "message": f"could not import {pkg}.core.build_core: {e}",
                           "traceback": traceback.format_exc(limit=5)}]})
        return 1

    try:
        core = build_core()
    except Exception as e:
        _emit({"ok": False,
               "errors": [{"path": pkg + ".core.build_core", "kind": "core",
                           "message": f"build_core() raised: {e}",
                           "traceback": traceback.format_exc(limit=5)}]})
        return 1

    # 3. Substitute parameters with defaults so ``${param}`` placeholders
    #    don't trip up Composite construction during validation. The runtime
    #    substitutes at run time; this is the validation-time analogue.
    try:
        from vivarium_dashboard.lib.composite_lookup import substitute_parameters
        state = substitute_parameters(spec.get("state") or {},
                                      spec.get("parameters") or {},
                                      overrides={})
    except Exception as e:
        _emit({"ok": False,
               "errors": [{"path": "parameters", "kind": "parameters",
                           "message": f"parameter substitution failed: {e}"}]})
        return 1

    # 4. Try to construct the Composite.
    try:
        from process_bigraph import Composite
    except Exception as e:
        _emit({"ok": False,
               "errors": [{"path": "process_bigraph", "kind": "import",
                           "message": f"could not import process_bigraph: {e}"}]})
        return 1

    try:
        Composite({"state": state}, core=core)
    except KeyError as e:
        # process_bigraph raises KeyError when an address doesn't resolve in
        # the registry. Surface that with a more helpful "resolve" kind.
        _emit({"ok": False,
               "errors": [{"path": "state", "kind": "resolve",
                           "message": f"address could not be resolved in core registry: {e}"}]})
        return 1
    except Exception as e:
        _emit({"ok": False,
               "errors": [{"path": "state", "kind": "construct",
                           "message": f"Composite construction failed: "
                                      f"{type(e).__name__}: {e}",
                           "traceback": traceback.format_exc(limit=8)}]})
        return 1

    _emit({"ok": True, "errors": [], "warnings": []})
    return 0


if __name__ == "__main__":  # pragma: no cover — entrypoint
    raise SystemExit(main(sys.argv))
