"""JSON serialization fallbacks extracted from server.py.

``_json_default`` is the ``json.dumps(default=...)`` fallback used across the
stdlib server (``_json_body``/``_json_sanitize`` and several embedded
subprocess scripts) AND by the FastAPI-ported builders that serialise a
pre-built composite state into a ``python -c`` child script.  It lives here so
those lib builders can import it without an ``import server`` (which would pull
the whole stdlib handler module into the pure lib layer).

The server keeps a thin name-shim (``_json_default = json_serialize._json_default``)
so its existing call-sites stay byte-identical.  ``_structured_array_to_json``
is ``_json_default``'s private leaf dependency and moves with it.
"""

from __future__ import annotations

from pathlib import Path


def _structured_array_to_json(o: object) -> object | None:
    """Serialize a NumPy structured array preserving its field names; else None.

    - With an ``id`` field (bulk molecules): an ``{id: count}`` map when a
      ``count`` field exists, otherwise ``{id: {other fields}}``.
    - Otherwise (unique molecules, etc.): a list of ``{field: value}`` records.

    Returns None for anything that isn't a 1-D+ structured array, so the caller
    falls through to its normal handling.
    """
    names = getattr(getattr(o, "dtype", None), "names", None)
    if not names or getattr(o, "ndim", 0) < 1:
        return None
    try:
        rows = o.tolist()  # type: ignore[attr-defined]  # list of per-row tuples
        records = [dict(zip(names, row)) for row in rows]
    except Exception:
        return None
    if "id" in names:
        if "count" in names:
            return {str(r["id"]): r["count"] for r in records}
        return {str(r["id"]): {k: v for k, v in r.items() if k != "id"} for r in records}
    return records


def _json_default(o: object) -> object:
    """JSON serialization fallback for objects json.dumps can't handle natively.

    Handles numpy arrays (which @composite_generator state docs often contain
    for spatial / field-based composites), numpy scalars, Path objects, sets,
    and anything with .tolist(). Falls back to repr() so a bad object still
    surfaces a string rather than killing the whole response.
    """
    # NumPy STRUCTURED array (a dtype with named fields, e.g. a bulk-molecule
    # array `(id, count, …submasses)` or a unique-molecule array `(unique_index,
    # domain_index, …)`). A plain `.tolist()` degrades each row to a positional
    # tuple, dropping the field names — which is why viewers render these stores
    # as meaningless 0,1,2,… indices. Preserve the field names so any consumer
    # shows real labels: an array with an `id` field becomes an {id: count} (or
    # {id: record}) map; otherwise a list of field-keyed records.
    structured = _structured_array_to_json(o)
    if structured is not None:
        return structured

    # numpy duck-typing without importing numpy (cheaper boot)
    tolist = getattr(o, "tolist", None)
    if callable(tolist):
        try:
            return tolist()
        except Exception:
            pass
    if hasattr(o, "item") and callable(o.item):
        try:
            return o.item()  # type: ignore[attr-defined]  # numpy scalar → python scalar
        except Exception:
            pass
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    if isinstance(o, Path):
        return str(o)
    return repr(o)
