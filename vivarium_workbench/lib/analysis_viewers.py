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
      # embed-only:
      "assets":      {"js":[url,...], "mount_id": str, "api_prefix": str},
    }

Discovery + launch touch the contributor's live callables (``applies`` /
``get_viewers`` / ``targets`` / ``launch``) — workspace Python that must not run in
the shared HTTP process — so both run in the workspace's **env worker**
(``analysis_viewers``, env-worker-protocol §11); only JSON-safe descriptors and
launch-result dicts cross the socket. This module is the thin HTTP-side seam. An
unavailable worker degrades to no viewers / a shaped 503, never a crash.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def viewers_public(ws_root: Path) -> list[dict]:
    """JSON-safe descriptors for GET /api/analysis-viewers — discovered in the
    workspace's env worker. Best-effort: an unavailable worker yields ``[]``."""
    from vivarium_workbench.lib.env_worker_pool import get_pool
    try:
        r = get_pool().call(Path(ws_root), "analysis_viewers", {"action": "list"})
    except Exception:  # noqa: BLE001 — no worker → no contributed viewers (page still renders)
        return []
    return r.get("viewers", []) if isinstance(r, dict) else []


def resolve_launch(ws_root: Path, uid: str, study: str | None = None,
                   run: str | None = None,
                   ctx: dict | None = None) -> dict[str, Any]:
    """Resolve a launcher viewer's ``uid`` and invoke its ``launch`` callable in the
    env worker.

    ``ctx`` carries request-derived context (e.g. ``public_base``) for viewers that
    serve data back over HTTP; contributors may ignore it. Returns the contributor's
    result dict (expected ``{"url": ...}`` on success, or ``{"error": ..., "status":
    ...}``). Never raises: unknown uid → 404-shaped error; a launch that raises →
    500-shaped error; an unavailable worker → 503-shaped error.
    """
    from vivarium_workbench.lib.env_worker_pool import get_pool
    try:
        r = get_pool().call(Path(ws_root), "analysis_viewers", {
            "action": "launch", "uid": uid, "study": study, "run": run,
            "ctx": ctx or {},
        })
    except Exception as e:  # noqa: BLE001 — worker down → shaped error, never a crash
        return {"error": f"analysis viewer unavailable ({type(e).__name__})", "status": 503}
    if not isinstance(r, dict) or not isinstance(r.get("result"), dict):
        return {"error": "viewer launch returned a malformed worker response", "status": 500}
    return r["result"]
