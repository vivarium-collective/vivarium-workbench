"""Pure builders for the two sms-api source-build POST routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_source_build_remote`` and ``_post_source_switch_build``.
Both talk to the sms-api over the network via the existing testable lib clients
(:class:`lib.sms_api_client.SmsApiClient`, :mod:`lib.remote_build_source`), so
they are pure builders returning ``(body, status)`` — the FastAPI route wraps a
non-200 in ``JSONResponse``.  No ``import server`` here.

The network names — :class:`SmsApiClient`, :class:`SmsApiError`,
:func:`list_build_sources`, :func:`materialize_build` — are bound at module
level so tests monkeypatch them with fakes and never touch a real network.

``switch_build`` re-points the active workspace via the 3a shared lib switch
``active_workspace.switch_workspace`` (sets ``lib._root`` + invalidates the lib
caches) — NOT the server's ``_switch_active_workspace`` (which also mutates the
stdlib ``WORKSPACE`` global; that part stays in ``server``, dedup at the flip).

``_sms_api_base`` is REUSED from :mod:`lib.workspace_deps_views` (the batch-13
canonical lib copy) rather than adding a 4th copy.  ``_normalize_repo_url`` is
server-only today, so it is COPIED verbatim here as a pure string fn (server
keeps its own; dedup at the flip).
"""

from __future__ import annotations

import json
from pathlib import Path

from vivarium_dashboard.lib import active_workspace
from vivarium_dashboard.lib.remote_build_source import list_build_sources, materialize_build
from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError
from vivarium_dashboard.lib.workspace_deps_views import _sms_api_base


def _normalize_repo_url(url: str) -> str:
    """Normalize a git remote URL for sms-api's simulator/upload.

    sms-api's ``/core/v1/simulator/upload`` 500s on a ``.git``-suffixed URL
    (it builds an image tag / repo path from the URL), so strip a trailing
    ``.git`` and surrounding whitespace.

    Verbatim copy of ``server._normalize_repo_url`` (server-only today; the
    dedup happens at the flip).
    """
    url = url.strip()
    if url.endswith(".git"):
        url = url[: -len(".git")]
    return url


def build_remote(body: dict) -> tuple[dict, int]:
    """Register a repo+branch HEAD as an sms-api build. Returns ``(body, status)``.

    Behaviour-preserving port of ``_post_source_build_remote``:

      * missing repo/branch  → ``({"error": "repo and branch are required"}, 400)``
      * unresolved HEAD      → ``({"error": "could not resolve branch HEAD via sms-api"}, 502)``
      * ``SmsApiError``      → ``({"error": f"sms-api: {e}"}, 502)``
      * happy path           → ``({"ok": True, "simulator_id", "repo", "branch", "commit"}, 200)``
    """
    repo = (body or {}).get("repo") or ""
    branch = (body or {}).get("branch") or ""
    if not repo or not branch:
        return {"error": "repo and branch are required"}, 400
    repo = _normalize_repo_url(repo)
    client = SmsApiClient(_sms_api_base())
    try:
        latest = client.latest_simulator(repo, branch)
        commit = latest.get("git_commit_hash") or ""
        if not commit:
            return {"error": "could not resolve branch HEAD via sms-api"}, 502
        reg = client.register_simulator(repo, branch, commit)
    except SmsApiError as e:
        return {"error": f"sms-api: {e}"}, 502
    return (
        {"ok": True, "simulator_id": reg.get("database_id"),
         "repo": repo, "branch": branch, "commit": commit},
        200,
    )


def switch_build(body: dict) -> tuple[dict, int]:
    """Materialize a build's workspace (cached) + re-point in-process. ``(body, status)``.

    Behaviour-preserving port of ``_post_source_switch_build``:

      * missing ``simulator_id`` → ``({"error": "missing 'simulator_id'"}, 400)``
      * sms-api unreachable      → ``({"error": f"sms-api unavailable: {err}"}, 502)``
      * build not found          → ``({"error": f"build {sim_id} not found"}, 404)``
      * materialize ``SmsApiError`` → ``({"error": f"materialize failed: {e}"}, 502)``
      * happy path               → ``({"ok": True, "source": {"path", "name"}}, 200)``
    """
    sim_id = (body or {}).get("simulator_id")
    if sim_id is None:
        return {"error": "missing 'simulator_id'"}, 400
    client = SmsApiClient(_sms_api_base())
    listing = list_build_sources(client)
    entry = next((b for b in listing["builds"] if b["simulator_id"] == sim_id), None)
    if entry is None:
        if listing.get("error"):
            return {"error": f"sms-api unavailable: {listing['error']}"}, 502
        return {"error": f"build {sim_id} not found"}, 404
    try:
        cache_dir = materialize_build(client, sim_id, entry["commit"])
    except SmsApiError as e:
        return {"error": f"materialize failed: {e}"}, 502
    # Stamp build provenance into the cache dir so the rail chip can show
    # "<branch> @ <commit> · remote build #<id>" (a materialized build is not
    # a git repo, so the chip can't derive branch/commit from git).
    try:
        (Path(cache_dir) / ".viv-build.json").write_text(json.dumps({
            "simulator_id": sim_id, "repo": entry.get("repo", ""),
            "branch": entry.get("branch", ""), "commit": entry.get("commit", ""),
            "repo_url": entry.get("repo_url", ""),
        }))
    except Exception:
        pass  # provenance stamp is best-effort, never block the switch
    active_workspace.switch_workspace(cache_dir)
    return {"ok": True, "source": {"path": str(cache_dir), "name": entry["label"]}}, 200
