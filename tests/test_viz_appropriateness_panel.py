"""Viz-appropriateness on the cycle panel: labels, census suffix, untied rollout toggle."""
import subprocess

import pytest
import yaml

from vivarium_workbench.lib import cycle_trace as ct

pytestmark = pytest.mark.skipif(
    not ct.engine_available() or not hasattr(__import__("discovery_engine.discover", fromlist=["x"]),
                                             "build_cycle_state"),
    reason="engine unavailable")

KINDS = ("viz_misplaced", "viz_duplicated", "viz_untied")


def _ws(tmp_path):
    ws = tmp_path / "ws"
    sd = ws / "studies" / "a"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (ws / "investigations" / "inv").mkdir(parents=True)
    (ws / "investigations" / "inv" / "investigation.yaml").write_text(
        yaml.safe_dump({"slug": "inv", "name": "inv", "studies": ["a"]}))
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "a", "composite": "x.y", "visualizations": [
            {"name": "x", "address": "local:Cross", "scope": "investigation", "covers": ["obs"]},
            {"name": "u", "address": "local:Per", "scope": "study"}]}))
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t",
                                                "commit", "-qm", "init"]):
        subprocess.run(["git", "-C", str(ws)] + cmd, check=True, capture_output=True)
    return ws


def _tr(c):
    return c.get("/api/discovery-cycle/inv").json()


def _items(tr):
    return [f for f in tr["summary"]["frontier"] if f["kind"] in KINDS]


def test_labels_and_tabs():
    assert ct._KIND_LABEL["viz_misplaced"].startswith("Figure in the wrong scope")
    assert ct._KIND_LABEL["viz_duplicated"].startswith("Same cross-arm figure hosted")
    assert ct._KIND_LABEL["viz_untied"] == "Figure not tied to any observable"
    assert all(ct._KIND_TAB[k] == "visualizations" for k in KINDS)


def test_misplaced_renders_and_untied_toggle(tmp_path, dashboard_client):
    c = dashboard_client(_ws(tmp_path))
    tr = _tr(c)
    items = _items(tr)
    mp = [i for i in items if i["kind"] == "viz_misplaced"]
    assert mp and mp[0]["kind_label"].startswith("Figure in the wrong scope")
    assert mp[0]["target"] == "fig:a/x" and mp[0]["rationale"]
    assert mp[0]["studies"] and mp[0]["studies"][0]["tab"] == "visualizations"
    assert not [i for i in items if i["kind"] == "viz_untied"]
    assert tr["summary"]["viz_untied_enabled"] is False
    assert "viz issue" in next(s for s in tr["steps"] if s["id"] == "run_cycle")["summary"]

    r = c.post("/api/discovery-cycle/inv/toggle-untied", json={"enabled": True})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["enabled"] is True
    tr = _tr(c)
    assert tr["summary"]["viz_untied_enabled"] is True
    assert any(i["kind"] == "viz_untied" and i["kind_label"] == "Figure not tied to any observable"
               for i in _items(tr))
    census = tr["summary"]["viz_appropriateness"]
    assert census["untied_surfaced"] is True and census["misplaced"] >= 1

    r = c.post("/api/discovery-cycle/inv/toggle-untied", json={"enabled": False})
    assert r.json()["enabled"] is False
    assert not [i for i in _items(_tr(c)) if i["kind"] == "viz_untied"]
