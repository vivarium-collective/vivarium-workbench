"""Route-level coverage for the composite-source read/write endpoints."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
_SPEC_PATH = "pbg_ws_increase_demo/composites/increase-demo.composite.yaml"
_SPEC_ID = "pbg_ws_increase_demo.composites.increase-demo"
_GEN_ID = "pbg_ws_increase_demo.composites.hint_test"
_GEN_MODULE = "pbg_ws_increase_demo.composites"


@pytest.fixture()
def ws_copy(tmp_path):
    dst = tmp_path / "ws_increase_demo"
    shutil.copytree(_FIXTURE, dst)
    return dst


def test_get_spec_composite_source(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    r = client.get("/api/composites/source?id=" + _SPEC_ID + "&source_path=" + _SPEC_PATH)
    body = r.json()
    assert body["ok"] is True
    assert body["lang"] == "yaml"
    assert "name: increase-demo" in body["source"]


def test_get_generator_composite_source(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    r = client.get("/api/composites/source?id=" + _GEN_ID + "&module=" + _GEN_MODULE)
    body = r.json()
    assert body["ok"] is True
    assert body["lang"] == "python"
    assert "def hint_test" in body["source"]


def test_post_spec_composite_source_saves(dashboard_client, ws_copy):
    client = dashboard_client(ws_copy)
    yaml_file = ws_copy / _SPEC_PATH
    edited = yaml_file.read_text().replace("default: 2.0", "default: 4.0")
    r = client.post("/api/composites/source", json={
        "id": _SPEC_ID, "source_path": _SPEC_PATH, "lang": "yaml", "source": edited,
    })
    assert r.json()["ok"] is True
    assert yaml_file.read_text() == edited


# --- read access to specs outside the workspace ---------------------------------------------------------


def test_an_installed_package_spec_is_readable_but_an_arbitrary_absolute_path_is_not(tmp_path, monkeypatch):
    """A composite shipped by an installed ``pbg-*`` package is named by its absolute path. The source viewer
    must keep reading exactly those files — and nothing else outside the workspace.

    Real discovery: a ``.dist-info`` + package directory on ``sys.path``, found by
    ``importlib.metadata``/``find_spec`` like any installed distribution. Proves the containment gate follows
    the registry's listing; it does not prove what a given wheel ships.
    """
    import sys

    from vivarium_workbench import env_worker

    site = tmp_path / "site"
    (site / "pbg_fakepkg-0.0.1.dist-info").mkdir(parents=True)
    (site / "pbg_fakepkg-0.0.1.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: pbg-fakepkg\nVersion: 0.0.1\n")
    comp = site / "pbg_fakepkg" / "composites"
    comp.mkdir(parents=True)
    (site / "pbg_fakepkg" / "__init__.py").write_text("")
    spec = comp / "fake.composite.yaml"
    spec.write_text("name: fake\nstate: {}\n")
    secret = tmp_path / "secret.yaml"
    secret.write_text("name: not-a-composite\n")

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("schema_version: 2\nname: srcws\n")

    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(env_worker, "_workspace", str(ws))
    for mod in [m for m in sys.modules if m == "pbg_fakepkg" or m.startswith("pbg_fakepkg.")]:
        monkeypatch.delitem(sys.modules, mod)

    path, lang, err = env_worker._composite_source_path({"source_path": str(spec)})
    assert err is None and path == str(spec.resolve()) and lang == "yaml"

    for outside in (str(secret), str(tmp_path / "site" / "pbg_fakepkg" / "__init__.py"), "../secret.yaml"):
        path, _lang, err = env_worker._composite_source_path({"source_path": outside})
        assert path is None and err and err.startswith("invalid source_path"), (outside, err)
