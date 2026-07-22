"""Worker-backed viz-render hooks for the HTTP-process render paths.

The study/investigation report render needs the workspace's **live** Visualization
classes (`build_viz_composite` reads each class's declared inputs) and a live
`process_bigraph.Composite` run — workspace Python that must not run in the HTTP
process. This provides the two seams `render_visualizations` needs, both backed by
the env worker (`viz_class_inputs` + `render_viz_doc`, env-worker-protocol §11):

  * ``inputs_by_class`` — ``{viz_class: declared_inputs}`` so ``build_viz_composite``
    can assemble a viz doc without holding the class object;
  * ``build_and_run(doc, _registry=None) -> html`` — renders one viz doc to HTML in
    the worker (``Composite({'state': doc}, core).run(1)``).

Best-effort: an unavailable worker yields an empty inputs map (every viz then
surfaces render_visualizations' per-viz error stub) and empty HTML — never a crash.
"""
from __future__ import annotations

from pathlib import Path


def viz_render_hooks(ws_root: Path) -> "tuple[dict, object]":
    """Return ``(inputs_by_class, build_and_run)`` for a workspace, backed by its
    env worker — so the HTTP process builds no core and imports no viz classes."""
    from vivarium_workbench.lib.env_worker_pool import get_pool

    try:
        r = get_pool().call(ws_root, "viz_class_inputs")
        inputs_by_class = r.get("inputs", {}) if isinstance(r, dict) else {}
    except Exception:  # noqa: BLE001 — best-effort; empty map → per-viz error stubs
        inputs_by_class = {}

    def build_and_run(doc, _registry=None):
        try:
            r = get_pool().call(ws_root, "render_viz_doc", {"viz_doc": doc})
            return r.get("html", "") if isinstance(r, dict) else ""
        except Exception:  # noqa: BLE001 — a render failure is a stub, not a crash
            return ""

    return inputs_by_class, build_and_run
