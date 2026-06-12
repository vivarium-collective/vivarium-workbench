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
