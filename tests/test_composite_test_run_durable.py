"""SP-B durability test: submit >20 runs for one spec and assert none are pruned.

The view previously called ``cr.prune_runs(conn, spec_id=spec_id, keep=20)``
before each ``save_metadata``, which silently evicted the oldest runs once the
count exceeded 20.  This test drives 25 submissions and asserts all 25
``runs_meta`` rows survive.

Fakes added (beyond the brief's skeleton):
* ``workspace.yaml``  — the view reads this via ``yaml.safe_load`` before it
  touches the DB; without it the call raises ``FileNotFoundError`` before
  ``save_metadata`` is ever reached.  A minimal ``name: ws`` is enough.
* ``v.run_registry.count_running`` → 0  — bypasses the 429 concurrency cap.
* ``v.run_registry.spawn_detached`` → fake pid 4321  — bypasses subprocess
  spawn so no real process is created.
* ``v.cr.generate_run_id`` → counter-based unique ids  — prevents PRIMARY KEY
  collisions on 25 successive inserts.
"""
from __future__ import annotations

from vivarium_workbench.lib import composite_test_run_views as v
from vivarium_workbench.lib import composite_runs as cr


def test_runs_are_durable_no_prune(tmp_path, monkeypatch):
    ws = tmp_path
    # workspace.yaml is read before save_metadata — required to get past
    # the yaml.safe_load call at line ~81 of composite_test_run_views.py.
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    (ws / ".pbg").mkdir()

    monkeypatch.setattr(v.run_registry, "count_running", lambda *a, **k: 0)
    monkeypatch.setattr(v.run_registry, "spawn_detached", lambda *a, **k: 4321)

    n = 0

    def _id(spec_id, params=None, now=None):
        nonlocal n
        n += 1
        return f"{spec_id}__{n}__abcdef"

    monkeypatch.setattr(v.cr, "generate_run_id", _id)

    for _ in range(25):
        body, status = v.composite_test_run(
            ws, {"id": "pkg.composites.x", "overrides": {}, "steps": 1}
        )
        assert status == 202, f"unexpected status {status}: {body}"

    conn = cr.connect(ws / ".pbg" / "composite-runs.db")
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM runs_meta WHERE spec_id=?",
            ("pkg.composites.x",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 25, f"expected 25 durable runs, got {count} (prune still active?)"
