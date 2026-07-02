"""Pure builder for the ``POST /api/visualization-preview-instance`` route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_visualization_preview_instance``.  It previews a
``workspace.yaml``-registered visualization instance BY NAME: it looks up the
entry, and either

  * returns a description-only **stub** when the entry has no ``class`` (one of
    three ``status_block`` variants depending on whether a generated response
    (``.pbg/viz-responses/<name>.py``) or a pending request
    (``.pbg/viz-requests/<name>.md``) exists), or
  * delegates to ``viz_preview_views.visualization_preview`` with
    ``{"address": f"local:{cls}", "config": …, "source": …}`` when the entry
    has a class.

NO network, NO subprocess.  The builder returns ``(body, status)`` so the
FastAPI route wraps every path in ``JSONResponse``: only a missing ``name`` is
400 and an unregistered instance is 404; the stub path is 200 and the has-class
path returns whatever ``viz_preview_views.visualization_preview`` returns.

``viz_preview_views`` is referenced at MODULE level so tests can monkeypatch
``viz_preview_instance_views.viz_preview_views.visualization_preview`` with a
canned ``(dict, 200)`` and never touch a real viz library.  The handler's
``WORKSPACE``/``workspace_paths()`` become ``WorkspacePaths.load(ws_root)`` /
``ws_root / "workspace.yaml"``.  No ``import server`` here (no ``lib → server``
edge).
"""

from __future__ import annotations

import html as _html
from pathlib import Path

import yaml

from vivarium_dashboard.lib import viz_preview_views
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def visualization_preview_instance(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Preview a ``workspace.yaml``-registered visualization instance by name.

    Behaviour-preserving port of ``_post_visualization_preview_instance``.
    Body: ``{name, source?}``.

    Returns ``(response_dict, code)``:

      * missing name        → ``({"error": "name is required"}, 400)``
      * not registered      → ``({"error": f"visualization '<name>' not
        registered"}, 404)``
      * description-only     → ``({"ok": True, "html": <stub>, "source_used":
        "stub", "notes": …}, 200)``
      * has class           → ``viz_preview_views.visualization_preview(ws_root,
        {"address": f"local:{cls}", "config": …, "source": …})``
    """
    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}, 400
    try:
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    except Exception:
        ws_data = {}
    entry = next(
        (v for v in (ws_data.get("visualizations") or [])
         if isinstance(v, dict) and v.get("name") == name),
        None,
    )
    if not entry:
        return {"error": f"visualization '{name}' not registered"}, 404
    cls = (entry.get("class") or "").strip()
    if not cls:
        # Description-only entry — there's no class to render against demo
        # data. Show a friendly stub instead of erroring out, so the user
        # sees what's there and what to do next.
        desc = entry.get("description") or "(no description)"
        resp_path = WorkspacePaths.load(ws_root).pbg / "viz-responses" / f"{name}.py"
        req_path = WorkspacePaths.load(ws_root).pbg / "viz-requests" / f"{name}.md"
        if resp_path.is_file():
            status_block = (
                '<p style="margin:8px 0;color:#1f7a3a">'
                '<strong>Code generated</strong> at <code>.pbg/viz-responses/'
                + name + '.py</code>. '
                'It hasn\'t been added to the project yet — use the '
                '<strong>Add to project</strong> button on this row to stage it.'
                '</p>'
            )
        elif req_path.is_file():
            status_block = (
                '<p style="margin:8px 0;color:#b45309">'
                '<strong>Request pending</strong>. A <code>/pbg-viz</code> request '
                'has been written to <code>.pbg/viz-requests/' + name + '.md</code> '
                'but no response file exists yet.<br>'
                'In your Claude Code session, run <code>/pbg-viz ' + name + '</code> '
                'and wait for it to write <code>.pbg/viz-responses/' + name + '.py</code>.'
                '</p>'
            )
        else:
            status_block = (
                '<p style="margin:8px 0;color:#555">'
                'This is a <strong>description-only</strong> visualization — '
                'no class is configured and no code has been generated yet. '
                'To make it renderable:'
                '<ol style="margin:6px 0 0 18px">'
                '<li>Click <strong>Create</strong> on this row to write a '
                '<code>/pbg-viz</code> request.</li>'
                '<li>In your Claude Code session, run <code>/pbg-viz '
                + name + '</code>.</li>'
                '<li>When the skill writes <code>.pbg/viz-responses/'
                + name + '.py</code>, click <strong>Add to project</strong>, '
                'then <strong>Commit</strong>.</li>'
                '<li>Or — easier — re-register this entry with a '
                '<strong>Class</strong> picked from the catalog and a '
                'Config dict; that path doesn\'t need code generation.</li>'
                '</ol>'
                '</p>'
            )
        stub_html = (
            '<div style="font-family:system-ui,sans-serif;padding:8px;color:#222">'
            '<h3 style="margin:0 0 8px">' + name + '</h3>'
            '<p style="margin:0 0 8px;color:#444"><em>' + _html.escape(desc) + '</em></p>'
            + status_block +
            '</div>'
        )
        return {
            "ok": True, "html": stub_html, "source_used": "stub",
            "notes": "description-only entry; nothing to render against demo data",
        }, 200
    return viz_preview_views.visualization_preview(ws_root, {
        "address": f"local:{cls}",
        "config": entry.get("config") or {},
        "source": (body.get("source") or "demo"),
    })
