"""Reconcile ``remote-pending-<id>`` placeholder runs against live sms-api status.

Background: a UI-dispatched Cloud run writes a ``remote-pending-<sim_id>`` row
into the study ``runs.db`` (``remote_run_views``) so the Runs tab shows the
campaign immediately instead of only once someone lands it. That row is
``status='running'`` and stays that way until "Land Results" is clicked. If the
run finishes (or fails) on sms-api but is never landed, the placeholder is a
phantom "running" row forever — nothing ever asks sms-api whether simulation
``<id>`` actually finished.

This module closes that gap. :func:`reconcile_pending_placeholders` reads a
``live_status`` map (``sim_id -> sms-api status string``, built for free by the
background remote refresh) and terminalizes every placeholder whose sim is
done/failed, writing a JSONL event too so the terminal state survives the
Simulations-DB fold (the index is JSONL-authoritative — a bare sqlite update
would be re-overwritten by the surviving ``started`` event on the next fold). A
completed placeholder is relabelled "completed, not landed" (the "Land Results"
action keeps working); a placeholder whose sim id sms-api does not know (404) is
marked ``orphaned``.

:func:`enrich_placeholder_status` force-adds placeholder sim ids to that map so
placeholders older than the newest-N enrichment window still resolve — they are
exactly the ids the user cares about.
"""
from __future__ import annotations

import time
from pathlib import Path

# Sentinel value placed in ``live_status`` for a sim id sms-api returns 404 for
# (unknown simulation). Distinct from "unresolved" (absent key) and from any real
# sms-api status string, so the reconciler can mark it ``orphaned`` unambiguously.
NOT_FOUND = "__sms_api_404__"

_PENDING_PREFIX = "remote-pending-"


def _pending_sim_id(run_id: str) -> "int | None":
    """Extract the sms-api simulation id from a ``remote-pending-<id>`` run id."""
    if not run_id.startswith(_PENDING_PREFIX):
        return None
    try:
        return int(run_id[len(_PENDING_PREFIX):])
    except ValueError:
        return None


def _pending_run_dbs(ws_root: Path) -> list[Path]:
    """Every runs DB in the workspace that could hold a placeholder row.

    Resolves via WorkspacePaths (``simulations_index._discover_dbs``) so a
    workspace that relocates ``studies/`` via its ``layout:`` map is honoured.
    """
    from vivarium_workbench.lib import simulations_index
    try:
        return [db for db, _rel in simulations_index._discover_dbs(Path(ws_root))]
    except Exception:  # noqa: BLE001 — a broken layout must not crash the refresh
        return []


def placeholder_sim_ids(ws_root: Path) -> set[int]:
    """Sim ids of every still-``running`` ``remote-pending-*`` row in the workspace."""
    from vivarium_workbench.lib import composite_runs as cr
    ids: set[int] = set()
    for db in _pending_run_dbs(ws_root):
        try:
            conn = cr.connect(db)
        except Exception:  # noqa: BLE001
            continue
        try:
            rows = conn.execute(
                "SELECT run_id FROM runs_meta WHERE status='running' "
                "AND run_id LIKE 'remote-pending-%'"
            ).fetchall()
        except Exception:  # noqa: BLE001
            rows = []
        finally:
            conn.close()
        for r in rows:
            sid = _pending_sim_id(r["run_id"])
            if sid is not None:
                ids.add(sid)
    return ids


def enrich_placeholder_status(client, ws_root: Path, live_status: dict) -> None:
    """Force-add placeholder sim ids' live status into ``live_status`` in place.

    The background refresh only enriches the newest window of sims; a placeholder
    older than that window would never resolve. This adds a targeted per-sim
    ``/status`` call for each placeholder not already enriched. A 404 (sms-api
    does not know the sim) records the :data:`NOT_FOUND` sentinel; any other
    error leaves the id unresolved (the row stays "running" until next refresh).
    Never raises — a down tunnel must not break the refresh.
    """
    for sid in placeholder_sim_ids(ws_root):
        if sid in live_status:
            continue
        try:
            live_status[sid] = (client.simulation_status(int(sid)) or {}).get("status")
        except Exception as e:  # noqa: BLE001
            if getattr(e, "status", None) == 404:
                live_status[sid] = NOT_FOUND
            # any other error: leave unresolved, retry on the next refresh


def reconcile_pending_placeholders(ws_root: "str | Path",
                                   live_status: dict) -> int:
    """Terminalize ``remote-pending-<id>`` rows whose sim is done/failed/unknown.

    ``live_status`` maps ``sim_id -> sms-api status string`` (or :data:`NOT_FOUND`
    for a 404). Returns the number of rows terminalized. Pure — does no network
    I/O; the enrichment (:func:`enrich_placeholder_status`) is a separate step so
    this stays trivially testable with a plain dict.
    """
    from vivarium_workbench.lib import composite_runs as cr
    from vivarium_workbench.lib.remote_simulations import _map_remote_status
    ws_root = Path(ws_root)
    deployment = _deployment_name()
    n = 0
    for db in _pending_run_dbs(ws_root):
        try:
            conn = cr.connect(db)
        except Exception:  # noqa: BLE001
            continue
        try:
            try:
                rows = conn.execute(
                    "SELECT run_id FROM runs_meta WHERE status='running' "
                    "AND run_id LIKE 'remote-pending-%'"
                ).fetchall()
            except Exception:  # noqa: BLE001
                rows = []
            for r in rows:
                run_id = r["run_id"]
                sid = _pending_sim_id(run_id)
                if sid is None or sid not in live_status:
                    continue
                raw = live_status.get(sid)
                if raw == NOT_FOUND:
                    _terminalize(conn, ws_root, run_id=run_id, status="orphaned",
                                 label=f"Remote run #{sid} — not found on {deployment}")
                    n += 1
                    continue
                st = _map_remote_status(raw if isinstance(raw, str) else None,
                                        has_out_uri=False)
                if st == "completed":
                    _terminalize(conn, ws_root, run_id=run_id, status="completed",
                                 label=f"Remote run #{sid} — completed, not landed")
                    n += 1
                elif st == "failed":
                    _terminalize(conn, ws_root, run_id=run_id, status="failed",
                                 label=f"Remote run #{sid} — failed on {deployment}")
                    n += 1
                # queued / running / unresolved: still pending — leave as-is
        finally:
            conn.close()
    return n


def _terminalize(conn, ws_root: Path, *, run_id: str, status: str,
                 label: str) -> None:
    """Set the placeholder row to a terminal ``status`` + ``label`` and mirror the
    change to the JSONL log (label included) so the fold reports the terminal
    state and label, not the surviving "running" ``started`` event."""
    from vivarium_workbench.lib import run_log
    now = time.time()
    conn.execute(
        "UPDATE runs_meta SET status=?, completed_at=?, n_steps=0, label=? "
        "WHERE run_id=?",
        (status, now, label, run_id),
    )
    conn.commit()
    run_log.append_run_event(Path(ws_root), {
        "run_id": run_id,
        "event": "completed" if status == "completed" else status,
        "status": status,
        "label": label,
        "completed_at": now,
        "n_steps": 0,
    })


def _deployment_name() -> str:
    """Best-effort remote deployment name for reason strings ("the deployment")."""
    try:
        from vivarium_workbench.lib import remote_pinned
        return remote_pinned.remote_deployment_name() or "the deployment"
    except Exception:  # noqa: BLE001
        return "the deployment"
