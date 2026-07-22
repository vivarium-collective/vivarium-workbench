"""Pure builder for the ``POST /api/visualization-preview`` route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_visualization_preview``.  It renders a ``pbg_superpowers``
Visualization class — instantiating the class and calling ``.update()`` (NO
subprocess) against either synthetic demo data or an existing investigation's
emitter outputs, and returns ``{ok, html, source_used, notes}``.

The class-touching render (resolve the class, demo/streaming render, bare-instance
``.update()``) runs in the **env worker** (``viz_preview``, env-worker-protocol §11)
— never in the HTTP process, which imports no viz classes and builds no core. This
builder is the thin HTTP-side orchestrator: it validates the request, and for an
``investigation:<name>`` source it assembles the ``inputs_store`` HTTP-side
(``gather_emitter_outputs`` reads the run SQLite and ``build_viz_composite`` maps
observables → inputs — both are workbench code, and ``build_viz_composite`` runs in
its worker-provided ``inputs_by_class`` mode so it needs no live class), then hands
that store to the worker for the render.

The builder returns ``(body, status)`` so the FastAPI route wraps every path in
``JSONResponse``.  Only a missing ``address`` is 400 and an unregistered class is
404; EVERY other path — including a demo render that RAISES — returns **200** (the
failure case as ``{"ok": False, ...}``), byte-identical to the live handler. If the
env worker cannot start, the render soft-degrades to a 200 error stub (never a 500).
"""

from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib import study_spec


def visualization_preview(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Render a viz against demo data or an investigation's emitter outputs.

    Behaviour-preserving port of ``_post_visualization_preview``.  Body:

        address: 'local:<Class>' (required) — the Visualization class to render
        config:  {} — config dict (used for both demo and investigation paths)
        source:  'demo' | 'investigation:<name>' (default 'demo')

    Returns ``(response_dict, code)``:

      * missing address      → ``({"error": "address is required"}, 400)``
      * class not registered → ``({"error": f"class not registered: …"}, 404)``
      * investigation render → ``({"ok": True, "html", "source_used":
        "investigation:<name>", "notes"}, 200)`` (falls back to demo on no
        runs.db / empty html / render/assemble exception, appending a note)
      * demo render          → ``({"ok": True, "html", "source_used": "demo",
        "notes"}, 200)``
      * demo render FAILURE  → ``({"ok": False, "html": "<…demo render
        failed…>", "source_used": "demo", "notes"}, 200)`` (still 200)
      * env worker down      → ``({"ok": False, "html": "<…unavailable…>",
        "source_used": <source>, "notes"}, 200)`` (still 200)
    """
    address = (body.get("address") or "").strip()
    if not address:
        return {"error": "address is required"}, 400
    config = body.get("config") or {}
    source = (body.get("source") or "demo").strip()

    # Investigation source: assemble the inputs_store HTTP-side (workbench code).
    # A missing runs.db / assemble failure records the fallback note and leaves the
    # store None so the worker renders demo — matching the old in-process fallbacks.
    note_prefix: list[str] = []
    investigation_inputs_store = None
    if source.startswith("investigation:"):
        inv_name = source.split(":", 1)[1].strip()
        inv_dir = study_spec.study_dir(ws_root, inv_name)
        runs_db = inv_dir / "runs.db"
        if not runs_db.is_file():
            note_prefix.append(f"investigation '{inv_name}' has no runs.db; falling back to demo")
        else:
            try:
                from vivarium_workbench.lib.investigations import (
                    gather_emitter_outputs, build_viz_composite,
                )
                from vivarium_workbench.lib.viz_render import viz_render_hooks
                gathered = gather_emitter_outputs(runs_db)
                inputs_by_class, _build_and_run = viz_render_hooks(ws_root)
                viz_spec = {"name": "preview", "address": address, "config": dict(config)}
                doc = build_viz_composite(
                    viz_spec, gathered, {}, inputs_by_class=inputs_by_class)
                investigation_inputs_store = dict(doc.get("inputs_store") or {})
            except Exception as e:  # noqa: BLE001
                note_prefix.append(
                    f"investigation render failed ({type(e).__name__}: {e}); falling back to demo")

    # Class-touching render happens in the worker (no viz import in this process).
    try:
        from vivarium_workbench.lib.env_worker_pool import get_pool
        result = get_pool().call(ws_root, "viz_preview", {
            "address": address,
            "config": config,
            "source": source,
            "investigation_inputs_store": investigation_inputs_store,
            "note_prefix": note_prefix,
        })
    except Exception as e:  # noqa: BLE001 — worker down → 200 stub, never a 500
        return {
            "ok": False,
            "html": ('<p style="color:#991b1b">preview unavailable: the workspace '
                     f'environment worker could not start ({type(e).__name__})</p>'),
            "source_used": source,
            "notes": "; ".join(note_prefix),
        }, 200

    if isinstance(result, dict) and result.get("status") == "not_registered":
        return {"error": f"class not registered: {address}"}, 404
    if not isinstance(result, dict):
        return {
            "ok": False,
            "html": '<p style="color:#991b1b">preview unavailable: malformed worker response</p>',
            "source_used": source,
            "notes": "; ".join(note_prefix),
        }, 200
    return result, 200
