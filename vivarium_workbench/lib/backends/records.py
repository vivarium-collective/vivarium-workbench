"""Where an existing remote run went: the record a run carries, and how a later operation finds it.

docs/backend-adapters.md step 1c (Jim, 2026-11-19): a later operation on a run (status, cancel, land, ...) is routed
by the run's stored record, not by the id's form plus whatever backend is configured now. Step 1c-1 writes the record
and provides the lookup; step 1c-2 makes the views use it.

The record is one block in a run row's ``params`` (placeholder and landed rows alike)::

    "remote": {"kind": "sms-legacy" | "viva-core" | "compose-api", "id": "<remote id>",
               "base_url": "<the URL the request went to>", "deployment": "<remote_deployment_name()>",
               "server_id": "<the backend's own deployment identity, or null>"}

``server_id`` is what tells two deployments apart that share a URL (both Stanford sites are ``http://api:8000``;
every laptop tunnel is ``http://localhost:8080``). It comes from the backend itself, through its ``Backend.identity``:
a stable ``deployment_id`` on viva-api's ``GET /viva/v1/health`` (viva-api#1055 ask F) for viva-core and sms-legacy,
and on compose-api's ``GET /health`` (biosimulations/compose-api#543). Until a deployment reports one it is null and
the lookup falls back to the URL and deployment name, which cannot tell such deployments apart.

Identities are cached per (identity family, URL): a family is one ``Backend.identity`` function, so viva-core and
sms-legacy (one viva-api) share an entry, and a compose-api and a viva-api at the same URL never share or cross one.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

KINDS = ("sms-legacy", "viva-core", "compose-api")

#: How long a backend's reported identity is trusted. A tunnel on localhost:8080 can be re-pointed at another
#: deployment while the workbench runs, so this is not per-process.
_IDENTITY_TTL = 30.0
#: Keyed by :func:`_key` (identity family, canonical URL), as are :data:`_FAILED` and :data:`_REFRESHED`.
_IDENTITY: "dict[tuple[object, str], tuple[float, Optional[str]]]" = {}

#: The field both servers report their identity in: viva-api's ``GET /viva/v1/health`` (ask F) and compose-api's
#: ``GET /health``.
IDENTITY_FIELD = "deployment_id"

#: The kind the one-argument forms of :func:`fetch_server_id` and friends mean: the viva family (viva-core and
#: sms-legacy), the only one that reported an identity before compose-api did.
DEFAULT_IDENTITY_KIND = "viva-core"


def _canonical(url: str) -> str:
    from vivarium_workbench.lib import backend_config

    try:
        return backend_config.canonical_url(url)
    except ValueError:
        return (url or "").strip().rstrip("/")


# --- the backend's own identity -----------------------------------------------------------------------------------

#: How long a failed identity request is remembered: short, so a blip does not stick, but long enough that a down
#: server is not asked again by every lookup (the Runs-tab poller, the reconciler).
_IDENTITY_FAIL_TTL = 5.0
_FAILED: "dict[tuple[object, str], float]" = {}

#: When each (family, URL)'s identity was last re-asked because a record disagreed with the cache
#: (see :func:`refresh_server_id`).
_REFRESHED: "dict[tuple[object, str], float]" = {}


def _identity_fn(kind: str) -> "Optional[Callable[[str], Optional[str]]]":
    """``Backend.identity`` of ``kind``'s backend (``None`` for a kind that reports none, or no such kind)."""
    from vivarium_workbench.lib.backends import by_kind

    try:
        return by_kind(kind).identity
    except KeyError:
        return None


def _key(base_url: str, kind: str = DEFAULT_IDENTITY_KIND) -> "tuple[object, str]":
    """The cache key of ``kind``'s server at ``base_url``: (identity family, canonical URL). The family is the
    backend's identity function, so kinds served by one server (viva-core, sms-legacy) share an entry, and another
    server at the same URL (compose-api, when a backend is named) never shares or crosses it."""
    return (_identity_fn(kind), _canonical(base_url))


def _ask(base_url: str, kind: str) -> Optional[str]:
    """One identity request to ``kind``'s server at ``base_url``, through that backend's own ``identity`` (cached on
    success; a failure remembered for :data:`_IDENTITY_FAIL_TTL`). Never raises."""
    ask = _identity_fn(kind)
    if ask is None:  # callers check has_identity first; a kind with no identity asks nothing and caches nothing
        return None
    key = (ask, _canonical(base_url))
    failed = _FAILED.get(key)
    if failed is not None and time.monotonic() - failed < _IDENTITY_FAIL_TTL:
        return None
    try:
        ident = ask(base_url)
    except Exception:  # noqa: BLE001 -- unreachable or not that kind of server: "reports none", never an error here
        _FAILED[key] = time.monotonic()
        return None
    _FAILED.pop(key, None)
    _IDENTITY[key] = (time.monotonic(), ident)
    return ident


def fetch_server_id(base_url: str, *, kind: str = DEFAULT_IDENTITY_KIND) -> Optional[str]:
    """The identity ``kind``'s server at ``base_url`` reports (cached for :data:`_IDENTITY_TTL`), or ``None`` when
    it reports none or cannot be asked. Through the circuit breaker with the probe policy; never raises."""
    hit = _IDENTITY.get(_key(base_url, kind))
    if hit and time.monotonic() - hit[0] < _IDENTITY_TTL:
        return hit[1]
    return _ask(base_url, kind)


def refresh_server_id(base_url: str, *, kind: str = DEFAULT_IDENTITY_KIND) -> Optional[str]:
    """Ask ``kind``'s server at ``base_url`` again, bypassing the cache, because a record disagreed with the cached
    identity (a tunnel re-pointed at another deployment must not refuse that deployment's runs). At most once per
    :data:`_IDENTITY_TTL` per (family, URL), so a workspace full of another deployment's runs costs one request, not
    one per lookup; within that window the answer of the last re-ask stands."""
    key = _key(base_url, kind)
    last = _REFRESHED.get(key)
    if last is not None and time.monotonic() - last < _IDENTITY_TTL:
        hit = _IDENTITY.get(key)
        return hit[1] if hit else None
    _REFRESHED[key] = time.monotonic()
    return _ask(base_url, kind)


def cached_server_id(base_url: str, *, kind: str = DEFAULT_IDENTITY_KIND) -> Optional[str]:
    """The cached identity only (no network call)."""
    hit = _IDENTITY.get(_key(base_url, kind))
    return hit[1] if hit and time.monotonic() - hit[0] < _IDENTITY_TTL else None


def has_identity(kind: str) -> bool:
    """Whether a backend of ``kind`` reports a server identity (``Backend.identity``)."""
    return _identity_fn(kind) is not None


# --- writing the record ---------------------------------------------------------------------------------------------

def remote_record(kind: str, remote_id: Any, base_url: str) -> dict:
    """The ``params["remote"]`` block for a run sent to ``base_url`` as ``kind`` and known there as ``remote_id``.

    Called once the backend has accepted (or served) the run. ``server_id`` is asked of it then, through the 30 s
    cache with the probe policy (one attempt, a short timeout, the circuit breaker): a backend that just answered
    answers this at once, and one that cannot be asked records ``null`` -- never an error."""
    from vivarium_workbench.lib import backend_config

    assert kind in KINDS, kind
    return {
        "kind": kind,
        "id": str(remote_id).strip(),
        "base_url": _canonical(base_url),
        "deployment": backend_config.deployment_name(),
        "server_id": fetch_server_id(base_url, kind=kind) if has_identity(kind) else None,
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


#: The ``backend`` labels remote rows were written with before step 1c (a local run's params may have a ``source``).
_REMOTE_LABELS = frozenset({"ray", "viva-v1", "compose-api"})


def _legacy_source(params: Any) -> Optional[str]:
    if isinstance(params, dict) and params.get("backend") in _REMOTE_LABELS:
        return params.get("source") or None
    return None


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
                             legacy_source=None if record else (_legacy_source(params)
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
    from vivarium_workbench.lib import backend_config

    return backend_config.explicit_backend_base_url() if kind == "compose-api" else backend_config.configured_base_url()


def _deployment_names_now() -> set[str]:
    """Every deployment name the current configuration could have written on a row before step 1c: the configured
    name, the named URL's host, each alias's host, and the default when no name is configured. Best-effort only."""
    import os
    from urllib.parse import urlsplit

    from vivarium_workbench.lib import backend_config

    names = {backend_config.deployment_name()}
    for var in backend_config.BACKEND_BASE_ENV_VARS:
        host = urlsplit(os.environ.get(var) or "").hostname
        if host:
            names.add(host)
    if not (os.environ.get("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT") or "").strip():
        names.add(backend_config._DEFAULT_DEPLOYMENT)
    return names


def _active_kinds() -> "set[str]":
    """The record kinds an active backend serves: compose-api and every active provider's backends."""
    from vivarium_workbench.lib.backends import by_kind

    out = set()
    for kind in KINDS:
        try:
            by_kind(kind)
        except KeyError:
            continue
        out.add(kind)
    return out


def refusal(rid: str, error: str, *, reason: str = "other-backend", phase: str = "other-backend",
            dispatched_to: Optional[str] = None) -> "tuple[dict, int]":
    """The one shape every refusal answers (409): terminal, so no poller retries it, and self-describing (the run's
    id under ``simulation_id`` and ``run``, the reason, and where the run went when that is known)."""
    body = {"error": error, "reason": reason, "phase": phase, "terminal": True, "reachable": False,
            "simulation_id": rid, "run": rid}
    if dispatched_to:
        body["dispatched_to"] = dispatched_to
    return body, 409


def not_installed(rid: str, error: str) -> "tuple[dict, int]":
    """Providers off: the request names something (a run no active backend owns, a build or an analysis) that only an
    installed provider could answer. Refused like a record whose provider is not active (terminal 409). One body for
    the run views and the trace (step P2, Runs b2; moved from ``remote_run_views``)."""
    return refusal(rid, error, reason="backend-not-installed", phase="backend-not-installed")


def unowned(rid: str) -> "tuple[dict, int]":
    """:func:`not_installed` for a run id no active backend owns."""
    return not_installed(rid, f"run {rid} belongs to no backend an installed provider serves in this workbench")


def _other_backend(record: dict, why: str) -> "tuple[dict, int]":
    now = configured_url(record.get("kind") or "")
    return refusal(str(record.get("id") or ""),
                   f"this run was dispatched to {record.get('deployment')} ({record.get('base_url')}); this "
                   f"workbench is configured for {now or 'no such backend'} ({why})",
                   dispatched_to=record.get("deployment"))


def _ambiguous(rid: str) -> Resolution:
    return Resolution(refusal=refusal(
        rid, f"run {rid} is recorded on more than one backend; open it from the Runs tab row it belongs to",
        reason="ambiguous"), via="record")


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
    # By (identity family, URL), as the module caches are: sms-legacy and viva-core share one entry, and a
    # compose-api at viva-core's URL (possible when a backend is named) has its own.
    now: "dict[tuple[object, str], Optional[str]]" = {}
    refreshed: "set[tuple[object, str]]" = set()

    def identity(kind: str, *, fresh: bool = False) -> Optional[str]:
        if not has_identity(kind):
            return None
        url = configured_url(kind)
        if not url:
            return None
        key = _key(url, kind)
        if fresh and key not in refreshed:
            refreshed.add(key)
            now[key] = refresh_server_id(url, kind=kind)
        elif key not in now:
            now[key] = fetch_server_id(url, kind=kind)
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
    from vivarium_workbench.lib import backend_config, backends

    rid = str(remote_id if remote_id is not None else "").strip()
    owner = backends.for_run(rid)
    by_form = owner.name if owner else None
    here_name = backend_config.deployment_name()
    found = find_records(ws_root, rid, study_db=study_db) if rid else []
    records = [f.record for f in found if f.record]
    legacy = [f.legacy_source for f in found if f.record is None and f.legacy_source]
    if deployment:
        # The caller (a Runs-tab row) says which deployment it means: only rows of that deployment count.
        records = [r for r in records if r.get("deployment") == deployment]
        legacy = [src for src in legacy if src == deployment]
        if not records and deployment not in _deployment_names_now():
            return Resolution(refusal=refusal(
                rid, f"this run was dispatched to {deployment}; this workbench is configured for {here_name}",
                dispatched_to=deployment), via="legacy-guard")
    if records:
        active = _active_kinds()
        inactive = sorted({r["kind"] for r in records} - active)
        if inactive and not ({r["kind"] for r in records} & active):
            # Step P1c: every record names a backend whose provider is not active (providers turned off): refuse,
            # whatever URL is configured now -- never route it by its id's form.
            return Resolution(refusal=refusal(
                rid, f"this run was dispatched to a {inactive[0]} backend, and no installed provider serves "
                     f"{inactive[0]} in this workbench", reason="backend-not-installed", phase="backend-not-installed"),
                via="record")
        res = _by_record(records, rid, here_name)
        if res.kind and res.kind not in active:
            # Step P1c: the record names a backend whose provider is not active (providers turned off). Refuse;
            # never route it by its id's form to whatever is configured now.
            return Resolution(refusal=refusal(
                rid, f"this run was dispatched to a {res.kind} backend, and no installed provider serves "
                     f"{res.kind} in this workbench", reason="backend-not-installed", phase="backend-not-installed"),
                via="record")
        return res
    if legacy and not (deployment and deployment == here_name):
        names = _deployment_names_now()
        if not (set(legacy) & names):
            name = sorted(set(legacy))[0]
            return Resolution(refusal=refusal(
                rid, f"this run was dispatched to {name}; this workbench is configured for {here_name}",
                dispatched_to=name), via="legacy-guard")
        return Resolution(kind=by_form, via="legacy-guard")
    return Resolution(kind=by_form, via="id-form")
