"""Wave 3a #26: GET /api/framework-metrics + the `_framework_metrics` worker.

The endpoint aggregates framework-self metrics across every study + every
investigation in the workspace via the deterministic
``pbg_superpowers.rigor.framework_metrics`` and returns
``{metrics, n_investigations, n_studies}`` so the dashboard can render a
"Framework scorecard" section. AI-free + tolerant: never 500, typed payload on
absence/failure.
"""
from __future__ import annotations

import json

import yaml
import pytest


@pytest.fixture
def tmp_ws(tmp_path, monkeypatch):
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    inv_dir = ws / "investigations" / "the-inv"
    inv_dir.mkdir(parents=True)
    inv_dir.joinpath("investigation.yaml").write_text(yaml.safe_dump({
        "name": "the-inv", "studies": ["s1", "s2"],
    }))
    for slug in ("s1", "s2"):
        sd = ws / "studies" / slug
        sd.mkdir(parents=True)
        sd.joinpath("study.yaml").write_text(yaml.safe_dump({
            "schema_version": 4, "name": slug,
            "findings": [{"id": "F-01", "tier": "observation",
                          "statement": "x"}],
        }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws


def test_framework_metrics_counts_studies_and_investigations(tmp_ws):
    import vivarium_dashboard.server as server
    body, code = server.Handler._framework_metrics_test(server.WORKSPACE)
    assert code == 200
    d = json.loads(body)
    assert d["n_investigations"] == 1
    assert d["n_studies"] == 2
    assert "metrics" in d and isinstance(d["metrics"], dict)


def test_framework_metrics_tolerant_on_missing_ws(tmp_path):
    import vivarium_dashboard.server as server
    body, code = server.Handler._framework_metrics_test(tmp_path / "nope")
    assert code == 200  # never 500
    d = json.loads(body)
    # No studies / investigations on disk → zero counts; metrics is a dict
    # (empty when pbg-superpowers is absent, all-zero entries when present).
    assert d["n_investigations"] == 0
    assert d["n_studies"] == 0
    assert isinstance(d["metrics"], dict)


def test_framework_metrics_tolerant_on_compute_failure(tmp_ws, monkeypatch):
    import vivarium_dashboard.server as server
    pytest.importorskip("pbg_superpowers.rigor")
    from pbg_superpowers import rigor as _rigor
    if not hasattr(_rigor, "framework_metrics"):
        pytest.skip("pbg_superpowers.rigor.framework_metrics not available")

    monkeypatch.setattr(_rigor, "framework_metrics",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    body, code = server.Handler._framework_metrics_test(server.WORKSPACE)
    assert code == 200  # never 500
    d = json.loads(body)
    assert d["metrics"] == {}
    # The counts are still computed even when the metric math fails.
    assert d["n_studies"] == 2
