"""The needs-attention -> fix-request bridge over a real workspace + dispatch."""

from __future__ import annotations

import yaml
import pytest

from vivarium_workbench.lib.needs_attention_requests import (
    build_requests_payload, run_autofix, run_fix, scan_and_bridge,
)


@pytest.fixture
def tmp_ws_uncovered_ac(tmp_path):
    """A workspace whose unkeyed acceptance criterion yields an uncovered_ac item
    (-> propose_study, review-gated)."""
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    inv_dir = ws / "investigations" / "the-inv"
    inv_dir.mkdir(parents=True)
    inv_dir.joinpath("investigation.yaml").write_text(yaml.safe_dump({
        "name": "the-inv",
        "studies": ["s1"],
        "acceptance_criteria": [
            {"study": "s1", "behavior": "b1"},
            {"behavior": "b2", "status": "pending"},   # unkeyed -> uncovered_ac
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
    return ws


def test_requests_payload_shape(tmp_ws_uncovered_ac):
    d = build_requests_payload(tmp_ws_uncovered_ac, "the-inv")
    assert d["investigation"] == "the-inv"
    assert set(d) >= {"total", "auto_fixable", "by_kind", "by_gate",
                      "requests", "reruns", "unmapped"}
    assert isinstance(d["requests"], list) and isinstance(d["reruns"], list)
    kinds = {r["kind"] for r in d["requests"]}
    assert "propose_study" in kinds           # uncovered_ac bridged
    # propose_study is a structural branch-point -> review, not auto.
    prop = next(r for r in d["requests"] if r["kind"] == "propose_study")
    assert prop["gate"] == "review"
    assert prop["provenance"]["source"] == "needs_attention"


def test_autofix_safe_noop_without_filler(tmp_ws_uncovered_ac, monkeypatch):
    monkeypatch.delenv("VIVA_FILLER_CMD", raising=False)
    d = run_autofix(tmp_ws_uncovered_ac, "the-inv", only_auto=True)
    assert d["filler_configured"] is False
    assert isinstance(d["outcomes"], list)
    # uncovered_ac -> propose_study is review-gated, so only_auto dispatches none.
    assert d["dispatched"] == 0


def test_fix_dispatches_single_with_stub_filler(tmp_ws_uncovered_ac):
    from discovery_engine.phases.generation import GenerationResult

    # Find the bridged request's (scan_kind, ref) to target the fix.
    b = scan_and_bridge(tmp_ws_uncovered_ac, "the-inv")
    assert b["requests"], "expected at least one bridged request"
    req = b["requests"][0]
    scan_kind = req.provenance["scan_kind"]
    ref = req.provenance["ref"]

    called = {}

    def stub_fill(r):
        called["id"] = r.id
        assert r.gate == "auto"       # user click forces this one to auto
        return GenerationResult(request_id=r.id, artifact={},
                                filler={"kind": "test", "id": "stub"},
                                status="applied")

    out = run_fix(tmp_ws_uncovered_ac, "the-inv", scan_kind, ref, fill=stub_fill)
    assert out["found"] is True
    assert called.get("id") == req.id         # the filler was actually invoked
    assert out["outcome"] and "outcome" in out["outcome"]


def test_fix_missing_item_is_reported(tmp_ws_uncovered_ac):
    out = run_fix(tmp_ws_uncovered_ac, "the-inv", "no_such_kind", "nope",
                  fill=lambda r: None)
    assert out["found"] is False
