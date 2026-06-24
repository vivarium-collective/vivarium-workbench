"""Shared normalization helpers for study / investigation spec fields.

Extracted from ``vivarium_dashboard.server`` so both the FastAPI seam
(``api/app.py`` routes) and ``server.py``'s handlers can share one
implementation.  ``server.py`` re-imports ``normalize_requirements`` as
``_normalize_requirements`` to keep its existing call-sites unchanged.
"""

from __future__ import annotations


def normalize_requirements(value) -> list:
    """Normalize a study's ``implementation_requirements`` / ``gaps`` field to a
    list of requirement dicts the renderers can iterate safely.

    Authors write this field two ways:
      • a YAML LIST of ``{id, title, ...}`` dicts (structured) — kept as-is;
      • a multi-line PROSE STRING (``implementation_requirements: |``) — must
        NOT be iterated character-by-character (that yields 1-char strings and
        ``char.title`` resolves to the str.title *method*, rendering its repr
        e.g. ``<built-in method title of str object>`` and a bogus
        "(492 items)" count).

    Contract:
      • a STRING becomes ONE prose requirement → ``[{"_prose": True,
        "description": <text>}]`` (count 1, not the character count);
      • a LIST is kept, but bare-string items are wrapped the same prose way so
        we never access ``.id`` / ``.title`` on a non-dict;
      • empty/None → ``[]``.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [{"_prose": True, "description": text}] if text else []
    if isinstance(value, dict):
        # A single mapping authored without a list wrapper.
        return [value]
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if isinstance(item, dict):
                out.append(item)
            else:
                text = "" if item is None else str(item).strip()
                if text:
                    out.append({"_prose": True, "description": text})
        return out
    # Unknown scalar — wrap defensively rather than iterate.
    text = str(value).strip()
    return [{"_prose": True, "description": text}] if text else []
