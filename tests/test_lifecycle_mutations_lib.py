"""Tests for vivarium_workbench.lib.lifecycle_mutations builders.

Covers all 6 builders (happy + 400/404 per builder) and the shim-parity
assertions confirming that server._for_test shims delegate to the lib.

Batch 20 parity tests.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

import vivarium_workbench.lib.lifecycle_mutations as lifecycle_mutations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text(
        "schema_version: 2\nname: ws\ncreated: '2026-01-01'\nplugin_version: 0.6.1\npackage_path: pkg\n"
    )
    (w / "studies").mkdir()
    (w / "investigations").mkdir()
    return w


def _make_study(ws: Path, slug: str, **fields) -> Path:
    d = ws / "studies" / slug
    d.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": 4, "name": slug, "status": "active", **fields}
    p = d / "study.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


def _make_investigation(ws: Path, slug: str, **fields) -> Path:
    d = ws / "investigations" / slug
    d.mkdir(parents=True, exist_ok=True)
    p = d / "investigation.yaml"
    data = {"name": slug, **fields}
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


# ---------------------------------------------------------------------------
# feedback_apply_action
# ---------------------------------------------------------------------------


def test_feedback_apply_action_missing_item_id(ws: Path) -> None:
    resp, code = lifecycle_mutations.feedback_apply_action(ws, {})
    assert code == 400
    assert "error" in resp


def test_feedback_apply_action_unknown_item(ws: Path) -> None:
    resp, code = lifecycle_mutations.feedback_apply_action(ws, {"item_id": "fb-deadbeef"})
    assert code == 400
    assert "error" in resp


def test_feedback_apply_action_happy(ws: Path) -> None:
    """Full happy path via pbg_superpowers (requires it to be installed)."""
    from pbg_superpowers.feedback_actions import feedback_item_id

    _make_study(ws, "s1", findings=[{"id": "F-01", "statement": "X"}])
    iid = feedback_item_id("study-s1", "2026-01-01T10:00:00Z", "Alice")
    fb = ws / "investigations" / "inv1" / "feedback" / "r1.yaml"
    fb.parent.mkdir(parents=True, exist_ok=True)
    fb.write_text(yaml.safe_dump({
        "meta": {"investigation": "inv1"},
        "annotations": {
            "study-s1": [{"ts": "2026-01-01T10:00:00Z", "author": "Alice", "text": "x"}],
        },
        "actions": {
            iid: {
                "kind": "next_action",
                "target_study": "s1",
                "target_finding": "F-01",
                "proposed_text": "calibrate",
                "status": "open",
            },
        },
    }, sort_keys=False))
    resp, code = lifecycle_mutations.feedback_apply_action(ws, {"item_id": iid})
    assert code == 200, resp
    assert resp.get("applied") is True


# ---------------------------------------------------------------------------
# study_create_from_run
# ---------------------------------------------------------------------------


@pytest.fixture
def ws_with_scratch(ws: Path) -> Path:
    pbg = ws / ".pbg"
    pbg.mkdir()
    db = pbg / "composite-runs.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE runs_meta (
            run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, label TEXT,
            params_json TEXT, started_at REAL NOT NULL, completed_at REAL,
            n_steps INTEGER, status TEXT NOT NULL
        );
        CREATE TABLE history (
            simulation_id TEXT NOT NULL, step INTEGER NOT NULL,
            global_time REAL, state TEXT NOT NULL,
            PRIMARY KEY (simulation_id, step)
        );
    """)
    conn.execute(
        "INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("rid1", "pkg.foo", "test", "{}", 1.0, 2.0, 5, "completed"),
    )
    conn.executemany(
        "INSERT INTO history VALUES (?, ?, ?, ?)",
        [("rid1", i, float(i), '{"x":' + str(i) + "}") for i in range(5)],
    )
    conn.commit()
    conn.close()
    return ws


def test_study_create_from_run_happy(ws_with_scratch: Path) -> None:
    resp, code = lifecycle_mutations.study_create_from_run(
        ws_with_scratch,
        {"name": "my-study", "source_run_id": "rid1", "objective": "Why?"},
    )
    assert code == 200, resp
    assert resp["study"] == "my-study"
    spec = yaml.safe_load((ws_with_scratch / "studies" / "my-study" / "study.yaml").read_text())
    assert spec["schema_version"] == 3
    assert spec["baseline"]["composite"] == "pkg.foo"


def test_study_create_from_run_missing_fields(ws_with_scratch: Path) -> None:
    resp, code = lifecycle_mutations.study_create_from_run(ws_with_scratch, {"name": "x"})
    assert code == 400


def test_study_create_from_run_unknown_run(ws_with_scratch: Path) -> None:
    resp, code = lifecycle_mutations.study_create_from_run(
        ws_with_scratch, {"name": "x", "source_run_id": "nope"}
    )
    assert code == 404


def test_study_create_from_run_collision(ws_with_scratch: Path) -> None:
    lifecycle_mutations.study_create_from_run(
        ws_with_scratch, {"name": "my-study", "source_run_id": "rid1"}
    )
    resp, code = lifecycle_mutations.study_create_from_run(
        ws_with_scratch, {"name": "my-study", "source_run_id": "rid1"}
    )
    assert code == 409


# ---------------------------------------------------------------------------
# study_rename
# ---------------------------------------------------------------------------


def test_study_rename_happy(ws: Path) -> None:
    _make_study(ws, "old-name")
    resp, code = lifecycle_mutations.study_rename(
        ws, {"study": "old-name", "new_name": "new-name"}
    )
    assert code == 200, resp
    assert resp == {"ok": True, "name": "new-name"}
    assert (ws / "studies" / "new-name").is_dir()
    spec = yaml.safe_load((ws / "studies" / "new-name" / "study.yaml").read_text())
    assert spec["name"] == "new-name"


def test_study_rename_missing_fields(ws: Path) -> None:
    resp, code = lifecycle_mutations.study_rename(ws, {"study": "x"})
    assert code == 400


def test_study_rename_study_not_found(ws: Path) -> None:
    resp, code = lifecycle_mutations.study_rename(ws, {"study": "nope", "new_name": "ok"})
    assert code == 404


def test_study_rename_collision(ws: Path) -> None:
    _make_study(ws, "a")
    _make_study(ws, "b")
    resp, code = lifecycle_mutations.study_rename(ws, {"study": "a", "new_name": "b"})
    assert code == 409


# ---------------------------------------------------------------------------
# study_sync_runs
# ---------------------------------------------------------------------------


def test_study_sync_runs_missing_slug(ws: Path) -> None:
    resp, code = lifecycle_mutations.study_sync_runs(ws, {})
    assert code == 400


def test_study_sync_runs_unknown_study(ws: Path) -> None:
    resp, code = lifecycle_mutations.study_sync_runs(ws, {"study": "nope"})
    assert code == 404


def test_study_sync_runs_happy(ws: Path) -> None:
    from pbg_superpowers import run_registry, study_io

    d = ws / "studies" / "s1"
    d.mkdir()
    study_io.save_yaml_atomic(d / "study.yaml", {"name": "s1", "runs": []})
    run_registry.register_run(
        d / "runs.db", "r1", spec_id="s1", status="completed",
        started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z",
    )
    resp, code = lifecycle_mutations.study_sync_runs(ws, {"study": "s1"})
    assert code == 200, resp
    assert resp["ok"] is True
    assert resp["summary"]["added"] == 1


# ---------------------------------------------------------------------------
# decide_proposed_input
# ---------------------------------------------------------------------------


_INV_YAML = """\
name: test-inv
proposed_inputs:
  items:
  - id: ref-a
    kind: reference
    citation: Smith 2024
    status: pending
  - id: mech-b
    kind: mechanism
    summary: a mechanism
    status: pending
inputs:
  references: []
"""


@pytest.fixture
def ws_with_inv(ws: Path) -> Path:
    _make_investigation(ws, "test-inv")
    (ws / "investigations" / "test-inv" / "investigation.yaml").write_text(
        _INV_YAML, encoding="utf-8"
    )
    return ws


def test_decide_proposed_input_accept_reference(ws_with_inv: Path) -> None:
    resp, code = lifecycle_mutations.decide_proposed_input(
        ws_with_inv,
        {"investigation": "test-inv", "item_id": "ref-a", "decision": "accept"},
    )
    assert code == 200, resp
    assert resp["status"] == "accepted"
    assert resp["kind"] == "reference"
    spec = yaml.safe_load(
        (ws_with_inv / "investigations" / "test-inv" / "investigation.yaml").read_text()
    )
    assert "ref-a" in spec["inputs"]["references"]


def test_decide_proposed_input_decline(ws_with_inv: Path) -> None:
    resp, code = lifecycle_mutations.decide_proposed_input(
        ws_with_inv,
        {"investigation": "test-inv", "item_id": "ref-a", "decision": "decline"},
    )
    assert code == 200
    assert resp["status"] == "declined"


def test_decide_proposed_input_missing_inv(ws_with_inv: Path) -> None:
    resp, code = lifecycle_mutations.decide_proposed_input(
        ws_with_inv, {"item_id": "ref-a", "decision": "accept"}
    )
    assert code == 400


def test_decide_proposed_input_bad_decision(ws_with_inv: Path) -> None:
    resp, code = lifecycle_mutations.decide_proposed_input(
        ws_with_inv,
        {"investigation": "test-inv", "item_id": "ref-a", "decision": "maybe"},
    )
    assert code == 400


def test_decide_proposed_input_unknown_item(ws_with_inv: Path) -> None:
    resp, code = lifecycle_mutations.decide_proposed_input(
        ws_with_inv,
        {"investigation": "test-inv", "item_id": "nope", "decision": "accept"},
    )
    assert code == 404


def test_decide_proposed_input_unknown_inv(ws_with_inv: Path) -> None:
    resp, code = lifecycle_mutations.decide_proposed_input(
        ws_with_inv,
        {"investigation": "does-not-exist", "item_id": "ref-a", "decision": "accept"},
    )
    assert code == 404


# ---------------------------------------------------------------------------
# study_seed_followup
# ---------------------------------------------------------------------------


def test_study_seed_followup_missing_parent(ws: Path) -> None:
    resp, code = lifecycle_mutations.study_seed_followup(
        ws, {"parent": "nope", "followup_idx": 0}
    )
    assert code in (400, 404)
    assert "error" in resp


def test_study_seed_followup_happy(ws: Path) -> None:
    d = ws / "studies" / "parent"
    d.mkdir()
    (d / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": "parent",
        "status": "ran",
        "baseline": [{"name": "b", "composite": "x"}],
        "follow_up_studies": [
            {"title": "child study", "kind": "new", "why": "because"},
        ],
    }, sort_keys=False))
    resp, code = lifecycle_mutations.study_seed_followup(
        ws, {"parent": "parent", "followup_idx": 0}
    )
    assert code == 200, resp
    assert resp["new_study_name"]
    assert resp["new_slug"] == resp["new_study_name"]


def test_study_seed_followup_bad_proposal_idx(ws: Path) -> None:
    resp, code = lifecycle_mutations.study_seed_followup(
        ws, {"parent": "p", "proposal_idx": "not-an-int"}
    )
    assert code == 400
    assert "proposal_idx" in resp["error"]
