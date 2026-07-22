"""Workspace data-source enumeration, as library code.

Extracted from server.py so the FastAPI ``/api/data-sources`` route can build
the payload without reaching into the stdlib server. The provider is a
``module:func`` spec declared under ``workspace.yaml`` ``dashboard.data_sources``;
it usually resolves into the workspace's own package, so it is imported + invoked
in the **env worker** (``data_sources_provider``), not the HTTP process. Results
are cached ~30s per workspace.
"""

from __future__ import annotations

import time
from pathlib import Path

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


def _provider_rows_via_worker(ws_root: Path, provider: str) -> dict:
    """Import + invoke the ``module:func`` provider in the workspace's env worker
    (not the HTTP process) and return ``{"rows": [...], "error": str|None}``. The
    worker catches provider errors into ``error``; an unavailable worker degrades
    to an ``error`` string too — never raises, so the caller's bundle stays intact."""
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
    from vivarium_workbench.lib.env_worker_pool import get_pool
    try:
        r = get_pool().call(ws_root, "data_sources_provider", {"provider": provider})
    except EnvWorkerUnavailable:
        return {"rows": [], "error": "data-source provider unavailable (env worker could not start)"}
    if not isinstance(r, dict):
        return {"rows": [], "error": "malformed worker response"}
    return {"rows": r.get("rows") or [], "error": r.get("error")}


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
            result = _provider_rows_via_worker(ws_root, provider)
            if result.get("error"):
                data = {"label": None, "sources": [], "error": result["error"]}
            else:
                sources = []
                for entry in result["rows"]:
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


def clear_cache() -> None:
    """Clear the per-workspace data-sources cache (called on workspace switch)."""
    _DATA_SOURCES_CACHE.clear()


# Register this module's cache-clear with the active-workspace registry so a
# workspace switch invalidates it via active_workspace.invalidate().
from . import active_workspace as _aw  # noqa: E402
_aw.register_clear_cb(clear_cache)
