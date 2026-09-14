"""Read a remote analysis's rendered figures + ptools straight from S3.

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

import os
from typing import Optional, Tuple

# The bucket is in the AWS GovCloud partition; default there but let the env win.
_DEFAULT_REGION = "us-gov-west-1"

# Serve small rendered artifacts only — never invite a bulk parquet pull here.
_FIGURE_SUFFIXES = (".html", ".svg", ".png")
_PTOOLS_SUFFIXES = (".tsv", ".csv", ".json")


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
    try:
        while True:
            kw = {"Bucket": bucket, "Prefix": base}
            if token:
                kw["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kw)
            for obj in resp.get("Contents", []) or []:
                key = obj.get("Key", "")
                if key.lower().endswith(suffixes):
                    out.append({
                        "path": key[len(root):] if key.startswith(root) else key,
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


def list_remote_analysis_figures(client, simulation_id: int) -> dict:
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
            # sms-api write-side gap; surface the row so the caller can flag it.
            rows.append({"name": name, "status": status, "result_uri": None,
                         "figures": [], "ptools": []})
            continue
        saw_result_uri = True
        parsed = parse_s3_uri(result_uri)
        if not parsed:
            rows.append({"name": name, "status": status, "result_uri": result_uri,
                         "figures": [], "ptools": []})
            continue
        bucket, prefix = parsed
        figures = _list_prefix(s3, bucket, prefix + "/viz", prefix, _FIGURE_SUFFIXES)
        ptools = _list_prefix(s3, bucket, prefix + "/ptools", prefix, _PTOOLS_SUFFIXES)
        rows.append({"name": name, "status": status, "result_uri": result_uri,
                     "figures": figures, "ptools": ptools})

    if not rows:
        return {"available": False, "reason": "no-analyses", "analyses": []}
    if not saw_result_uri:
        reason = "no-result-uri" if saw_completed else "no-completed-analysis"
        return {"available": False, "reason": reason, "analyses": rows}
    return {"available": True, "reason": "ok", "analyses": rows}


def study_remote_figures(ws_root, client, slug: str, max_sims: int = 10,
                         per_sim: int = 8, per_ptools: int = 60) -> dict:
    """Aggregate S3 figures + ptools across a study's COMPLETED remote sims, so
    the study Visualizations/Analyses tabs can render them via the remote setting.

    Volume-capped (a study can map hundreds of remote sims, each analysis holding
    hundreds of figures): at most ``max_sims`` sims, ``per_sim`` figure/ptools
    paths each, with the true totals reported so the UI can say "showing N of M".
    Figure bytes are served lazily by ``/api/remote-analysis-figure``."""
    try:
        from vivarium_workbench.lib import simulations_index
        data = simulations_index.build_simulations_data_cached(ws_root, include_remote=True)
        sims = data.get("simulations") or []
    except Exception:
        return {"available": False, "reason": "no-sims", "study": slug, "sims": []}

    cand = []
    for s in sims:
        if s.get("study_slug") == slug and s.get("status") == "completed":
            ro = s.get("remote_origin") or {}
            sid = ro.get("simulation_id")
            if sid:
                cand.append((sid, s.get("sim_name") or s.get("label") or str(sid)))

    out_sims: list = []
    total_figures = 0
    # Scan candidates until we've collected max_sims sims that actually HAVE
    # figures, bounded by scan_cap so a study whose first rows lack rendered
    # analyses (null result_uri / not-yet-analyzed) still surfaces the ones that
    # do, without probing all N (each probe is an sms-api + S3 round-trip).
    scan_cap = max(max_sims * 6, 40)
    scanned = 0
    for sid, name in cand:
        if len(out_sims) >= max_sims or scanned >= scan_cap:
            break
        scanned += 1
        res = list_remote_analysis_figures(client, sid)
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

    return {
        "available": bool(out_sims),
        "reason": "ok" if out_sims else ("no-figures" if cand else "no-remote-sims"),
        "study": slug, "sims": out_sims,
        "total_completed_remote_sims": len(cand), "shown_sims": len(out_sims),
        "scanned_sims": scanned, "total_figures_across_shown": total_figures,
    }


def fetch_by_analysis(client, simulation_id: int, analysis_name: str,
                      relpath: str) -> Optional[Tuple[bytes, str]]:
    """Resolve ``analysis_name``'s ``result_uri`` on ``simulation_id`` (so the
    caller never passes a raw S3 uri), then fetch ``relpath`` under it. Returns
    ``(body, content_type)`` or None (unknown analysis, null result_uri, missing
    object, or a traversal attempt)."""
    try:
        analyses = client.list_analyses(simulation_id)
    except Exception:
        return None
    result_uri = None
    for a in analyses:
        if (a.get("name") or a.get("job_name")) == analysis_name and a.get("result_uri"):
            result_uri = a["result_uri"]
            break
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
