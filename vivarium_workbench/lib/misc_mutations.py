"""Pure builders for 2 miscellaneous workspace POST routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_click`` / ``_post_render``.
Both are workspace-scoped (they take a ``ws_root``) and do only local FS
work / an in-process render — no subprocess, no network, no in-memory manager.
No ``import server`` here.

Return contract (mirrors the other ``lib.*_mutations`` modules):

  * ``record_click(ws_root, body) -> None`` — pure side-effect (FS append); the
    FastAPI route turns this into a RAW empty ``204 No Content`` (no JSON body),
    byte-matching the legacy ``send_response(204)``.
  * ``render_dashboard(ws_root) -> (dict, int)`` — the route wraps every
    path (success AND error) in ``JSONResponse`` so the lib-returned status code
    is preserved verbatim.

``record_click`` serialises its appends with a MODULE-LEVEL ``threading.Lock()``
mirroring the stdlib server's process-global ``LOCK``.

``render_workspace_report`` is imported as a module-level name (reached via this
module's namespace) so tests can monkeypatch
``misc_mutations.render_workspace_report`` with a fake.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from vivarium_workbench.lib.report import render_workspace_report
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

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


def render_dashboard(ws_root: Path, body: Any = None) -> tuple[dict, int]:
    """POST /api/render — re-render the workspace dashboard in-process.

    Phase 2.1d — ``body`` (all fields optional; ``None``/``{}`` preserves the
    previous unconditional-render behavior) mirrors the old ``/viva-report``
    skill's CLI render usage (``viva_superpowers.report.render_workspace_report``)
    so the skill can call this endpoint as a thin client instead of
    reimplementing the render:

      * ``today`` — forwarded to ``render_workspace_report`` as the render
        date, for a byte-stable CI render (the old ``--today`` option). Only
        passed through when truthy, so a monkeypatched/legacy
        ``render_workspace_report(ws_root)`` (no ``today`` kwarg) still works
        for the no-body call.
      * ``force`` — mirrors the old skill's forced-render path
        (``render_workspace_report(force=True, on_force_log_overrides=True)``):
        BEFORE rendering, every currently-blocking (error-level,
        not-yet-overridden) ``report_linter`` finding is logged to
        ``.pbg/report-lint-overrides.json`` — see :func:`_log_force_overrides`.

    Port of ``_post_render``:

      * success → ``({"ok": True}, 200)``
      * any exception → ``({"error": str(e)}, 500)``

    ``render_workspace_report`` is reached via this module's namespace so tests
    can monkeypatch it.
    """
    body = body if isinstance(body, dict) else {}
    try:
        if body.get("force"):
            _log_force_overrides(ws_root)
        kwargs: dict = {}
        today = body.get("today")
        if today:
            kwargs["today"] = today
        render_workspace_report(ws_root, **kwargs)
        return {"ok": True}, 200
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}, 500


def _log_force_overrides(ws_root: Path) -> None:
    """Best-effort port of the plugin's forced-render override logging.

    Mirrors ``viva_superpowers.report.render_workspace_report(force=True,
    on_force_log_overrides=True)``: run the structural linter and, for every
    currently error-level finding not already logged in
    ``.pbg/report-lint-overrides.json``, append it via
    ``report_linter.write_override``. Reuses each finding's own
    ``override_key`` (itself derived by ``report_linter._override_key`` at
    lint time) — the formula is never re-derived here.

    Tolerant of an absent/older ``viva_superpowers`` or a workspace-scan
    failure: this is a logging side-effect and must never be the reason a
    render request fails.
    """
    try:
        from discovery_engine.report_linter import (
            lint_workspace_report,
            load_overrides,
            write_override,
        )
    except Exception:  # noqa: BLE001
        return
    try:
        findings = lint_workspace_report(ws_root)
        overrides = load_overrides(ws_root)
    except Exception:  # noqa: BLE001
        return
    for f in findings:
        if f.level == "error" and f.override_key not in overrides:
            try:
                write_override(
                    ws_root, f,
                    reason="force-published via POST /api/render (force=true)",
                )
            except Exception:  # noqa: BLE001
                pass
