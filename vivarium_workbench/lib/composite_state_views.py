"""Composite-state builder for ``GET /api/composite-state`` (library seam).

The HTTP-free worker behind the Composite Explorer's state route:

  * ``GET /api/composite-state?ref=<id-or-path>&fresh=<bool>`` AND
  * ``GET /api/composite-state/<ref>.json`` (the loom's static ``?stateUrl=`` form)
    → :func:`build_composite_state`

It mirrors the legacy ``server._get_composite_state`` handler EXACTLY (status
codes + body shapes): a ``@composite_generator`` build run in a fresh
subprocess (its own main thread — some composite deps call ``signal.signal()``
at import, which only works in the main thread), a robust static-state
fallback when a live build fails, then dotted-spec / workspace-relative /
static path resolution, else a structured 404.

Pure ``ws_root``-parameterised functions: NO ``import server`` — crucially the
EMBEDDED SUBPROCESS SCRIPT no longer imports ``vivarium_workbench.server``
either (it does ``sys.path.insert(0, sys.argv[1])`` directly), so this seam is
flip-ready.  The stdlib ``vivarium_workbench.server`` keeps thin shims that
delegate here.  The FastAPI app imports this module directly.

Caching: this module owns :data:`_COMPOSITE_STATE_CACHE`, DISJOINT from
``lib.observables_views._OBS_CACHE``.  :func:`clear_cache` is wired into
``server._invalidate_workspace_caches`` so a workspace switch clears it.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from vivarium_workbench.lib import process_docs

# Cache of built composite-state payloads, keyed by ``(ws_root, ref)``:
# {ref: (built_at_epoch, payload_dict)}. Building a whole-cell composite is
# ~1s+ (run in a subprocess), so repeat explorer opens + pop-outs are cached.
# Short TTL so code edits are picked up. 16-entry cap. EXCLUSIVE to this route
# (observables owns its own _OBS_CACHE as of Batch 8).
_COMPOSITE_STATE_CACHE: dict = {}
_COMPOSITE_STATE_TTL_S = 300.0  # seconds


def clear_cache() -> None:
    """Clear the composite-state build cache (called on workspace switch)."""
    _COMPOSITE_STATE_CACHE.clear()


def composite_state_via_subprocess(ws_root: Path, ref: str) -> "dict | None":
    """Build a generator composite's state in the workspace's **env worker**.

    ``build_generator`` (and the discovery that primes it) must import
    composite-specific workspace deps and call ``build_generator`` — work that
    belongs in the session's env worker (``docs/env-worker-protocol.md``), not
    the HTTP process. So this routes ``resolve_composite_state{ref}`` to the warm
    pool; the worker builds on the *workspace's own interpreter* (correct for a
    workspace like v2ecoli that pins a Python the workbench can't run) and does
    the ``summarize_large_values`` + ``attach_process_docs`` decoration there,
    before the (numpy-free) JSON crosses back. Returns one of:
      {"state": <doc>, "module": <str>, "emitters": [...]}  on success (already
                                          summarized + docs; "emitters" is the
                                          registered entry's declared
                                          ``emitters=[...]`` decl list, or [])
      {"__build_error__": <str>, "emitters": [...]}  generator found but build
                                          raised (emitters still resolved —
                                          entry lookup happens before the build)
      {"__not_registered__": true}        ref is not a registered generator
      None                                the worker itself was unavailable

    (Historically this spawned a one-off ``sys.executable`` subprocess with an
    embedded script; the pooled worker replaces that — warm, correct-interpreter,
    and session-isolated — while keeping this exact return contract so
    ``build_composite_state``'s branch logic is unchanged.)
    """
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
    from vivarium_workbench.lib.env_worker_pool import get_pool

    try:
        return get_pool().call(ws_root, "resolve_composite_state", {"ref": ref})
    except EnvWorkerUnavailable:
        return None


def build_composite_state(
    ws_root: Path, ref: str, *, fresh: bool = False
) -> "tuple[dict, int]":
    """GET /api/composite-state worker — returns ``(payload_dict, status)``.

    Mirrors the legacy ``server._get_composite_state`` branch logic EXACTLY:

    - **no ref** → 400 ``{"error": "ref required"}``.
    - **generator branch** (subprocess returns ``{state, module}``) → 200
      ``{state, kind: "generator", module}`` (cached).
    - **build-error → static fallback** (subprocess returns ``{__build_error__}``):
      if ``reports/composite-state/<ref>.json`` exists, load + ``attach_process_docs``
      the inner state → 200 ``{state, kind: "static-fallback", note}`` (cached);
      else 400 ``{"error": "generator build failed: <e>"}``.
    - **spec/path resolution** (``__not_registered__`` / subprocess failure):
      resolve via ``find_composite_path``, then workspace-relative ``ws_root/ref``,
      then static ``reports/composite-state/<ref>.json``; parse (json if ``.json``
      else yaml) + ``attach_process_docs`` → 200 ``{state, kind: "spec"}``; parse
      failure → 500 ``{"error": "parse failed: <e>"}``.
    - **nothing resolves** → 404 ``{"error": "composite not found: ... ", "unresolved": true, "ref": ref}``.

    A TTL cache keyed by ``(ws_root, ref)`` (16-entry cap) is checked first; ``fresh=True``
    bypasses it and a cache hit adds ``"cached": True``.
    """
    ref = (ref or "").strip()
    if not ref:
        return {"error": "ref required"}, 400

    ws_root = Path(ws_root)

    # Building a whole-cell composite (build_generator) takes ~3s and is re-run
    # on every explorer open / pop-out. Checked FIRST so a hit skips the
    # per-request sys.path + subprocess setup entirely. Bypass with ?fresh=1.
    cache = _COMPOSITE_STATE_CACHE
    ws_str = str(ws_root)
    # Key by (workspace, ref): the same ref resolves to a DIFFERENT composite
    # in a different workspace, so a bare ``ref`` key would serve one session's
    # state to another under multi-session (slice 3 of the multi-workspace
    # refactor). data_sources/observables/readouts/report_views already key by
    # ws_root; this closes the composite-state hole.
    ckey = (ws_str, ref)
    if not fresh:
        hit = cache.get(ckey)
        if hit is not None and (time.time() - hit[0]) < _COMPOSITE_STATE_TTL_S:
            return {**hit[1], "cached": True}, 200

    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)

    # Generator-kind branch: build in a SUBPROCESS (its own main thread).
    res = composite_state_via_subprocess(ws_root, ref)
    if res is not None and "state" in res:
        state_doc = res["state"]
        _embed_declared_emit_paths(state_doc, res.get("emitters"))
        payload = {"state": state_doc, "kind": "generator", "module": res.get("module")}
        cache[ckey] = (time.time(), payload)
        if len(cache) > 16:  # cap memory; drop the oldest entry
            cache.pop(next(iter(cache)))
        return payload, 200
    if res is not None and "__build_error__" in res:
        # ROBUST FALLBACK: a live build can fail for environmental reasons
        # (e.g. a stale ParCa cache missing 'tf_ids') even when the composite
        # is valid — serve the pre-generated static state if it exists.
        e = res["__build_error__"]
        _static = ws_root / "reports" / "composite-state" / (ref + ".json")
        if _static.is_file():
            try:
                _doc = json.loads(_static.read_text(encoding="utf-8"))
                _inner = _doc.get("state", _doc) if isinstance(_doc, dict) else _doc
                _inner = process_docs.attach_process_docs_via_worker(ws_root, _inner)
                # The subprocess resolved `entry` before the build failed, so
                # its declared emitters are still authoritative here (more
                # accurate than trusting a possibly-stale static artifact).
                _embed_declared_emit_paths(_inner, res.get("emitters"))
                _payload = {"state": _inner, "kind": "static-fallback",
                            "note": f"served pre-generated state (live build failed: {e})"}
                cache[ckey] = (time.time(), _payload)
                return _payload, 200
            except Exception:
                pass
        return {"error": f"generator build failed: {e}"}, 400
    # __not_registered__ or subprocess failure → fall through to path resolution.

    path = None
    # Try to resolve as a dotted spec ID via composite_lookup.
    try:
        from vivarium_workbench.lib.composite_lookup import find_composite_path
        ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
        pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))
        found = find_composite_path(ws_root, pkg, ref)
        if found is not None:
            path = found
    except Exception:
        pass

    # Fall back to workspace-relative path.
    if path is None:
        candidate = ws_root / ref
        if candidate.is_file():
            path = candidate

    # ROBUST: a pre-generated static composite-state (incl. alias forms a study
    # ref uses, e.g. `baseline` or `...baseline_millard`).
    if path is None:
        _static = ws_root / "reports" / "composite-state" / (ref + ".json")
        if _static.is_file():
            path = _static

    if path is None or not path.is_file():
        # Honest, structured degrade payload so the loom / Composites view can
        # render "composite not found / not a registered composite". ``unresolved``
        # is the machine-readable flag the client keys on.
        return {
            "error": (f"composite not found: {ref} — not a registered composite "
                      "(this study may not declare a real composite)"),
            "unresolved": True,
            "ref": ref,
        }, 404

    try:
        text = path.read_text(encoding="utf-8")
        doc: Any = json.loads(text) if path.suffix.lower() == ".json" else (yaml.safe_load(text) or {})
    except Exception as e:  # noqa: BLE001
        return {"error": f"parse failed: {e}"}, 500

    doc = process_docs.attach_process_docs_via_worker(ws_root, doc)  # per-process docstrings for the inspector
    # This branch's `doc` is either a raw composite-spec file (top-level
    # `state:`/`emitters:` keys, e.g. a `.composite.yaml`) or an already
    # resolve()-shaped static snapshot (top-level `state:` nested one level,
    # same as `reports/composite-state/<id>.json`) — either way the emit
    # declarations live at `doc["emitters"]` and the tree to embed into is
    # `doc["state"]`.
    if isinstance(doc, dict) and isinstance(doc.get("state"), dict):
        _embed_declared_emit_paths(doc["state"], doc.get("emitters"))
    return {"state": doc, "kind": "spec"}, 200


def _embed_declared_emit_paths(state_doc: Any, decls: "list | None") -> None:
    """Embed the composite's declared emit-all paths INSIDE ``state_doc``.

    Mutates ``state_doc`` in place, adding a top-level ``_declared_emit_paths``
    key when ``decls`` (an ``emitters=[...]`` decl list, e.g. from
    ``emitter_defaults``/``spec.emitters``) yields a non-empty path set.
    No-op when ``state_doc`` isn't a dict or nothing is declared.

    Nested INSIDE the state tree (not a sibling field on the response
    payload) on purpose: every client hop that carries this document onward
    — the dashboard's ``composite:load`` postMessage (``msg.state``), the
    ``?stateUrl=`` static-snapshot fetch, and the ``?composite=`` URL param —
    forwards only the ``state`` sub-object, dropping payload-level siblings
    like ``kind``/``module``/``emitters``. Loom's ``convert.ts:
    declaredEmitPaths`` reads this same key back out.
    """
    if not isinstance(state_doc, dict):
        return
    from vivarium_workbench.lib.composite_resolve import declared_emit_paths
    declared = declared_emit_paths(decls)
    if declared:
        state_doc["_declared_emit_paths"] = declared


# Register this module's cache-clear with the active-workspace registry so a
# workspace switch invalidates it via active_workspace.invalidate().
from . import active_workspace as _aw  # noqa: E402
_aw.register_clear_cb(clear_cache)
