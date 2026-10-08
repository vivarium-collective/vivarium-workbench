"""Batch 8 parity: lib.observables_views / lib.report_views == the legacy
``server`` workers (invoked via the ``Handler._*_test`` seams).

The stdlib server's ``_observables_for_ref`` / ``_study_observable_check`` /
``_linkage_index`` are now thin shims delegating to these lib builders, so the
parity is structural — but we lock it with a real-build test (the cheap
``ws_increase_demo`` spec composite) plus synthetic workspaces for the
build-free + SP4b linkage paths (including the ``observable_registry`` /
``composite`` paths that source observables from lib).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import observables_views as ov
from vivarium_workbench.lib import report_views as rv

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
_REF = "pbg_ws_increase_demo.composites.increase-demo"
_REAL_LEAF = "stores.level"


@pytest.fixture
def demo_ws(tmp_path):
    """A throwaway copy of the increase-demo workspace (real spec composite)."""
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURE, ws)
    return ws


def _write_study(ws: Path, slug: str, spec: dict) -> None:
    sdir = ws / "studies" / slug
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "study.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")


# ---------------------------------------------------------------------------
# observables (real build + error paths)
# ---------------------------------------------------------------------------

def test_observables_real_build(demo_ws):
    ov.clear_cache()
    lib_body, lib_status = ov.build_observables(demo_ws, _REF)
    assert lib_status == 200
    assert _REAL_LEAF in lib_body["leaves"]


def test_observables_no_ref_400(demo_ws):
    lib_body, lib_status = ov.build_observables(demo_ws, "")
    assert lib_status == 400
    assert lib_body == {"error": "ref required"}


def test_observables_unknown_ref_404(demo_ws):
    lib_body, lib_status = ov.build_observables(demo_ws, "nope.not.a.composite")
    assert lib_status == 404


# ---------------------------------------------------------------------------
# study-observable-check (real build + error paths)
# ---------------------------------------------------------------------------

def test_study_observable_check_real_build(demo_ws):
    _write_study(demo_ws, "the-study", {
        "name": "the-study",
        "baseline": [{"name": "base", "composite": _REF}],
        "readouts": [
            {"name": "real-one", "store_path": _REAL_LEAF},
            {"name": "phantom-one", "store_path": "stores.nonexistent"},
        ],
    })
    lib_body, lib_status = ov.build_study_observable_check(demo_ws, "the-study")
    assert lib_status == 200
    assert lib_body["composite"] == _REF
    assert any(r["name"] == "phantom-one" and r["status"] == "not_in_structure"
               for r in lib_body["readouts"])


_SECOND_REF = "pbg_ws_increase_demo.composites.other-demo"
_SECOND_LEAF = "stores.other"


def _write_second_composite(ws: Path) -> None:
    """Write a sibling spec composite whose only leaf is ``stores.other`` (a
    DIFFERENT structure from increase-demo's ``stores.level``)."""
    (ws / "pbg_ws_increase_demo" / "composites" / "other-demo.composite.yaml").write_text(
        yaml.safe_dump({
            "name": "other-demo",
            "requires": {"processes": ["IncreaseProcess", "RAMEmitter"]},
            "state": {
                "increase": {
                    "_type": "process",
                    "address": "local:IncreaseProcess",
                    "config": {"rate": 2.0},
                    "inputs": {"level": ["stores", "other"]},
                    "outputs": {"level": ["stores", "other"]},
                    "interval": 1.0,
                },
                "stores": {"other": 1.0},
                "emitter": {
                    "_type": "step",
                    "address": "local:RAMEmitter",
                    "config": {"emit": {"other": "float"}},
                    "inputs": {"other": ["stores", "other"]},
                },
            },
        }),
        encoding="utf-8",
    )


def test_study_observable_check_validates_against_all_baselines(demo_ws):
    """A study with more than one baseline composite must validate each readout
    against ALL baseline composites, not only baseline[0] (#1306). A readout
    that is a real leaf of the SECOND baseline (but not the first) must not be
    reported ``not_in_structure``."""
    _write_second_composite(demo_ws)
    _write_study(demo_ws, "multi-baseline", {
        "name": "multi-baseline",
        "baseline": [
            {"name": "base-a", "composite": _REF},          # exposes stores.level
            {"name": "base-b", "composite": _SECOND_REF},   # exposes stores.other
        ],
        "readouts": [
            {"name": "from-first", "store_path": _REAL_LEAF},     # ok vs base-a
            {"name": "from-second", "store_path": _SECOND_LEAF},  # ok vs base-b only
            {"name": "phantom", "store_path": "stores.nowhere"},  # in neither
        ],
    })
    lib_body, lib_status = ov.build_study_observable_check(demo_ws, "multi-baseline")
    assert lib_status == 200, lib_body
    by_name = {r["name"]: r for r in lib_body["readouts"]}
    # The readout belonging to the non-first baseline must NOT be a false negative.
    assert by_name["from-second"]["status"] == "ok", lib_body
    assert by_name["from-first"]["status"] == "ok", lib_body
    # A readout exposed by no baseline is still flagged (never-fabricate holds).
    assert by_name["phantom"]["status"] == "not_in_structure", lib_body
    # Every declared baseline composite was considered.
    assert set(lib_body.get("composites") or []) == {_REF, _SECOND_REF}, lib_body


def test_study_observable_check_invalid_slug_400(demo_ws):
    lib_body, lib_status = ov.build_study_observable_check(demo_ws, "UPPER-CASE")
    assert lib_status == 400
    assert lib_body == {"error": "invalid slug"}


def test_study_observable_check_not_found_404(demo_ws):
    lib_body, lib_status = ov.build_study_observable_check(demo_ws, "no-such-study")
    assert lib_status == 404


def test_study_observable_check_uncomputable_422(demo_ws):
    _write_study(demo_ws, "broken-study", {
        "name": "broken-study",
        "baseline": [{"name": "base",
                      "composite": "pbg_ws_increase_demo.composites.does-not-exist"}],
        "readouts": [{"name": "real-one", "store_path": _REAL_LEAF}],
    })
    lib_body, lib_status = ov.build_study_observable_check(demo_ws, "broken-study")
    assert lib_status == 422


def test_study_observable_check_honors_nested_workspace_layout(tmp_path):
    """A workspace.yaml that nests studies under workspace/studies (v2ecoli
    layout) must still resolve the study — not 404 'study not found' (#913)."""
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump({
        "name": "ws",
        "layout": {"studies": "workspace/studies",
                   "investigations": "workspace/investigations"},
    }))
    sd = tmp_path / "workspace" / "studies" / "demo"
    sd.mkdir(parents=True)
    # No baseline -> the worker returns 422 (found, but no composite), proving
    # the study was RESOLVED via the layout rather than 404'd.
    (sd / "study.yaml").write_text(yaml.safe_dump({"name": "demo", "readouts": []}))

    body, status = ov.build_study_observable_check(tmp_path, "demo")
    assert status != 404, body
    assert status == 422, body  # found via layout, but no baseline composite
    assert body.get("error") != "study not found: demo"


def test_study_observable_check_extracts_v4_conditions_baseline(tmp_path):
    """A schema_version 4 study carries its baseline composite under
    conditions.baseline.composite — the worker must project it (not 422 with
    'study has no baseline composite') (#913)."""
    sd = tmp_path / "studies" / "v4demo"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": "v4demo",
        "conditions": {"baseline": {"composite": "some.composite.ref"}},
        "readouts": [],
    }))
    body, status = ov.build_study_observable_check(tmp_path, "v4demo")
    # Baseline extraction must succeed (not 400 parse / not "no baseline"); the
    # ref then fails to build in a bare tmp workspace -> 422 with a note.
    assert body.get("error") != "study has no baseline composite", body
    assert status == 422, body
    assert body.get("composite") == "some.composite.ref", body


# ---------------------------------------------------------------------------
# linkage-index — build-free paths
# ---------------------------------------------------------------------------

@pytest.fixture
def linkage_ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    inv_dir = ws / "investigations" / "the-inv"
    inv_dir.mkdir(parents=True)
    inv_dir.joinpath("investigation.yaml").write_text(yaml.safe_dump({
        "name": "the-inv",
        "studies": ["s1"],
        "acceptance_criteria": [
            {"study": "s1", "behavior": "b1"},
            {"behavior": "b2", "status": "failed"},
        ],
    }))
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    sd.joinpath("study.yaml").write_text(yaml.safe_dump({
        "name": "s1", "investigation": "the-inv",
        "cites": ["bib-X"],
        "tests": [{"name": "b1"}],
        "runs": [{"name": "r1", "status": "completed",
                  "outcomes": {"b1": {"result": "PASS"}}}],
    }))
    return ws


def _linkage_build(ws, **kw):
    lib_body, lib_status = rv.build_linkage_index(
        ws,
        # build_linkage_index (Batch 7) calls fn(ws_root, ref) — pass a 2-arg callable.
        observables_for_ref_fn=ov.observables_for_ref_payload,
        **kw,
    )
    assert lib_status == 200
    return lib_body


def test_linkage_investigation(linkage_ws):
    body = _linkage_build(linkage_ws, investigation="the-inv")
    assert "ac_matrix" in body or "nodes" in body


def test_linkage_source(linkage_ws):
    body = _linkage_build(linkage_ws, source="bib-X")
    assert "s1" in (body.get("studies") or [])


def test_linkage_no_filter(linkage_ws):
    _linkage_build(linkage_ws)


def test_linkage_tolerant_missing_ws(tmp_path):
    _linkage_build(tmp_path / "does-not-exist", investigation="nope")


# ---------------------------------------------------------------------------
# linkage-index — SP4b observable_registry / composite (real build,
# observables sourced from lib).
# ---------------------------------------------------------------------------

def test_linkage_observable_registry_real_build(demo_ws):
    _write_study(demo_ws, "s1", {
        "name": "s1",
        "baseline": {"name": "bl", "composite": _REF},
        "tests": [{"name": "b1", "measure": {"field": _REAL_LEAF}}],
    })
    body = _linkage_build(demo_ws, observable_registry=_REAL_LEAF)
    assert set(body) == {"studies", "composites"}


def test_linkage_composite_real_build(demo_ws):
    _write_study(demo_ws, "s1", {
        "name": "s1",
        "baseline": {"name": "bl", "composite": _REF},
        "tests": [{"name": "b1", "measure": {"field": _REAL_LEAF}}],
    })
    body = _linkage_build(demo_ws, composite=_REF)
    assert set(body) == {"emits", "used_by_studies"}
