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
