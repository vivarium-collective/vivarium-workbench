"""Pure builder for the ``POST /api/visualization-preview`` route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_visualization_preview``.  It renders a ``pbg_superpowers``
Visualization class IN-PROCESS — instantiating the class and calling ``.update()``
(NO subprocess) — against either synthetic demo data or an existing
investigation's emitter outputs, and returns ``{ok, html, source_used, notes}``.

The builder returns ``(body, status)`` so the FastAPI route wraps every path in
``JSONResponse``.  Only a missing ``address`` is 400 and an unregistered class is
404; EVERY other path — including a demo render that RAISES — returns **200**
(the failure case as ``{"ok": False, ...}``), byte-identical to the live handler.

The three viz-core helpers the handler reached as instance methods are now the
module-level ``viz_core`` functions (the merged extraction):

  * ``self._resolve_viz_class(address)``  → ``viz_core.resolve_viz_class(ws_root, address)``
  * ``self._demo_state_for(cls, key)``    → ``viz_core.demo_state_for(cls, key)``
  * ``self._build_workspace_core()``      → ``viz_core.build_workspace_core(ws_root)``

and the handler's ``_study_dir(inv_name)`` shim → ``study_spec.study_dir(ws_root,
inv_name)``.  ``viz_core`` is referenced at module level so tests monkeypatch
``viz_preview_views.viz_core.resolve_viz_class`` / ``demo_state_for`` /
``build_workspace_core`` with a FAKE Visualization class and never touch a real
viz library.  No ``import server`` here (no ``lib → server`` edge).
"""

from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib import study_spec
from vivarium_workbench.lib import viz_core


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
        runs.db / empty html / render exception, appending a note)
      * demo render          → ``({"ok": True, "html", "source_used": "demo",
        "notes"}, 200)``
      * demo render FAILURE  → ``({"ok": False, "html": "<…demo render
        failed…>", "source_used": "demo", "notes"}, 200)`` (still 200)
    """
    address = (body.get("address") or "").strip()
    if not address:
        return {"error": "address is required"}, 400
    config = body.get("config") or {}
    source = (body.get("source") or "demo").strip()

    cls, class_key = viz_core.resolve_viz_class(ws_root, address)
    if cls is None:
        return {"error": f"class not registered: {address}"}, 404

    notes = []
    # Try investigation source first if requested.
    if source.startswith("investigation:"):
        inv_name = source.split(":", 1)[1].strip()
        inv_dir = study_spec.study_dir(ws_root, inv_name)
        runs_db = inv_dir / "runs.db"
        if not runs_db.is_file():
            notes.append(f"investigation '{inv_name}' has no runs.db; falling back to demo")
        else:
            try:
                from vivarium_workbench.lib.investigations import (
                    gather_emitter_outputs, build_viz_composite,
                )
                gathered = gather_emitter_outputs(runs_db)
                viz_spec = {
                    "name": "preview", "address": address,
                    "config": dict(config),
                }
                registry = {class_key: cls}
                doc = build_viz_composite(viz_spec, gathered, registry)
                inst = cls.__new__(cls)
                inst.config = config or {}
                html = inst.update(dict(doc.get("inputs_store") or {})).get("html", "")
                if html:
                    return {
                        "ok": True, "html": html,
                        "source_used": f"investigation:{inv_name}",
                        "notes": "; ".join(notes),
                    }, 200
                notes.append("investigation render produced empty html; falling back to demo")
            except Exception as e:
                notes.append(f"investigation render failed ({type(e).__name__}: {e}); falling back to demo")

    # Demo path (default or fallback).
    try:
        state = viz_core.demo_state_for(cls, class_key)

        # Detect streaming-style viz (all inputs are scalar types). For
        # these, feed N synthetic timesteps so the accumulator builds up a
        # meaningful trajectory. The 5 default v2 classes use list[float]
        # inputs and render in a single call; wrapper classes like
        # ReaDDyPlots/BioreactorPlots use scalar inputs and accumulate.
        scalar_types = {"float", "integer", "string", "boolean"}
        # Probe inputs without full init (bare instance is enough for inputs()).
        probe = cls.__new__(cls)
        try:
            probe.config = config or {}
        except Exception:
            pass
        declared: dict = {}
        try:
            declared = probe.inputs() or {}
        except Exception:
            pass
        is_streaming = (
            bool(declared)
            and all(t in scalar_types for t in declared.values())
            and not state
        )

        # Construct the real instance. Streaming viz typically need their
        # __init__ to run (to set up accumulator buffers), so try a proper
        # constructor with a fresh core; fall back to object.__new__ if
        # the class's signature doesn't accept (config, core).
        inst = None
        if is_streaming:
            core, _ = viz_core.build_workspace_core(ws_root)
            if core is None:
                try:
                    from bigraph_schema import allocate_core
                    core = allocate_core()
                except Exception:
                    core = None
            for ctor_args in (
                {"config": config or {}, "core": core},
                {"config": config or {}},
            ):
                try:
                    inst = cls(**ctor_args)
                    break
                except Exception:
                    continue
        if inst is None:
            inst = cls.__new__(cls)
            try:
                inst.config = config or {}
            except Exception:
                pass

        if is_streaming:
            # Synthesize 12 timesteps with smoothly-varying scalar values
            # so the accumulator has enough data to render a trajectory.
            import math
            html = ""
            for step in range(12):
                synth: dict = {}
                for port, port_type in declared.items():
                    if port_type == "float":
                        if port in ("time", "t"):
                            synth[port] = float(step) * 0.5
                        else:
                            # Smooth wave; offset per-port via hash to avoid collinear demos.
                            phase = (hash(port) & 0xff) / 40.0
                            synth[port] = 1.0 + 0.5 * math.sin(step * 0.6 + phase) + step * 0.1
                    elif port_type == "integer":
                        synth[port] = int(50 + step * 7)
                    elif port_type == "boolean":
                        synth[port] = step % 2 == 0
                    else:
                        synth[port] = f"step-{step}"
                result = inst.update(synth) or {}
                html = result.get("html", "") or html
        else:
            html = inst.update(state).get("html", "")

        if not html:
            html = (
                f'<div style="padding:20px;font-family:system-ui">'
                f'<p><strong>{class_key}</strong>: no demo state available.</p>'
                f'<p style="color:#666">Add a <code>demo()</code> classmethod to '
                f'the viz class, or register an instance in workspace.yaml and '
                f'use the Preview button on the instance row to render against '
                f'real emitter data.</p></div>'
            )
        return {
            "ok": True, "html": html, "source_used": "demo",
            "notes": "; ".join(notes),
        }, 200
    except Exception as e:
        return {
            "ok": False,
            "html": f'<p style="color:#991b1b">demo render failed: {type(e).__name__}: {e}</p>',
            "source_used": "demo",
            "notes": "; ".join(notes),
        }, 200
