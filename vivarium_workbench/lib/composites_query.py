"""Subprocess-isolated composite discovery for GET /api/composites.

Composite generator discovery via ``@composite_generator`` scanning is unreliable
in a long-running process because stale ``sys.modules`` entries hide newly-added
generators.  Running a fresh Python interpreter in a child process avoids that
problem: the child sees the full, current set.

This module is **stdlib-only** (``subprocess``, ``json``, ``sys``).  It must
never import ``vivarium_workbench.server`` — the FastAPI seam (``api/app.py``)
calls this from a context where importing server would couple the typed app to
the legacy 16k-line module.

``server.py``'s ``_get_composites`` handler re-imports ``composites_via_subprocess``
so the stdlib server still has one implementation (no duplication).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from vivarium_workbench.lib import env_compat

# Module-level cache: composite discovery spawns a fresh Python subprocess that
# re-imports the whole workspace package (~8s cold on v2ecoli). Without a cache
# every /api/composites hit paid that in full — and, fired at page boot, the
# slow calls saturated the browser's connection pool and stalled other tabs
# (Sources' "Loading…"). Keyed by str(ws_root); cleared on workspace switch.
_COMPOSITES_CACHE: dict = {}

# Default 1h: composite discovery only changes when modules are installed/
# removed or workspace source changes -- both invalidate the cache explicitly
# (see clear_composites_cache()) -- so the TTL just bounds staleness between
# those events. A short TTL (previously a hardcoded 30s) meant every request
# past 30s re-ran discovery, including composite_study_stats()'s workspace
# scan (minutes on an NFS-backed workspace before workspace_walk.py). Override
# per deployment via ``VIVARIUM_WORKBENCH_COMPOSITES_TTL`` (seconds).
_COMPOSITES_TTL_DEFAULT = 3600.0


def _composites_ttl() -> float:
    """Current composites-cache TTL in seconds (env-overridable; see above)."""
    raw = env_compat.get_env("COMPOSITES_TTL")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return _COMPOSITES_TTL_DEFAULT


def clear_composites_cache(ws_root: "Path | str | None" = None) -> None:
    """Invalidate the composite-discovery cache (call on workspace switch).

    Always clears the in-memory TTL cache (every workspace). When ``ws_root``
    is given, also clears the on-disk catalog cache (``lib.catalog_disk_cache``)
    for that workspace — best-effort, never raises.
    """
    _COMPOSITES_CACHE.clear()
    if ws_root is not None:
        try:
            from vivarium_workbench.lib import catalog_disk_cache
            catalog_disk_cache.clear(ws_root)
        except Exception:
            pass

# Fence markers — chosen to be unlikely to appear in real Python output.
_START = "@@@C_START@@@"
_END = "@@@C_END@@@"


def composites_via_subprocess(ws_root: Path, *, bypass_cache: bool = False) -> dict | None:
    """Return composite discovery data by running a fresh Python subprocess.

    The child process imports ``lib.composite_lookup``, calls
    ``composites_data(ws_root)``, and prints the result as JSON fenced between
    ``@@@C_START@@@`` / ``@@@C_END@@@`` markers.  Fencing lets the parent ignore
    the noisy import warnings that ``@composite_generator`` scanning emits to
    stdout.

    Parameters
    ----------
    ws_root:
        Workspace root directory (e.g. ``/path/to/my-workspace``).

    Returns
    -------
    dict | None
        The parsed payload dict on success (``{"composites": [...], ...}``),
        or ``None`` on any failure (timeout, non-zero exit, parse error).
    """
    ws_root_str = str(ws_root)
    now = time.time()
    _slot = _COMPOSITES_CACHE.get(ws_root_str)
    if not bypass_cache and _slot is not None and now - _slot["ts"] < _composites_ttl():
        return _slot["data"]

    # Persistent on-disk cache UNDER the in-memory one, mirroring the registry
    # (see lib/catalog_disk_cache.py + lib/registry.py::build_registry). Keyed
    # by the same "what could change the catalog" signature, so a cold
    # worker/pod with an unchanged venv skips both the pooled call AND the
    # fresh-subprocess fallback below entirely. Best-effort: any failure here
    # just falls through to the live paths.
    from vivarium_workbench.lib import catalog_disk_cache
    sig = catalog_disk_cache.catalog_signature(ws_root)
    if not bypass_cache:
        cached = catalog_disk_cache.load(ws_root, "composites", sig)
        if cached is not None:
            _COMPOSITES_CACHE[ws_root_str] = {"data": cached, "ts": now}
            return cached

    # Prefer the WARM pooled env-worker (build_core + workspace imports amortized,
    # like the registry) over a fresh subprocess that re-imports everything (~8s
    # cold — the recurring CI timeout). Fall back to the subprocess if the pool is
    # unavailable / errors.
    try:
        from vivarium_workbench.lib.env_worker_pool import get_pool
        _pooled = get_pool().call(ws_root, "composites_full")
        # Trust the pool ONLY when it returns a NON-EMPTY, error-free result. A
        # cold pooled worker can answer with an empty {"composites": []} (no
        # error) before the workspace package is fully importable; caching that
        # left the Composites tab reading "No composites registered." for the
        # whole TTL. Treat an empty pool answer as "not ready" and fall through
        # to the authoritative subprocess below, which does a full fresh import
        # (and caches its result — empty or not — so a genuinely-empty workspace
        # still gets cached, just via the fallback path).
        if (isinstance(_pooled, dict) and _pooled.get("composites")
                and not _pooled.get("error")):
            _COMPOSITES_CACHE[ws_root_str] = {"data": _pooled, "ts": now}
            catalog_disk_cache.store(ws_root, "composites", sig, _pooled)
            return _pooled
    except Exception:
        pass

    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from vivarium_workbench.lib.composite_lookup import composites_data\n"
        f"_ws = Path({ws_root_str!r})\n"
        "try:\n"
        "    _result = composites_data(_ws)\n"
        "except Exception as _e:\n"
        "    _result = {'composites': [], 'error': str(_e)}\n"
        f"print({_START!r} + json.dumps(_result) + {_END!r})\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ws_root),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None

    stdout = result.stdout or ""
    start_idx = stdout.find(_START)
    end_idx = stdout.find(_END)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return None

    json_text = stdout[start_idx + len(_START) : end_idx]
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return None
    # Cache successful discovery only; failures (None above) are never cached so
    # a transient import error re-tries on the next request.
    _COMPOSITES_CACHE[ws_root_str] = {"data": data, "ts": now}
    catalog_disk_cache.store(ws_root, "composites", sig, data)
    return data


# Invalidate the composite-discovery cache on workspace switch.
from . import active_workspace as _aw  # noqa: E402
_aw.register_clear_cb(clear_composites_cache)
