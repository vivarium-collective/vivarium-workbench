"""Expert accept/decline of agent-proposed inputs.

`_decide_proposed_input_for_test` backs POST /api/proposed-input-decision:
accept/decline an item in `proposed_inputs.items[]`, and on accept promote a
`kind: reference` item into `inputs.references`.
"""
import yaml

from vivarium_workbench.lib.lifecycle_mutations import decide_proposed_input
from vivarium_workbench.lib.report_views import build_iset_detail


def _decide_proposed_input_for_test(ws, inv, item_id, decision):
    return decide_proposed_input(
        ws, {"investigation": inv, "item_id": item_id, "decision": decision})


def _build_iset_detail_for_test(ws, name):
    detail = build_iset_detail(ws, name)
    if detail is None:
        return {"error": f"investigation '{name}' not found"}, 404
    return detail, 200

_INV_YAML = """\
name: dnaa-replication
title: DnaA
status: in-progress
studies: []
inputs:
  references:
  - already-provided-ref
proposed_inputs:
  _note: agent-suggested; not provided by the expert
  items:
  - id: chipseq-ref
    kind: reference
    citation: Smith 2024 ChIP-seq
    related_study: dnaa-3
    rationale: argued a box-capacity cap
    provenance: commit abc123; not provided
    status: pending
  - id: data-sink
    kind: mechanism
    summary: datA titration sink
    related_study: dnaa-3
    rationale: would lower free DnaA-ATP
    provenance: not provided / out of scope
    status: pending
"""


def _ws(tmp):
    (tmp / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    inv = tmp / "investigations" / "dnaa-replication"
    (inv / "studies").mkdir(parents=True)
    (inv / "investigation.yaml").write_text(_INV_YAML, encoding="utf-8")
    return tmp


def _spec(ws):
    return yaml.safe_load(
        (ws / "investigations" / "dnaa-replication" / "investigation.yaml").read_text())


def test_detail_includes_proposed_inputs(tmp_path):
    ws = _ws(tmp_path)
    detail, code = _build_iset_detail_for_test(ws, "dnaa-replication")
    assert code == 200
    assert len(detail["proposed_inputs"]["items"]) == 2


def test_accept_reference_promotes_to_inputs(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _decide_proposed_input_for_test(ws, "dnaa-replication", "chipseq-ref", "accept")
    assert code == 200
    assert resp["status"] == "accepted"
    assert resp["kind"] == "reference"
    spec = _spec(ws)
    items = {it["id"]: it for it in spec["proposed_inputs"]["items"]}
    assert items["chipseq-ref"]["status"] == "accepted"
    # Promoted into the provided references (de-duped against the existing one).
    assert "chipseq-ref" in spec["inputs"]["references"]
    assert spec["inputs"]["references"].count("chipseq-ref") == 1


def test_accept_mechanism_marks_only(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _decide_proposed_input_for_test(ws, "dnaa-replication", "data-sink", "accept")
    assert code == 200
    assert resp["status"] == "accepted"
    assert resp["kind"] == "mechanism"
    spec = _spec(ws)
    items = {it["id"]: it for it in spec["proposed_inputs"]["items"]}
    assert items["data-sink"]["status"] == "accepted"
    # A mechanism is NOT auto-added to references.
    assert "data-sink" not in spec["inputs"]["references"]


def test_decline_marks_declined(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _decide_proposed_input_for_test(ws, "dnaa-replication", "chipseq-ref", "decline")
    assert code == 200
    assert resp["status"] == "declined"
    spec = _spec(ws)
    items = {it["id"]: it for it in spec["proposed_inputs"]["items"]}
    assert items["chipseq-ref"]["status"] == "declined"
    assert "chipseq-ref" not in spec["inputs"]["references"]


def test_unknown_item_404(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _decide_proposed_input_for_test(ws, "dnaa-replication", "nope", "accept")
    assert code == 404
    assert "error" in resp


def test_bad_decision_400(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _decide_proposed_input_for_test(ws, "dnaa-replication", "chipseq-ref", "maybe")
    assert code == 400
    assert "error" in resp


def test_missing_investigation_404(tmp_path):
    ws = _ws(tmp_path)
    resp, code = _decide_proposed_input_for_test(ws, "does-not-exist", "chipseq-ref", "accept")
    assert code == 404
    assert "error" in resp
