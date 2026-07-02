# vivarium_workbench/testing/test_modular_tests_payload.py
import json
from pathlib import Path

from vivarium_workbench.lib import study_spec


def _ws(tmp_path):
    d = tmp_path / "studies" / "demo"
    (d / "viz" / "report_card").mkdir(parents=True)
    (d / "viz" / "report_card" / "standard.html").write_text("<h1>card</h1>", encoding="utf-8")
    (d / "viz" / "report_card" / "standard.verdict.json").write_text(
        json.dumps({"overall": "drift"}), encoding="utf-8")
    (d / "study.yaml").write_text(
        "schema_version: 4\nname: demo\nquestion: demo question\n"
        "conditions:\n  baseline:\n    composite: v2ecoli.composites.baseline.baseline\n"
        "tests:\n"
        "- {name: behavioral-one, measure: {kind: listener_path, path: x}}\n"
        "- {name: card-one, kind: report_card, card: standard}\n"
        "status: planned\n",
        encoding="utf-8")
    return tmp_path


def test_report_card_urls_and_kind_in_payload(tmp_path):
    ws = _ws(tmp_path)
    spec = study_spec.load_study_detail_spec(str(ws), "demo")
    tests = spec.get("tests") or spec.get("behavior_tests") or []
    by = {t["name"]: t for t in tests}
    assert by["behavioral-one"].get("kind", "behavioral") == "behavioral"
    assert by["card-one"]["kind"] == "report_card" and by["card-one"]["card"] == "standard"
    rc = spec["report_card_urls"]["standard"]
    assert rc["verdict"] == "drift"
    assert rc["url"].endswith("/studies/demo/viz/report_card/standard.html")
