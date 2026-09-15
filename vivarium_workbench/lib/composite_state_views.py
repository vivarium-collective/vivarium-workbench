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


def _is_parca_cache_error(msg: str) -> bool:
    """True when a generator build error is a missing/stale ParCa cache — the
    expected failure when building an ecoli composite with no local ``out/cache``
    (a materialized remote build ships none). Keyed on the ParCa cache-version
    guard's wording plus the plain "cache does not exist" case."""
    m = msg.lower()
    if "cache" not in m:
        return False
    return any(s in m for s in ("stale or unversioned", "unversioned",
                                "does not exist", "no such file", "tf_ids"))


# Last-known-good composite state, keyed by ``(ws_str, ref)`` with NO TTL — so a
# view survives a transient build failure (a stale ParCa cache, an env-probe
# drift, a mid-restart worker) by showing the last wiring that DID build, clearly
# labelled, instead of a hard 400 that blanks the Composites view and hides the
# Run button. Persisted per-workspace to ``.pbg/composite-state-cache/<ref>.json``
# so a server RESTART also has something to show. This is distinct from
# ``_COMPOSITE_STATE_CACHE`` (a short-TTL hot cache of fresh builds).
_LAST_GOOD: dict = {}


def clear_cache() -> None:
    """Clear the composite-state build cache (called on workspace switch)."""
    _COMPOSITE_STATE_CACHE.clear()
    _LAST_GOOD.clear()


def _last_good_path(ws_root: Path, ref: str) -> "Path | None":
    """Persisted last-good file for ``ref`` under this workspace's ``.pbg``."""
    try:
        from vivarium_workbench.lib.workspace_paths import WorkspacePaths
        safe = ref.replace("/", "_").replace(":", "_")
        return WorkspacePaths.load(ws_root).pbg / "composite-state-cache" / (safe + ".json")
    except Exception:
        return None


def _record_last_good(ws_root: Path, ref: str, payload: dict) -> None:
    """Remember a successfully-built composite state (memory + disk), best-effort."""
    key = (str(ws_root), ref)
    _LAST_GOOD[key] = payload
    if len(_LAST_GOOD) > 64:  # cap memory; drop the oldest entry
        _LAST_GOOD.pop(next(iter(_LAST_GOOD)))
    p = _last_good_path(ws_root, ref)
    if p is None:
        return
    try:
        from vivarium_workbench.lib import atomic_io
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_io.atomic_write_text(p, json.dumps(payload, default=str))
    except Exception:
        pass  # a cosmetic cache write must never break the request


def _load_last_good(ws_root: Path, ref: str) -> "dict | None":
    """Last-good state for ``ref`` — memory first, then the persisted file."""
    hit = _LAST_GOOD.get((str(ws_root), ref))
    if hit is not None:
        return hit
    p = _last_good_path(ws_root, ref)
    if p is not None and p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _build_error_kind(err_str: str) -> str:
    """Classify a generator build failure for the client's warning chip."""
    if _is_parca_cache_error(err_str):
        return "stale-cache"
    low = err_str.lower()
    if "import" in low or "module" in low:
        return "import"
    return "build-error"


def _degrade_build_error(
    ws_root: Path, ref: str, overrides: "dict | None", err: Any,
    emitters: Any, cache_key: tuple,
) -> "tuple[dict, int]":
    """A generator build failed — return a 200 degrade instead of a 400, so the
    Composites view still renders (with a warning chip) and the Run button stays
    reachable. Whether a Run is *allowed* is a separate decision the dispatch path
    makes (a cloud run needs no local cache; a local run does). Three tiers:

      1. static artifact (``reports/composite-state/<ref>.json``) — the default
         wiring; when overrides were supplied it can't reflect them, so it is
         served with ``stale_overrides: True`` and a notice (the user still sees
         their Apply did not render), rather than the old hard refusal.
      2. last-known-good — the last state that DID build (this session or a prior
         one, via the persisted file).
      3. skeleton — an honest ``wiring_status: "unavailable"`` placeholder.

    Every tier attaches ``build_error`` so the card can render the warning.
    """
    err_str = str(err)
    build_error: "dict[str, Any]" = {"kind": _build_error_kind(err_str), "detail": err_str}
    # A materialized remote build ships no local ParCa cache — keep the clearer,
    # expected-for-Cloud wording as the notice rather than a raw build trace.
    if _is_parca_cache_error(err_str):
        try:
            from vivarium_workbench.lib.remote_simulations import _read_build_meta
            _meta = _read_build_meta(ws_root)
        except Exception:
            _meta = None
        if _meta is not None:
            _sim = _meta.get("simulator_id")
            _commit = str(_meta.get("commit") or "")[:7]
            who = (f"remote build #{_sim}" if _sim is not None else "this remote build") \
                + (f" @ {_commit}" if _commit else "")
            build_error["notice"] = (
                f"{who} has no local ParCa cache, so its wiring preview can't be built "
                f"here — run it on the Cloud, or provision a local out/cache.")
            build_error["remote_no_cache"] = True

    def _finish(payload: dict) -> "tuple[dict, int]":
        payload["build_error"] = build_error
        _COMPOSITE_STATE_CACHE[cache_key] = (time.time(), payload)
        if len(_COMPOSITE_STATE_CACHE) > 16:
            _COMPOSITE_STATE_CACHE.pop(next(iter(_COMPOSITE_STATE_CACHE)))
        return payload, 200

    # Tier 1: static artifact.
    static = ws_root / "reports" / "composite-state" / (ref + ".json")
    if static.is_file():
        try:
            doc = json.loads(static.read_text(encoding="utf-8"))
            inner = doc.get("state", doc) if isinstance(doc, dict) else doc
            inner = process_docs.attach_process_docs_via_worker(ws_root, inner)
            _embed_declared_emit_paths(inner, emitters)
            note = ("served pre-generated default wiring (live build failed: "
                    f"{err_str})")
            payload = {"state": inner, "kind": "static-fallback", "note": note}
            if overrides:
                # The artifact is the UNOVERRIDDEN default; say so, so the user
                # sees their Config → Apply did not render (the old code refused
                # outright here, blanking the view).
                payload["stale_overrides"] = True
                payload["note"] = ("Config → Apply could not be rendered locally "
                                   f"({err_str}); showing DEFAULT wiring.")
            return _finish(payload)
        except Exception:
            pass

    # Tier 2: last-known-good.
    lg = _load_last_good(ws_root, ref)
    if lg is not None and isinstance(lg.get("state"), (dict, list)):
        payload = {"state": lg["state"], "kind": "last-good",
                   "note": ("showing the last wiring that built successfully "
                            f"(current build failed: {err_str})")}
        if overrides:
            payload["stale_overrides"] = True
        return _finish(payload)

    # Tier 3: honest skeleton — never a 400 for a build failure.
    notice = build_error.get("notice") or (
        f"wiring preview is unavailable (build failed: {err_str})")
    payload = {"state": None, "kind": "skeleton", "wiring_status": "unavailable",
               "notice": notice}
    return _finish(payload)


def composite_state_via_subprocess(
    ws_root: Path, ref: str, overrides: "dict | None" = None
) -> "dict | None":
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
        return get_pool().call(
            ws_root, "resolve_composite_state",
            {"ref": ref, "overrides": overrides or {}},
        )
    except EnvWorkerUnavailable:
        return None


def inner_composite_state_via_subprocess(
    ws_root: Path, ref: str, hops: "list[list[str]]",
    overrides: "dict | None" = None,
) -> "dict | None":
    """Drill into a Composite Process via the workspace env worker.

    Routes ``resolve_inner_composite_state{ref, hops, overrides}`` to the warm
    pool, which instantiates the generator ``ref`` (WITH ``overrides`` so the
    root's config-applied wiring — e.g. the batch's ``batch_runner`` — is present
    for ``hops`` to navigate), walks ``hops`` (a list of node paths) into
    successive inner composites, and returns the innermost composite's loom
    state. Returns ``{"state": <doc>, "crumbs": [...]}`` on success, a sentinel
    (``{"__not_registered__"}`` / ``{"__error__"}`` / ``{"__build_error__"}``),
    or ``None`` when the worker is unavailable. Mirrors
    ``composite_state_via_subprocess``'s contract."""
    from vivarium_workbench.lib.env_worker_client import EnvWorkerUnavailable
    from vivarium_workbench.lib.env_worker_pool import get_pool

    try:
        return get_pool().call(
            ws_root, "resolve_inner_composite_state",
            {"ref": ref, "hops": hops, "overrides": overrides or {}})
    except EnvWorkerUnavailable:
        return None


def build_inner_composite_state(
    ws_root: Path, ref: str, hops: "list[list[str]]",
    overrides: "dict | None" = None,
) -> "tuple[dict, int]":
    """GET /api/composite-inner-state worker — ``(payload_dict, status)``.

    ``ref`` is the ROOT generator id; ``hops`` is the accumulated drill path
    (list of node-path segment lists). Returns 200 ``{state, kind:
    "inner", crumbs}`` on success; 400 on a bad hop / non-composite node or a
    build error; 404 when ``ref`` is not a registered generator; 503 when the
    env worker is unavailable. Result cached by ``(ws_root, ref, hops)``."""
    ref = (ref or "").strip()
    if not ref:
        return {"error": "ref required"}, 400
    ws_root = Path(ws_root)
    if overrides:
        overrides = {k: v for k, v in overrides.items() if v != ""}
    _ovkey = json.dumps(overrides or {}, sort_keys=True, default=str)
    ckey = (str(ws_root), ref, tuple(tuple(h) for h in hops), _ovkey)
    hit = _COMPOSITE_STATE_CACHE.get(ckey)
    if hit is not None and (time.time() - hit[0]) < _COMPOSITE_STATE_TTL_S:
        return {**hit[1], "cached": True}, 200

    res = inner_composite_state_via_subprocess(ws_root, ref, hops, overrides)
    if res is None:
        return {"error": "env worker unavailable"}, 503
    if "state" in res:
        payload = {"state": res["state"], "kind": "inner",
                   "crumbs": res.get("crumbs", [])}
        _COMPOSITE_STATE_CACHE[ckey] = (time.time(), payload)
        if len(_COMPOSITE_STATE_CACHE) > 16:
            _COMPOSITE_STATE_CACHE.pop(next(iter(_COMPOSITE_STATE_CACHE)))
        return payload, 200
    if res.get("__not_registered__"):
        return {"error": f"composite not found: {ref}", "unresolved": True,
                "ref": ref}, 404
    err = res.get("__error__") or res.get("__build_error__") or "drill failed"
    return {"error": err}, 400


def build_composite_state(
    ws_root: Path, ref: str, *, fresh: bool = False, overrides: "dict | None" = None
) -> "tuple[dict, int]":
    """GET /api/composite-state worker — returns ``(payload_dict, status)``.

    Mirrors the legacy ``server._get_composite_state`` branch logic EXACTLY:

    - **no ref** → 400 ``{"error": "ref required"}``.
    - **generator branch** (subprocess returns ``{state, module}``) → 200
      ``{state, kind: "generator", module}`` (cached).
    - **build-error → graceful degrade** (subprocess returns ``{__build_error__}``):
      never a 400 (a build failure is usually environmental — a stale/absent ParCa
      cache, an env-probe drift — not a broken composite, and a Cloud run needs no
      local cache). :func:`_degrade_build_error` returns 200 with a ``build_error``
      chip and the best available wiring: the static artifact
      (``reports/composite-state/<ref>.json``, ``kind: "static-fallback"``; served
      with ``stale_overrides`` when overrides couldn't render), else the
      last-known-good state (``kind: "last-good"``), else an honest skeleton
      (``kind: "skeleton"``, ``wiring_status: "unavailable"``).
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

    # An empty Configure field arrives as "" — the loom renders a parameter whose
    # default is None as a blank text input and submits that blank on resolve/Apply.
    # Treat a top-level "" as UNSET: drop it so the generator sees the parameter's
    # DEFAULT, not an explicit empty string. Without this, ecoli_baseline's batch
    # guard rejects e.g. match_simdata="" ("single-cell-only … not supported in
    # batch mode") for a param the user never set, and the entire config-applied
    # build falls back to the bare composite (injected processes vanish). Only
    # top-level blanks are dropped; nested config values are left untouched.
    if overrides:
        overrides = {k: v for k, v in overrides.items() if v != ""}

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
    # Key by (workspace, ref, overrides): the same ref under different Config
    # overrides (e.g. n_cells) resolves to a different wiring, so overrides MUST
    # be part of the cache key or an Apply would serve the unoverridden state.
    _ovkey = json.dumps(overrides or {}, sort_keys=True, default=str)
    ckey = (ws_str, ref, _ovkey)
    if not fresh:
        hit = cache.get(ckey)
        if hit is not None and (time.time() - hit[0]) < _COMPOSITE_STATE_TTL_S:
            return {**hit[1], "cached": True}, 200

    if ws_str not in sys.path:
        sys.path.insert(0, ws_str)

    # Generator-kind branch: build in a SUBPROCESS (its own main thread).
    res = composite_state_via_subprocess(ws_root, ref, overrides)
    if res is not None and "state" in res:
        state_doc = res["state"]
        _embed_declared_emit_paths(state_doc, res.get("emitters"))
        payload = {"state": state_doc, "kind": "generator", "module": res.get("module")}
        cache[ckey] = (time.time(), payload)
        if len(cache) > 16:  # cap memory; drop the oldest entry
            cache.pop(next(iter(cache)))
        _record_last_good(ws_root, ref, payload)  # so a later build failure can degrade to this
        return payload, 200
    if res is not None and "__build_error__" in res:
        # A live build can fail for ENVIRONMENTAL reasons (a stale/absent ParCa
        # cache, an env-probe drift, a mid-restart worker) even when the composite
        # is perfectly valid. Do NOT 400 — that blanks the Composites view and
        # hides the Run button, which for a Cloud run needs no local cache at all.
        # Degrade to a labelled 200 (static default / last-good / skeleton), with a
        # build_error the card renders as a warning chip. Whether a Run is allowed
        # is decided at dispatch, not here.
        return _degrade_build_error(
            ws_root, ref, overrides, res["__build_error__"], res.get("emitters"), ckey)
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

    doc = process_docs.attach_process_docs_via_worker(ws_root, doc, spec_id=ref)  # per-process docstrings for the inspector; spec_id resolves bare addresses (local:EcoliWCM) for Composite-Process flagging
    # This branch's `doc` is either a raw composite-spec file (top-level
    # `state:`/`emitters:` keys, e.g. a `.composite.yaml`) or an already
    # resolve()-shaped static snapshot (top-level `state:` nested one level,
    # same as `reports/composite-state/<id>.json`) — either way the emit
    # declarations live at `doc["emitters"]` and the tree to embed into is
    # `doc["state"]`.
    if isinstance(doc, dict) and isinstance(doc.get("state"), dict):
        _embed_declared_emit_paths(doc["state"], doc.get("emitters"))
    _spec_payload = {"state": doc, "kind": "spec"}
    _record_last_good(ws_root, ref, _spec_payload)
    return _spec_payload, 200


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
