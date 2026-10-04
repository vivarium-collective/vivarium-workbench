"""Regression: per-study lookups resolve nested + ``layout:``-relocated studies.

Many workers resolved a study as ``WorkspacePaths.studies / slug`` (layout-aware
but blind to ``investigations/<inv>/studies/<slug>/``) or as a literal
``ws_root / "studies" / slug``. ``GET /api/study/{slug}`` uses the shared
resolver (``WorkspacePaths.study_dir``) and found such a study, while e.g.
``GET /api/study-observable-check`` answered ``study not found``. Every test
here writes REAL files into a workspace whose ``workspace.yaml`` relocates the
investigations root (the shape of the reported workspace) and nests the study
under its investigation, then drives the real worker / route.
"""
from __future__ import annotations

import shutil
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from vivarium_workbench.api.app import create_app, get_workspace

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
_REF = "pbg_ws_increase_demo.composites.increase-demo"
_INV = "simulator-benchmarking"
_SLUG = "bench-nested"


@pytest.fixture
def nested_ws(tmp_path) -> Path:
    """The increase-demo workspace (real buildable composite) with
    ``layout: {investigations: workspace/investigations}`` and one study nested
    at ``workspace/investigations/<inv>/studies/<slug>/study.yaml``."""
    ws = tmp_path / "ws"
    shutil.copytree(_FIXTURE, ws)
    wy = ws / "workspace.yaml"
    wy.write_text(
        wy.read_text(encoding="utf-8")
        + "layout:\n  investigations: workspace/investigations\n",
        encoding="utf-8",
    )
    inv = ws / "workspace" / "investigations" / _INV
    sdir = inv / "studies" / _SLUG
    sdir.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(f"name: {_INV}\n", encoding="utf-8")
    (sdir / "study.yaml").write_text(yaml.safe_dump({
        "name": _SLUG,
        "investigation": _INV,
        "baseline": [{"name": "base", "composite": _REF}],
        "readouts": [
            {"name": "real-one", "store_path": "stores.level"},
            {"name": "phantom-one", "store_path": "stores.nonexistent"},
        ],
    }), encoding="utf-8")
    return ws


def _study_dir(ws: Path) -> Path:
    return ws / "workspace" / "investigations" / _INV / "studies" / _SLUG


@pytest.fixture
def client(nested_ws) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: nested_ws
    return TestClient(app)


def test_get_study_finds_nested_study(client):
    """The reference behaviour every other lookup must agree with."""
    assert client.get(f"/api/study/{_SLUG}").status_code == 200


def test_observable_check_route_finds_nested_study(client):
    r = client.get("/api/study-observable-check", params={"study": _SLUG})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["composite"] == _REF
    statuses = {x["name"]: x["status"] for x in body["readouts"]}
    assert statuses == {"real-one": "ok", "phantom-one": "not_in_structure"}


def test_readouts_route_finds_nested_study(client):
    r = client.get("/api/study-readouts", params={"study": _SLUG})
    assert r.status_code == 200, r.text
    assert r.json()["composite"] == _REF


def test_study_export_zips_nested_study(nested_ws):
    from vivarium_workbench.lib.download_views import study_export_zip

    names = zipfile.ZipFile(BytesIO(study_export_zip(nested_ws, _SLUG))).namelist()
    assert f"{_SLUG}/study.yaml" in names


def test_node_store_resolves_nested_study(nested_ws):
    from vivarium_workbench.lib.node_store import load_study_nodes, study_dir

    findings = _study_dir(nested_ws) / "findings"
    findings.mkdir()
    (findings / "f1.yaml").write_text(
        yaml.safe_dump({"id": "finding/f1", "type": "finding"}), encoding="utf-8")
    assert study_dir(nested_ws, _SLUG) == _study_dir(nested_ws).resolve()
    assert "finding/f1" in load_study_nodes(nested_ws, _SLUG)


def test_study_variants_resolves_nested_study_under_layout(nested_ws):
    from vivarium_workbench.lib.study_variants import _study_yaml

    assert _study_yaml(nested_ws, _SLUG) == (_study_dir(nested_ws) / "study.yaml").resolve()


def test_study_rename_renames_nested_study_in_place(nested_ws):
    from vivarium_workbench.lib.lifecycle_mutations import study_rename

    body, code = study_rename(nested_ws, {"study": _SLUG, "new_name": "bench-renamed"})
    assert (body, code) == ({"ok": True, "name": "bench-renamed"}, 200)
    renamed = _study_dir(nested_ws).parent / "bench-renamed" / "study.yaml"
    assert yaml.safe_load(renamed.read_text(encoding="utf-8"))["name"] == "bench-renamed"
    assert not _study_dir(nested_ws).exists()


# --- lib-level read lookups switched from ``wp.studies / slug`` to the resolver ---


def _expected(ws: Path) -> Path:
    return _study_dir(ws).resolve()


def test_study_narrative_resolves_nested_study(nested_ws):
    from vivarium_workbench.lib.study_narrative import _study_yaml

    assert _study_yaml(nested_ws, _SLUG) == _expected(nested_ws) / "study.yaml"


def test_study_findings_resolves_nested_study(nested_ws):
    from vivarium_workbench.lib.study_findings import study_dir_from_slug

    assert study_dir_from_slug(nested_ws, _SLUG) == _expected(nested_ws)


def test_study_tests_paths_resolve_nested_study(nested_ws):
    from vivarium_workbench.lib.study_tests import _study_paths

    study_dir, tests_dir, spec_path = _study_paths(nested_ws, _SLUG)
    assert (study_dir, spec_path) == (_expected(nested_ws), _expected(nested_ws) / "study.yaml")


def test_single_study_report_loads_nested_study(nested_ws):
    from vivarium_workbench.lib.single_study_report import _load_study_spec

    assert _load_study_spec(nested_ws, _SLUG)["name"] == _SLUG


def test_study_prereqs_reads_nested_study(nested_ws):
    from vivarium_workbench.lib.run_jobs import study_prereqs
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths

    sf = _study_dir(nested_ws) / "study.yaml"
    spec = yaml.safe_load(sf.read_text(encoding="utf-8"))
    spec["pipeline_gate"] = {"prerequisites": [{"study": "upstream"}]}
    sf.write_text(yaml.safe_dump(spec), encoding="utf-8")
    assert study_prereqs(WorkspacePaths.load(nested_ws), _SLUG) == ["upstream"]


def test_verify_parent_study_found_when_nested(nested_ws):
    from vivarium_workbench.lib.study_verify import _check_parent_studies

    findings = list(_check_parent_studies({"parent_studies": [_SLUG]}, nested_ws))
    assert [f.check for f in findings] == []


def test_refresh_viz_finds_nested_study(nested_ws):
    from vivarium_workbench.lib.study_viz_views import study_refresh_viz

    out = study_refresh_viz(nested_ws, _SLUG)
    assert "not_found" not in out, out
    assert out["study"] == _SLUG


def test_study_tests_run_route_finds_nested_study(client, nested_ws):
    """POST /api/study-tests-run answered ``study not found`` for a nested study
    under a relocated investigations root that GET /api/study/{slug} resolves."""
    r = client.post("/api/study-tests-run", json={"study": _SLUG})
    assert r.status_code == 200, r.text
    assert r.json()["note"] == "no tests directory"
    # The last-results stamp landed in the nested study.yaml, not a flat copy.
    assert not (nested_ws / "studies" / _SLUG).exists()
    spec = yaml.safe_load((_study_dir(nested_ws) / "study.yaml").read_text(encoding="utf-8"))
    assert spec["last_test_run"]["passed"] == 0


def test_study_rename_refuses_slug_taken_elsewhere_in_workspace(nested_ws):
    """A nested study must not be renamed onto a slug a flat study already
    holds: the resolver checks flat first, so the renamed study would vanish."""
    from vivarium_workbench.lib.lifecycle_mutations import study_rename

    taken = nested_ws / "studies" / "taken"
    taken.mkdir(parents=True)
    (taken / "study.yaml").write_text("name: taken\n", encoding="utf-8")
    body, code = study_rename(nested_ws, {"study": _SLUG, "new_name": "taken"})
    assert (body, code) == ({"error": "study 'taken' already exists"}, 409)
    assert _study_dir(nested_ws).is_dir()


def test_study_findings_still_rejects_path_traversal(nested_ws):
    from vivarium_workbench.lib.errors import APIError
    from vivarium_workbench.lib.study_findings import study_dir_from_slug

    with pytest.raises(APIError):
        study_dir_from_slug(nested_ws, "../../etc")


def test_study_prereqs_skips_malformed_member_slug(nested_ws):
    """study_prereqs is documented total: a non-plain slug yields no edges."""
    from vivarium_workbench.lib.run_jobs import study_prereqs
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths

    assert study_prereqs(WorkspacePaths.load(nested_ws), "foo/bar") == []
