"""Tests for placeholder-run lifecycle (#1108): delete tombstoning (d1) and the
remote-pending reconciler (d2)."""
from __future__ import annotations

import time

import pytest

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import run_log
from vivarium_workbench.lib.simulations_index import (
    backfill_index_into_jsonl,
    build_simulations_data,
)


class _PluginModule:
    """A plugin module imported on first attribute use, so this module collects without the plugin (step P3, K2c):
    only the ``plugin``-marked tests touch it. Read-only -- monkeypatch the real module (``_PluginModule.load()``)."""

    def __init__(self, name):
        self._name = name

    def load(self):
        import importlib
        return importlib.import_module(self._name)

    def __getattr__(self, attr):
        return getattr(self.load(), attr)


remote_reconcile = _PluginModule("vivarium_workbench_sms.remote_reconcile")


@pytest.fixture(autouse=True)
def _this_deployment(monkeypatch):
    """Step 1c-2: the reconciler touches only this deployment's placeholders. These tests write them as
    ``smstest``'s, so that is the deployment this workbench is configured for (the other-deployment case is its
    own test below)."""
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", "smstest")


def _make_workspace(tmp_path, slug="baseline"):
    """A minimal workspace with one study holding a runs.db."""
    (tmp_path / "workspace.yaml").write_text(
        "schema_version: 2\nname: ws_reconcile_test\nphases: []\n",
        encoding="utf-8",
    )
    study_dir = tmp_path / "studies" / slug
    study_dir.mkdir(parents=True)
    return tmp_path, study_dir


def _write_placeholder(study_dir, ws_root, sim_id, *, deployment="smstest"):
    """Write a `remote-pending-<sim_id>` row exactly as remote_run_views does —
    status='running', params carrying source + simulation_id, workspace passed so
    a JSONL 'started' event is appended."""
    conn = cr.connect(study_dir / "runs.db")
    try:
        cr.save_metadata(
            conn,
            spec_id="ecoli",
            run_id=f"remote-pending-{sim_id}",
            params={"source": deployment, "simulation_id": sim_id, "backend": "ray"},
            label=f"Remote dispatch ({deployment}) — in progress",
            started_at=time.time(),
            n_steps=0,
            workspace=ws_root,
        )
    finally:
        conn.close()


def _folded_status(ws_root, run_id):
    folded = run_log.fold_runs_jsonl(ws_root)
    rec = folded.get(run_id)
    return rec.get("status") if rec else None


# --- d1: tombstone on delete ------------------------------------------------


def test_delete_run_without_workspace_leaves_started_event(tmp_path):
    """Control: a bare delete (no workspace) leaves the JSONL 'started' event, so
    the fold still resurrects the run — the pre-fix behaviour."""
    ws_root, study_dir = _make_workspace(tmp_path)
    _write_placeholder(study_dir, ws_root, 42)
    conn = cr.connect(study_dir / "runs.db")
    try:
        assert cr.delete_run(conn, run_id="remote-pending-42") is True
    finally:
        conn.close()
    # No tombstone -> the started event survives -> the fold resurrects it.
    assert _folded_status(ws_root, "remote-pending-42") == "running"


def test_delete_run_tombstones_and_does_not_reappear_after_fold(tmp_path):
    """A placeholder deleted WITH workspace is tombstoned and does NOT reappear
    after an index fold (the #1108 root-cause fix)."""
    ws_root, study_dir = _make_workspace(tmp_path)
    _write_placeholder(study_dir, ws_root, 42)
    assert _folded_status(ws_root, "remote-pending-42") == "running"  # present pre-delete

    conn = cr.connect(study_dir / "runs.db")
    try:
        assert cr.delete_run(conn, run_id="remote-pending-42", workspace=ws_root) is True
    finally:
        conn.close()

    # The tombstone drops it from the fold...
    assert _folded_status(ws_root, "remote-pending-42") is None
    # ...and a backfill (which re-migrates surviving sqlite rows) does not
    # resurrect it either — the row is gone from sqlite and the tombstone blocks it.
    backfill_index_into_jsonl(ws_root)
    assert _folded_status(ws_root, "remote-pending-42") is None
    data = build_simulations_data(ws_root, include_remote=False)
    run_ids = {r.get("run_id") for r in data.get("simulations", [])}
    assert "remote-pending-42" not in run_ids


# --- d2: reconciler ---------------------------------------------------------


@pytest.mark.plugin
def test_reconcile_completed_becomes_not_landed(tmp_path):
    ws_root, study_dir = _make_workspace(tmp_path)
    _write_placeholder(study_dir, ws_root, 100)

    n = remote_reconcile.reconcile_pending_placeholders(ws_root, {100: "completed"})
    assert n == 1

    conn = cr.connect(study_dir / "runs.db")
    try:
        row = cr.query_run_meta(conn, run_id="remote-pending-100")
    finally:
        conn.close()
    assert row["status"] == "completed"
    assert "completed, not landed" in row["label"]
    # The terminal state (and label) survive the JSONL fold, not the "running"
    # started event.
    folded = run_log.fold_runs_jsonl(ws_root)
    assert folded["remote-pending-100"]["status"] == "completed"
    assert "completed, not landed" in folded["remote-pending-100"]["label"]


@pytest.mark.plugin
def test_reconcile_failed(tmp_path):
    ws_root, study_dir = _make_workspace(tmp_path)
    _write_placeholder(study_dir, ws_root, 101)

    n = remote_reconcile.reconcile_pending_placeholders(ws_root, {101: "failed"})
    assert n == 1
    assert _folded_status(ws_root, "remote-pending-101") == "failed"


@pytest.mark.plugin
def test_reconcile_404_orphaned(tmp_path):
    ws_root, study_dir = _make_workspace(tmp_path)
    _write_placeholder(study_dir, ws_root, 102)

    n = remote_reconcile.reconcile_pending_placeholders(
        ws_root, {102: remote_reconcile.NOT_FOUND})
    assert n == 1

    conn = cr.connect(study_dir / "runs.db")
    try:
        row = cr.query_run_meta(conn, run_id="remote-pending-102")
    finally:
        conn.close()
    assert row["status"] == "orphaned"
    assert "not found" in row["label"].lower()
    assert _folded_status(ws_root, "remote-pending-102") == "orphaned"


@pytest.mark.plugin
def test_reconcile_running_left_pending(tmp_path):
    """A placeholder whose sim is still running stays 'running' (not terminalized)."""
    ws_root, study_dir = _make_workspace(tmp_path)
    _write_placeholder(study_dir, ws_root, 103)

    n = remote_reconcile.reconcile_pending_placeholders(ws_root, {103: "running"})
    assert n == 0
    assert _folded_status(ws_root, "remote-pending-103") == "running"


@pytest.mark.plugin
def test_reconcile_unknown_id_left_pending(tmp_path):
    """A placeholder absent from live_status (unresolved) is left alone."""
    ws_root, study_dir = _make_workspace(tmp_path)
    _write_placeholder(study_dir, ws_root, 104)

    n = remote_reconcile.reconcile_pending_placeholders(ws_root, {})
    assert n == 0
    assert _folded_status(ws_root, "remote-pending-104") == "running"


# --- enrichment (force-include placeholder ids outside the window) ----------


class _StubClient:
    """Minimal sms-api client stub: maps sim id -> status, raising a 404-shaped
    SmsApiError for ids marked missing."""

    def __init__(self, statuses, missing=()):
        self._statuses = statuses
        self._missing = set(missing)

    def simulation_status(self, sim_id):
        from vivarium_workbench_sms.sms_api_client import SmsApiError
        if sim_id in self._missing:
            raise SmsApiError(f"GET /status -> 404", status=404)
        return {"status": self._statuses.get(sim_id)}


@pytest.mark.plugin
def test_enrich_placeholder_status_adds_ids_and_maps_404(tmp_path):
    ws_root, study_dir = _make_workspace(tmp_path)
    _write_placeholder(study_dir, ws_root, 200)
    _write_placeholder(study_dir, ws_root, 201)

    client = _StubClient({200: "completed"}, missing={201})
    live_status: dict = {}
    remote_reconcile.enrich_placeholder_status(client, ws_root, live_status)

    assert live_status[200] == "completed"
    assert live_status[201] == remote_reconcile.NOT_FOUND

    # And the reconciler terminalizes both from that enriched map.
    n = remote_reconcile.reconcile_pending_placeholders(ws_root, live_status)
    assert n == 2
    assert _folded_status(ws_root, "remote-pending-200") == "completed"
    assert _folded_status(ws_root, "remote-pending-201") == "orphaned"


def test_reconcile_belt_and_braces_synthesizes_remote_origin(tmp_path):
    """A still-running placeholder surfaces with a remote_origin.simulation_id
    derived from params (so the frontend poller can key on it)."""
    ws_root, study_dir = _make_workspace(tmp_path)
    _write_placeholder(study_dir, ws_root, 300)

    data = build_simulations_data(ws_root, include_remote=False)
    row = next(r for r in data["simulations"]
               if r.get("run_id") == "remote-pending-300")
    assert (row.get("remote_origin") or {}).get("simulation_id") == 300


@pytest.mark.plugin
def test_another_deployments_placeholder_is_never_reconciled(tmp_path, monkeypatch):
    """Step 1c-2: a placeholder written for another deployment is not asked about here, so this server's 404 (or a
    colliding id's status) never marks it orphaned or finished."""
    ws_root, study_dir = _make_workspace(tmp_path)
    _write_placeholder(study_dir, ws_root, 101, deployment="prod-elsewhere")
    assert remote_reconcile.placeholder_sim_ids(ws_root) == set()
    assert remote_reconcile.reconcile_pending_placeholders(ws_root, {101: remote_reconcile.NOT_FOUND}) == 0
