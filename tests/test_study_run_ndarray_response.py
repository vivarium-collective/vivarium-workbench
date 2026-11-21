"""Regression: study-run HTTP responses must serialize composite numpy output.

``POST /api/study-run-baseline`` (and ``/api/study-run-variant``) return the
run-result dict, which can embed numpy arrays/scalars emitted by the composite's
state. Starlette's stock ``JSONResponse.render`` calls ``json.dumps`` with no
``default=``, so such a body raised ``TypeError: Object of type ndarray is not
JSON serializable`` → a 500, even though the detached run SUCCEEDED and runs.db
was written. Reported on the ``viva_simulation_core`` ``sepsis_sbml`` studies:
the run graded nothing into ``outcomes`` because the HTTP flush 500'd, so the
gate verdict stayed pending. The handlers now return ``SanitizedJSONResponse``,
which renders with ``_json_default`` (+ a ``_json_sanitize`` non-finite pass).
"""
import json

import numpy as np
import pytest
from fastapi.responses import JSONResponse

from vivarium_workbench.api import app as api_app


def test_sanitized_response_encodes_numpy_and_non_finite():
    resp = api_app.SanitizedJSONResponse(content={
        "arr": np.array([1.0, 2.0, 3.0]),
        "matrix": np.array([[1.0, 2.0], [3.0, 4.0]]),
        "scalar": np.float64(2.5),
        "bad": float("nan"),
        "inf": float("inf"),
        "ok": "x",
    })
    data = json.loads(resp.body)
    assert data["arr"] == [1.0, 2.0, 3.0]
    assert data["matrix"] == [[1.0, 2.0], [3.0, 4.0]]
    assert data["scalar"] == 2.5
    assert data["bad"] is None       # non-finite float → null (browser JSON.parse-safe)
    assert data["inf"] is None
    assert data["ok"] == "x"


def test_stock_jsonresponse_would_have_raised_on_ndarray():
    # Proves the bug the subclass fixes: the stock response can't encode an array.
    with pytest.raises(TypeError):
        JSONResponse(content={"arr": np.array([1.0, 2.0])})


def test_sanitized_response_is_byte_identical_for_plain_content():
    # The subclass only ADDS numpy/non-finite handling; a body that already
    # serialized must render to the exact same bytes as the stock response.
    content = {"b": 2, "a": "x", "nested": {"z": [1, 2, 3]}, "u": "café"}
    assert (
        api_app.SanitizedJSONResponse(content=content).body
        == JSONResponse(content=content).body
    )


def test_study_run_baseline_route_serializes_numpy_body(tmp_path, inproc_client, monkeypatch):
    # The runner returns a result dict carrying an emitted numpy field; the route
    # must respond 200 with the array serialized, not 500.
    result = {
        "run_id": "r1",
        "status": "complete",
        "field": np.array([[1.0, 2.0], [3.0, 4.0]]),
        "outcomes": {"c_oxygen": np.float64(0.42)},
    }
    monkeypatch.setattr(
        api_app._study_runs, "run_study_baseline",
        lambda ws, body: (result, 200),
    )
    client = inproc_client(workspace=tmp_path)
    r = client.post("/api/study-run-baseline", json={"study": "s1", "steps": 20})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["field"] == [[1.0, 2.0], [3.0, 4.0]]
    assert data["outcomes"]["c_oxygen"] == 0.42
    assert data["status"] == "complete"


def test_study_run_variant_route_serializes_numpy_body(tmp_path, inproc_client, monkeypatch):
    result = {"run_id": "v1", "status": "complete", "series": np.arange(3, dtype=float)}
    monkeypatch.setattr(
        api_app._study_runs, "run_study_variant",
        lambda ws, body: (result, 200),
    )
    client = inproc_client(workspace=tmp_path)
    r = client.post("/api/study-run-variant", json={"study": "s1", "variant": "v1"})
    assert r.status_code == 200, r.text
    assert r.json()["series"] == [0.0, 1.0, 2.0]
