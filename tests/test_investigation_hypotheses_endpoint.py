"""Wave 3b #6/#16: GET /api/investigation-hypotheses + the
``_investigation_hypotheses`` worker.

The endpoint returns the investigation's competing ``hypotheses[]`` with a
COMPUTED ``support_log`` folded in via the deterministic
``pbg_superpowers.hypotheses.rollup_support`` (falling back to
``score_support`` per hypothesis). AI-free + tolerant: never 500, returns the
authored hypotheses un-enriched on absence/failure.
"""
from __future__ import annotations

import sys
import types

import yaml
import pytest

from vivarium_workbench.lib.investigation_views import (
    build_investigation_hypotheses,
)


@pytest.fixture
def tmp_ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    inv_dir = ws / "investigations" / "the-inv"
    inv_dir.mkdir(parents=True)
    inv_dir.joinpath("investigation.yaml").write_text(yaml.safe_dump({
        "name": "the-inv", "studies": ["s1"],
        "hypotheses": [
            {"id": "H1", "statement": "closure without viability",
             "predictions": [{"observable": "closure_gap", "expected": "< 0.1"}],
             "status": "open"},
        ],
    }))
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    sd.joinpath("study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4, "name": "s1",
        "discovery_implications": {"alternate_hypotheses": [
            {"claim": "it is closed", "hypothesis_id": "H1", "status": "excluded"},
        ]},
    }))
    return ws


def test_returns_authored_hypotheses(tmp_ws):
    d = build_investigation_hypotheses(tmp_ws, "the-inv")
    assert d["investigation"] == "the-inv"
    assert isinstance(d["hypotheses"], list) and len(d["hypotheses"]) == 1
    assert d["hypotheses"][0]["id"] == "H1"


def test_tolerant_on_missing_investigation(tmp_ws):
    d = build_investigation_hypotheses(tmp_ws, "nope")
    assert d["hypotheses"] == []


def test_rollup_support_enriches_support_log(tmp_ws, monkeypatch):
    """When pbg_superpowers.hypotheses.rollup_support is importable, its enriched
    hypotheses (with support_log) flow through to the payload."""

    def _rollup(inv_spec, study_specs):
        hyps = []
        for h in (inv_spec.get("hypotheses") or []):
            h2 = dict(h)
            h2["support_log"] = [
                {"study": s.get("name"), "observation": "excluded alt", "delta": "supports"}
                for s in study_specs
            ]
            hyps.append(h2)
        return {**inv_spec, "hypotheses": hyps}

    fake = types.ModuleType("pbg_superpowers.hypotheses")
    fake.rollup_support = _rollup
    monkeypatch.setitem(sys.modules, "pbg_superpowers.hypotheses", fake)

    d = build_investigation_hypotheses(tmp_ws, "the-inv")
    log = d["hypotheses"][0]["support_log"]
    assert log and log[0]["study"] == "s1"
    assert log[0]["delta"] == "supports"


def test_falls_back_to_score_support(tmp_ws, monkeypatch):
    """When rollup_support is absent but score_support exists, support_log is
    computed per hypothesis."""

    fake = types.ModuleType("pbg_superpowers.hypotheses")
    # No rollup_support attribute → the import in the worker raises ImportError.
    fake.score_support = lambda h, study_specs: [
        {"study": s.get("name"), "observation": "matched prediction", "delta": "weakens"}
        for s in study_specs
    ]
    monkeypatch.setitem(sys.modules, "pbg_superpowers.hypotheses", fake)

    d = build_investigation_hypotheses(tmp_ws, "the-inv")
    log = d["hypotheses"][0]["support_log"]
    assert log and log[0]["delta"] == "weakens"


def test_tolerant_on_compute_failure(tmp_ws, monkeypatch):
    """A throwing rollup_support degrades to the authored hypotheses, not 500."""

    fake = types.ModuleType("pbg_superpowers.hypotheses")
    fake.rollup_support = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setitem(sys.modules, "pbg_superpowers.hypotheses", fake)

    d = build_investigation_hypotheses(tmp_ws, "the-inv")
    assert d["hypotheses"][0]["id"] == "H1"  # authored, un-enriched
