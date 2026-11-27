"""KnowledgeStep wired read-only into the investigation composite run."""
import yaml

from vivarium_workbench.lib import knowledge_step
from vivarium_workbench.lib.investigation_execution import (
    build_investigation_composite,
    run_investigation_composite,
)


def _mk(ws, inv, members):
    (ws / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv_dir = ws / "investigations" / inv
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(
        yaml.safe_dump({"name": inv, "members": members}), encoding="utf-8")
    for s in members:
        sd = ws / "studies" / s
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(
            yaml.safe_dump({"claim": f"{s} claim"}), encoding="utf-8")


def test_default_build_is_unchanged(tmp_path):
    _mk(tmp_path, "inv", ["s0", "s1"])
    state = build_investigation_composite(tmp_path, "inv")
    assert "knowledge_step" not in state
    assert "knowledge" not in state


def test_include_knowledge_adds_a_readonly_node(tmp_path):
    _mk(tmp_path, "inv", ["s0", "s1"])
    state = build_investigation_composite(tmp_path, "inv", include_knowledge=True)
    ks = state["knowledge_step"]
    assert ks["address"] == "local:KnowledgeStep"
    assert ks["config"] == {"workspace": str(tmp_path), "investigation_slug": "inv"}
    assert ks["inputs"] == {}                              # reads the workspace, not run stores
    assert ks["outputs"] == {"knowledge": ["knowledge"]}
    assert "knowledge" in state                            # the output store exists


def test_run_produces_a_knowledge_node(tmp_path):
    _mk(tmp_path, "inv", ["s0"])
    knowledge_step._KNOWLEDGE_INPUTS = {
        "inv_spec": {"studies": ["s0"]},
        "studies_by_slug": {"s0": {"claim": "Oxygen limits growth", "verdict": "supported"}},
    }
    try:
        result = run_investigation_composite(
            tmp_path, "inv", run_study_fn=lambda w, s: {"written": [], "errors": []})
    finally:
        knowledge_step._KNOWLEDGE_INPUTS = None
    assert "knowledge" in result
    kn = result["knowledge"]
    assert kn is not None and "concepts" in kn and "verdicts" in kn
    assert kn["concepts"], "the single member study should seed one Concept"
