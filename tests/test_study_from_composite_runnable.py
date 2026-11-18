"""Regression: a study created by ``POST /api/study-create-from-composite`` can be
run through the workbench's own run routes, on a ``layout:``-relocated workspace
as well as the flat default.

The create route writes ``<layout.studies>/<name>/spec.yaml`` (a legacy v2-shape
spec: it has no ``study.yaml``). ``study_runs._resolve_study_dir`` looked the dir up
through ``WorkspacePaths`` (gated on ``study.yaml``), and on a miss fell back to a
HARDCODED ``ws_root / "studies" / name`` -- which does not exist when the workspace
relocates ``studies:`` (e.g. ``workspace/studies``). The run routes then answered
``404 {"error": "study not found"}`` for a study the same workbench had just
created. ``study_spec.study_dir`` (the layout-aware twin) already resolved it.

Everything on the path is REAL: the workspace fixture, the catalog scan, the create
route, the study-dir resolution and the run route's spec read / migration /
projection / manifest build. Only the execution tail ``_launch_run_and_flush`` (the
detached subprocess + post-run flush) is stubbed, and only in the run-baseline test.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import study_runs

COMPOSITE = "particle_diffusion"


def _make_workspace(root: Path, *, relocated: bool) -> Path:
    """A real workspace with one catalog composite; optionally ``studies:`` relocated."""
    root.mkdir(parents=True, exist_ok=True)
    ws_yaml: dict = {"name": "testws", "package_path": "pbg_testws"}
    if relocated:
        ws_yaml["layout"] = {"studies": "workspace/studies"}
    (root / "workspace.yaml").write_text(yaml.safe_dump(ws_yaml, sort_keys=False), encoding="utf-8")
    pkg = root / "pbg_testws"
    (pkg / "composites").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(
        "from bigraph_schema import allocate_core\n"
        "def build_core(): return allocate_core()\n"
    )
    (pkg / "composites" / f"{COMPOSITE}.composite.yaml").write_text(yaml.safe_dump({
        "name": COMPOSITE,
        "state": {"particles": {"count": {"_type": "integer", "_default": 10}}},
    }))
    return root


def _create_study(client, ws: Path, *, relocated: bool) -> tuple[str, Path]:
    """POST the real create route; return (name, the dir it must have written)."""
    r = client.post("/api/study-create-from-composite", {"composite_name": COMPOSITE})
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    studies_root = ws / "workspace" / "studies" if relocated else ws / "studies"
    study_dir = studies_root / name
    assert (study_dir / "spec.yaml").is_file(), "create route did not write spec.yaml here"
    assert not (study_dir / "study.yaml").exists()  # the legacy shape this regression is about
    return name, study_dir


@pytest.fixture
def captured_launch(monkeypatch):
    """Stub ONLY the execution tail; record what the run route resolved before it."""
    seen: dict = {}

    def fake_launch(ws_root, study_dir, spec_id, params, n_steps, **kw):
        seen.update(study_dir=Path(study_dir), spec_id=spec_id, n_steps=n_steps,
                    db_file=kw.get("db_file"))
        return {"run_id": "r-stub", "status": "running"}, 202

    monkeypatch.setattr(study_runs, "_launch_run_and_flush", fake_launch)
    return seen


@pytest.mark.parametrize("relocated", [True, False], ids=["relocated-layout", "flat-layout"])
def test_created_study_is_runnable_through_run_baseline(tmp_path, inproc_client, captured_launch,
                                                        relocated):
    """Falsifies: "POST /api/study-run-baseline resolves a study that
    POST /api/study-create-from-composite just created". Before the fix the
    relocated case answers 404 {"error": "study not found"} and never reaches the
    launch; the flat case is the unchanged-behaviour guard (it passed before too)."""
    ws = _make_workspace(tmp_path / "ws", relocated=relocated)
    client = inproc_client(ws)
    name, study_dir = _create_study(client, ws, relocated=relocated)

    r = client.post("/api/study-run-baseline", {"study": name, "steps": 100})

    assert r.status_code == 202, r.text
    assert captured_launch["study_dir"] == study_dir  # resolved to where create wrote it
    assert captured_launch["db_file"] == str(study_dir / "runs.db")
    assert captured_launch["n_steps"] == 100


@pytest.mark.parametrize("relocated", [True, False], ids=["relocated-layout", "flat-layout"])
def test_created_study_config_file_resolves_through_study_config_route(tmp_path, inproc_client,
                                                                      relocated):
    """Falsifies: "GET /api/study-config-file finds a file co-located in a study dir
    that POST /api/study-create-from-composite created" -- the other consumer of
    ``_resolve_study_dir``. Before the fix, on the relocated layout the study dir
    resolved to a non-existent ``investigations/<name>`` so the co-located file was
    missed (404). Nothing is stubbed."""
    ws = _make_workspace(tmp_path / "ws", relocated=relocated)
    client = inproc_client(ws)
    name, study_dir = _create_study(client, ws, relocated=relocated)
    (study_dir / "run_config.json").write_text('{"n_seeds": 3}', encoding="utf-8")

    r = client.get(f"/api/study-config-file?study={name}&ref=run_config.json")

    assert r.status_code == 200, r.text
    assert r.json()["content"] == {"n_seeds": 3}


def test_resolve_study_dir_honors_layout_for_legacy_spec_dir(tmp_path):
    """Falsifies: "_resolve_study_dir returns the layout-mapped dir of a legacy
    (spec.yaml-only) study". Direct resolver check, no route, no mocks."""
    ws = _make_workspace(tmp_path / "ws", relocated=True)
    legacy = ws / "workspace" / "studies" / "legacy-study"
    legacy.mkdir(parents=True)
    (legacy / "spec.yaml").write_text(yaml.safe_dump({"name": "legacy-study"}))

    assert study_runs._resolve_study_dir(ws, "legacy-study") == legacy


def test_resolve_study_dir_unchanged_for_study_yaml_and_absent(tmp_path):
    """Falsifies: "the fix changed how a v3 study (study.yaml) or an unknown slug
    resolves". A study.yaml study is found where it lives; an absent slug resolves to
    the (non-existent) ``<investigations root>/<slug>`` path, never a hit."""
    ws = _make_workspace(tmp_path / "ws", relocated=True)
    v3 = ws / "workspace" / "studies" / "v3-study"
    v3.mkdir(parents=True)
    (v3 / "study.yaml").write_text(yaml.safe_dump({"name": "v3-study"}))

    assert study_runs._resolve_study_dir(ws, "v3-study") == v3
    absent = study_runs._resolve_study_dir(ws, "no-such-study")
    assert absent == ws / "investigations" / "no-such-study"
    assert not absent.exists()


def _seed_scratch_run(ws: Path) -> None:
    """One completed scratchpad run (.pbg/composite-runs.db), as ``study-create-from-run`` reads it."""
    import sqlite3

    (ws / ".pbg").mkdir(exist_ok=True)
    conn = sqlite3.connect(str(ws / ".pbg" / "composite-runs.db"))
    conn.executescript("""
        CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, label TEXT,
                                params_json TEXT, started_at REAL NOT NULL, completed_at REAL,
                                n_steps INTEGER, status TEXT NOT NULL);
        CREATE TABLE history (simulation_id TEXT NOT NULL, step INTEGER NOT NULL, global_time REAL,
                              state TEXT NOT NULL, PRIMARY KEY (simulation_id, step));
    """)
    conn.execute("INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 ("rid1", f"pbg_testws.composites.{COMPOSITE}", "test", "{}",
                  1715620800.0, 1715620812.0, 10, "completed"))
    conn.executemany("INSERT INTO history VALUES (?, ?, ?, ?)",
                     [("rid1", i, float(i), '{"x": %d}' % i) for i in range(10)])
    conn.commit()
    conn.close()


@pytest.mark.parametrize("relocated", [True, False], ids=["relocated-layout", "flat-layout"])
def test_study_created_from_a_run_lands_where_the_run_routes_look(tmp_path, inproc_client,
                                                                  relocated):
    """Falsifies: "POST /api/study-create-from-run writes the study to the layout-mapped
    studies root, where ``_resolve_study_dir`` (the run routes) looks". That route used a
    hardcoded ``ws_root/"studies"``; on a relocated layout the study landed outside every
    layout-aware lookup (``WorkspacePaths``, ``study_spec.study_dir``) and was reachable
    only through ``_resolve_study_dir``'s old hardcoded flat fallback -- which the layout
    fix removes, so it must be created in the right place. Flat layout: unchanged guard.
    (The created study's dict-shaped ``baseline`` is a separate matter: the baseline route
    answers 400 for it on either layout, so this test stops at resolution.)"""
    ws = _make_workspace(tmp_path / "ws", relocated=relocated)
    _seed_scratch_run(ws)
    client = inproc_client(ws)

    r = client.post("/api/study-create-from-run", {"name": "from-run", "source_run_id": "rid1"})
    assert r.status_code == 200, r.text

    studies_root = ws / "workspace" / "studies" if relocated else ws / "studies"
    study_dir = studies_root / "from-run"
    assert (study_dir / "study.yaml").is_file()
    if relocated:
        assert not (ws / "studies").exists()  # nothing left at the literal flat path
    assert study_runs._resolve_study_dir(ws, "from-run") == study_dir
