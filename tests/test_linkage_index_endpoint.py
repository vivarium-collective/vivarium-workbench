"""SP4a: GET /api/linkage-index + the `_linkage_index` worker.

The endpoint runs the deterministic linkage index/queries from
``pbg_superpowers.linkage_index`` over the workspace and returns JSON so the
dashboard can render the AC→study gating matrix (and surface unlinked-AC gaps).
The dashboard adds no AI — it just runs the derive and renders. Tolerant:
never 500, empty on absence.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
import pytest


@pytest.fixture
def tmp_ws_mixed_ac(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    (ws).mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    inv_dir = ws / "investigations" / "the-inv"
    inv_dir.mkdir(parents=True)
    inv_dir.joinpath("investigation.yaml").write_text(yaml.safe_dump({
        "name": "the-inv",
        "studies": ["s1"],
        "acceptance_criteria": [
            {"study": "s1", "behavior": "b1"},      # keyed
            {"behavior": "b2", "status": "failed"},  # unkeyed → gap
        ],
    }))
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    sd.joinpath("study.yaml").write_text(yaml.safe_dump({
        "name": "s1", "investigation": "the-inv",
        "cites": ["bib-X"],
        "tests": [{"name": "b1"}],
        "runs": [{"name": "r1", "status": "completed",
                  "outcomes": {"b1": {"result": "PASS"}}}],
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_linkage_index_endpoint(tmp_ws_mixed_ac):
    import vivarium_dashboard.server as server
    body, code = server.Handler._linkage_index_test(
        server.WORKSPACE, investigation="the-inv")
    d = json.loads(body)
    assert code == 200
    assert "ac_matrix" in d or "nodes" in d
    matrix = d.get("ac_matrix") or {}
    assert any(r.get("gap") for r in matrix.get("criteria", []))


def test_linkage_index_source_query(tmp_ws_mixed_ac):
    import vivarium_dashboard.server as server
    body, code = server.Handler._linkage_index_test(
        server.WORKSPACE, source="bib-X")
    d = json.loads(body)
    assert code == 200
    assert "s1" in (d.get("studies") or [])


def test_linkage_index_tolerant_on_missing_ws(tmp_path, monkeypatch):
    import vivarium_dashboard.server as server
    missing = tmp_path / "does-not-exist"
    body, code = server.Handler._linkage_index_test(missing, investigation="nope")
    assert code == 200  # never 500
    json.loads(body)  # valid JSON
