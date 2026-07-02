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
from pathlib import Path


# emitter tag -> capitalized label the UI pills key on (mirrors server.py).
_EMITTER_LABEL = {"sqlite": "SQLite", "parquet": "Parquet", "xarray": "XArray", "none": "—"}


def _sms_api_base() -> str:
    return os.environ.get("SMS_API_BASE", "http://localhost:8080")


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
            "deployment": f"build #{sid}",
            "simulation_id": db_id,
            "experiment_id": experiment_id,
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


def list_remote_simulations(ws_root: Path, base_url: str | None = None) -> list[dict]:
    """Remote runs for the active build's (repo, commit), or ``[]``.

    Returns ``[]`` when the workspace is not a materialized remote build, or
    when sms-api is unreachable — never raises, so a down tunnel can't break the
    local Simulations DB listing.
    """
    bm = _read_build_meta(ws_root)
    if not bm:
        return []
    active_id = bm.get("simulator_id")
    if active_id is None:
        return []
    try:
        from vivarium_dashboard.lib.sms_api_client import SmsApiClient, SmsApiError
    except ImportError:
        return []
    client = SmsApiClient(base_url or _sms_api_base())
    try:
        builds = _builds_list(client.list_simulators())
    except SmsApiError:
        return []
    except Exception:
        return []

    # Resolve the active build's (repo, commit), then the set of builds sharing
    # it (a commit may be built more than once). Fall back to the build-meta's
    # own commit if the active id isn't in the versions list.
    by_id = {_build_id(b): b for b in builds}
    active = by_id.get(active_id)
    repo = (active or {}).get("git_repo_url") if active else None
    commit = _short((active or {}).get("git_commit_hash") if active else bm.get("commit"))
    if not commit:
        return []
    matching = {
        _build_id(b) for b in builds
        if _short(b.get("git_commit_hash")) == commit
        and (repo is None or b.get("git_repo_url") == repo)
    }
    matching.add(active_id)

    try:
        sims = client.list_build_simulations(active_id)
    except SmsApiError:
        return []
    except Exception:
        return []
    if not isinstance(sims, list):
        return []

    rows = [_normalize(rec) for rec in sims
            if isinstance(rec, dict) and rec.get("simulator_id") in matching]
    rows.sort(key=lambda r: r.get("started_at") or 0.0, reverse=True)
    return rows
