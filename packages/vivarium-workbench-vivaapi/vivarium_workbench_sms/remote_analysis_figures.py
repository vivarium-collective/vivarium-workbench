"""Read a remote analysis's rendered figures + ptools tables.

Two sources, tried in this order for each simulation:

1. **Registered datasets** (viva-core, ``viva-v1-datasets``). A deployment that
   advertises the capability registers an analysis's rendered figures and ptools
   tables as single-file datasets, with ``source = simulation:<id>`` and (from the
   registry's walk) the analysis directory in ``attributes.analysis_dir``. They are
   placed by folder (``viz/``, ``ptools/``), not by kind, so the ``ptools/`` listing is
   the same set of files as on S3 (see ``_BUCKET_BY_FOLDER``). The bytes are the same
   objects, served through the API. The listing is ``GET /viva/v1/datasets?source=...``
   and the bytes come from ``GET /viva/v1/datasets/{id}/content``: through the API,
   with **no storage credentials** in the workbench. This includes in-region toolkit
   re-fires that have no analysis record (the registry walks the output prefix).

2. **S3 directly** (the legacy path, below). Used when the backend does not
   advertise ``viva-v1-datasets``, or advertises it but has registered nothing for
   this simulation (a registry not yet backfilled -- prod at 0.9.182 has ~800 figure
   datasets against ~150k on stanford-test). It is removed once every site's
   registry covers its analyses (docs/backend-adapters.md, Appendix A, old step 4).

Both produce the same listing shape. A simulation is listed from BOTH when the
backend serves the registry: the registry's analyses, plus any analysis directory
S3 has that the registry has not registered yet (a partial backfill must not hide
analyses the legacy path showed). The registry names an analysis by its output
directory; S3 rows keep the analysis record's name. ``fetch_by_analysis`` tries
the registry with the name it is given, then S3, so either kind of name resolves.

The legacy S3 path, as it was:

A completed GovCloud analysis (sms-api ``GET /api/v1/simulations/{id}/analyses``)
carries a ``result_uri`` — an ``s3://`` prefix like
``s3://<bucket>/vecoli-output/<experiment>/analyses/analysis-mnp-...`` — that holds
its rendered outputs:

    <result_uri>/viz/*.html      rendered figures (e.g. chromosome_state_view__*.html)
    <result_uri>/ptools/*.tsv    ptools / EcoCyc overlay tables
    <result_uri>/analysis.json   the raw analysis payload

The sms-api ``/analyses/{id}/plots`` and ``/simulations/{id}/data`` paths stream
the *whole* native-store parquet/tar through the SSM tunnel and time out. But the
rendered ``viz/*.html`` and ``ptools/*.tsv`` are small objects, and small-object
S3 GETs sign fine from the laptop (the ``RequestTimeTooSkewed`` problem is a
DuckDB-httpfs *bulk*-read issue, not plain boto3 GETs). So we read the figures
directly from ``result_uri`` instead — the "accessible through the remote setting"
path.

Credentials: boto3's default chain. When the workbench is served against GovCloud
the process env supplies them (e.g. ``AWS_PROFILE=stanford-sso``); region defaults
to the GovCloud partition (``us-gov-west-1``) but honours ``AWS_DEFAULT_REGION`` /
``AWS_REGION``. Every entry point degrades gracefully — missing boto3, missing
credentials, an unreachable bucket, or a null ``result_uri`` all return an empty /
"unavailable" result rather than raising, so a caller can fall back to the legacy
land-the-tar path.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional, Tuple

log = logging.getLogger(__name__)

# The bucket is in the AWS GovCloud partition; default there but let the env win.
_DEFAULT_REGION = "us-gov-west-1"

# Serve small rendered artifacts only — never invite a bulk parquet pull here.
_FIGURE_SUFFIXES = (".html", ".svg", ".png")
_PTOOLS_SUFFIXES = (".tsv", ".csv", ".json")

# Figure base names to hide from the gallery. ``chromosome_state_view`` renders
# one HTML per timestep/agent (hundreds per sim — the ``t = N min · oriC = K``
# circles), which floods Visualizations with little analytical value. The files
# stay in S3; they are just filtered out of the listing. Matched on the base
# name — the part of ``viz/<name>__variant=..._gen=..._agent=....html`` before
# the first ``__``.
_HIDDEN_FIGURE_NAMES = {"chromosome_state_view"}


def _figure_base_name(path: str) -> str:
    """``viz/chromosome_state_view__variant=0_....html`` -> ``chromosome_state_view``.

    The name is the part before the first ``__`` (variant/seed/gen/agent suffix);
    for a figure with no such suffix, drop the file extension instead."""
    stem = path.rsplit("/", 1)[-1].split("__", 1)[0]
    for suf in _FIGURE_SUFFIXES:
        if stem.lower().endswith(suf):
            return stem[: -len(suf)]
    return stem


# ---------------------------------------------------------------------------
# Source 1: registered datasets (viva-core /viva/v1/datasets)
# ---------------------------------------------------------------------------

#: Listing bucket by the file's folder inside the analysis directory, as the S3 path lists them. Placed
#: by folder, not by dataset kind, because the PTools contract is the folder (``**/ptools/*.tsv``, what
#: pbg-ptools globs). The registry's ``ptools-analysis`` kind is narrower on purpose: only Omics Viewer
#: time series (``ptools_*``); the other tables v2ecoli writes to ``ptools/`` (``cd1_*``) are ``analysis``.
_BUCKET_BY_FOLDER = {"viz": ("figures", _FIGURE_SUFFIXES), "ptools": ("ptools", _PTOOLS_SUFFIXES)}
_DATASET_PAGE = 200            # viva-core's DATASET_LIST_MAX_LIMIT
_DATASET_MAX_PAGES = 50        # 10k rows per simulation; past that the listing says "truncated"
_CAPS_TTL = 60.0
_CAPS_FAIL_TTL = 10.0          # an unreachable backend is re-probed sooner, but not once per figure

_caps_lock = threading.Lock()
_caps_cache: dict = {}         # base_url -> (served: bool, checked_at: float, ttl: float)


def datasets_served(client) -> bool:
    """True when the backend advertises ``viva-v1-datasets``. Cached per base URL, because a
    gallery fetches one figure per request. Never raises: an unreachable or capability-less
    backend means "use the legacy path" (cached briefly, so an outage costs one probe per
    ``_CAPS_FAIL_TTL``, not one per figure)."""
    base = getattr(client, "base_url", None)
    now = time.monotonic()
    if base is not None:
        with _caps_lock:
            hit = _caps_cache.get(base)
        if hit and now - hit[1] < hit[2]:
            return hit[0]
    try:
        from vivarium_workbench_sms.sms_api_client import (
            CAPABILITY_VIVA_V1_DATASETS, fetch_capabilities)
        served, ttl = fetch_capabilities(client).supports(CAPABILITY_VIVA_V1_DATASETS), _CAPS_TTL
    except Exception as e:
        log.warning("capability check failed for %s; using S3 for analysis figures: %s", base, e)
        served, ttl = False, _CAPS_FAIL_TTL
    if base is not None:
        with _caps_lock:
            _caps_cache[base] = (served, now, ttl)
    return served


def _path_in_analysis(uri: str, analysis_dir: str) -> Optional[str]:
    """``.../analyses/<analysis_dir>/viz/x.html`` -> ``viz/x.html``; None if the uri does not
    contain the directory."""
    marker = f"/{analysis_dir}/"
    i = (uri or "").rfind(marker)
    return uri[i + len(marker):] if i >= 0 else None


def _analysis_dir_of(dataset: dict) -> Optional[str]:
    """The analysis directory a registered file belongs to. The registry's walk records it as
    ``attributes.analysis_dir``; a row registered from a run event may not, so fall back to the
    path segment after ``/analyses/`` in its uri. None when neither places it."""
    adir = (dataset.get("attributes") or {}).get("analysis_dir")
    if isinstance(adir, str) and adir and "/" not in adir:
        return adir
    uri = dataset.get("uri") or ""
    i = uri.rfind("/analyses/")
    if i < 0:
        return None
    head, sep, _ = uri[i + len("/analyses/"):].partition("/")
    return head if sep and head else None


def _list_via_datasets(client, simulation_id: int) -> Tuple[list, list, bool]:
    """``(rows, errors, truncated)`` from the registry: one row per analysis directory, holding
    its ``viz/`` figures and ``ptools/`` tables.

    A listing failure is reported in ``errors`` (and logged), keeping the rows read before it.
    Rows are de-duplicated by dataset id, because offset paging over a newest-change-first order
    can repeat a row the registry touched mid-listing. (It can also skip one that moved behind
    the offset; the next listing picks it up.)"""
    rows: dict = {}
    seen: set = set()
    errors: list = []
    truncated = False
    offset = 0
    try:
        for _ in range(_DATASET_MAX_PAGES):
            page = client.list_datasets({"source": f"simulation:{int(simulation_id)}",
                                         "limit": _DATASET_PAGE, "offset": offset}) or {}
            for d in page.get("datasets") or []:
                if d.get("id") in seen:
                    continue
                seen.add(d.get("id"))
                adir = _analysis_dir_of(d)
                rel = _path_in_analysis(d.get("uri") or "", adir) if adir else None
                folder = rel.split("/", 1)[0] if rel and "/" in rel else None
                if folder not in _BUCKET_BY_FOLDER:
                    continue
                bucket, suffixes = _BUCKET_BY_FOLDER[folder]
                if not rel.lower().endswith(suffixes):
                    continue
                if bucket == "figures" and _figure_base_name(rel) in _HIDDEN_FIGURE_NAMES:
                    continue
                row = rows.setdefault(adir, {"name": adir, "status": "completed", "result_uri": None,
                                             "figures": [], "ptools": [], "source": "datasets"})
                row[bucket].append({"path": rel, "dataset_id": d.get("id"), "size": d.get("size_bytes") or 0})
            nxt = page.get("next_offset")
            if nxt is None:
                break
            offset = int(nxt)
        else:
            truncated = True
    except Exception as e:
        log.warning("listing the datasets of simulation %s failed: %s", simulation_id, e)
        errors.append(str(e))
    for row in rows.values():
        row["figures"].sort(key=lambda f: f["path"])
        row["ptools"].sort(key=lambda f: f["path"])
    return [rows[k] for k in sorted(rows)], errors, truncated


def _fetch_via_datasets(client, simulation_id: int, analysis_dir: str,
                        relpath: str) -> Optional[Tuple[bytes, str]]:
    """One registered file's bytes through the API, or None when the registry has no file at
    ``<analysis_dir>/<relpath>`` for this simulation. Found by a uri substring (``q``), which
    works whether or not the row carries ``analysis_dir``. viva-api runs ``q`` as an unescaped
    ILIKE, so ``_`` matches any character: the match is then checked exactly.
    Transport errors propagate."""
    page = client.list_datasets({"source": f"simulation:{int(simulation_id)}",
                                 "q": f"/{analysis_dir}/{relpath}", "limit": _DATASET_PAGE}) or {}
    for d in page.get("datasets") or []:
        if _path_in_analysis(d.get("uri") or "", analysis_dir) == relpath and d.get("id") is not None:
            return client.dataset_content(int(d["id"])), _content_type(relpath)
    return None


# ---------------------------------------------------------------------------
# Source 2: S3 directly (legacy; see the module docstring)
# ---------------------------------------------------------------------------


def _region() -> str:
    return os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or _DEFAULT_REGION


def parse_s3_uri(uri: str) -> Optional[Tuple[str, str]]:
    """``s3://bucket/key/prefix`` -> ``(bucket, "key/prefix")``; None if not s3://."""
    if not uri or not isinstance(uri, str) or not uri.startswith("s3://"):
        return None
    rest = uri[len("s3://"):]
    if "/" not in rest:
        return rest, ""
    bucket, key = rest.split("/", 1)
    return bucket, key.rstrip("/")


def _s3_client():
    """A boto3 S3 client, or None if boto3 / credentials are unavailable.

    Never raises — the caller treats None as "remote figures unavailable, fall
    back"."""
    try:
        import boto3  # type: ignore
        from botocore.exceptions import BotoCoreError, NoCredentialsError  # noqa: F401
    except Exception:
        return None
    try:
        return boto3.client("s3", region_name=_region())
    except Exception:
        return None


def _aws_creds_ok() -> bool:
    """True when AWS credentials currently resolve.

    Used to disambiguate an empty S3 walk: with valid creds an empty result
    means "no rendered figures", but with expired/absent creds every S3 list
    fails (swallowed → []) and looks identical. A cheap STS ``get_caller_identity``
    needs no S3 policy and fails fast on missing/expired creds. Best-effort —
    any error (no boto3, no creds, network) → False."""
    try:
        import boto3  # type: ignore
        boto3.client("sts", region_name=_region()).get_caller_identity()
        return True
    except Exception:
        return False


def _content_type(path: str) -> str:
    p = path.lower()
    if p.endswith(".html"):
        return "text/html; charset=utf-8"
    if p.endswith(".svg"):
        return "image/svg+xml"
    if p.endswith(".png"):
        return "image/png"
    if p.endswith(".json"):
        return "application/json"
    if p.endswith((".tsv", ".csv")):
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def _list_prefix(s3, bucket: str, list_under: str, rel_to: str, suffixes: tuple) -> list:
    """List object keys under ``list_under`` whose basename ends in one of
    ``suffixes``. ``path`` is the key relative to ``rel_to`` (the analysis
    ``result_uri`` prefix), so it round-trips through ``fetch_remote_analysis_
    object`` — e.g. ``viz/chromosome_state_view__....html``. Empty on any error."""
    out: list = []
    token = None
    base = list_under.rstrip("/") + "/"
    root = rel_to.rstrip("/") + "/"
    is_viz = base.endswith("/viz/")
    try:
        while True:
            kw = {"Bucket": bucket, "Prefix": base}
            if token:
                kw["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kw)
            for obj in resp.get("Contents", []) or []:
                key = obj.get("Key", "")
                if key.lower().endswith(suffixes):
                    rel = key[len(root):] if key.startswith(root) else key
                    if is_viz and _figure_base_name(rel) in _HIDDEN_FIGURE_NAMES:
                        continue
                    out.append({
                        "path": rel,
                        "key": key,
                        "size": obj.get("Size", 0),
                    })
            if resp.get("IsTruncated") and resp.get("NextContinuationToken"):
                token = resp["NextContinuationToken"]
            else:
                break
    except Exception:
        return []
    return out


def _list_analysis_dirs(s3, bucket: str, analyses_prefix: str) -> list:
    """List the immediate analysis subdirectories under ``<out_uri>/analyses/`` —
    one per analysis, INCLUDING in-region toolkit re-fires written by raw
    ``aws s3 cp`` that create no sms-api DB record. Returns ``[(name, dir_key)]``;
    empty on any error."""
    out: list = []
    base = analyses_prefix.rstrip("/") + "/"
    token = None
    try:
        while True:
            kw = {"Bucket": bucket, "Prefix": base, "Delimiter": "/"}
            if token:
                kw["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kw)
            for cp in resp.get("CommonPrefixes", []) or []:
                p = (cp.get("Prefix") or "").rstrip("/")
                if p:
                    out.append((p.rsplit("/", 1)[-1], p))
            if resp.get("IsTruncated") and resp.get("NextContinuationToken"):
                token = resp["NextContinuationToken"]
            else:
                break
    except Exception:
        return []
    return out


def _analyses_root(client, simulation_id: int, analyses: list) -> Optional[Tuple[str, str]]:
    """Resolve ``(bucket, "<...>/analyses" key)`` for a sim — from any analysis's
    ``result_uri`` (its grandparent), or the sim's output prefix via
    ``get_simulation``. None if unresolvable. Lets us walk S3 for analysis dirs
    that have no DB record."""
    for a in (analyses or []):
        ru = a.get("result_uri")
        parsed = parse_s3_uri(ru) if ru else None
        if parsed and "/analyses/" in parsed[1]:
            bucket, key = parsed
            return bucket, key.rsplit("/analyses/", 1)[0] + "/analyses"
    try:
        sim = client.get_simulation(simulation_id) or {}
    except Exception:
        return None
    cfg = sim.get("config") or {}
    out_uri = ((cfg.get("emitter_arg") or {}).get("out_uri")) or cfg.get("out_uri") or cfg.get("outdir")
    parsed = parse_s3_uri(out_uri) if out_uri else None
    if parsed:
        bucket, key = parsed
        return bucket, key.rstrip("/") + "/analyses"
    return None


def list_remote_analysis_figures(client, simulation_id: int, *,
                                 use_datasets: Optional[bool] = None) -> dict:
    """List ``simulation_id``'s rendered figures and ptools tables, one row per analysis.

    Without the registry, this is the S3 listing (:func:`_list_via_s3`). With it, the
    registry's analyses come first, and S3 adds only the analysis directories the registry
    has not registered. ``via`` is ``datasets``, ``s3`` or ``datasets+s3``, by where the
    files shown came from. ``errors`` lists registry failures; ``truncated`` means the
    registry listing hit its page cap. ``use_datasets`` skips the capability check when the
    caller already made it."""
    if use_datasets is None:
        use_datasets = datasets_served(client)
    if not use_datasets:
        return {**_list_via_s3(client, simulation_id), "via": "s3"}

    reg_rows, errors, truncated = _list_via_datasets(client, simulation_id)
    if reg_rows and not _s3_readable():
        # No credentials (the deployment this path exists for): the registry is all there is to show,
        # and walking S3 would only pay the credential chain's timeouts on every request.
        legacy: dict = {"analyses": []}
    else:
        legacy = _list_via_s3(client, simulation_id)
    by_dir = {r["name"]: r for r in reg_rows}
    extra: list = []
    s3_files = False
    for r in legacy.get("analyses") or []:
        has_files = bool(r.get("figures") or r.get("ptools"))
        reg = by_dir.get(_s3_row_dir(r) or "")
        if reg is not None:
            # Same directory from both: add the files the registry has not registered yet.
            for bucket in ("figures", "ptools"):
                known = {f["path"] for f in reg[bucket]}
                added = [f for f in r.get(bucket) or [] if f.get("path") not in known]
                if added:
                    reg[bucket] = sorted(reg[bucket] + added, key=lambda f: f["path"])
                    s3_files = True
        elif has_files or not reg_rows:
            extra.append(r)
            s3_files = s3_files or has_files
    out: dict = {"analyses": reg_rows + extra,
                 "via": ("datasets+s3" if s3_files else "datasets") if reg_rows else "s3"}
    if reg_rows or legacy.get("available"):
        out.update(available=True, reason="ok")
    else:
        out.update(available=False, reason="datasets-error" if errors else legacy.get("reason", "no-analyses"))
    if errors:
        out["errors"] = errors
    if truncated:
        out["truncated"] = True
    return out


_S3_CHECK_TTL = 60.0
_s3_check: list = []   # [(readable: bool, checked_at: float)]


def _s3_readable() -> bool:
    """Whether S3 credentials resolve, checked at most once a minute (the check itself can wait on
    the credential chain's metadata lookups when there are none)."""
    now = time.monotonic()
    with _caps_lock:
        if _s3_check and now - _s3_check[0][1] < _S3_CHECK_TTL:
            return _s3_check[0][0]
    ok = _aws_creds_ok()
    with _caps_lock:
        _s3_check[:] = [(ok, now)]
    return ok


def _s3_row_dir(row: dict) -> Optional[str]:
    """The analysis directory an S3-listed row stands for: the last segment of its
    ``result_uri``, else its name (an S3-only row is named by its directory)."""
    uri = row.get("result_uri")
    if isinstance(uri, str) and uri.rstrip("/"):
        return uri.rstrip("/").rsplit("/", 1)[-1]
    return row.get("name")


def _list_via_s3(client, simulation_id: int) -> dict:
    """For every completed analysis on ``simulation_id`` with a non-null
    ``result_uri``, list its S3 ``viz/`` figures and ``ptools/`` tables.

    Returns::

        {"available": bool, "reason": str, "analyses": [
            {"name", "status", "result_uri",
             "figures": [{path,key,size}...], "ptools": [{path,key,size}...]},
        ]}

    ``available`` is False (with a ``reason``) when boto3/creds are missing or no
    completed analysis has a populated ``result_uri`` — so the caller can fall
    back to the legacy land-the-tar path instead of showing an empty tab."""
    try:
        analyses = client.list_analyses(simulation_id)
    except Exception:
        analyses = []

    s3 = _s3_client()
    if s3 is None:
        return {"available": False, "reason": "s3-unavailable", "analyses": []}

    rows: list = []
    covered: set = set()   # analysis-dir names already surfaced via a DB record
    saw_completed = False
    saw_result_uri = False
    for a in analyses:
        status = str(a.get("status") or "").lower()
        result_uri = a.get("result_uri")
        name = a.get("name") or a.get("job_name") or ""
        if status == "completed":
            saw_completed = True
        if not result_uri:
            # completed-but-null result_uri where objects may still exist is the
            # sms-api write-side gap; surface the row so the caller can flag it
            # (the S3 walk below may still find its figures under a re-fire dir).
            rows.append({"name": name, "status": status, "result_uri": None,
                         "figures": [], "ptools": [], "source": "record"})
            continue
        saw_result_uri = True
        parsed = parse_s3_uri(result_uri)
        if not parsed:
            rows.append({"name": name, "status": status, "result_uri": result_uri,
                         "figures": [], "ptools": [], "source": "record"})
            continue
        bucket, prefix = parsed
        figures = _list_prefix(s3, bucket, prefix + "/viz", prefix, _FIGURE_SUFFIXES)
        ptools = _list_prefix(s3, bucket, prefix + "/ptools", prefix, _PTOOLS_SUFFIXES)
        covered.add(prefix.rsplit("/", 1)[-1])
        rows.append({"name": name, "status": status, "result_uri": result_uri,
                     "figures": figures, "ptools": ptools, "source": "record"})

    # S3-prefix-walk UNION: surface analysis dirs on S3 that have no sms-api DB
    # record. In-region toolkit re-fires write figures via raw `aws s3 cp` to
    # <out_uri>/analyses/analysis-ptools-<scale>/ without registering a record —
    # so a re-fire auto-surfaces here (S3 is the source of truth), no write-side
    # result_uri backfill needed.
    root = _analyses_root(client, simulation_id, analyses)
    if root:
        rbucket, rkey = root
        for dname, dkey in _list_analysis_dirs(s3, rbucket, rkey):
            if dname in covered:
                continue
            figs = _list_prefix(s3, rbucket, dkey + "/viz", dkey, _FIGURE_SUFFIXES)
            pts = _list_prefix(s3, rbucket, dkey + "/ptools", dkey, _PTOOLS_SUFFIXES)
            if figs or pts:
                covered.add(dname)
                saw_result_uri = True
                rows.append({"name": dname, "status": "completed",
                             "result_uri": f"s3://{rbucket}/{dkey}",
                             "figures": figs, "ptools": pts, "source": "s3"})

    if not rows:
        return {"available": False, "reason": "no-analyses", "analyses": []}
    if not saw_result_uri:
        reason = "no-result-uri" if saw_completed else "no-completed-analysis"
        return {"available": False, "reason": reason, "analyses": rows}
    return {"available": True, "reason": "ok", "analyses": rows}


def _this_backends_sms_run(ws_root, sim_id, deployment, study) -> bool:
    """Step 1c-2: a candidate from the local index may be another deployment's run, or not an SMS simulation at
    all; only this backend's SMS simulations are asked for figures."""
    from vivarium_workbench.lib import backends

    return backends.for_existing(ws_root, sim_id, deployment=deployment, study=study) is backends.SMS_LEGACY


def study_remote_figures(ws_root, client, slug: str, max_sims: int = 10,
                         per_sim: int = 8, per_ptools: int = 60) -> dict:
    """Aggregate S3 figures + ptools across a study's COMPLETED remote sims, so
    the study Visualizations/Analyses tabs can render them via the remote setting.

    Volume-capped (a study can map hundreds of remote sims, each analysis holding
    hundreds of figures): at most ``max_sims`` sims, ``per_sim`` figure/ptools
    paths each, with the true totals reported so the UI can say "showing N of M".
    Figure bytes are served lazily by ``/api/remote-analysis-figure``."""
    # Candidate remote sims for this study. Source from the authoritative
    # remote-sim list (``list_remote_simulations`` applies the workspace's
    # ``remote_run_study_map`` tagging) rather than the combined
    # ``build_simulations_data_cached`` index: that index appends remote rows
    # via a stale-while-revalidate cache which returns ZERO remote rows on a
    # COLD cache, silently collapsing this endpoint to "no-remote-sims" even for
    # a study with hundreds of tagged remote sims. Fall back to the combined
    # index only if the remote list is empty (covers remote runs that landed
    # locally with a runs_meta ``remote_origin`` the sms-api list may not echo).
    sims: list = []
    try:
        from vivarium_workbench_sms import remote_simulations as _rs
        sims = _rs.list_remote_simulations(ws_root) or []
    except Exception:
        sims = []
    # Listed live from the configured server: its own runs by construction. Only rows from the local index (below)
    # can be another deployment's, or not SMS simulations, and are checked against their records (step 1c-2).
    from_index = not sims
    if not sims:
        try:
            from vivarium_workbench.lib import simulations_index
            data = simulations_index.build_simulations_data_cached(
                ws_root, include_remote=True)
            sims = data.get("simulations") or []
        except Exception:
            return {"available": False, "reason": "no-sims", "study": slug, "sims": []}

    cand = []
    for s in sims:
        if s.get("study_slug") == slug and s.get("status") == "completed":
            ro = s.get("remote_origin") or {}
            sid = ro.get("simulation_id")
            if sid:
                cand.append((sid, s.get("sim_name") or s.get("label") or str(sid), ro.get("deployment")))

    out_sims: list = []
    total_figures = 0
    use_datasets = datasets_served(client) if cand else False
    read_s3 = False   # did any simulation fall back to S3? Only then can credentials explain "nothing"
    registry_failed = False
    truncated_sims = 0
    # Scan candidates until we've collected max_sims sims that actually HAVE
    # figures, bounded by scan_cap so a study whose first rows lack rendered
    # analyses (null result_uri / not-yet-analyzed) still surfaces the ones that
    # do, without probing all N (each probe is an sms-api + S3 round-trip).
    scan_cap = max(max_sims * 6, 40)
    scanned = 0
    for sid, name, deployment in cand:
        if len(out_sims) >= max_sims or scanned >= scan_cap:
            break
        scanned += 1
        if from_index and not _this_backends_sms_run(ws_root, sid, deployment, slug):
            continue  # bounded by scan_cap: at most that many record lookups per request
        res = list_remote_analysis_figures(client, sid, use_datasets=use_datasets)
        read_s3 = read_s3 or res.get("via") != "datasets"
        truncated_sims += 1 if res.get("truncated") else 0
        registry_failed = registry_failed or bool(res.get("errors"))
        if not res.get("available"):
            continue
        analyses = []
        for a in res["analyses"]:
            if a["figures"] or a["ptools"]:
                analyses.append({
                    "name": a["name"], "status": a["status"], "simulation_id": sid,
                    "n_figures": len(a["figures"]), "n_ptools": len(a["ptools"]),
                    "figures": [f["path"] for f in a["figures"][:per_sim]],
                    "ptools": [p["path"] for p in a["ptools"][:per_ptools]],
                })
                total_figures += len(a["figures"])
        if analyses:
            out_sims.append({"simulation_id": sid, "sim_name": name, "analyses": analyses})

    # Reason. Distinguish a genuine "no rendered figures" from the server simply
    # being unable to READ S3 (expired/absent AWS credentials): every S3 walk
    # swallows its exceptions and returns [], so an auth failure otherwise looks
    # identical to missing data. When candidates exist but nothing surfaced,
    # probe the credentials and report "s3-auth-error" so the UI can say
    # "figures are on S3 but the server can't read them (check AWS credentials)"
    # instead of the misleading "no figures".
    if out_sims:
        reason = "ok"
    elif not cand:
        reason = "no-remote-sims"
    elif registry_failed:
        reason = "datasets-error"   # the registry failed: never send the user to check AWS credentials
    elif read_s3 and not _aws_creds_ok():
        reason = "s3-auth-error"
    else:
        reason = "no-figures"
    return {
        "available": bool(out_sims),
        "reason": reason,
        "study": slug, "sims": out_sims,
        "total_completed_remote_sims": len(cand), "shown_sims": len(out_sims),
        "scanned_sims": scanned, "total_figures_across_shown": total_figures,
        # sims whose registry listing hit its page cap: their counts are lower bounds
        **({"truncated_sims": truncated_sims} if truncated_sims else {}),
    }


def fetch_by_analysis(client, simulation_id: int, analysis_name: str,
                      relpath: str) -> Optional[Tuple[bytes, str]]:
    """Resolve ``analysis_name``'s ``result_uri`` on ``simulation_id`` (so the
    caller never passes a raw S3 uri), then fetch ``relpath`` under it. Returns
    ``(body, content_type)`` or None (unknown analysis, null result_uri, missing
    object, or a traversal attempt).

    The registry first, when the backend serves it and ``analysis_name`` can be a
    directory name; S3 when it does not, or has no such file registered. The S3 path is
    unchanged, including for record names the registry cannot resolve."""
    rel = (relpath or "").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    if (analysis_name and "/" not in analysis_name and analysis_name != ".."
            and datasets_served(client)):
        try:
            got = _fetch_via_datasets(client, simulation_id, analysis_name, rel)
        except Exception as e:
            log.warning("registry fetch of %s/%s (simulation %s) failed; trying S3: %s",
                        analysis_name, rel, simulation_id, e)
            got = None
        if got:
            return got
    # Credential-less API fallback: sms-api serves the stored figure from S3 with
    # its OWN credentials via GET /api/v1/simulations/{id}/analysis-figure, so a
    # workbench host with no AWS credentials (the common case) still resolves the
    # figure. Tried before the direct-S3 path below, which needs this client's own
    # AWS credentials; best-effort, so any failure falls through to S3 unchanged.
    _api_figure = getattr(client, "analysis_figure", None)
    if callable(_api_figure):
        try:
            body = _api_figure(simulation_id, analysis_name, rel)
        except Exception:
            body = None
        if body:
            return body, _content_type(rel)
    try:
        analyses = client.list_analyses(simulation_id)
    except Exception:
        analyses = []
    result_uri = None
    for a in analyses:
        if (a.get("name") or a.get("job_name")) == analysis_name and a.get("result_uri"):
            result_uri = a["result_uri"]
            break
    if not result_uri:
        # S3-only analysis (toolkit re-fire with no DB record): construct its
        # result_uri as <out_uri>/analyses/<analysis_name> — the same dir the
        # union walk in list_remote_analysis_figures surfaced it from.
        root = _analyses_root(client, simulation_id, analyses)
        if root:
            rbucket, rkey = root
            result_uri = f"s3://{rbucket}/{rkey}/{analysis_name}"
    if not result_uri:
        return None
    return fetch_remote_analysis_object(result_uri, relpath)


def fetch_remote_analysis_object(result_uri: str, relpath: str) -> Optional[Tuple[bytes, str]]:
    """Fetch a single object under ``result_uri`` (e.g. ``viz/foo.html``).
    Returns ``(body_bytes, content_type)`` or None. ``relpath`` is confined to the
    ``result_uri`` prefix (no ``..`` traversal, no absolute keys)."""
    parsed = parse_s3_uri(result_uri)
    if not parsed:
        return None
    rel = (relpath or "").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    bucket, prefix = parsed
    key = prefix + "/" + rel
    s3 = _s3_client()
    if s3 is None:
        return None
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read(), _content_type(rel)
    except Exception:
        return None
