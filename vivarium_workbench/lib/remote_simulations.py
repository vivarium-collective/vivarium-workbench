"""Surface a remote build's server-side simulation runs in the Simulations DB.

When the active workspace is a materialized remote build (it carries a
``.viv-build.json`` stamped at switch time), the Simulations DB should list the
remote deployment's runs *for that build's commit & repo* alongside the local
workspace's run files. This module fetches those runs from sms-api and
normalizes each into the same row shape the local index emits
(``simulations_index.list_simulations``), tagged ``remote_origin`` so the
frontend renders a "remote" Origin pill and an S3 Location.

Design (see Simulations-DB remote-runs decision):
  * Scope = the active build's (repo, commit). sms-api's
    ``GET /api/v1/simulations`` returns every recorded simulation regardless of
    the required ``simulator_id`` param, so we resolve the set of simulator
    builds sharing the active build's (repo, commit) and filter to those.
  * Merge = both, labeled. These rows are appended to the local rows; the
    Origin column distinguishes remote from local.
  * Graceful = if the workspace isn't a remote build, or sms-api is
    unreachable (tunnel down), return ``[]`` so the local listing never breaks.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
from pathlib import Path


# TTL cache for the remote fetch. The sms-api list round-trip (+ per-record
# normalize) is the dominant cost of the Simulations index, and every call that
# re-derives the index (the Runs tab, its filters/refresh, the study cards'
# remote counts) would otherwise re-pay it. Remote (GovCloud) runs don't change
# second-to-second, so a cache keeps paging/filtering snappy; pass
# use_cache=False to force a fresh fetch (the Runs-tab refresh button).
#
# The fetch itself can be SLOW over a laggy tunnel (observed ~2 min cold), so the
# TTL is generous and the study-index path uses the stale-while-revalidate helper
# below (never blocks a page render on a cold/expired remote fetch).
_REMOTE_CACHE: dict = {}
_REMOTE_CACHE_TTL = 300.0
# A FAILED fetch (tunnel down / sms-api unreachable) is cached for only this
# long — long enough that the Runs tab's 15s auto-refresh doesn't re-probe a
# wedged tunnel on every poll (each probe costs the fresh client's timeout),
# short enough that a recovered tunnel is picked up promptly.
_NEGATIVE_CACHE_TTL = 30.0
# Bounds for the request-blocking "fresh" fetch (?refresh=true): a wedged tunnel
# must not pin the uvicorn worker for timeout * _GET_RETRIES seconds.
_FRESH_TIMEOUT = 10.0
_FRESH_MAX_RETRIES = 1
# Per-key provenance for the last completed fetch, so the /api/simulations
# payload can report remote-source state (fresh/stale/refreshing/unavailable)
# instead of a spinner. {key: {"as_of": epoch|None, "error": str|None}}.
_REMOTE_META: dict = {}
# Keys with an in-flight background refresh, so we never spawn more than one
# refresh thread per (ws_root, base_url, limit) at a time.
_REMOTE_REFRESH_INFLIGHT: set = set()
_REMOTE_REFRESH_LOCK = threading.Lock()


def _cache_key(ws_root, base_url: str | None, limit: int) -> tuple:
    return (str(ws_root), base_url or "", int(limit))


def _store_result(key: tuple, rows: list, error: "str | None") -> list:
    """Record a fetch outcome in the TTL cache + provenance map, and return the
    rows that callers should serve.

    On success the fresh rows are cached for the full TTL and ``as_of`` is
    stamped. On failure the LAST-KNOWN rows (if any) are kept and re-cached for
    the short negative TTL, ``as_of`` is preserved, and the error is recorded so
    the state resolves to "stale" (we have older rows) or "unavailable" (we
    never had any).
    """
    now = time.time()
    if error:
        prev = _REMOTE_CACHE.get(key)
        rows = prev[1] if prev else rows
        _REMOTE_CACHE[key] = (now + _NEGATIVE_CACHE_TTL, rows)
        prev_meta = _REMOTE_META.get(key) or {}
        _REMOTE_META[key] = {"as_of": prev_meta.get("as_of"), "error": error}
    else:
        _REMOTE_CACHE[key] = (now + _REMOTE_CACHE_TTL, rows)
        _REMOTE_META[key] = {"as_of": now, "error": None}
    return rows


def remote_state(ws_root: Path, base_url: str | None = None,
                 limit: int = 2000) -> dict:
    """Provenance of the remote-runs source for the /api/simulations payload.

    ``{"state": "fresh"|"stale"|"refreshing"|"unavailable", "as_of": epoch|None,
    "error": str|None}`` — computed from the same SWR cache the fetch-invocation
    functions populate, so the Runs tab can show "as of HH:MM (refreshing…)"
    instead of blocking or spinning.
    """
    key = _cache_key(ws_root, base_url, limit)
    now = time.time()
    hit = _REMOTE_CACHE.get(key)
    meta = _REMOTE_META.get(key) or {}
    with _REMOTE_REFRESH_LOCK:
        refreshing = key in _REMOTE_REFRESH_INFLIGHT
    as_of = meta.get("as_of")
    error = meta.get("error")
    if refreshing:
        state = "refreshing"
    elif error:
        # A failure with no prior success is a dead source; with prior rows it's
        # a stale-but-serving source.
        state = "stale" if as_of is not None else "unavailable"
    elif hit and hit[0] > now and as_of is not None:
        state = "fresh"
    elif as_of is not None:
        state = "stale"
    else:
        # Nothing fetched yet (cold, no refresh in flight) — honest "refreshing"
        # since the SWR read will kick one.
        state = "refreshing"
    return {"state": state, "as_of": as_of, "error": error}


# emitter tag -> capitalized label the UI pills key on (mirrors server.py).
_EMITTER_LABEL = {"sqlite": "SQLite", "parquet": "Parquet", "xarray": "XArray", "none": "—"}

# The sms-api LIST endpoint (GET /api/v1/simulations) carries NO run status — a
# record only tells us it exists. Live status lives on the per-sim
# GET /api/v1/simulations/<id>/status endpoint. So a run that is queued or
# running would otherwise be mislabeled "completed". We enrich the newest window
# of records with their live status (bounded, because /status is one round-trip
# each): a just-launched run is always among the newest ids, so this is enough to
# surface it as queued/running without paying a status call for all ~1300 sims.
_STATUS_ENRICH_WINDOW = 40
# sms-api status strings that mean the run has NOT finished — these are surfaced
# even when the run targets a build outside the pinned scope, so nothing a user
# launches is ever invisible in the Runs DB.
_ACTIVE_STATES = {"queued", "pending", "submitted", "running", "in_progress", "started"}


def _map_remote_status(raw: "str | None", has_out_uri: bool) -> str:
    """Map an sms-api status string to a Runs-DB status.

    ``raw`` is the live per-sim status (``None`` when we didn't enrich this
    record). With no live status, a persisted sim (it has an ``out_uri``) is
    ``completed``; one without is assumed still ``running`` rather than falsely
    ``completed``.
    """
    s = (raw or "").strip().lower()
    if s in ("completed", "done", "success", "succeeded", "finished"):
        return "completed"
    if s in ("failed", "error", "errored", "cancelled", "canceled"):
        return "failed"
    if s in ("running", "in_progress", "started"):
        return "running"
    if s in ("queued", "pending", "submitted"):
        return "queued"
    if s:
        return s
    return "completed" if has_out_uri else "running"


def _load_remote_study_map(ws_root: Path) -> list:
    """Workspace-declared experiment_id -> study_slug rules for remote runs.

    Reads ``remote_run_study_map`` from the workspace config (``workspace.yaml``):
    an ordered list of ``{pattern: <regex>, study: <slug>}`` entries, first match
    wins. Returns a list of ``(compiled_regex, study_slug)``.

    Why this exists: a remote (GovCloud) run is surfaced with ``db_path=None``
    (its store is an ``s3://`` uri), so the local index's study-slug-from-db_path
    inference cannot fire and every remote run lands orphaned
    (``study_slug=None``). This lets a workspace declare how its remote runs'
    ``experiment_id``s map to its studies so they show under the right study.

    INTERIM (stopgap). The durable fix is stamping ``study_slug`` + the
    RunIdentity record into the run at dispatch (viva-api#590), after which
    remote runs carry their own study association and this heuristic is moot.
    """
    cfg = Path(ws_root) / "workspace.yaml"
    if not cfg.is_file():
        return []
    try:
        import yaml
        doc = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    rules = doc.get("remote_run_study_map") or []
    out = []
    for r in rules:
        if isinstance(r, dict) and r.get("pattern") and r.get("study"):
            try:
                out.append((re.compile(str(r["pattern"]), re.I), str(r["study"])))
            except re.error:
                continue
    return out


def _infer_study_slug(experiment_id: str, rules: list) -> "str | None":
    """First workspace rule whose pattern matches ``experiment_id`` (or None)."""
    e = experiment_id or ""
    for pat, slug in rules:
        if pat.search(e):
            return slug
    return None


def _study_investigation_map(ws_root: Path) -> dict:
    """Map each study slug -> its investigation slug (study.yaml ``investigation:``).

    Lets a remote run that was associated with a study (via
    ``remote_run_study_map``) also carry the right ``investigation_slug``, so the
    investigation-scoped Runs/Sims views surface it. Without this a remote run
    has a study but a null investigation and is filtered out of every
    investigation scope (only local/backfill runs, which set investigation_slug
    directly, showed). Best-effort + tolerant of flat and nested layouts.
    """
    import glob
    import yaml as _yaml
    out: dict = {}
    ws = Path(ws_root)
    patterns = [
        ws / "studies" / "*" / "study.yaml",
        ws / "workspace" / "studies" / "*" / "study.yaml",
        ws / "investigations" / "*" / "studies" / "*" / "study.yaml",
        ws / "workspace" / "investigations" / "*" / "studies" / "*" / "study.yaml",
    ]
    for pat in patterns:
        for f in glob.glob(str(pat)):
            try:
                d = _yaml.safe_load(open(f, encoding="utf-8")) or {}
            except Exception:
                continue
            slug = Path(f).parent.name
            inv = d.get("investigation")
            if inv and slug not in out:
                out[slug] = inv
    return out


def _sms_api_base() -> str:
    # VIVA_API_BASE is canonical; SMS_API_BASE is a fallback alias (backend
    # repo was renamed sms-api -> viva-api).
    return os.environ.get("VIVA_API_BASE") or os.environ.get("SMS_API_BASE", "http://localhost:8080")


def _builds_list(versions_resp) -> list:
    """Normalize the /core/v1/simulator/versions response to a list of build dicts."""
    if isinstance(versions_resp, list):
        return versions_resp
    if isinstance(versions_resp, dict):
        for key in ("versions", "simulators", "builds"):
            if isinstance(versions_resp.get(key), list):
                return versions_resp[key]
        # Fall back to the first list-valued entry.
        for v in versions_resp.values():
            if isinstance(v, list):
                return v
    return []


def _build_id(b: dict):
    return b.get("database_id") or b.get("id") or b.get("simulator_id")


def _short(c) -> str:
    return (str(c or ""))[:7]


def _repo_key(url) -> str:
    """Trailing ``owner/repo`` (lower-cased, no ``.git``) so a build's full
    ``https://github.com/Owner/Repo`` URL and a workspace remote in any form
    (ssh, https, ``owner/repo``) compare equal."""
    u = (str(url or "")).strip().rstrip("/")
    if u.lower().endswith(".git"):
        u = u[: -len(".git")]
    parts = [p for p in u.replace(":", "/").split("/") if p]
    return "/".join(parts[-2:]).lower() if len(parts) >= 2 else u.lower()


def _deployment_name() -> str:
    """The deployment a remote run targets — the truthful Origin label."""
    try:
        from vivarium_workbench.lib.remote_pinned import remote_deployment_name
        return remote_deployment_name()
    except Exception:
        return "remote"


def _to_epoch(s):
    """Parse an sms-api timestamp ('YYYY-MM-DD HH:MM:SS.ffffff' or ISO) to epoch
    seconds; None on failure."""
    if not s:
        return None
    txt = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(txt, fmt).timestamp()
        except ValueError:
            pass
    try:
        return _dt.datetime.fromisoformat(txt.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _normalize(rec: dict, live_status: "str | None" = None) -> dict:
    """Convert one sms-api simulation record to a Simulations-DB row dict.

    ``live_status`` is the per-sim ``/status`` value when we enriched this
    record (see ``_fetch_remote_simulations``); ``None`` falls back to the
    record's own status or the out_uri heuristic.
    """
    cfg = rec.get("config") or {}
    sid = rec.get("simulator_id")
    db_id = rec.get("database_id")
    experiment_id = rec.get("experiment_id") or cfg.get("experiment_id") or f"sim{sid}-db{db_id}"
    out_uri = ((cfg.get("emitter_arg") or {}).get("out_uri")) or None
    emitter_tag = (cfg.get("emitter") or "").lower() or None
    ts = _to_epoch(rec.get("last_updated") or rec.get("created_at"))
    status = _map_remote_status(live_status or rec.get("status"), bool(out_uri))
    return {
        "run_id": experiment_id,
        "spec_id": "",
        "sim_name": cfg.get("description") or experiment_id,
        "label": experiment_id,
        # Live status from the per-sim /status endpoint (the list endpoint
        # carries none), so a queued/running cloud run shows as such instead of a
        # false "completed"; falls back to the out_uri heuristic when unenriched.
        "status": status,
        "n_steps": None,
        "progress_step": None,
        "started_at": ts,
        "completed_at": ts,
        "db_path": None,
        "store_path": out_uri,                 # s3:// — shown in the Location column
        "emitter": emitter_tag,
        "emitter_type": _EMITTER_LABEL.get(emitter_tag or "", emitter_tag or "—"),
        "studies": [],
        "study_slug": None,
        "investigation_slug": None,            # remote builds aren't investigation-organized
        "remote_origin": {
            # Where it ran — the deployment name (e.g. the GovCloud stack), not
            # the internal build number. The build is kept for the tooltip.
            "deployment": _deployment_name(),
            "simulation_id": db_id,
            "experiment_id": experiment_id,
            "build": sid,
            "backend": (cfg.get("aws") or {}).get("batch_queue") or "aws",
            "s3_uri": out_uri,
        },
        "source": "remote",
    }


def _read_build_meta(ws_root: Path) -> dict | None:
    meta = Path(ws_root) / ".viv-build.json"
    if not meta.is_file():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8")) or None
    except (ValueError, OSError):
        return None


def _workspace_repo_key(ws_root: Path) -> str | None:
    """The workspace's origin repo as an ``owner/repo`` key, or ``None``."""
    try:
        from vivarium_workbench.lib.git_status import remote_repo_url
        url = remote_repo_url(Path(ws_root))
    except Exception:
        url = None
    return _repo_key(url) if url else None


def _scope_build_ids(ws_root: Path, bm, builds) -> "tuple[set, object] | tuple[None, None]":
    """Which remote build ids this workspace should surface runs for, plus one
    seed id to list against. Two modes:

      * Materialized remote build (``.viv-build.json``): the active build's
        (repo, commit) — the exact source this tab runs.
      * Local checkout: every build of the workspace's *repo* (any commit), so
        all of the project's remote runs show up — this is what makes remote
        runs visible in a plain local workspace, not just a switched-in build.
    """
    by_id = {_build_id(b): b for b in builds}
    if bm and bm.get("simulator_id") is not None:
        active_id = bm.get("simulator_id")
        active = by_id.get(active_id)
        repo = (active or {}).get("git_repo_url") if active else None
        commit = _short((active or {}).get("git_commit_hash") if active else bm.get("commit"))
        if not commit:
            return None, None
        matching = {
            _build_id(b) for b in builds
            if _short(b.get("git_commit_hash")) == commit
            and (repo is None or _repo_key(b.get("git_repo_url")) == _repo_key(repo))
        }
        matching.add(active_id)
        return matching, active_id
    key = _workspace_repo_key(ws_root)
    if not key:
        return None, None
    matching = {_build_id(b) for b in builds if _repo_key(b.get("git_repo_url")) == key}
    if not matching:
        return None, None
    return matching, next(iter(matching))


def list_remote_simulations(ws_root: Path, base_url: str | None = None,
                            limit: int = 2000, use_cache: bool = True, *,
                            timeout: float | None = None,
                            max_retries: int | None = None) -> list[dict]:
    """Remote sms-api runs to surface in the Simulations DB, or ``[]``.

    Cached for ``_REMOTE_CACHE_TTL`` seconds (see the module cache) so repeated
    index derivations don't re-hit sms-api; ``use_cache=False`` forces a fresh
    fetch. This is the BLOCKING variant — the ``?refresh=true`` path uses it with
    a bounded ``timeout``/``max_retries`` so a wedged tunnel can't hang the
    request. Thin wrapper over :func:`_fetch_remote_simulations_meta`; a failed
    fetch is negative-cached (short TTL) so it isn't re-probed every poll.
    """
    key = _cache_key(ws_root, base_url, limit)
    if use_cache:
        hit = _REMOTE_CACHE.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    rows, error = _fetch_remote_simulations_meta(
        ws_root, base_url, limit, timeout=timeout, max_retries=max_retries)
    return _store_result(key, rows, error)


def list_remote_simulations_swr(ws_root: Path, base_url: str | None = None,
                                limit: int = 2000) -> list[dict]:
    """Stale-while-revalidate variant of :func:`list_remote_simulations`.

    Returns whatever is in the cache **immediately** — even if expired, even if
    empty — and NEVER blocks on the (potentially ~minutes-long) sms-api fetch.
    When the cache is missing or stale it kicks off a single background daemon
    refresh so the next caller gets fresh data. Use this on latency-sensitive
    page renders (the study index) where remote run counts are an enhancement,
    not a blocker; use :func:`list_remote_simulations` when you must have the
    freshest rows (the Runs-tab refresh).
    """
    key = _cache_key(ws_root, base_url, limit)
    now = time.time()
    hit = _REMOTE_CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]                      # fresh
    _kick_remote_refresh(key, ws_root, base_url, limit)
    return hit[1] if hit else []           # stale (serve last-known) or empty


def _kick_remote_refresh(key: tuple, ws_root: Path, base_url: str | None,
                         limit: int) -> None:
    """Spawn at most one background refresh per cache key."""
    with _REMOTE_REFRESH_LOCK:
        if key in _REMOTE_REFRESH_INFLIGHT:
            return
        _REMOTE_REFRESH_INFLIGHT.add(key)

    def _run() -> None:
        try:
            rows, error = _fetch_remote_simulations_meta(ws_root, base_url, limit)
            _store_result(key, rows, error)
        except Exception:  # noqa: BLE001 — best-effort; a down tunnel must not crash the thread
            pass
        finally:
            with _REMOTE_REFRESH_LOCK:
                _REMOTE_REFRESH_INFLIGHT.discard(key)

    threading.Thread(target=_run, name="remote-sims-refresh", daemon=True).start()


def _fetch_remote_simulations(ws_root: Path, base_url: str | None = None,
                              limit: int = 2000) -> list[dict]:
    """Uncached remote fetch (rows only) — back-compat wrapper over
    :func:`_fetch_remote_simulations_meta`. Never raises."""
    rows, _error = _fetch_remote_simulations_meta(ws_root, base_url, limit)
    return rows


def _fetch_remote_simulations_meta(
    ws_root: Path, base_url: str | None = None, limit: int = 2000, *,
    timeout: float | None = None, max_retries: int | None = None,
) -> "tuple[list[dict], str | None]":
    """Uncached remote fetch returning ``(rows, error)``.

    ``error`` is a human-readable string when sms-api was UNREACHABLE
    (connection/timeout — the case the negative cache exists to suppress) and
    ``None`` on success, including a successful fetch that yields zero rows (a
    local checkout with no matching remote builds is success, not failure). This
    lets the SWR layer negative-cache a down tunnel while still treating "no
    remote runs" as fresh.

    For a materialized remote build, the scope is the active build's (repo,
    commit); for a plain local checkout, every remote run of the workspace's
    repo, capped to the ``limit`` most recent. Never raises — a down tunnel can't
    break the local listing. ``timeout``/``max_retries`` bound the request when
    the caller cannot tolerate the default (30s x 3) budget.
    """
    try:
        from vivarium_workbench.lib.sms_api_client import SmsApiClient
    except ImportError:
        return [], None
    client = SmsApiClient(
        base_url or _sms_api_base(),
        timeout=timeout if timeout is not None else 30.0,
        max_retries=max_retries if max_retries is not None else 3,
    )
    try:
        builds = _builds_list(client.list_simulators())
    except Exception as e:  # noqa: BLE001 — unreachable source, report for negative cache
        return [], f"sms-api unreachable: {e}"

    matching, seed = _scope_build_ids(ws_root, _read_build_meta(ws_root), builds)
    if not matching or seed is None:
        return [], None

    try:
        # The list endpoint ignores its simulator_id filter and returns every
        # recorded sim, so any build id seeds it; we filter client-side.
        sims = client.list_build_simulations(seed)
    except Exception as e:  # noqa: BLE001
        return [], f"sms-api unreachable: {e}"
    if not isinstance(sims, list):
        return [], None

    # Enrich the newest window with live status from the per-sim /status endpoint
    # (the list carries none). A just-launched run is always among the newest ids,
    # so this window is enough to catch queued/running runs without a status call
    # per record. Failures are swallowed — an un-enriched record falls back to the
    # out_uri heuristic in _normalize.
    newest = sorted(
        (s for s in sims if isinstance(s, dict) and s.get("database_id") is not None),
        key=lambda s: s.get("database_id") or 0, reverse=True,
    )[:_STATUS_ENRICH_WINDOW]
    live_status: dict = {}
    for s in newest:
        did = s.get("database_id")
        if did is None:
            continue
        try:
            live_status[did] = (client.simulation_status(int(did)) or {}).get("status")
        except Exception:
            pass

    # Reconcile `remote-pending-<id>` placeholder rows against live status
    # (#1108 / item d2). Force-include placeholder sim ids in the enrichment
    # window so older placeholders (outside the newest-N window above) resolve,
    # then terminalize any whose sim is done/failed/unknown. Best-effort — a
    # down tunnel or a broken workspace layout must not break the remote listing.
    try:
        from vivarium_workbench.lib import remote_reconcile
        remote_reconcile.enrich_placeholder_status(client, ws_root, live_status)
        remote_reconcile.reconcile_pending_placeholders(ws_root, live_status)
    except Exception:  # noqa: BLE001
        pass

    def _keep(rec: dict) -> bool:
        # Pinned-build scope (completed history for this exact build/commit)…
        if rec.get("simulator_id") in matching:
            return True
        # …plus any ACTIVE run on any build, so nothing a user just launched is
        # invisible in the Runs DB merely because it targets a different build.
        st = (live_status.get(rec.get("database_id")) or "").strip().lower()
        return st in _ACTIVE_STATES

    rows = [_normalize(rec, live_status.get(rec.get("database_id")))
            for rec in sims
            if isinstance(rec, dict) and _keep(rec)]
    # Associate each remote run with its study via the workspace's declared
    # experiment_id -> study_slug rules (remote runs have no db_path, so the
    # local study-slug-from-path inference in simulations_index can't fire).
    study_rules = _load_remote_study_map(ws_root)
    if study_rules:
        inv_map = _study_investigation_map(ws_root)
        for r in rows:
            slug = _infer_study_slug(r.get("run_id", ""), study_rules)
            if slug:
                r["study_slug"] = slug
                r["studies"] = [slug]
                # Carry the study's investigation so investigation-scoped views
                # (Runs/Sims filtered to e.g. cd2) surface this remote run —
                # otherwise it has a study but a null investigation and is hidden.
                inv = inv_map.get(slug)
                if inv:
                    r["investigation_slug"] = inv
    rows.sort(key=lambda r: r.get("started_at") or 0.0, reverse=True)
    return (rows[:limit] if limit and limit > 0 else rows), None
