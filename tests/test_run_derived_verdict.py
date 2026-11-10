"""The Verdict pill is run-aware: with no authored verdict, graded PASSING
behavior-test outcomes yield the engine's DERIVED verdict (flagged
verdict_source="derived"); an authored verdict still wins; ungraded stays pending."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytest.importorskip("discovery_engine")

from vivarium_workbench.lib.investigation_graph_views import build_investigation_graph  # noqa: E402
from vivarium_workbench.lib.investigations_index import build_investigations  # noqa: E402
from vivarium_workbench.lib.report_views import build_iset_detail  # noqa: E402
from vivarium_workbench.lib.study_ontology import derive_verdict, resolve_verdict  # noqa: E402

_TESTS = [{"name": "t1"}, {"name": "t2"}]


def _graded(result="PASS"):
    return [{"status": "completed", "timestamp": "2026-01-01",
             "outcomes": {"t1": {"result": result}, "t2": {"result": "PASS"}}}]


def _write(ws: Path, name: str, **fields) -> None:
    p = ws / "studies" / name / "study.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(
        {"schema_version": 3, "name": name,
         "baseline": [{"composite": "x", "name": "b"}], **fields}, sort_keys=False))


def _ws(tmp_path):
    ws = tmp_path / "ws"
    _write(ws, "passing", behavior_tests=_TESTS, runs=_graded())
    _write(ws, "authored", behavior_tests=_TESTS, runs=_graded(), verdict="refuted")
    _write(ws, "ungraded")
    p = ws / "investigations" / "inv" / "investigation.yaml"
    p.parent.mkdir(parents=True)
    p.write_text(yaml.safe_dump({"name": "inv", "title": "inv",
                                 "studies": ["passing", "authored", "ungraded"]}))
    return ws


def test_passing_graded_tests_report_derived_verdict(tmp_path):
    ws = _ws(tmp_path)
    rows = {r["name"]: r for r in build_investigations(ws)["investigations"]}
    assert rows["passing"]["verdict"] == "supported"        # was pending (None)
    assert rows["passing"]["verdict_source"] == "derived"
    assert rows["authored"]["verdict"] == "refuted"         # authored wins
    assert rows["authored"]["verdict_source"] == "authored"
    assert rows["ungraded"]["verdict"] is None              # genuinely pending
    assert rows["ungraded"]["verdict_source"] is None

    detail = {s["name"]: s for s in build_iset_detail(ws, "inv")["studies"]}
    assert (detail["passing"]["verdict"], detail["passing"]["verdict_source"]) == ("supported", "derived")
    assert detail["authored"]["verdict"] == "refuted"

    graph, _ = build_investigation_graph(ws, "inv")
    node = next(n for n in graph["studies"] if n["slug"] == "passing")
    assert (node["verdict"], node["verdict_source"]) == ("supported", "derived")


def test_resolve_verdict_unit():
    assert resolve_verdict({"verdict": "supported"}) == ("supported", "authored")
    assert resolve_verdict({}) == (None, None)
    assert derive_verdict({"behavior_tests": _TESTS, "runs": _graded("FAIL")}) == "refuted"
