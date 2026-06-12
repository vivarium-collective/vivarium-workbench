"""SP5 Task 4: GET /api/needs-attention + the `_needs_attention` worker.

The endpoint runs the deterministic ``pbg_superpowers.needs_attention.
scan_investigation`` over the workspace and returns its
``{"items": [...], "summary": {...}}`` payload so the dashboard can render a
"Needs attention" panel. Build-free by default (no ``observables_for_ref``).
The dashboard adds no AI — it just runs the scan and renders. Tolerant: never
500, empty-typed payload on absence/failure.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml
import pytest


@pytest.fixture
def tmp_ws_uncovered_ac(tmp_path, monkeypatch):
    """A workspace with an unkeyed acceptance criterion → an uncovered-AC gap,
    so ``scan_investigation`` returns a non-empty ``items`` list."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    inv_dir = ws / "investigations" / "the-inv"
    inv_dir.mkdir(parents=True)
    inv_dir.joinpath("investigation.yaml").write_text(yaml.safe_dump({
        "name": "the-inv",
        "studies": ["s1"],
        "acceptance_criteria": [
            {"study": "s1", "behavior": "b1"},        # keyed/covered
            {"behavior": "b2", "status": "pending"},   # unkeyed → uncovered_ac
        ],
    }))
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    sd.joinpath("study.yaml").write_text(yaml.safe_dump({
        "name": "s1", "investigation": "the-inv",
        "tests": [{"name": "b1"}],
        "runs": [{"name": "r1", "status": "completed",
                  "outcomes": {"b1": {"result": "PASS"}}}],
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_needs_attention_endpoint(tmp_ws_uncovered_ac):
    import vivarium_dashboard.server as server
    body, code = server.Handler._needs_attention_test(
        server.WORKSPACE, investigation="the-inv")
    d = json.loads(body)
    assert code == 200
    assert "items" in d and "summary" in d
    assert isinstance(d["items"], list)
    summ = d["summary"]
    assert set(summ["by_severity"]) == {"high", "medium", "low"}
    assert "total" in summ and "by_kind" in summ
    # The unkeyed AC should surface at least one item.
    assert summ["total"] >= 1
    assert d["items"], "expected at least one needs-attention item"


def test_needs_attention_tolerant_on_missing_ws(tmp_path):
    import vivarium_dashboard.server as server
    missing = tmp_path / "does-not-exist"
    body, code = server.Handler._needs_attention_test(missing, investigation="nope")
    assert code == 200  # never 500
    d = json.loads(body)
    assert d["items"] == []
    assert d["summary"]["total"] == 0
    assert d["summary"]["by_severity"] == {"high": 0, "medium": 0, "low": 0}


def test_needs_attention_tolerant_on_scan_failure(tmp_ws_uncovered_ac, monkeypatch):
    import vivarium_dashboard.server as server
    from pbg_superpowers import needs_attention as _na

    def _boom(ws_root, inv_slug, **kw):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr(_na, "scan_investigation", _boom)
    body, code = server.Handler._needs_attention_test(
        server.WORKSPACE, investigation="the-inv")
    d = json.loads(body)
    assert code == 200  # never 500
    assert d["items"] == []
    assert d["summary"] == {
        "by_severity": {"high": 0, "medium": 0, "low": 0},
        "by_kind": {},
        "total": 0,
    }
