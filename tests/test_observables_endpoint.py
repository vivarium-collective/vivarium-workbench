"""Tests for the SP2b-i never-fabricate observable guard endpoints.

Two dashboard endpoints wire the (previously orphaned) ``readout_validation``
into a live path:

  * ``GET /api/observables?ref=<composite>``   → emittable leaves + catalogs
    (``Handler._observables_for_ref`` / ``_observables_for_ref_test``)
  * ``GET /api/study-observable-check?study=<slug>`` → per-readout validation
    against the study's composite, surfacing ``not_in_structure`` phantom
    observables (``Handler._study_observable_check`` / ``..._test``)

Unit tests use the cheap ``ws_increase_demo`` spec-composite fixture; the
whole-cell build is reserved for the v2e-invest golden (Task 4).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from vivarium_dashboard import server

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
_REF = "pbg_ws_increase_demo.composites.increase-demo"


@pytest.fixture
def demo_ws(tmp_path):
    """A throwaway copy of the increase-demo workspace (spec composite)."""
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURE, ws)
    return ws


def _write_study(ws: Path, slug: str, spec: dict) -> None:
    import yaml
    sdir = ws / "studies" / slug
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "study.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")


# ---------------------------------------------------------------------------
# Task 1: GET /api/observables
# ---------------------------------------------------------------------------

def test_observables_endpoint_lists_leaves_and_catalogs(demo_ws):
    body, code = server.Handler._observables_for_ref_test(demo_ws, _REF)
    assert code == 200, body
    d = json.loads(body)
    assert isinstance(d["leaves"], list) and d["leaves"]      # emittable paths
    assert "stores.level" in d["leaves"]                      # a known leaf
    assert isinstance(d["catalogs"], dict)                    # {observable: [labels]}


def test_observables_endpoint_unknown_ref_clear_error(demo_ws):
    body, code = server.Handler._observables_for_ref_test(demo_ws, "nope.not.a.composite")
    assert code >= 400
    assert "error" in json.loads(body)


# ---------------------------------------------------------------------------
# Task 2: GET /api/study-observable-check
# ---------------------------------------------------------------------------

def test_study_observable_check_flags_phantom(demo_ws):
    _write_study(demo_ws, "the-study", {
        "name": "the-study",
        "baseline": [{"name": "base", "composite": _REF}],
        "readouts": [
            {"name": "real-one", "store_path": "stores.level"},
            {"name": "phantom-one", "store_path": "stores.nonexistent"},
        ],
    })
    body, code = server.Handler._study_observable_check_test(demo_ws, "the-study")
    assert code == 200, body
    payload = json.loads(body)
    res = payload["readouts"]
    assert payload["composite"] == _REF
    # the never-fabricate flag: a phantom selector is flagged, not passed
    assert any(r["name"] == "phantom-one" and r["status"] == "not_in_structure"
               for r in res), res
    assert any(r["status"] == "ok" for r in res), res     # the real one passes


def test_study_observable_check_uncomputable_composite_clear_status(demo_ws):
    _write_study(demo_ws, "broken-study", {
        "name": "broken-study",
        "baseline": [{"name": "base", "composite": "pbg_ws_increase_demo.composites.does-not-exist"}],
        "readouts": [{"name": "real-one", "store_path": "stores.level"}],
    })
    body, code = server.Handler._study_observable_check_test(demo_ws, "broken-study")
    # composite can't build → a clear non-crash status, never a 500
    assert code in (200, 422), body
    assert code != 500
