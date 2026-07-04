"""Repo-contributed analysis viewers (generic, name-agnostic).

A workspace's package — or any installed ``pbg-*`` distribution — MAY contribute
"analysis viewers" (launcher buttons or embedded mini-apps on the Analyses page)
by exposing a module named ``workbench_viewers`` with a top-level::

    def get_viewers(ws_root) -> list[dict]:
        ...

This is the generic replacement for the workbench formerly hardcoding v2ecoli's
Pathway-Tools launcher. The workbench itself stays free of any repo-specific
(``ptools``/``v2ecoli``/EcoCyc) strings: it only discovers, exposes public
descriptors, and resolves launches back to the contributing package.

Each viewer dict:
    {
      "id":          str,                # unique within its package
      "title":       str,
      "description": str,                # optional
      "kind":        "launcher"|"embed", # default "launcher"
      "applies":     callable|bool,      # optional; (ws_root)->bool, default True
      # launcher-only:
      "launch":      callable,           # (ws_root, study, run, ctx)->{"url":..}
                                         #   | {"error":.., "status":..}
                                         # ctx: {"public_base": str} — the
                                         #   externally-reachable base URL derived
                                         #   from the request Host, for viewers
                                         #   that serve data back over HTTP.
      "targets":     callable|list,      # optional; (ws_root)->[{study,label,detail}]
                                         #   — the launchable rows the card renders
                                         #   (e.g. studies with exported data).
      # embed-only:
      "assets":      {"js":[url,...], "mount_id": str, "api_prefix": str},
    }

Discovery mirrors :mod:`lib.composite_lookup`: the workspace's own package first,
then every installed ``pbg-*`` distribution. A package with no ``workbench_viewers``
module (the common case) simply contributes nothing.
"""
from __future__ import annotations

import importlib
import importlib.metadata as metadata
import warnings
from pathlib import Path
from typing import Any

import yaml


def _workspace_package(ws_root: Path) -> str:
    """The workspace's own Python package name (``package_path`` in
    workspace.yaml, else ``pbg_<name>``). Empty string if unresolvable."""
    try:
        ws_data = yaml.safe_load(
            (ws_root / "workspace.yaml").read_text(encoding="utf-8")
        ) or {}
    except Exception:  # noqa: BLE001
        return ""
    return ws_data.get("package_path") or (
        "pbg_" + str(ws_data.get("name", "")).replace("-", "_")
    )


def _candidate_packages(ws_root: Path) -> list[str]:
    """Package names to probe for a ``workbench_viewers`` submodule: the
    workspace package first, then every installed ``pbg-*`` distribution.
    Order-preserving, de-duplicated."""
    seen: set[str] = set()
    out: list[str] = []

    def _add(pkg: str) -> None:
        if pkg and pkg not in seen:
            seen.add(pkg)
            out.append(pkg)

    _add(_workspace_package(ws_root))
    try:
        for dist in metadata.distributions():
            name = (dist.metadata.get("Name") or "").strip()
            if name.startswith("pbg-"):
                _add(name.replace("-", "_"))
    except Exception:  # noqa: BLE001 — never let discovery crash the page
        pass
    return out


def _load_viewers_module(pkg: str):
    """Import ``<pkg>.workbench_viewers`` or return None if absent/broken."""
    mod_name = f"{pkg}.workbench_viewers"
    try:
        return importlib.import_module(mod_name)
    except ModuleNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001 — a broken contributor must not 500 the page
        warnings.warn(
            f"analysis_viewers: {mod_name} failed to import: "
            f"{type(e).__name__}: {e}",
            stacklevel=2,
        )
        return None


def _applies(viewer: dict, ws_root: Path) -> bool:
    cond = viewer.get("applies", True)
    if callable(cond):
        try:
            return bool(cond(ws_root))
        except Exception:  # noqa: BLE001
            return False
    return bool(cond)


def discover_viewers(ws_root: Path) -> list[dict]:
    """Every contributed viewer visible to *ws_root*, each tagged with its
    ``package`` and a namespaced ``uid`` (``<pkg>::<id>``). Retains callables
    (``applies``/``launch``) for server-side use — NOT JSON-safe. Only viewers
    whose ``applies`` predicate passes are returned."""
    ws_root = Path(ws_root)
    out: list[dict] = []
    for pkg in _candidate_packages(ws_root):
        mod = _load_viewers_module(pkg)
        if mod is None or not hasattr(mod, "get_viewers"):
            continue
        try:
            viewers = mod.get_viewers(ws_root) or []
        except Exception as e:  # noqa: BLE001
            warnings.warn(
                f"analysis_viewers: {pkg}.workbench_viewers.get_viewers raised "
                f"{type(e).__name__}: {e}",
                stacklevel=2,
            )
            continue
        for v in viewers:
            if not isinstance(v, dict) or not v.get("id"):
                continue
            if not _applies(v, ws_root):
                continue
            rec = dict(v)
            rec["package"] = pkg
            rec["uid"] = f"{pkg}::{v['id']}"
            out.append(rec)
    return out


def _resolve_targets(viewer: dict, ws_root: Path) -> list[dict]:
    """Resolve a launcher viewer's ``targets`` (callable or list) into a JSON-safe
    list of ``{study, label, detail}`` rows. Never raises."""
    t = viewer.get("targets")
    if callable(t):
        try:
            t = t(ws_root)
        except Exception:  # noqa: BLE001
            return []
    if not isinstance(t, list):
        return []
    out: list[dict] = []
    for item in t:
        if isinstance(item, dict) and item.get("study"):
            out.append({
                "study": str(item["study"]),
                "label": str(item.get("label") or item["study"]),
                "detail": str(item.get("detail") or ""),
            })
    return out


def _public_spec(viewer: dict, ws_root: Path) -> dict:
    """Strip callables → the JSON-safe descriptor the frontend renders."""
    assets = viewer.get("assets") or {}
    return {
        "uid": viewer["uid"],
        "id": viewer.get("id"),
        "package": viewer.get("package"),
        "title": viewer.get("title") or viewer.get("id"),
        "description": viewer.get("description", ""),
        "kind": viewer.get("kind", "launcher"),
        "targets": _resolve_targets(viewer, ws_root),
        "assets": {
            "js": list(assets.get("js") or []),
            "mount_id": assets.get("mount_id"),
            "api_prefix": assets.get("api_prefix"),
        } if assets else None,
    }


def viewers_public(ws_root: Path) -> list[dict]:
    """JSON-safe descriptors for GET /api/analysis-viewers."""
    ws_root = Path(ws_root)
    return [_public_spec(v, ws_root) for v in discover_viewers(ws_root)]


def resolve_launch(ws_root: Path, uid: str, study: str | None = None,
                   run: str | None = None,
                   ctx: dict | None = None) -> dict[str, Any]:
    """Resolve a launcher viewer's ``uid`` and invoke its ``launch`` callable.

    ``ctx`` carries request-derived context (e.g. ``public_base``) for viewers
    that serve data back over HTTP; contributors may ignore it. Returns the
    contributor's result dict (expected ``{"url": ...}`` on success, or
    ``{"error": ..., "status": ...}``). Never raises: unknown uid → 404-shaped
    error; a launch that raises → 500-shaped error.
    """
    ws_root = Path(ws_root)
    ctx = ctx or {}
    match = next((v for v in discover_viewers(ws_root) if v.get("uid") == uid), None)
    if match is None:
        return {"error": f"viewer not found: {uid}", "status": 404}
    launch = match.get("launch")
    if not callable(launch):
        return {"error": f"viewer {uid} is not launchable", "status": 400}
    try:
        result = launch(ws_root, study, run, ctx)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}", "status": 500}
    if not isinstance(result, dict):
        return {"error": "viewer launch returned a non-dict result", "status": 500}
    return result
