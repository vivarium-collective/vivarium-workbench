"""Tests for ``POST /api/study-findings-populate-observations``
(``lib.finding_observations_views.study_findings_populate_observations``).

Phase 2.1f (rewire-first): this endpoint wraps
``discovery_engine.finding_observations.populate_finding_observations``
unchanged — the plugin still fills the code-owned finding slots, only the
caller (the workbench, on behalf of the ``/viva-biology-forward`` skill) moves.
These tests exercise the lib builder directly (the same "endpoint test calls
the lib fn" idiom as ``test_study_readout_migrate_endpoint.py``) plus an
equivalence check against calling ``populate_finding_observations`` directly.
"""
from pathlib import Path

import pytest

from vivarium_workbench.lib import finding_observations_views as views

# A study with one finding linked to a behavior_test via evidence.from_test,
# a canonical run whose computed_outcomes carries the measured_value, and a
# matching readout for units. The measured value (0.35) lands inside the
# pass_if band [0.2, 0.5] → divergence_factor == 0.0.
STUDY_YAML_TEXT = """\
# A hand-authored study with comments that MUST survive population.
name: dnaa-test
objective: |
  Multi-line objective prose.
readouts:
  - name: DnaA-ATP fraction
    status: primary
    identifier: listeners.dnaa_atp_fraction
    units: fraction
behavior_tests:
  - name: dnaa_atp_frac
    measure:
      path: listeners.dnaa_atp_fraction
    pass_if:
      low: 0.2
      high: 0.5
    cites: [smith2020]
runs:
  - name: baseline-001
    canonical: true
    status: complete
    computed_outcomes:
      dnaa_atp_frac:
        measured_value: 0.35
findings:
  - id: dnaa-lands-in-band
    evidence:
      from_test: dnaa_atp_frac
# trailing comment
"""


def _study_ws(tmp_path: Path, slug: str = "dnaa-test") -> tuple[Path, Path]:
    ws = tmp_path / "ws"
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: findings-populate-test\n")
    (ws / ".pbg").mkdir()
    sy = sd / "study.yaml"
    sy.write_text(STUDY_YAML_TEXT)
    return ws, sy


def test_missing_study_400(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = views.study_findings_populate_observations(ws, {})
    assert status == 400
    assert "study" in body["error"]


def test_unknown_study_404(tmp_path):
    ws, _ = _study_ws(tmp_path)
    body, status = views.study_findings_populate_observations(
        ws, {"study": "does-not-exist"}
    )
    assert status == 404
    assert "does-not-exist" in body["error"]


def test_populate_fills_code_owned_slots(tmp_path):
    ws, sy = _study_ws(tmp_path)
    original = sy.read_text()

    body, status = views.study_findings_populate_observations(
        ws, {"study": "dnaa-test"}
    )

    assert status == 200
    assert body["study"] == "dnaa-test"
    assert body["filled"] == 1
    assert body["skipped"] == 0

    text = sy.read_text()
    assert text != original
    # comments + hand-authored content survive the ruamel round-trip.
    assert "MUST survive population" in text
    assert "trailing comment" in text
    # code-owned slots are now present.
    assert "observed: 0.35" in text
    assert "divergence_factor: 0.0" in text
    assert "baseline-001" in text  # provenance.run_ids


def test_populate_is_idempotent(tmp_path):
    ws, sy = _study_ws(tmp_path)

    body1, status1 = views.study_findings_populate_observations(
        ws, {"study": "dnaa-test"}
    )
    assert status1 == 200 and body1["filled"] == 1

    after_first = sy.read_text()
    body2, status2 = views.study_findings_populate_observations(
        ws, {"study": "dnaa-test"}
    )
    assert status2 == 200
    assert body2["filled"] == 0  # nothing left to fill
    assert sy.read_text() == after_first  # second call does not rewrite


def test_equivalence_with_direct_populate_call(tmp_path):
    """The endpoint's result must match calling
    ``discovery_engine.finding_observations.populate_finding_observations``
    directly — same ``filled``/``skipped`` counts."""
    pbg_finding_observations = pytest.importorskip(
        "discovery_engine.finding_observations"
    )

    ws, sy = _study_ws(tmp_path)
    endpoint_body, status = views.study_findings_populate_observations(
        ws, {"study": "dnaa-test"}
    )
    assert status == 200

    # Fresh copy for the direct call (endpoint above already wrote to `ws`).
    ws2, sy2 = _study_ws(tmp_path / "cmp2", slug="dnaa-test")
    direct = pbg_finding_observations.populate_finding_observations(sy2.parent)

    assert endpoint_body["filled"] == direct["filled"]
    assert endpoint_body["skipped"] == direct["skipped"]
