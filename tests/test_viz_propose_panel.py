"""Viz-loop Task 9 (workbench): propose_viz step, Accept/Reject endpoints, figures suffix."""
import sqlite3

import pytest
import yaml

from vivarium_workbench.lib import cycle_trace as ct

pytestmark = pytest.mark.skipif(not ct.engine_available(), reason="engine unavailable")


def _ws(tmp_path, vis=()):
    ws = tmp_path / "ws"
    sd = ws / "studies" / "s5"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (ws / "investigations" / "inv").mkdir(parents=True)
    (ws / "investigations" / "inv" / "investigation.yaml").write_text(
        yaml.safe_dump({"slug": "inv", "name": "inv", "studies": ["s5"]}))
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "s5", "composite": "x.y", "visualizations": list(vis),
        "behavior_tests": [{"name": "t-a", "measure": {"kind": "final", "path": "pop/survivors"},
                            "pass_if": {"low": 1, "high": 2}}],
        "findings": [{"id": "f-a", "evidence": {"from_test": "t-a"}}]}))
    c = sqlite3.connect(sd / "runs.db")
    c.execute("CREATE TABLE runs_meta (run_id TEXT, started_at REAL, completed_at REAL, sim_name TEXT, params_json TEXT)")
    c.execute("INSERT INTO runs_meta VALUES ('r1', 1.0, 10.0, 'default', '{}')")
    c.commit(); c.close()
    return ws, sd


def _frontier(c):
    return c.get("/api/discovery-cycle/inv").json()["summary"]["frontier"]


def _vp(c):
    return next(f for f in _frontier(c) if f["kind"] == "viz_proposed")


def test_viz_kinds_are_resolvable_and_propose_step_present(tmp_path, inproc_client):
    assert {"viz_broken", "viz_missing", "viz_stale", "viz_proposed"} <= ct._RESOLVABLE_KINDS
    ws, _ = _ws(tmp_path)
    c = inproc_client(ws)
    tr = c.get("/api/discovery-cycle/inv").json()
    step = next(s for s in tr["steps"] if s["id"] == "propose_viz")
    assert len(step["result"]["proposals"]) == 1
    item = _vp(c)
    assert item["resolvable"] and item["acceptable"] and item["proposal_id"]
    assert item["needs_author"] is True      # no catalog class -> author supplies the address


def test_accept_writes_entry_and_next_cycle_is_viz_missing(tmp_path, inproc_client):
    ws, sd = _ws(tmp_path)
    c = inproc_client(ws)
    vp = _vp(c)["proposal_id"]
    base = f"/api/discovery-cycle/inv/accept-viz-proposal"
    for bad in ({"proposal": vp, "address": "local:X", "rationale": "  "},
                {"proposal": vp, "rationale": "needs an address"}):
        r = c.post(base, json=bad)
        assert r.status_code >= 400 or r.json().get("ok") is False
    assert not yaml.safe_load((sd / "study.yaml").read_text())["visualizations"]
    r = c.post(base, json={"proposal": vp, "address": "local:BandOverlayViz", "rationale": "reviewer wants it"}).json()
    assert r["ok"], r
    ent = yaml.safe_load((sd / "study.yaml").read_text())["visualizations"][0]
    assert ent["origin"] == {"kind": "viz_proposal", "ref": vp}
    inv = yaml.safe_load((ws / "investigations/inv/investigation.yaml").read_text())
    assert inv["viz_proposals"][0]["status"] == "accepted"
    kinds = [f["kind"] for f in _frontier(c)]
    assert "viz_missing" in kinds and "viz_proposed" not in kinds


def test_reject_is_terminal_and_blank_rationale_refused(tmp_path, inproc_client):
    ws, sd = _ws(tmp_path)
    c = inproc_client(ws)
    vp = _vp(c)["proposal_id"]
    base = "/api/discovery-cycle/inv/reject-viz-proposal"
    r = c.post(base, json={"proposal": vp, "rationale": ""})
    assert r.status_code >= 400 or r.json().get("ok") is False
    assert _vp(c)
    r = c.post(base, json={"proposal": vp, "rationale": "not useful"}).json()
    assert r["ok"], r
    assert not [f for f in _frontier(c) if f["kind"].startswith("viz_")]
    assert not yaml.safe_load((sd / "study.yaml").read_text())["visualizations"]
    inv = yaml.safe_load((ws / "investigations/inv/investigation.yaml").read_text())
    assert inv["viz_proposals"][0]["status"] == "rejected"


def test_run_cycle_summary_shows_figures_when_figures_exist(tmp_path):
    ws, _ = _ws(tmp_path, vis=[{"name": "a", "rendered": "viz/a.html"}])
    tr = ct.build_cycle_trace(ws, "inv")
    s = next(s for s in tr["steps"] if s["id"] == "run_cycle")["summary"]
    assert " figures" in s and "· 0 figures" not in s, s
