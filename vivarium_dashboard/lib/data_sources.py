"""Workspace data-source enumeration, as library code.

Extracted from server.py so the FastAPI ``/api/data-sources`` route can build
the payload without reaching into the stdlib server. The provider is a
``module:func`` spec declared under ``workspace.yaml`` ``dashboard.data_sources``;
it is imported and called in-process. Results are cached ~30s per workspace.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Callable

import yaml

_DATA_SOURCES_TTL = 30.0  # seconds
# Keyed by str(ws_root) so calls for different workspaces don't share an entry.
_DATA_SOURCES_CACHE: dict[str, dict] = {}


def data_sources_config(ws_data: dict | None) -> dict:
    """Return the workspace's ``dashboard.data_sources`` block (or {})."""
    dash = (ws_data or {}).get("dashboard")
    dash = dash if isinstance(dash, dict) else {}
    ds = dash.get("data_sources")
    return ds if isinstance(ds, dict) else {}


def import_provider(spec: str) -> Callable:
    """Import a ``module:func`` spec and return the callable."""
    if ":" not in spec:
        raise ValueError(f"provider must be 'module:func', got {spec!r}")
    mod_name, _, func_name = spec.partition(":")
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, func_name)
    if not callable(fn):
        raise TypeError(f"provider {spec!r} is not callable")
    return fn


def enumerate_data_sources(ws_root: Path, bypass_cache: bool = False) -> dict:
    """Resolve + invoke the workspace data-source provider, with 30s caching.

    Always returns ``{"label": str|None, "sources": [...]}`` (never raises). A
    missing provider yields ``{"sources": []}``; a provider error yields
    ``{"label", "sources": [], "error": str}`` so the UI can degrade.
    """
    key = str(ws_root)
    now = time.time()
    cached = _DATA_SOURCES_CACHE.get(key)
    if not bypass_cache and cached is not None and now - cached["ts"] < _DATA_SOURCES_TTL:
        return cached["data"]

    data: dict
    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
        cfg = data_sources_config(ws_data)
        provider = str(cfg.get("provider") or "").strip()
        label = cfg.get("label")
        if not provider:
            data = {"sources": []}
        else:
            fn = import_provider(provider)
            raw = fn() or []
            sources = []
            for entry in raw:
                if not isinstance(entry, dict) or "key" not in entry:
                    continue
                sources.append({
                    "key": str(entry.get("key")),
                    "path": str(entry.get("path") or ""),
                    "category": str(entry.get("category") or "uncategorized"),
                    "kind": str(entry.get("kind") or "inherited"),
                    "size_bytes": int(entry.get("size_bytes") or 0),
                    "url": str(entry.get("url") or ""),
                })
            data = {"label": label, "sources": sources}
    except Exception as e:  # noqa: BLE001 — never break the dashboard
        data = {"label": None, "sources": [], "error": f"{type(e).__name__}: {e}"}

    _DATA_SOURCES_CACHE[key] = {"data": data, "ts": now}
    return data
