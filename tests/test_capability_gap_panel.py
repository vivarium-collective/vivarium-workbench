"""Capability-gap panel wiring (P2 #2): a capability_gap is a labeled, actionable row in
the Discovery Engine (Cycle) panel — accept → build → close-on-coverage.

The engine's propose/route/accept/reject/build logic is tested in the discovery-engine repo;
these cover the WORKBENCH layer: labeling + action flags in the gate inspector and the
accept/reject/build lib wrappers (persist + commit + refreshed trace). A proposed gap is
pre-seeded onto the investigation's persisted ``capability_gaps`` ledger (how the engine
frontier surfaces them), so the test is independent of the catalog.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import cycle_trace as ct

pytestmark = pytest.mark.skipif(
    not ct.engine_available(), reason="discovery_engine not importable")

_SRC = Path(__file__).parent / "_fixtures" / "ws_federation_collision"
_SLUG = "local_inv"
_GAP_ID = "cg:local_inv:deadbeef"
_GAP = {"id": _GAP_ID, "status": "proposed", "kind": "capability_gap",
        "op": "build_process", "mechanism": "novel_bridge_mechanism", "approval": "review",
        "study": "shared", "derivable": False}


def _ws(tmp_path, *, status="proposed", accept=None):
    ws = tmp_path / "ws"
    shutil.copytree(_SRC, ws)
    iv = ws / "investigations" / _SLUG / "investigation.yaml"
    spec = yaml.safe_load(iv.read_text())
    g = dict(_GAP, status=status)
    if accept:
        g["accept"] = accept
    spec["capability_gaps"] = [g]
    iv.write_text(yaml.safe_dump(spec))
    return ws


def _gap_row(trace):
    rows = [r for r in (trace["summary"].get("frontier") or []) if r.get("kind") == "capability_gap"]
    return rows[0] if rows else None


def test_proposed_gap_surfaces_labeled_and_acceptable(tmp_path):
    row = _gap_row(ct.build_cycle_trace(_ws(tmp_path), _SLUG))
    assert row is not None
    assert row["kind_label"] and row["kind_label"] != "capability_gap"      # has a human label
    assert row["acceptable_gap"] is True and row["rejectable_gap"] is True
    assert row["buildable_gap"] is False
    assert row["mechanism"] == "novel_bridge_mechanism"


def test_accept_persists_and_flips_affordance_to_build(tmp_path):
    ws = _ws(tmp_path)
    out = ct.accept_capability_gap(ws, _SLUG, _GAP_ID, "needed for the coupled bridge")
    assert out["ok"] is True and out["gap"]["status"] == "accepted"
    on_disk = yaml.safe_load((ws / "investigations" / _SLUG / "investigation.yaml").read_text())
    assert on_disk["capability_gaps"][0]["status"] == "accepted"
    row = _gap_row(out["after"])
    assert row["buildable_gap"] is True and row["acceptable_gap"] is False


def test_accept_requires_a_rationale(tmp_path):
    out = ct.accept_capability_gap(_ws(tmp_path), _SLUG, _GAP_ID, "   ")
    assert out["ok"] is False and "rationale" in (out["detail"] or "").lower()


def test_reject_is_terminal_and_leaves_the_frontier(tmp_path):
    ws = _ws(tmp_path)
    out = ct.reject_capability_gap(ws, _SLUG, _GAP_ID, "out of scope this cycle")
    assert out["ok"] is True and out["gap"]["status"] == "rejected"
    assert _gap_row(out["after"]) is None          # terminal -> no longer raised


def test_build_requires_accept_first(tmp_path):
    out = ct.build_capability_gap(_ws(tmp_path), _SLUG, _GAP_ID)  # still proposed
    assert out["ok"] is False and "accepted" in (out["detail"] or "")


def test_build_without_a_workspace_hook_is_a_clear_message(tmp_path):
    ws = _ws(tmp_path, status="accepted", accept={"rationale": "x", "by": "author"})
    out = ct.build_capability_gap(ws, _SLUG, _GAP_ID)
    assert out["ok"] is False and "build hook" in (out["detail"] or "")


def test_bad_gap_id_is_rejected(tmp_path):
    out = ct.accept_capability_gap(_ws(tmp_path), _SLUG, "not-a-cg-id", "why")
    assert out["ok"] is False and "cg:" in (out["detail"] or "")
