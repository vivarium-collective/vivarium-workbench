"""Expert resolution of open investigation decisions.

`resolve_decision` backs POST /api/investigation-decision-resolve: move an
`executive.decisions_needed[index]` entry into `executive.decisions_resolved`
with the expert's resolution note, preserving the remaining open decisions.
Mirrors the proposed-input-decision seam + its test.
"""
import yaml

from vivarium_workbench.lib.lifecycle_mutations import resolve_decision
from vivarium_workbench.lib.report_views import build_iset_detail

_INV_YAML = """\
name: origins-of-life-vent
title: Origins of life
status: in-progress
studies: []
executive:
  what_is_this: A campaign.
  verdict: In progress.
  verdict_status: in-progress
  decisions_resolved:
  - 'Primary engine: real PHREEQC.'
  decisions_needed:
  - question: Close the paper at the pre-genetic arc or extend?
    context: Both scopes are available.
  - question: Which enrichment module to build first?
    context: Carbon is not the only bottleneck.
inputs:
  references:
  - already-provided-ref
  expert_docs:
  - name: Scientific Enrichment Plan
    path: references/scientific-enrichment-plan.md
"""


def _ws(tmp):
    (tmp / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    inv = tmp / "investigations" / "origins-of-life-vent"
    (inv / "studies").mkdir(parents=True)
    (inv / "investigation.yaml").write_text(_INV_YAML, encoding="utf-8")
    return tmp


def _spec(ws):
    return yaml.safe_load(
        (ws / "investigations" / "origins-of-life-vent" / "investigation.yaml").read_text())


def _resolve(ws, **body):
    body.setdefault("investigation", "origins-of-life-vent")
    return resolve_decision(ws, body)


def test_resolve_moves_decision_to_resolved(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _resolve(ws, index=0, resolution="Close at the pre-genetic arc.")
    assert code == 200
    assert resp["ok"] is True
    assert resp["remaining"] == 1
    spec = _spec(ws)
    ex = spec["executive"]
    # The chosen decision is gone from decisions_needed...
    assert len(ex["decisions_needed"]) == 1
    assert ex["decisions_needed"][0]["question"].startswith("Which enrichment")
    # ...and appended to decisions_resolved with both question + note.
    assert len(ex["decisions_resolved"]) == 2
    last = ex["decisions_resolved"][-1]
    assert "Close the paper at the pre-genetic arc" in last
    assert "Close at the pre-genetic arc." in last


def test_resolve_second_decision_by_index(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _resolve(ws, index=1, resolution="Nitrogen first.")
    assert code == 200
    spec = _spec(ws)
    ex = spec["executive"]
    assert len(ex["decisions_needed"]) == 1
    assert ex["decisions_needed"][0]["question"].startswith("Close the paper")
    assert "Nitrogen first." in ex["decisions_resolved"][-1]


def test_question_guard_matches(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _resolve(
        ws, index=0, resolution="ok",
        question="Close the paper at the pre-genetic arc or extend?")
    assert code == 200


def test_question_guard_mismatch_409(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _resolve(
        ws, index=0, resolution="ok", question="A different, stale question")
    assert code == 409
    assert "error" in resp
    # Nothing mutated on a 409.
    assert len(_spec(ws)["executive"]["decisions_needed"]) == 2


def test_index_out_of_range_404(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _resolve(ws, index=5, resolution="ok")
    assert code == 404
    assert "error" in resp


def test_missing_resolution_400(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _resolve(ws, index=0, resolution="   ")
    assert code == 400
    assert "error" in resp


def test_missing_index_400(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _resolve(ws, resolution="ok")
    assert code == 400
    assert "error" in resp


def test_non_integer_index_400(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _resolve(ws, index="abc", resolution="ok")
    assert code == 400
    assert "error" in resp


def test_missing_investigation_404(tmp_path):
    ws = _ws(tmp_path)
    resp, code = resolve_decision(
        ws, {"investigation": "does-not-exist", "index": 0, "resolution": "ok"})
    assert code == 404
    assert "error" in resp


def test_no_executive_block_404(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "bare"
    (inv / "studies").mkdir(parents=True)
    (inv / "investigation.yaml").write_text("name: bare\nstudies: []\n", encoding="utf-8")
    resp, code = resolve_decision(
        tmp_path, {"investigation": "bare", "index": 0, "resolution": "ok"})
    assert code == 404
    assert "error" in resp


def test_detail_exposes_decisions_and_campaign_docs(tmp_path):
    ws = _ws(tmp_path)
    detail = build_iset_detail(ws, "origins-of-life-vent")
    assert detail is not None
    # The cockpit reads executive.decisions_needed / decisions_resolved from here.
    ex = detail["executive"]
    assert len(ex["decisions_needed"]) == 2
    assert len(ex["decisions_resolved"]) == 1
    # campaign_docs merges inputs.expert_docs (with the plan's path) — the doc
    # the cockpit links. Deduped by name, path-bearing entry preferred.
    docs = {d["name"]: d for d in detail["campaign_docs"]}
    assert "Scientific Enrichment Plan" in docs
    assert docs["Scientific Enrichment Plan"]["path"] == "references/scientific-enrichment-plan.md"


def test_round_trip_preserves_other_fields(tmp_path):
    ws = _ws(tmp_path)
    _resolve(ws, index=0, resolution="done")
    spec = _spec(ws)
    # Unrelated blocks survive the ruamel round-trip write.
    assert spec["inputs"]["references"] == ["already-provided-ref"]
    assert spec["inputs"]["expert_docs"][0]["path"] == "references/scientific-enrichment-plan.md"
    assert spec["executive"]["what_is_this"] == "A campaign."
