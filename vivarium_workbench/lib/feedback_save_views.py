"""``POST /api/feedback-save`` builder — persist a collected inline-feedback
payload into the workspace feedback tree.

The study page's capture widget (``static/study-feedback.js``) lets a reviewer
annotate any region across the six study tabs. On the live server it POSTs the
collected notes here as a canonical ``{meta, annotations}`` payload; this view
hands it to ``viva_superpowers.feedback_import.write_feedback_payload``, which
writes it under ``investigations/<inv>/feedback/<ts>.yaml`` — the exact path the
dashboard and the ``/viva-*`` responder skills scan for expert feedback.

Rewire-first, like the other feedback endpoints: the workbench keeps importing
the plugin's writer so the shape stays single-sourced. Best-effort with clear
errors (returns ``{"saved": False, "error": ...}`` + a status code rather than
raising). In the read-only published bundle there is no backend, so the widget
never calls this — it falls back to download/copy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def feedback_save(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/feedback-save: write a ``{meta, annotations}`` feedback payload.

    Body::

        {meta: {investigation, study?, reviewer?, generated_at?, ...},
         annotations: {section_id: [{author, text, ts, ...}], ...}}

    ``meta.investigation`` decides the target folder (a payload can never escape
    its own investigation). Returns ``({saved: True, path}, 200)`` on success.

    - 400 ``{saved: False, error: "meta.investigation required"}`` — missing it.
    - 400 ``{saved: False, error: "annotations required"}`` — nothing to save.
    - 404 ``{saved: False, error: ...}`` — the investigation dir doesn't exist.
    - 500 ``{saved: False, error: ...}`` — plugin missing / write failure.
    """
    body = body or {}
    meta = body.get("meta") or {}
    annotations = body.get("annotations") or {}

    if not isinstance(meta, dict) or not str(meta.get("investigation") or "").strip():
        return {"saved": False, "error": "meta.investigation required"}, 400
    if not isinstance(annotations, dict) or not annotations:
        return {"saved": False, "error": "annotations required"}, 400

    try:
        from viva_superpowers.feedback_import import (
            write_feedback_payload,
            FeedbackImportError,
        )
    except ImportError as e:  # noqa: BLE001
        return {"saved": False, "error": f"feedback-save requires viva-superpowers: {e}"}, 500

    payload: dict[str, Any] = {"meta": meta, "annotations": annotations}

    try:
        target = write_feedback_payload(Path(ws_root), payload)
    except FeedbackImportError as e:
        msg = str(e)
        status = 404 if "does not exist" in msg else 400
        return {"saved": False, "error": msg}, status
    except Exception as e:  # noqa: BLE001 — never a bare 500 with no message
        return {"saved": False, "error": f"save failed: {e}"}, 500

    return {"saved": True, "path": str(target)}, 200
