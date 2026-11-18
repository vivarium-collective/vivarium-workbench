"""Process-level memo for the discovery-engine cycle projection.

``discover.run_cycle`` is a **pure** projection (dicts in, dicts out — see the
engine's architecture doc). Many read-only workbench surfaces recompute it per
request over the same inputs: a single investigation-report build runs it through
``_build_discovery``, ``build_generation_requests`` and ``build_needs_attention``;
the ``/api/investigation-discovery``, ``/api/generation-requests``,
``/api/validation-panel`` and ``/api/needs-attention`` endpoints each run it again.

Because ``run_cycle`` is pure, its output is fully determined by its arguments, so
we can memoize it keyed by a content fingerprint of ``(inv_spec, studies, kwargs)``
(mirroring ``study_spec.read_spec_yaml_cached``, which keys by file mtime). Any
edit to the investigation or a member study changes the loaded dicts → a different
fingerprint → automatic recompute. The memo therefore cannot serve staler data
than a fresh ``run_cycle`` would: it only skips redundant identical computations.

Safety properties:
- Content-keyed: same inputs ⇒ same key ⇒ same (pure) result. Different inputs ⇒
  different key. No reliance on file paths, mtimes or engine internals.
- Deep-copied in and out: callers that mutate the returned bundle cannot corrupt
  the cache or another caller's copy.
- Graceful fallback: inputs that don't serialize cleanly (e.g. a non-stable
  kwarg) simply bypass the cache — never a wrong answer, just no speedup.
- Bounded: the cache is cleared when it exceeds ``_MAX`` distinct keys.

NOT for the interactive cycle trace (``cycle_trace.py``), which intentionally
recomputes/steps and also writes back — those call ``discover.run_cycle`` directly.
"""
from __future__ import annotations

import copy
import hashlib
import json

_CACHE: "dict[str, dict]" = {}
_MAX = 64


def _fingerprint(inv_spec, studies, kwargs) -> "str | None":
    """A stable content hash of run_cycle's arguments, or None if unserializable."""
    try:
        blob = json.dumps(
            {"inv": inv_spec, "studies": studies, "kw": kwargs},
            sort_keys=True,
            default=str,
            ensure_ascii=False,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()


def cached_run_cycle(discover_mod, inv_spec, studies, **kwargs):
    """``discover_mod.run_cycle(inv_spec, studies, **kwargs)`` with a process-level
    memo keyed by the content of the arguments. Returns a deep copy the caller may
    freely mutate. Use only for read-only projection surfaces.
    """
    key = _fingerprint(inv_spec, studies, kwargs)
    if key is None:
        return discover_mod.run_cycle(inv_spec, studies, **kwargs)
    hit = _CACHE.get(key)
    if hit is not None:
        return copy.deepcopy(hit)
    result = discover_mod.run_cycle(inv_spec, studies, **kwargs)
    if len(_CACHE) >= _MAX:
        _CACHE.clear()
    _CACHE[key] = copy.deepcopy(result)
    return copy.deepcopy(result)


def clear_cache() -> None:
    """Drop all memoized cycles (tests; or a belt-and-braces invalidation hook)."""
    _CACHE.clear()
