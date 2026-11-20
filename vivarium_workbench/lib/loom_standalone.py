"""Bake a composite into the single-file loom bundle → a self-contained, server-free
HTML the user can download, email, drop in a report, or host on any static site.

The single-file loom (``loom/_dist_single/index.html``, built by
``scripts/build_loom.sh`` via ``vite-plugin-singlefile`` with every JS/CSS asset
inlined) has no external references. We inject a tiny pre-script that *self-completes*
the loom's embed handshake: the loom posts ``explore:ready`` on mount (to
``window.parent`` — which is ``window`` itself for a top-level file), and this script,
listening on ``window``, answers with the ``composite:load`` message carrying the baked
state. So the composite renders with no server and no parent frame — even opened as a
double-clicked ``file://``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from vivarium_workbench import loom_assets


def single_file_template() -> "str | None":
    """The built single-file loom HTML (``loom/_dist_single/index.html``), resolved
    next to the serving ``_dist`` so a worktree inherits the main checkout's build.
    ``None`` when it has not been built (``scripts/build_loom.sh``)."""
    cand = loom_assets.asset_dir().parent / "_dist_single" / "index.html"
    try:
        return cand.read_text(encoding="utf-8") if cand.is_file() else None
    except OSError:
        return None


def standalone_loom_html(resolved: dict) -> "str | None":
    """Bake a resolved composite (the ``/api/composite-resolve`` payload: ``state``,
    ``parameters``, ``name``, …) into the single-file loom. Returns the self-contained
    HTML, or ``None`` when the single-file bundle is not built."""
    tmpl = single_file_template()
    if not tmpl:
        return None
    payload = {
        "type": "composite:load",
        "state": resolved.get("state"),
        "parameters": resolved.get("parameters") or {},
        "default_n_steps": resolved.get("default_n_steps"),
        "metadata": {"name": resolved.get("name"), "description": resolved.get("description")},
    }
    # `</` escaped so a string inside the state can never close the <script>.
    msg = json.dumps(payload, default=str).replace("</", "<\\/")
    seed = (
        "<script>(function(){var M=" + msg + ";"
        "function send(){try{window.postMessage(M,'*');}catch(e){}}"
        "window.addEventListener('message',function(e){"
        "if(e&&e.data&&e.data.type==='explore:ready')send();});"
        "window.addEventListener('load',function(){setTimeout(send,250);setTimeout(send,1200);});"
        "})();</script>"
    )
    m = re.search(r"<head[^>]*>", tmpl)
    if not m:
        return tmpl
    i = m.end()
    return tmpl[:i] + seed + tmpl[i:]
