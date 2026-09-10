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
import time
from pathlib import Path


# Short-TTL cache for the remote fetch. The sms-api list round-trip (+ per-record
# normalize) is the dominant cost of the Simulations index, and every call that
# re-derives the index (the Runs tab, its filters/refresh, the study cards'
# remote counts) would otherwise re-pay it. Remote (GovCloud) runs don't change
# second-to-second, so a brief cache keeps paging/filtering snappy; pass
# use_cache=False to force a fresh fetch (the Runs-tab refresh button).
_REMOTE_CACHE: dict = {}
_REMOTE_CACHE_TTL = 60.0


# emitter tag -> capitalized label the UI pills key on (mirrors server.py).
_EMITTER_LABEL = {"sqlite": "SQLite", "parquet": "Parquet", "xarray": "XArray", "none": "—"}


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


def _normalize(rec: dict) -> dict:
    """Convert one sms-api simulation record to a Simulations-DB row dict."""
    cfg = rec.get("config") or {}
    sid = rec.get("simulator_id")
    db_id = rec.get("database_id")
    experiment_id = rec.get("experiment_id") or cfg.get("experiment_id") or f"sim{sid}-db{db_id}"
    out_uri = ((cfg.get("emitter_arg") or {}).get("out_uri")) or None
    emitter_tag = (cfg.get("emitter") or "").lower() or None
    ts = _to_epoch(rec.get("last_updated") or rec.get("created_at"))
    return {
        "run_id": experiment_id,
        "spec_id": "",
        "sim_name": cfg.get("description") or experiment_id,
        "label": experiment_id,
        # The list endpoint carries no run status; these are recorded, persisted
        # simulations (they have an out_uri). Surface as completed; the per-sim
        # /status endpoint could enrich this later without changing the shape.
        "status": rec.get("status") or "completed",
        "n_steps": None,
        "progress_step": None,
        "started_at": ts,
        "completed_at": ts,
        "db_path": None,
        "store_path": out_uri,                 # s3:// — shown in the Location column
        "emitter": emitter_tag,
        "emitter_type": _EMITTER_LABEL.get(emitter_tag, emitter_tag or "—"),
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
                            limit: int = 2000, use_cache: bool = True) -> list[dict]:
    """Remote sms-api runs to surface in the Simulations DB, or ``[]``.

    Cached for ``_REMOTE_CACHE_TTL`` seconds (see the module cache) so repeated
    index derivations don't re-hit sms-api; ``use_cache=False`` forces a fresh
    fetch. Thin wrapper over :func:`_fetch_remote_simulations`.
    """
    key = (str(ws_root), base_url or "", int(limit))
    now = time.time()
    if use_cache:
        hit = _REMOTE_CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    rows = _fetch_remote_simulations(ws_root, base_url, limit)
    _REMOTE_CACHE[key] = (now + _REMOTE_CACHE_TTL, rows)
    return rows


def _fetch_remote_simulations(ws_root: Path, base_url: str | None = None,
                              limit: int = 2000) -> list[dict]:
    """Uncached remote fetch — see :func:`list_remote_simulations`.

    For a materialized remote build, that's the active build's (repo, commit).
    For a plain local checkout, it's every remote run of the workspace's repo,
    so the project's GovCloud runs appear alongside local ones — capped to the
    ``limit`` most recent. Returns ``[]`` when the repo can't be resolved or
    sms-api is unreachable — never raises, so a down tunnel can't break the
    local listing.
    """
    try:
        from vivarium_workbench.lib.sms_api_client import SmsApiClient
    except ImportError:
        return []
    client = SmsApiClient(base_url or _sms_api_base())
    try:
        builds = _builds_list(client.list_simulators())
    except Exception:
        return []

    matching, seed = _scope_build_ids(ws_root, _read_build_meta(ws_root), builds)
    if not matching or seed is None:
        return []

    try:
        # The list endpoint ignores its simulator_id filter and returns every
        # recorded sim, so any build id seeds it; we filter client-side.
        sims = client.list_build_simulations(seed)
    except Exception:
        return []
    if not isinstance(sims, list):
        return []

    rows = [_normalize(rec) for rec in sims
            if isinstance(rec, dict) and rec.get("simulator_id") in matching]
    # Associate each remote run with its study via the workspace's declared
    # experiment_id -> study_slug rules (remote runs have no db_path, so the
    # local study-slug-from-path inference in simulations_index can't fire).
    study_rules = _load_remote_study_map(ws_root)
    if study_rules:
        for r in rows:
            slug = _infer_study_slug(r.get("run_id", ""), study_rules)
            if slug:
                r["study_slug"] = slug
                r["studies"] = [slug]
    rows.sort(key=lambda r: r.get("started_at") or 0.0, reverse=True)
    return rows[:limit] if limit and limit > 0 else rows
