"""Pure builders for 3 miscellaneous workspace POST routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_click`` / ``_post_render`` / ``_post_feedback_import``.
All three are workspace-scoped (they take a ``ws_root``) and do only local FS
work / an in-process render — no subprocess, no network, no in-memory manager.
No ``import server`` here.

Return contract (mirrors the other ``lib.*_mutations`` modules):

  * ``record_click(ws_root, body) -> None`` — pure side-effect (FS append); the
    FastAPI route turns this into a RAW empty ``204 No Content`` (no JSON body),
    byte-matching the legacy ``send_response(204)``.
  * ``render_dashboard(ws_root) -> (dict, int)`` and
    ``feedback_import(ws_root, body) -> (dict, int)`` — the route wraps every
    path (success AND error) in ``JSONResponse`` so the lib-returned status code
    is preserved verbatim.

``record_click`` serialises its appends with a MODULE-LEVEL ``threading.Lock()``
mirroring the stdlib server's process-global ``LOCK``.

``render_workspace_report`` is imported as a module-level name (reached via this
module's namespace) so tests can monkeypatch
``misc_mutations.render_workspace_report`` with a fake. ``feedback_import``
keeps the pbg writer import LOCAL to the function (matching the handler) so the
``ImportError`` fallback path stays reproducible.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from vivarium_dashboard.lib.report import render_workspace_report
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths

# Mirrors the stdlib server's process-global ``LOCK`` — serialises concurrent
# appends to the events log so interleaved writers can't corrupt a JSON line.
_CLICK_LOCK = threading.Lock()


def record_click(ws_root: Path, body: Any) -> None:
    """POST /api/click — append the body as a JSON line to the events log.

    Port of ``_post_click``: under the module lock, ensure
    ``<ws>/.pbg/server/state/events`` exists and append ``json.dumps(body) +
    "\\n"``.  Returns ``None``; the route emits a RAW empty ``204 No Content``.
    """
    with _CLICK_LOCK:
        events = WorkspacePaths.load(ws_root).pbg / "server" / "state" / "events"
        events.parent.mkdir(parents=True, exist_ok=True)
        with events.open("a") as f:
            f.write(json.dumps(body) + "\n")


def render_dashboard(ws_root: Path) -> tuple[dict, int]:
    """POST /api/render — re-render the workspace dashboard in-process.

    Port of ``_post_render``:

      * success → ``({"ok": True}, 200)``
      * any exception → ``({"error": str(e)}, 500)``

    ``render_workspace_report`` is reached via this module's namespace so tests
    can monkeypatch it.
    """
    try:
        render_workspace_report(ws_root)
        return {"ok": True}, 200
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}, 500


def feedback_import(ws_root: Path, body: Any) -> tuple[dict, int]:
    """POST /api/feedback-import — write a report-widget feedback payload.

    Port of ``_post_feedback_import``:

      * ``pbg_superpowers`` unavailable → ``({"error": "pbg-superpowers not
        available for feedback import"}, 500)``
      * ``FeedbackImportError`` → ``({"error": str(e)}, 400)``
      * any other exception → ``({"error": f"feedback import failed: {e}"}, 500)``
      * success → ``({"ok": True, "path": <ws-relative>, "n_entries": <int>},
        200)`` where ``n_entries`` sums the list-valued annotation entries.

    The pbg writer import is kept LOCAL (matching the handler) so the
    ``ImportError`` fallback path stays reproducible; tests monkeypatch
    ``pbg_superpowers.feedback_import.write_feedback_payload``.
    """
    try:
        from pbg_superpowers.feedback_import import (
            write_feedback_payload, FeedbackImportError,
        )
    except ImportError:
        return {"error": "pbg-superpowers not available for feedback import"}, 500
    try:
        target = write_feedback_payload(ws_root, body)
    except FeedbackImportError as e:
        return {"error": str(e)}, 400
    except Exception as e:  # noqa: BLE001
        return {"error": f"feedback import failed: {e}"}, 500
    anns = body.get("annotations") or {}
    n_entries = sum(len(v or []) for v in anns.values() if isinstance(v, list))
    return {
        "ok": True,
        "path": str(target.relative_to(ws_root)),
        "n_entries": n_entries,
    }, 200
