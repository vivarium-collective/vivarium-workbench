"""RFC-0007 validation panel: route shape, graceful degrade, SPA wiring."""
from pathlib import Path

import pytest
import yaml

STATIC = Path(__file__).resolve().parent.parent / "vivarium_workbench" / "static"


def _ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    inv = tmp_path / "investigations" / "demo-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({"name": "demo-inv", "studies": []}))


def test_validation_panel_route_shape(tmp_path, inproc_client):
    pytest.importorskip("discovery_engine.reporting.validation")
    _ws(tmp_path)
    r = inproc_client(workspace=tmp_path).get("/api/validation-panel/demo-inv")
    assert r.status_code == 200
    d = r.json()
    for k in ("census", "coverage", "types", "scopes", "violations", "records"):
        assert k in d
    assert len(d["types"]) == 8 and len(d["scopes"]) == 7
    assert set(d["census"]) >= {"passed", "failed", "inconclusive", "error"}
    assert isinstance(d["records"], list)


def test_build_validation_panel_degrades(tmp_path):
    from vivarium_workbench.lib.validation_panel import build_validation_panel
    assert build_validation_panel(tmp_path / "nope", "bogus") is None


def test_spa_wires_validation_panel():
    js = (STATIC / "walkthrough.js").read_text()
    assert "/api/validation-panel/" in js
    assert "function _vpRender" in js
    assert "function _vivLoadValidationPanel" in js
    assert "_vivLoadValidationPanel(name)" in js
