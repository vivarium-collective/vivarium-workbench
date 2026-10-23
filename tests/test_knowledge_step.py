"""KnowledgeStep — the thin process-bigraph adapter around
viva_superpowers.knowledge_store.derive_knowledge.

These tests drive the REAL process-bigraph engine (as the skeleton tests do):
a Composite containing one KnowledgeStep runs on init and must populate the
wired `knowledge` store with Concepts + canonical Verdicts. The pure derivation
is unit-tested in viva-superpowers; here we prove the pbg grounding end-to-end
and that the Progress/Verdict decoupling survives the engine round-trip.

Requires `process_bigraph` + `viva_superpowers` (the v2ecoli venv).
"""
from pathlib import Path

import yaml
from bigraph_schema import allocate_core
from process_bigraph import Composite

import vivarium_workbench.lib.knowledge_step as m
from vivarium_workbench.lib.knowledge_step import KnowledgeStep


def _core():
    return allocate_core(top={"KnowledgeStep": KnowledgeStep})


def _run_with_inputs(inv_spec, studies_by_slug):
    """Run a one-step Composite with inputs injected via the hermetic hook;
    return the knowledge store the engine wrote."""
    state = {
        "knowledge_store": {},
        "k": {
            "_type": "step",
            "address": "local:KnowledgeStep",
            "config": {"workspace": "/ws", "investigation_slug": "inv"},
            "inputs": {},
            "outputs": {"knowledge": ["knowledge_store"]},
        },
    }
    m._KNOWLEDGE_INPUTS = {"inv_spec": inv_spec, "studies_by_slug": studies_by_slug}
    try:
        comp = Composite({"state": state, "run_steps_on_init": True}, core=_core())
    finally:
        m._KNOWLEDGE_INPUTS = None
    return comp.state["knowledge_store"]


def _pass_study(*names):
    return {
        "name": "s",
        "behavior_tests": [{"name": n} for n in names],
        "runs": [{"name": "r1", "status": "completed",
                  "outcomes": {n: {"result": "PASS"} for n in names}}],
    }


def _fail_study(*names):
    s = _pass_study(*names)
    s["runs"][0]["outcomes"] = {n: {"result": "FAIL"} for n in names}
    return s


class TestKnowledgeStepInComposite:
    def test_engine_populates_knowledge_store(self):
        k = _run_with_inputs(
            {"studies": ["a", "b"]},
            {"a": _pass_study("t1"), "b": _fail_study("t1")},
        )
        assert set(k["verdicts"]) == {"a", "b"}
        assert k["verdicts"]["a"]["verdict"] == "supported"
        assert k["verdicts"]["b"]["verdict"] == "refuted"
        assert set(k["concepts"]) == {"a", "b"}
        assert k["concepts"]["a"]["derived"] is True

    def test_decoupling_survives_engine_round_trip(self):
        study = _fail_study("t1")
        study["execution_status"] = "failed"
        study["verdict"] = "supported"
        k = _run_with_inputs({"studies": ["a"]}, {"a": study})
        v = k["verdicts"]["a"]
        assert v["verdict"] == "supported"   # authored, not downgraded by progress
        assert v["progress"] == "failed"
        assert v["diverges"] is True


class TestLoadKnowledgeInputsFromDisk:
    def test_loads_investigation_and_member_studies(self, tmp_path):
        ws = tmp_path
        (ws / "workspace.yaml").write_text(
            yaml.safe_dump({"schema_version": 1, "name": "ws",
                            "created": "2026-10-23", "plugin_version": "0"}))
        inv_dir = ws / "investigations" / "inv"
        inv_dir.mkdir(parents=True)
        (inv_dir / "investigation.yaml").write_text(
            yaml.safe_dump({"name": "inv", "title": "Inv", "studies": ["a"]}))
        st_dir = ws / "studies" / "a"
        st_dir.mkdir(parents=True)
        (st_dir / "study.yaml").write_text(
            yaml.safe_dump({"name": "a", "verdict": "qualified"}))

        inv_spec, studies_by_slug = m._load_knowledge_inputs(str(ws), "inv")
        assert inv_spec["studies"] == ["a"]
        assert studies_by_slug["a"]["verdict"] == "qualified"
