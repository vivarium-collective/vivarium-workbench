"""End-to-end: a runs.db row stamped env_stale, synced into study.yaml via
study_outcomes.record_runs, is surfaced by needs_attention.scan_investigation.

Ported from the plugin's tests/test_study_outcomes.py in Phase 2.1k batch 2
(needs_attention moved into vivarium_workbench/lib). study_outcomes / study_io /
run_registry stay in the plugin (workbench-free core), so they are imported from
viva_superpowers; needs_attention is imported from its new lib home.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from discovery_engine import study_io, run_registry
from discovery_engine import study_outcomes as so
from vivarium_workbench.lib import needs_attention

# record_runs' provenance sync shipped with the reproducible-rerun spine. A stale
# installed viva_superpowers (e.g. a dev venv pinned to an old pre-spine version)
# lacks it, which would make this end-to-end assertion spuriously fail — skip
# there. CI installs a current pbg-superpowers, so the test runs for real.
if "provenance_status" not in inspect.getsource(so):
    pytest.skip(
        "installed discovery_engine.study_outcomes predates provenance sync",
        allow_module_level=True,
    )


def _add_provenance_columns(db: Path, run_id: str, *, provenance_status=None, env_id=None):
    """Simulate a runs.db already migrated by vwb's composite_runs.py (which
    ALTERs in these nullable columns) — pbg's own DDL doesn't create them."""
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
        if "provenance_status" not in have:
            conn.execute("ALTER TABLE runs_meta ADD COLUMN provenance_status TEXT")
        if "env_id" not in have:
            conn.execute("ALTER TABLE runs_meta ADD COLUMN env_id TEXT")
        conn.execute("UPDATE runs_meta SET provenance_status=?, env_id=? WHERE run_id=?",
                     (provenance_status, env_id, run_id))
        conn.commit()
    finally:
        conn.close()


def test_env_stale_synced_run_surfaces_needs_attention(tmp_path: Path):
    """The load-bearing gap this closes: a DB row stamped env_stale, synced via
    record_runs into study.yaml, is picked up by needs_attention.scan_investigation
    as an env_stale item."""
    root = tmp_path / "ws"
    root.mkdir()
    study_io.save_yaml_atomic(root / "workspace.yaml", {"name": "ws", "package_path": "pbg_ws"})
    inv_yaml = root / "investigations" / "inv" / "investigation.yaml"
    inv_yaml.parent.mkdir(parents=True, exist_ok=True)
    study_io.save_yaml_atomic(inv_yaml, {"name": "inv", "studies": ["s1"]})
    d = root / "studies" / "s1"
    d.mkdir(parents=True)
    study_io.save_yaml_atomic(d / "study.yaml", {"name": "s1", "runs": []})
    db = d / "runs.db"
    run_registry.register_run(db, "r1", spec_id="s1", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    _add_provenance_columns(db, "r1", provenance_status="env_stale", env_id="env-b-hash")

    so.record_runs(d)
    res = needs_attention.scan_investigation(root, "inv")
    stale = [i for i in res["items"] if i["kind"] == "env_stale"]
    assert stale and stale[0]["study"] == "s1" and stale[0]["ref"] == "r1"
