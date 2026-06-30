"""Run/inspect seam behind the `vdash run/rerun/runs/status/logs` commands.

Local-first: study/investigation/composite runs reuse the same lib orchestration
the dashboard server uses. `server=<url>` delegates to the existing HTTP
endpoints instead. Pure-ish: no argparse here, returns (dict, int) like the
server handlers so it is unit-testable.
"""
from __future__ import annotations

import json
from pathlib import Path

from vivarium_dashboard.lib import composite_runs as cr
from vivarium_dashboard.lib import study_runs
from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def _post_server(base_url: str, route: str, payload: dict) -> tuple[dict, int]:
    import urllib.request
    import urllib.error
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + route, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode()), r.status
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()}, e.code
    except urllib.error.URLError as e:
        return {"error": f"could not reach server {base_url}: {e.reason}"}, 502


def run_study(ws_root, study, *, variant=None, steps=None, params=None,
              dry_run=False, detach=False, server=None) -> tuple[dict, int]:
    if server and dry_run:
        return (
            {"error": "--dry-run is local-only; drop --server to preview a run"},
            400,
        )
    body = {"study": study}
    if steps is not None:
        body["steps"] = int(steps)
    if params:
        body["overrides"] = dict(params)
    if variant:
        body["variant"] = variant
    if server:
        route = "/api/study-run-variant" if variant else "/api/study-run-baseline"
        return _post_server(server, route, body)
    if dry_run:
        body["dry_run"] = True
    if variant:
        return study_runs.run_study_variant(ws_root, body)
    return study_runs.run_study_baseline(ws_root, body)


def run_investigation(ws_root, name, *, studies=None, server=None) -> tuple[dict, int]:
    body = {"name": name}
    if studies:
        body["studies"] = list(studies)
    if server:
        return _post_server(server, "/api/investigation-run", body)
    from vivarium_dashboard.lib import investigation_run_views
    return investigation_run_views.investigation_run(ws_root, body)


def run_composite(ws_root, spec_id, *, steps=5, emit_paths=None,
                  params=None, dry_run=False, detach=False) -> tuple[dict, int]:
    from vivarium_dashboard.lib import composite_test_run_views
    body = {"id": spec_id, "steps": int(steps),
            "emit_paths": list(emit_paths or [])}
    if params:
        body["overrides"] = dict(params)
    if dry_run:
        run_id = cr.generate_run_id(spec_id, params or {})
        return {"dry_run": True, "request": {
            "spec_id": spec_id, "steps": int(steps),
            "emit_paths": list(emit_paths or []),
            "overrides": dict(params or {}),
            "run_id": run_id}}, 200
    return composite_test_run_views.composite_test_run(ws_root, body)


def find_run(ws_root, run_id) -> tuple[str | None, dict | None]:
    wp = WorkspacePaths.load(ws_root)
    candidates = [str(wp.pbg / "composite-runs.db")]
    for sd in wp.iter_study_dirs():
        candidates.append(str(sd / "runs.db"))
    for db_file in candidates:
        if not Path(db_file).is_file():
            continue
        conn = cr.connect(db_file)
        try:
            row = cr.query_run_meta(conn, run_id=run_id)
        finally:
            conn.close()
        if row:
            return db_file, row
    return None, None


def list_study_runs(ws_root, study) -> list[dict]:
    wp = WorkspacePaths.load(ws_root)
    db_file = wp.study_dir(study) / "runs.db"
    if not db_file.is_file():
        return []
    conn = cr.connect(str(db_file))
    try:
        return cr.query_all_runs(conn)
    finally:
        conn.close()


def rerun(ws_root, run_id, *, steps=None, detach=False) -> tuple[dict, int]:
    """Replay a previously recorded run as a composite run.

    Looks up ``run_id`` across all known DBs (study runs.db files and the
    workspace-level composite-runs.db), then calls ``run_composite`` with the
    recorded ``spec_id``, ``params``, and ``n_steps`` (overridden by ``steps``
    if supplied).

    Note: runs that originally executed through the study path (baseline /
    variant) are replayed as composite runs — they are not re-resolved through
    the study path and do not restore study-specific emit paths or runtime
    config.  The recorded ``spec_id`` + params + step count are faithfully
    reproduced; only the landing DB changes (composite-runs.db, not the
    study's runs.db).
    """
    db_file, row = find_run(ws_root, run_id)
    if row is None:
        return {"error": f"run not found: {run_id}"}, 404
    # query_run_meta already deserializes params_json -> params (dict)
    overrides = row.get("params") or {}
    n_steps = int(steps) if steps is not None else int(row.get("n_steps") or 5)
    return run_composite(ws_root, row["spec_id"], steps=n_steps,
                         params=overrides, emit_paths=[], detach=detach)


def read_run_log(ws_root, run_id) -> str | None:
    _db, row = find_run(ws_root, run_id)
    if not row or not row.get("log_path"):
        return None
    p = Path(ws_root) / row["log_path"]
    return p.read_text(encoding="utf-8") if p.is_file() else None
