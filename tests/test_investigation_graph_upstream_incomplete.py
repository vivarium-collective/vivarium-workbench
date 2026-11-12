"""DAG-aware completion: a study's "Completed" execution badge reflects only its
own run, ignoring the dependency graph. A downstream study that declares it
consumes another member's output (interface ``inputs[].from``) must be flagged
``upstream_incomplete`` when that upstream member is not itself completed — so the
UI can mark it provisional instead of cleanly done.

Mirrors the real v2ecoli colonies investigation, where cs-06 (completed) consumes
cs-03 (planned) + cs-05 (running) and was shown as a clean "Completed".
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib import _root
from vivarium_workbench.lib.investigation_graph_views import build_investigation_graph


def _study(ws: Path, slug: str, *, completed: bool, inputs: list[str]) -> None:
    d = ws / "studies" / slug
    d.mkdir(parents=True)
    spec = {
        "schema_version": 4,
        "name": slug,
        # gate_status: passed -> execution_status "completed" (as cs-06 reads);
        # otherwise the study reads "planned".
        "gate_status": "passed" if completed else "",
        "simulation_status": "run" if completed else "not_run",
        "inputs": [{"artifact": src, "from": src} for src in inputs],
    }
    (d / "study.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")


def _investigation(ws: Path, slug: str, members: list[str]) -> None:
    d = ws / "investigations" / slug
    d.mkdir(parents=True)
    (d / "investigation.yaml").write_text(
        yaml.safe_dump({"name": slug, "members": members}), encoding="utf-8")


def _graph(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    # a (ran) -> b (not_run) -> c (ran); c also consumes a directly.
    _study(ws, "a", completed=True, inputs=[])
    _study(ws, "b", completed=False, inputs=["a"])
    _study(ws, "c", completed=True, inputs=["b", "a"])
    _investigation(ws, "inv", ["a", "b", "c"])
    _root.set_workspace_root(ws)
    graph, code = build_investigation_graph(ws, "inv")
    assert code == 200
    return {n["slug"]: n for n in graph["studies"]}


def test_completed_study_with_unfinished_upstream_is_flagged(tmp_path):
    nodes = _graph(tmp_path)

    # c ran, but its input b has not -> flagged (a is completed, so not listed).
    assert nodes["c"]["execution_status"] == "completed"
    assert nodes["c"].get("upstream_incomplete") == ["b"]


def test_completed_study_with_all_upstream_done_is_not_flagged(tmp_path):
    nodes = _graph(tmp_path)

    # a depends on nothing -> never flagged.
    assert nodes["a"]["execution_status"] == "completed"
    assert "upstream_incomplete" not in nodes["a"]


def test_flag_lists_every_unfinished_upstream(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    _study(ws, "up1", completed=False, inputs=[])
    _study(ws, "up2", completed=False, inputs=[])
    _study(ws, "down", completed=True, inputs=["up1", "up2"])
    _investigation(ws, "inv", ["up1", "up2", "down"])
    _root.set_workspace_root(ws)
    graph, _ = build_investigation_graph(ws, "inv")
    nodes = {n["slug"]: n for n in graph["studies"]}
    assert set(nodes["down"].get("upstream_incomplete") or []) == {"up1", "up2"}
