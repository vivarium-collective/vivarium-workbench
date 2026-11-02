"""The shared two-axis node derivation (study_ontology.two_axis_node_fields).

Both the investigation GRAPH builder and the DETAIL endpoint's study builder call this one
helper, so the DAG cards (rendered from the detail payload) and the graph can never diverge on
parked/synthesis again — the split that produced parked-as-pending (#1551) and the synthesis
"Completed · pending" (#1552) on the cards even after both were fixed in the graph builder.
"""
from vivarium_workbench.lib.study_ontology import two_axis_node_fields as f


def test_parked_not_yet_started_reads_parked():
    r = f({"name": "s", "status": "planned", "parked": True, "kind": "refinement"})
    assert r["execution_status"] == "parked" and r["parked"] is True


def test_parked_is_overridden_by_executed_status():
    # a study that actually ran keeps its execution status though still flagged parked
    r = f({"name": "s", "status": "complete", "parked": True})
    assert r["execution_status"] == "completed" and r["parked"] is True


def test_synthesis_verdict_seeded_from_terminal_authored_confidence():
    r = f({"name": "s", "kind": "synthesis", "status": "complete", "confidence": "Accepted"})
    assert r["verdict"] == "supported" and r["verdict_source"] == "derived"
    assert f({"name": "s", "kind": "synthesis", "status": "complete",
              "confidence": "Refuted"})["verdict"] == "refuted"


def test_non_synthesis_accepted_confidence_stays_pending():
    r = f({"name": "s", "status": "complete", "confidence": "Accepted"})
    assert r.get("verdict") is None


def test_kind_is_carried_through():
    assert f({"name": "s", "kind": "synthesis"}).get("kind") == "synthesis"
    assert "kind" not in f({"name": "s"})


def test_graph_and_detail_builders_use_the_same_helper(tmp_path):
    """Parity: a parked study's node from build_investigation_graph and its study entry from
    build_iset_detail agree on execution_status + parked (both route through the helper)."""
    import yaml
    from vivarium_workbench.lib.investigation_graph_views import build_investigation_graph
    from vivarium_workbench.lib.report_views import build_iset_detail
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    inv = tmp_path / "investigations" / "demo"; inv.mkdir(parents=True)
    inv.joinpath("investigation.yaml").write_text(yaml.safe_dump({"name": "demo", "studies": ["p1"]}))
    sd = tmp_path / "studies" / "p1"; sd.mkdir(parents=True)
    sd.joinpath("study.yaml").write_text(yaml.safe_dump(
        {"schema_version": 4, "name": "p1", "status": "planned", "parked": True, "kind": "refinement"}))
    gnode = {n["slug"]: n for n in build_investigation_graph(tmp_path, "demo")[0]["studies"]}["p1"]
    detail = build_iset_detail(tmp_path, "demo")
    dstudy = {s["name"]: s for s in (detail.get("studies") or [])}["p1"]
    assert gnode["execution_status"] == dstudy["execution_status"] == "parked"
    assert gnode.get("parked") == dstudy.get("parked") is True
