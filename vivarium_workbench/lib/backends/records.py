"""Where an existing remote run went: the record a run carries, and how a later operation finds it.

docs/backend-adapters.md step 1c (Jim, 2026-11-19): a later operation on a run (status, cancel, land, ...) is routed
by the run's stored record, not by the id's form plus whatever backend is configured now. Step 1c-1 writes the record
and provides the lookup; step 1c-2 makes the views use it.

The record is one block in a run row's ``params`` (placeholder and landed rows alike)::

    "remote": {"kind": "sms-legacy" | "viva-core" | "compose-api", "id": "<remote id>",
               "base_url": "<the URL the request went to>", "deployment": "<remote_deployment_name()>",
               "server_id": "<the backend's own deployment identity, or null>"}

``server_id`` is what tells two deployments apart that share a URL (both Stanford sites are ``http://api:8000``;
every laptop tunnel is ``http://localhost:8080``). It comes from the backend itself (viva-api#1055 ask F: a stable
``deployment_id`` on ``GET /viva/v1/health``); until a deployment reports one it is null and the lookup falls back to
the URL and deployment name, which cannot tell such deployments apart.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

KINDS = ("sms-legacy", "viva-core", "compose-api")

#: How long a backend's reported identity is trusted. A tunnel on localhost:8080 can be re-pointed at another
#: deployment while the workbench runs, so this is not per-process.
_IDENTITY_TTL = 30.0
_IDENTITY: "dict[str, tuple[float, Optional[str]]]" = {}

#: The field ask F asks viva-api to add to ``GET /viva/v1/health``.
IDENTITY_FIELD = "deployment_id"


def _canonical(url: str) -> str:
    from vivarium_workbench.lib.sms_api_client import _canonical_url

    try:
        return _canonical_url(url)
    except ValueError:
        return (url or "").strip().rstrip("/")


# --- the backend's own identity -----------------------------------------------------------------------------------

def fetch_server_id(base_url: str) -> Optional[str]:
    """The identity ``base_url`` reports now (cached for :data:`_IDENTITY_TTL`), or ``None`` when it reports none or
    cannot be asked. Through the circuit breaker with the probe policy; never raises."""
    key = _canonical(base_url)
    hit = _IDENTITY.get(key)
    if hit and time.monotonic() - hit[0] < _IDENTITY_TTL:
        return hit[1]
    return refresh_server_id(base_url)


def refresh_server_id(base_url: str) -> Optional[str]:
    """Ask ``base_url`` for its identity now, bypassing the cache (before refusing on a mismatch)."""
    from vivarium_workbench.lib.sms_api_client import SmsApiClient

    try:
        health = SmsApiClient.for_("probe", base_url).health_v1() or {}
        value = health.get(IDENTITY_FIELD)
        ident = str(value).strip() if value not in (None, "") else None
    except Exception:  # noqa: BLE001 -- unreachable or not a viva-core: "reports none", never an error here
        return None  # not cached: a blip must not stick
    _IDENTITY[_canonical(base_url)] = (time.monotonic(), ident)
    return ident


def cached_server_id(base_url: str) -> Optional[str]:
    """The cached identity only (no network call)."""
    hit = _IDENTITY.get(_canonical(base_url))
    return hit[1] if hit and time.monotonic() - hit[0] < _IDENTITY_TTL else None


# --- writing the record ---------------------------------------------------------------------------------------------

def remote_record(kind: str, remote_id: Any, base_url: str) -> dict:
    """The ``params["remote"]`` block for a run sent to ``base_url`` as ``kind`` and known there as ``remote_id``.

    Called once the backend has accepted (or served) the run. ``server_id`` is asked of it then, through the 30 s
    cache with the probe policy (one attempt, a short timeout, the circuit breaker): a backend that just answered
    answers this at once, and one that cannot be asked records ``null`` -- never an error."""
    from vivarium_workbench.lib import remote_pinned

    assert kind in KINDS, kind
    return {
        "kind": kind,
        "id": str(remote_id).strip(),
        "base_url": _canonical(base_url),
        "deployment": remote_pinned.remote_deployment_name(),
        "server_id": fetch_server_id(base_url) if kind != "compose-api" else None,
    }


# --- finding the record ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Found:
    """One stored row that names ``remote_id``: its record (``None`` for a row written before step 1c, whose
    legacy ``source`` is then in ``legacy_source``) and where it is."""

    db: Path
    run_id: str
    record: Optional[dict]
    legacy_source: Optional[str] = None


def run_dbs(ws_root: Path) -> Iterator[Path]:
    """Every runs DB a remote run's row can be in: the workspace's composite runs, every study directory in both
    layouts (no de-duplication), and the legacy ``investigations/<name>/runs.db``. Missing files are skipped."""
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths

    wp = WorkspacePaths.load(Path(ws_root))
    candidates = [wp.pbg / "composite-runs.db"]
    studies, investigations = wp.dir("studies"), wp.dir("investigations")
    if studies.is_dir():
        candidates += sorted(d / "runs.db" for d in studies.iterdir() if d.is_dir())
    if investigations.is_dir():
        for inv in sorted(d for d in investigations.iterdir() if d.is_dir()):
            candidates.append(inv / "runs.db")
            if (inv / "studies").is_dir():
                candidates += sorted(d / "runs.db" for d in (inv / "studies").iterdir() if d.is_dir())
    seen: set[Path] = set()
    for db in candidates:
        if db.is_file() and db not in seen:
            seen.add(db)
            yield db


_QUERY = """
SELECT run_id, params_json, manifest_json FROM runs_meta WHERE
  (CASE WHEN json_valid(params_json) THEN json_extract(params_json, '$.remote.id') END) = :id
  OR (CASE WHEN json_valid(params_json) THEN CAST(json_extract(params_json, '$.simulation_id') AS TEXT) END) = :id
  OR (CASE WHEN json_valid(params_json) THEN json_extract(params_json, '$.viva_v1.run_id') END) = :id
  OR (CASE WHEN json_valid(params_json) THEN json_extract(params_json, '$.compose.run_id') END) = :id
  OR (CASE WHEN json_valid(manifest_json) THEN json_extract(manifest_json, '$.compose.run_id') END) = :id
"""


def _rows(db: Path, remote_id: str) -> "list[tuple[str, str, str]]":
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return []
    try:
        return conn.execute(_QUERY, {"id": remote_id}).fetchall()
    except sqlite3.Error:  # no runs_meta table, a locked or damaged file: this DB has nothing to say
        return []
    finally:
        conn.close()


def find_records(ws_root: Path, remote_id: Any, *, study_db: Optional[Path] = None) -> "list[Found]":
    """Every stored row naming ``remote_id`` (read-only; never creates or migrates a DB). ``study_db`` is searched
    first, and the whole workspace only when it holds none."""
    rid = str(remote_id if remote_id is not None else "").strip()
    if not rid:
        return []
    dbs = [study_db] if study_db is not None and study_db.is_file() else []
    found = _scan(dbs, rid)
    return found or _scan([d for d in run_dbs(ws_root) if d not in dbs], rid)


def _scan(dbs: "list[Path]", rid: str) -> "list[Found]":
    out: list[Found] = []
    for db in dbs:
        for run_id, params_json, _manifest_json in _rows(db, rid):
            try:
                params = json.loads(params_json) if params_json else {}
            except ValueError:
                params = {}
            block = params.get("remote") if isinstance(params, dict) else None
            # Only a well-formed record counts (a local composite may have an input named ``remote``).
            record = block if (isinstance(block, dict) and block.get("kind") in KINDS and block.get("id")) else None
            out.append(Found(db=db, run_id=run_id, record=record,
                             legacy_source=None if record else ((params.get("source") if isinstance(params, dict) else None)
                                                   or None)))
    return out


# --- deciding: route by the record, refuse another backend's run ------------------------------------------------

@dataclass(frozen=True)
class Resolution:
    """Which backend answers for an existing run (``kind``; ``None`` = no backend owns the id, the view's own
    "id required" answer applies), or why none may (``refusal``: a ``(body, 409)`` to return as is)."""

    kind: Optional[str] = None
    refusal: "Optional[tuple[dict, int]]" = None
    via: str = "id-form"  # "record" | "legacy-guard" | "id-form"


def configured_url(kind: str) -> Optional[str]:
    """The URL a run of ``kind`` would be asked at under the current configuration."""
    from vivarium_workbench.lib.sms_api_client import explicit_backend_base_url, sms_api_base

    return explicit_backend_base_url() if kind == "compose-api" else sms_api_base()


def _deployment_names_now() -> set[str]:
    """Every deployment name the current configuration could have written on a row before step 1c: the configured
    name, the named URL's host, each alias's host, and the default when no name is configured. Best-effort only."""
    import os
    from urllib.parse import urlsplit

    from vivarium_workbench.lib import remote_pinned
    from vivarium_workbench.lib.sms_api_client import BACKEND_BASE_ENV_VARS

    names = {remote_pinned.remote_deployment_name()}
    for var in BACKEND_BASE_ENV_VARS:
        host = urlsplit(os.environ.get(var) or "").hostname
        if host:
            names.add(host)
    if not (os.environ.get("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT") or "").strip():
        names.add(remote_pinned._DEFAULT_REMOTE_DEPLOYMENT)
    return names


def _other_backend(record: dict, why: str) -> "tuple[dict, int]":
    now = configured_url(record.get("kind") or "")
    return ({"error": f"this run was dispatched to {record.get('deployment')} ({record.get('base_url')}); this "
                      f"workbench is configured for {now or 'no such backend'} ({why})",
             "reason": "other-backend", "phase": "other-backend", "reachable": False,
             "run": record.get("id"), "dispatched_to": record.get("deployment")}, 409)


def _ambiguous(rid: str) -> Resolution:
    return Resolution(refusal=({
        "error": f"run {rid} is recorded on more than one backend; open it from the Runs tab row it belongs to",
        "reason": "ambiguous", "phase": "other-backend", "reachable": False}, 409), via="record")


def _by_record(records: "list[dict]", rid: str, here_name: str) -> Resolution:
    """Route by stored records. Provenance is (server identity, run id); a URL is only an address.

    1. Identity: for each kind, ask the configured server its identity (cached). Records whose ``server_id`` equals
       it are this server's run, whatever URL or name they were written with. If the server reports an identity and
       a record's differs, that record is another deployment's (asked once more, bypassing the cache, before
       concluding so). A server that reports none, or cannot be asked, decides nothing here.
    2. Best effort, for the records identity did not settle (no ``server_id``, or a server that reports none): the
       URL and the deployment name must both match.

    Exactly one kind left: that backend. None: refused (``other-backend``). Several: refused (``ambiguous``).
    """
    now: "dict[str, Optional[str]]" = {}   # by URL: sms-legacy and viva-core share one
    refreshed: set[str] = set()

    def identity(kind: str, *, fresh: bool = False) -> Optional[str]:
        url = configured_url(kind)
        if not url:
            return None
        key = _canonical(url)
        if fresh and key not in refreshed:
            refreshed.add(key)
            now[key] = refresh_server_id(url)
        elif key not in now:
            now[key] = fetch_server_id(url)
        return now[key]

    def _here(r: dict) -> bool:
        url = configured_url(r.get("kind") or "")
        return bool(url) and r.get("base_url") == _canonical(url) and r.get("deployment") == here_name

    with_id = [r for r in records if r.get("server_id")]
    if any(identity(r["kind"]) not in (None, r["server_id"]) for r in with_id):
        for kind in {r["kind"] for r in with_id}:
            identity(kind, fresh=True)  # a stale cache must never refuse a real run
    matched = [r for r in with_id if identity(r["kind"]) == r["server_id"]]
    foreign = [r for r in with_id if identity(r["kind"]) not in (None, r["server_id"])]
    undecided = [r for r in records if r not in matched and r not in foreign]
    kinds = {r["kind"] for r in matched} | {r["kind"] for r in undecided if _here(r)}
    if len(kinds) == 1:
        return Resolution(kind=kinds.pop(), via="record")
    if len(kinds) > 1:
        return _ambiguous(rid)
    sample = (foreign or records)[0]
    if foreign and sample is foreign[0]:
        return Resolution(refusal=_other_backend(
            sample, f"the server reports deployment {identity(sample['kind'])!r}, the run was recorded on "
                    f"{sample['server_id']!r}"), via="record")
    url = configured_url(sample.get("kind") or "")
    why = ("no backend of that kind is configured" if not url else
           "a different URL" if sample.get("base_url") != _canonical(url) else "a different deployment name")
    return Resolution(refusal=_other_backend(sample, why), via="record")


def resolve_existing(ws_root: Path, remote_id: Any, *, deployment: Optional[str] = None,
                     study_db: Optional[Path] = None) -> Resolution:
    """Which backend may answer for the existing run ``remote_id``.

    1. Rows carrying a record (step 1c onwards): :func:`_by_record` -- the configured server's identity decides when
       both sides report one; otherwise the URL and the deployment name must match. ``deployment`` (the Runs-tab
       row's) narrows which rows are meant.
    2. Rows from before step 1c (no record): the id's form decides, unless their ``source`` names a deployment the
       current configuration could not have written -- refused. A caller that passes ``deployment`` equal to the
       current name proceeds (a run listed live from the configured server).
    3. Nothing stored (a run listed live, or just dispatched by this page): the id's form, current configuration.
    """
    from vivarium_workbench.lib import backends, remote_pinned

    rid = str(remote_id if remote_id is not None else "").strip()
    owner = backends.for_run(rid)
    by_form = owner.name if owner else None
    here_name = remote_pinned.remote_deployment_name()
    found = find_records(ws_root, rid, study_db=study_db) if rid else []
    records = [f.record for f in found if f.record]
    legacy = [f.legacy_source for f in found if f.record is None and f.legacy_source]
    if deployment:
        # The caller (a Runs-tab row) says which deployment it means: only rows of that deployment count.
        records = [r for r in records if r.get("deployment") == deployment]
        legacy = [src for src in legacy if src == deployment]
        if not records and deployment not in _deployment_names_now():
            return Resolution(refusal=({
                "error": f"this run was dispatched to {deployment}; this workbench is configured for {here_name}",
                "reason": "other-backend", "phase": "other-backend", "reachable": False,
                "run": rid, "dispatched_to": deployment}, 409), via="legacy-guard")
    if records:
        return _by_record(records, rid, here_name)
    if legacy and not (deployment and deployment == here_name):
        names = _deployment_names_now()
        if not (set(legacy) & names):
            name = sorted(set(legacy))[0]
            return Resolution(refusal=({
                "error": f"this run was dispatched to {name}; this workbench is configured for {here_name}",
                "reason": "other-backend", "phase": "other-backend", "reachable": False,
                "run": rid, "dispatched_to": name}, 409), via="legacy-guard")
        return Resolution(kind=by_form, via="legacy-guard")
    return Resolution(kind=by_form, via="id-form")
