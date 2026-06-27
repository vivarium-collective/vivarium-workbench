"""Pure builders for 3 miscellaneous workspace POST routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_suggest`` / ``_post_study_report_single`` /
``_post_open_window``.  All three are workspace-scoped (they take a ``ws_root``)
and do only local FS work plus a short ``subprocess.run`` (git-log / browser
open) — no network.  No ``import server`` here.

Return contract (mirrors the other ``lib.*_views`` / ``lib.*_mutations`` seams):
each builder returns ``(body, status)`` so the FastAPI route wraps every path
(success AND error) in ``JSONResponse``, preserving the lib-returned status code
verbatim.

``subprocess`` and ``platform`` are bound at module level so tests monkeypatch
``misc_post_views.subprocess.run`` / ``misc_post_views.platform.system`` and
never spawn a real git or open a real browser window.  ``suggest_requests`` /
``work_state`` / ``single_study_report`` are imported as module-level names
(reached via this module's namespace) so tests can monkeypatch
``suggest_requests.write_request`` / ``work_state.load_state`` /
``single_study_report.build_single_study_report_for_test`` with fakes.

The workspace root is threaded explicitly as ``ws_root`` (replacing the server
``WORKSPACE`` global / ``workspace_paths()`` helper).  ``_ws_add_to_sys_path`` is
replicated inline so the workspace's own package stays importable, matching the
legacy handlers.  The ``?skeptic`` query handling for study-report-single stays
at the FastAPI ROUTE (the builder only reads ``body["skeptic"]``).
"""

from __future__ import annotations

import platform
import subprocess
import sys
import json
from pathlib import Path
from typing import Any

import yaml

from vivarium_dashboard.lib import suggest_requests
from vivarium_dashboard.lib import work_state
from vivarium_dashboard.lib import single_study_report
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Make the workspace's own Python package(s) importable.

    Inline replica of ``server._ws_add_to_sys_path`` (which used the ``WORKSPACE``
    global): insert ``ws_root`` at the front of ``sys.path`` so a top-level
    workspace package (e.g. ``pbg_chromosome_rep1``) resolves.
    """
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def suggest(ws_root: Path, body: dict) -> tuple[dict, int]:
    """POST /api/suggest — write a Claude-suggestion request file.

    Behaviour-preserving port of ``_post_suggest`` (body ``{kind,
    context_extras?}``):

      * ``kind`` not in ``VALID_KINDS`` →
        ``({"error": f"invalid kind (must be one of {VALID_KINDS})"}, 400)``
      * happy path → ``({"ok": True, "id", "skill_command", "instructions"}, 200)``

    Builds the request context (workspace name + description, active branch, and
    the ``main..<branch>`` git-log commits capped at 30) and persists it via
    ``suggest_requests.write_request``.
    """
    _ws_add_to_sys_path(ws_root)
    write_request = suggest_requests.write_request
    VALID_KINDS = suggest_requests.VALID_KINDS

    kind = (body.get("kind") or "").strip()
    if kind not in VALID_KINDS:
        return {"error": f"invalid kind (must be one of {VALID_KINDS})"}, 400

    # Build context: workspace name + description, workstream info, recent commits.
    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    state = work_state.load_state() or {}
    branch = state.get("active_branch")
    commits = []
    if branch:
        r = subprocess.run(
            ["git", "log", "--format=%h %s", f"main..{branch}"],
            cwd=ws_root, capture_output=True, text=True,
        )
        if r.returncode == 0:
            commits = [line for line in (r.stdout or "").splitlines() if line.strip()]

    context = {
        "workspace_name": ws_data.get("name", ""),
        "workspace_description": ws_data.get("description", ""),
        "active_branch": branch,
        "commits": commits[:30],
        "extras": body.get("context_extras") or {},
    }

    req_id = write_request(ws_root, kind, context)
    return {
        "ok": True,
        "id": req_id,
        "skill_command": f"/pbg-suggest {req_id}",
        "instructions": (
            f"Open Claude Code in this workspace and run `/pbg-suggest {req_id}`. "
            f"The dashboard will pick up the response automatically."
        ),
    }, 200


def study_report_single(ws_root: Path, body: dict) -> tuple[dict, int]:
    """POST /api/study-report-single — render a standalone one-study HTML report.

    Behaviour-preserving port of the ``_post_study_report_single`` body — a thin
    try/except wrapper over the existing
    ``single_study_report.build_single_study_report_for_test`` seam:

      * builder result → returned verbatim (``({...}, 200)`` / ``({error}, 4xx)``)
      * any exception → ``({"error": str(e)}, 500)``

    The ``?skeptic`` query handling stays at the FastAPI ROUTE (which merges the
    flag into ``body`` before calling this); the builder only reads
    ``body["skeptic"]``.
    """
    try:
        _ws_add_to_sys_path(ws_root)
        return single_study_report.build_single_study_report_for_test(ws_root, body)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}, 500


def open_window(ws_root: Path, body: dict) -> tuple[dict, int]:
    """POST /api/open-window — open a dashboard URL in the user's browser.

    Behaviour-preserving port of ``_post_open_window`` (body ``{route?}``):

      * server-info file absent →
        ``({"error": "server-info file not found - is the dashboard running?"}, 503)``
      * server-info parse error →
        ``({"error": f"server-info parse failed: {e}"}, 500)``
      * unsupported platform → ``({"error": f"unsupported platform: {plat}"}, 501)``
      * open failed → ``({"error": f"open failed: {e}"}, 500)``
      * happy path → ``({"ok": True, "url": url}, 200)``

    Reads the base URL from ``<ws>/.pbg/server/server-info`` and dispatches the
    platform-appropriate open command (``open`` / ``xdg-open`` / ``cmd /c start``)
    via a 5s-timeout ``subprocess.run``.
    """
    route = (body.get("route") or "/").strip()
    if not route.startswith("/"):
        route = "/" + route
    info_file = WorkspacePaths.load(ws_root).pbg / "server" / "server-info"
    if not info_file.is_file():
        return (
            {"error": "server-info file not found - is the dashboard running?"},
            503,
        )
    try:
        info = json.loads(info_file.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"server-info parse failed: {e}"}, 500
    url = (info.get("url") or "").rstrip("/") + route
    plat = platform.system().lower()
    if plat == "darwin":
        cmd = ["open", url]
    elif plat.startswith("linux"):
        cmd = ["xdg-open", url]
    elif plat == "windows":
        cmd = ["cmd", "/c", "start", url]
    else:
        return {"error": f"unsupported platform: {plat}"}, 501
    try:
        subprocess.run(cmd, capture_output=True, timeout=5)
    except Exception as e:  # noqa: BLE001
        return {"error": f"open failed: {e}"}, 500
    return {"ok": True, "url": url}, 200
