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


# ---------------------------------------------------------------------------
# Task 3: lineage-prefix normalization (the SP2b-i false-positive fix)
# ---------------------------------------------------------------------------

def test_lineage_prefix_normalization_bare_readouts_match():
    """The whole-cell composite runs as a LINEAGE: cell leaves are nested under
    ``agents.<n>.``.  Studies author *bare* single-cell paths.  The dashboard's
    ``_augment_lineage_aliases`` strips a leading ``agents.<n>.`` so a bare
    readout matches the real prefixed leaf — WITHOUT inventing arbitrary leaves
    (a genuinely-absent path still flags ``not_in_structure``).
    """
    from pbg_superpowers.readout_validation import validate_readouts

    available = {
        "leaves": ["agents.0.listeners.x", "agents.0.unique.y"],
        "catalogs": {},
    }
    aug = server._augment_lineage_aliases(available)
    # raw prefixed forms are preserved …
    assert "agents.0.listeners.x" in aug["leaves"]
    # … and the stripped aliases are added
    assert "listeners.x" in aug["leaves"]
    assert "unique.y" in aug["leaves"]

    spec = {"readouts": [
        {"name": "x", "store_path": "listeners.x"},
        {"name": "y", "store_path": "unique.y"},
        {"name": "bogus", "store_path": "listeners.bogus"},
    ]}
    by = {r["name"]: r["status"] for r in validate_readouts(spec, available=aug)}
    assert by["x"] == "ok", by
    assert by["y"] == "ok", by
    # never-fabricate preserved: a genuinely-absent leaf still flags
    assert by["bogus"] == "not_in_structure", by


# ---------------------------------------------------------------------------
# Task 4: v2e-invest golden (skipif absent / not buildable in this interpreter)
# ---------------------------------------------------------------------------

_V2E_INVEST = Path("/Users/eranagmon/code/v2e-invest")
_V2E_BASELINE = "v2ecoli.composites.baseline.baseline"
_V2E_STUDY = "dnaa-00-stage1-baseline"
_VALID_STATUSES = {"ok", "unresolved", "not_in_structure", "aspirational"}


def _v2e_observables_or_skip():
    """Build the real v2ecoli baseline composite's observable set, or skip.

    Skips when v2e-invest is absent OR when the whole-cell composite cannot be
    built in the *current* interpreter (the bare vivarium-dashboard venv lacks
    v2ecoli's runtime deps — dill/unum/etc.). In a v2ecoli-equipped venv this
    becomes a real golden; everywhere else the suite stays green. READ-ONLY:
    never writes under v2e-invest.
    """
    if not (_V2E_INVEST / "workspace.yaml").is_file():
        pytest.skip("v2e-invest not present")
    try:
        from pbg_superpowers.readout_validation import available_observables  # noqa: F401
    except Exception:
        pytest.skip("pbg_superpowers.readout_validation unavailable")
    body, code = server.Handler._observables_for_ref_test(_V2E_INVEST, _V2E_BASELINE)
    payload = json.loads(body)
    if code != 200 or not payload.get("leaves"):
        pytest.skip(f"v2ecoli baseline not buildable in this interpreter: {code} {payload.get('error')}")
    return payload


def test_v2e_invest_golden_observables_nonempty():
    payload = _v2e_observables_or_skip()
    leaves = payload["leaves"]
    assert isinstance(leaves, list) and len(leaves) > 100, len(leaves)
    # the real whole-cell composite nests the cell under agents/0/.
    assert any(l.endswith("listeners.mass.cell_mass") for l in leaves), leaves[:10]


def test_v2e_invest_golden_study_readout_statuses():
    _v2e_observables_or_skip()  # gate: ensures buildable before checking the study
    body, code = server.Handler._study_observable_check_test(_V2E_INVEST, _V2E_STUDY)
    assert code == 200, body
    payload = json.loads(body)
    assert payload["composite"] == _V2E_BASELINE
    res = payload["readouts"]
    assert res, "study should declare readouts"
    # every readout gets a status from the valid set — no crash, no fabrication
    assert all(r["status"] in _VALID_STATUSES for r in res), res
    statuses = {r["status"] for r in res}
    # the prose/`derived` readouts can't be parsed → unresolved
    assert "unresolved" in statuses, statuses


def test_v2e_invest_golden_real_leaf_ok_phantom_flagged():
    """Against the REAL composite structure, exercising the lineage-prefix fix:
    a BARE single-cell readout (`listeners.mass.cell_mass`, NOT pre-prefixed
    with ``agents.0.``) passes (`ok`) once the dashboard normalizes the
    available set, while an invented leaf (`listeners.totally_fabricated`) is
    still flagged (`not_in_structure`) — the never-fabricate value preserved.
    """
    payload = _v2e_observables_or_skip()
    from pbg_superpowers.readout_validation import validate_readouts
    # the real emitted form is prefixed (lineage); the study authors it bare
    assert any(l.endswith("listeners.mass.cell_mass") for l in payload["leaves"])
    assert "listeners.mass.cell_mass" not in payload["leaves"]  # bare form NOT raw-emitted
    available = server._augment_lineage_aliases(
        {"leaves": payload["leaves"], "catalogs": payload["catalogs"]}
    )
    spec = {"readouts": [
        {"name": "real", "store_path": "listeners.mass.cell_mass"},
        {"name": "phantom", "store_path": "listeners.totally_fabricated"},
    ]}
    res = validate_readouts(spec, available=available)
    by = {r["name"]: r["status"] for r in res}
    assert by["real"] == "ok", res
    assert by["phantom"] == "not_in_structure", res
