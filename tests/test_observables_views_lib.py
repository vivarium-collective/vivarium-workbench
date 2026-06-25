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

import json
import shutil
from pathlib import Path

import pytest
import yaml

from vivarium_dashboard.lib import observables_views as ov
from vivarium_dashboard.lib import report_views as rv

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


def _legacy(seam_result):
    """(json_bytes, status) → (dict, status)."""
    body, status = seam_result
    return json.loads(body), status


# ---------------------------------------------------------------------------
# observables parity (real build + error paths)
# ---------------------------------------------------------------------------

def test_observables_parity_real_build(demo_ws):
    from vivarium_dashboard import server
    # Server shim + lib builder share ov._OBS_CACHE; clear before each so neither
    # observes the other's cache entry (which would add a "cached": True key).
    ov.clear_cache()
    legacy_body, legacy_status = _legacy(
        server.Handler._observables_for_ref_test(demo_ws, _REF))
    ov.clear_cache()
    lib_body, lib_status = ov.build_observables(demo_ws, _REF)
    assert legacy_status == lib_status == 200
    assert legacy_body == lib_body
    assert _REAL_LEAF in lib_body["leaves"]


def test_observables_parity_no_ref_400(demo_ws):
    from vivarium_dashboard import server
    legacy_body, legacy_status = _legacy(
        server.Handler._observables_for_ref_test(demo_ws, ""))
    lib_body, lib_status = ov.build_observables(demo_ws, "")
    assert legacy_status == lib_status == 400
    assert legacy_body == lib_body == {"error": "ref required"}


def test_observables_parity_unknown_ref_404(demo_ws):
    from vivarium_dashboard import server
    legacy_body, legacy_status = _legacy(
        server.Handler._observables_for_ref_test(demo_ws, "nope.not.a.composite"))
    lib_body, lib_status = ov.build_observables(demo_ws, "nope.not.a.composite")
    assert legacy_status == lib_status == 404
    assert legacy_body == lib_body


# ---------------------------------------------------------------------------
# study-observable-check parity (real build + error paths)
# ---------------------------------------------------------------------------

def test_study_observable_check_parity_real_build(demo_ws):
    from vivarium_dashboard import server
    _write_study(demo_ws, "the-study", {
        "name": "the-study",
        "baseline": [{"name": "base", "composite": _REF}],
        "readouts": [
            {"name": "real-one", "store_path": _REAL_LEAF},
            {"name": "phantom-one", "store_path": "stores.nonexistent"},
        ],
    })
    legacy_body, legacy_status = _legacy(
        server.Handler._study_observable_check_test(demo_ws, "the-study"))
    lib_body, lib_status = ov.build_study_observable_check(demo_ws, "the-study")
    assert legacy_status == lib_status == 200
    assert legacy_body == lib_body
    assert lib_body["composite"] == _REF
    assert any(r["name"] == "phantom-one" and r["status"] == "not_in_structure"
               for r in lib_body["readouts"])


def test_study_observable_check_parity_invalid_slug_400(demo_ws):
    from vivarium_dashboard import server
    legacy_body, legacy_status = _legacy(
        server.Handler._study_observable_check_test(demo_ws, "UPPER-CASE"))
    lib_body, lib_status = ov.build_study_observable_check(demo_ws, "UPPER-CASE")
    assert legacy_status == lib_status == 400
    assert legacy_body == lib_body == {"error": "invalid slug"}


def test_study_observable_check_parity_not_found_404(demo_ws):
    from vivarium_dashboard import server
    legacy_body, legacy_status = _legacy(
        server.Handler._study_observable_check_test(demo_ws, "no-such-study"))
    lib_body, lib_status = ov.build_study_observable_check(demo_ws, "no-such-study")
    assert legacy_status == lib_status == 404
    assert legacy_body == lib_body


def test_study_observable_check_parity_uncomputable_422(demo_ws):
    from vivarium_dashboard import server
    _write_study(demo_ws, "broken-study", {
        "name": "broken-study",
        "baseline": [{"name": "base",
                      "composite": "pbg_ws_increase_demo.composites.does-not-exist"}],
        "readouts": [{"name": "real-one", "store_path": _REAL_LEAF}],
    })
    legacy_body, legacy_status = _legacy(
        server.Handler._study_observable_check_test(demo_ws, "broken-study"))
    lib_body, lib_status = ov.build_study_observable_check(demo_ws, "broken-study")
    assert legacy_status == lib_status == 422
    assert legacy_body == lib_body


# ---------------------------------------------------------------------------
# linkage-index parity — build-free paths
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


def _linkage_parity(ws, **kw):
    from vivarium_dashboard import server
    legacy_body, legacy_status = _legacy(
        server.Handler._linkage_index_test(ws, **kw))
    lib_body, lib_status = rv.build_linkage_index(
        ws,
        # build_linkage_index (Batch 7) calls fn(ws_root, ref) — pass a 2-arg callable.
        observables_for_ref_fn=ov.observables_for_ref_payload,
        **kw,
    )
    assert legacy_status == 200
    assert lib_status == 200
    assert legacy_body == lib_body
    return lib_body


def test_linkage_parity_investigation(linkage_ws):
    body = _linkage_parity(linkage_ws, investigation="the-inv")
    assert "ac_matrix" in body or "nodes" in body


def test_linkage_parity_source(linkage_ws):
    body = _linkage_parity(linkage_ws, source="bib-X")
    assert "s1" in (body.get("studies") or [])


def test_linkage_parity_no_filter(linkage_ws):
    _linkage_parity(linkage_ws)


def test_linkage_parity_tolerant_missing_ws(tmp_path):
    _linkage_parity(tmp_path / "does-not-exist", investigation="nope")


# ---------------------------------------------------------------------------
# linkage-index parity — SP4b observable_registry / composite (real build,
# observables sourced from lib).  This locks the route to the server worker on
# the build paths.
# ---------------------------------------------------------------------------

def test_linkage_parity_observable_registry_real_build(demo_ws):
    _write_study(demo_ws, "s1", {
        "name": "s1",
        "baseline": {"name": "bl", "composite": _REF},
        "tests": [{"name": "b1", "measure": {"field": _REAL_LEAF}}],
    })
    body = _linkage_parity(demo_ws, observable_registry=_REAL_LEAF)
    assert set(body) == {"studies", "composites"}


def test_linkage_parity_composite_real_build(demo_ws):
    _write_study(demo_ws, "s1", {
        "name": "s1",
        "baseline": {"name": "bl", "composite": _REF},
        "tests": [{"name": "b1", "measure": {"field": _REAL_LEAF}}],
    })
    body = _linkage_parity(demo_ws, composite=_REF)
    assert set(body) == {"emits", "used_by_studies"}
