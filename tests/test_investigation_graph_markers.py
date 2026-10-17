"""Server-side: build_investigation_graph surfaces verdict + evidence_tier on
nodes and the authored parent_relations on edges."""
import yaml
from pathlib import Path

from vivarium_workbench.lib.investigation_graph_views import build_investigation_graph


def _ws(tmp_path: Path) -> Path:
    inv = tmp_path / "investigations" / "inv1"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.dump({
        "schema_version": 2, "name": "inv1", "studies": ["s-parent", "s-child"],
    }))
    for slug, extra in [
        ("s-parent", {}),
        ("s-child", {
            "verdict": "honest-negative", "evidence_tier": "conceptual_demonstration",
            "parent_studies": [{"study": "s-parent", "condition": "tests-passed"}],
            "parent_relations": [{"study": "s-parent", "relation": "alternative-to"}],
            "pipeline_gate": {"prerequisites": [{"study": "s-parent"}]},
        }),
    ]:
        d = tmp_path / "studies" / slug
        d.mkdir(parents=True)
        base = {"schema_version": 4, "name": slug, "title": slug.upper(),
                "baseline": [{"name": "b", "composite": "a.b"}]}
        base.update(extra)
        (d / "study.yaml").write_text(yaml.dump(base))
    (tmp_path / "workspace.yaml").write_text(yaml.dump({"schema_version": 2, "name": "w"}))
    return tmp_path


def test_node_carries_verdict_and_tier(tmp_path):
    payload, code = build_investigation_graph(_ws(tmp_path), "inv1")
    assert code == 200
    child = next(n for n in payload["studies"] if n["slug"] == "s-child")
    # Legacy authored verdict "honest-negative" folds to the canonical "refuted".
    assert child["verdict"] == "refuted"
    assert child["evidence_tier"] == "conceptual_demonstration"
    # Every node carries a derived execution_status (the Progress axis).
    assert child["execution_status"] in {"proposed", "planned", "running", "completed", "failed"}


def test_node_omits_absent_fields(tmp_path):
    payload, _ = build_investigation_graph(_ws(tmp_path), "inv1")
    parent = next(n for n in payload["studies"] if n["slug"] == "s-parent")
    assert "verdict" not in parent and "evidence_tier" not in parent


def test_edge_carries_authored_relation(tmp_path):
    payload, _ = build_investigation_graph(_ws(tmp_path), "inv1")
    edge = next(e for e in payload["study_edges"]
                if e["source"] == "study/s-parent" and e["target"] == "study/s-child")
    assert edge["relation"] == "alternative-to"
