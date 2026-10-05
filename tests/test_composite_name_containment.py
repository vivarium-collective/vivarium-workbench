"""A composite name or a loom layout mode is a file-name part, never a path.

The investigation composite-document and state-tree builders join ``composite`` onto the study's
``composites/`` directory; the composite-layout routes join ``mode`` onto ``.pbg/loom-layouts/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vivarium_workbench.api import app as appmod
import yaml

from vivarium_workbench.lib import investigation_views as inv_views

NOT_PLAIN = ["../../outside", "a/b", "..", "/abs/path", "..\\outside"]


def _make_workspace(tmp_path: Path) -> Path:
    """An investigation ``my-inv`` whose study has one composite, ``my-comp``, plus a ``.yaml`` outside it."""
    (tmp_path / "investigations" / "my-inv").mkdir(parents=True)
    (tmp_path / "investigations" / "my-inv" / "investigation.yaml").write_text(
        yaml.dump({"name": "my-inv", "studies": ["my-inv"]}), encoding="utf-8"
    )
    study = tmp_path / "studies" / "my-inv"
    (study / "composites").mkdir(parents=True)
    (study / "study.yaml").write_text(yaml.dump({"name": "my-inv"}), encoding="utf-8")
    (study / "composites" / "my-comp.yaml").write_text(
        yaml.dump({"process": "P"}), encoding="utf-8"
    )
    (tmp_path / "outside.yaml").write_text(
        yaml.dump({"secret": "outside"}), encoding="utf-8"
    )
    return tmp_path


def test_a_plain_composite_still_loads(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    assert inv_views.build_investigation_composite_doc(ws, "my-inv", "my-comp")


@pytest.mark.parametrize("composite", NOT_PLAIN)
@pytest.mark.parametrize(
    "build",
    [
        inv_views.build_investigation_composite_doc,
        inv_views.build_investigation_state_tree,
    ],
)
def test_a_composite_that_is_not_a_plain_name_is_refused(
    tmp_path: Path, build, composite: str
) -> None:
    ws = _make_workspace(tmp_path)
    with pytest.raises(inv_views.InvViewError) as exc:
        build(ws, "my-inv", composite)
    assert exc.value.status == 400


@pytest.fixture
def client(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    app = appmod.create_app() if hasattr(appmod, "create_app") else appmod.app
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    try:
        yield TestClient(app), ws
    finally:
        app.dependency_overrides.pop(appmod.get_workspace, None)


@pytest.mark.parametrize("mode", ["../../escape", "a/b", "..\\x"])
def test_a_layout_mode_stays_a_file_name_inside_loom_layouts(client, mode: str) -> None:
    c, ws = client
    resp = c.post(
        "/api/composite-layout",
        json={"id": "comp", "mode": mode, "positions": {"n": [1, 2]}},
    )
    assert resp.status_code == 200 and resp.json()["ok"]
    layouts = ws / ".pbg" / "loom-layouts"
    written = [p for p in ws.rglob("*.json")]
    assert written and all(p.parent == layouts for p in written)
    got = c.get("/api/composite-layout", params={"id": "comp", "mode": mode}).json()
    assert got == {"positions": {"n": [1, 2]}}


def test_an_ordinary_layout_mode_is_unchanged(client) -> None:
    c, ws = client
    c.post(
        "/api/composite-layout",
        json={"id": "comp", "mode": "hierarchy", "positions": {"a": 1}},
    )
    assert (ws / ".pbg" / "loom-layouts" / "comp__hierarchy.json").is_file()
