# vivarium_dashboard/testing/test_modular_tests_e2e.py
"""End-to-end payload check: study with mixed behavioral + report_card tests.

Serves a fixture workspace via FastAPI TestClient and asserts that
GET /api/study/<slug> returns:
  - report_card_urls[<card>] with the correct url + verdict (A1)
  - tests entries with the correct kind field (A1/A2)
"""

import json
from pathlib import Path

import pytest

# NOTE: fastapi's TestClient (via starlette) requires ``httpx`` at *import*
# time. Importing it at module top-level makes this module a landmine: any
# eager package-discovery walk (e.g. bigraph-schema's ``build_core()``) that
# imports everything under the installed ``vivarium_dashboard`` package would
# crash with a RuntimeError when httpx is absent. ``pytest.importorskip``
# raises ``Skipped`` (a ``BaseException``), which a plain ``import`` would NOT
# swallow either — so the heavy imports live *inside* the test function. The
# module then imports cleanly everywhere; the test simply skips when httpx is
# not installed. ``httpx`` is declared in the ``[dev]`` extra so dashboard CI
# still runs it.


def _fixture(tmp_path: Path) -> Path:
    d = tmp_path / "studies" / "demo"
    (d / "viz" / "report_card").mkdir(parents=True)
    (d / "viz" / "report_card" / "standard.html").write_text(
        "<h1>std card</h1>", encoding="utf-8"
    )
    (d / "viz" / "report_card" / "standard.verdict.json").write_text(
        json.dumps({"overall": "mismatch"}), encoding="utf-8"
    )
    # v4 schema shape — the shape that passes dashboard validation
    # (schema_version: 4, conditions.baseline.composite, question, status)
    (d / "study.yaml").write_text(
        "schema_version: 4\n"
        "name: demo\n"
        "question: demo question\n"
        "conditions:\n"
        "  baseline:\n"
        "    composite: v2ecoli.composites.baseline.baseline\n"
        "tests:\n"
        "- {name: beh, measure: {kind: listener_path, path: x}}\n"
        "- {name: std, kind: report_card, card: standard}\n"
        "status: planned\n",
        encoding="utf-8",
    )
    return tmp_path


def test_study_detail_payload_has_mixed_tests_and_card_url(tmp_path: Path) -> None:
    pytest.importorskip("httpx")  # starlette's TestClient needs httpx at import
    from fastapi.testclient import TestClient

    from vivarium_dashboard.api.app import create_app, get_workspace

    ws = _fixture(tmp_path)
    app = create_app()
    # Inject the fixture workspace via dependency_overrides (create_app takes no args)
    app.dependency_overrides[get_workspace] = lambda: ws
    client = TestClient(app)
    r = client.get("/api/study/demo")
    assert r.status_code == 200
    d = r.json()
    rc = d["report_card_urls"]["standard"]
    assert rc["verdict"] == "mismatch"
    assert rc["url"].endswith("/studies/demo/viz/report_card/standard.html")
    kinds = {t["name"]: t.get("kind", "behavioral") for t in (d.get("tests") or [])}
    assert kinds == {"beh": "behavioral", "std": "report_card"}
