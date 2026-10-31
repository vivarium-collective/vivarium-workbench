"""Investigation tab freshness (Verdict + Narrative): derived state + accept-gated refresh."""
import pytest
import yaml

from vivarium_workbench.lib import cycle_trace as ct

pytestmark = pytest.mark.skipif(not ct.engine_available(), reason="engine unavailable")


def _ws(tmp_path, executive=None, claim="c1"):
    ws = tmp_path / "ws"
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (ws / "investigations" / "inv").mkdir(parents=True)
    (ws / "investigations" / "inv" / "investigation.yaml").write_text(yaml.safe_dump(
        {"slug": "inv", "name": "inv", "studies": ["s1"], "executive": executive or {}}))
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "s1", "composite": "x.y",
        "findings": [{"id": "f-a", "statement": claim}]}))
    return ws, sd


def _inv(ws):
    return yaml.safe_load((ws / "investigations/inv/investigation.yaml").read_text())


def _fr(c):
    return c.get("/api/investigation/inv/freshness").json()["freshness"]


STALE = {"verdict_status": "supported", "computed_verdict_status": "refuted"}


def test_verdict_expired_then_accept_makes_fresh(tmp_path, dashboard_client):
    ws, _ = _ws(tmp_path, STALE)
    c = dashboard_client(ws)
    fr = _fr(c)
    assert fr["verdict"]["state"] == "expired" and fr["verdict"]["computed"] == "refuted"
    r = c.post("/api/investigation/inv/accept-verdict", json={"rationale": "  "})
    assert r.status_code == 400
    assert _fr(c)["verdict"]["state"] == "expired"
    r = c.post("/api/investigation/inv/accept-verdict", json={"rationale": "reviewed the tests"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert _fr(c)["verdict"]["state"] == "fresh"
    ex = _inv(ws)["executive"]
    assert ex["verdict_status"] == "refuted" and len(ex["verdict_history"]) == 1


def test_narrative_unstamped_then_expired_then_refreshed(tmp_path, dashboard_client):
    ws, sd = _ws(tmp_path)
    c = dashboard_client(ws)
    assert _fr(c)["narrative"]["state"] == "unstamped"
    assert c.post("/api/investigation/inv/refresh-narrative", json={"rationale": ""}).status_code == 400
    assert c.post("/api/investigation/inv/refresh-narrative", json={"rationale": "read it"}).json()["ok"]
    assert _fr(c)["narrative"]["state"] == "fresh"
    spec = yaml.safe_load((sd / "study.yaml").read_text())
    spec["findings"][0]["statement"] = "changed"
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    assert _fr(c)["narrative"]["state"] == "expired"
    assert c.post("/api/investigation/inv/refresh-narrative", json={"rationale": "re-read"}).json()["ok"]
    assert _fr(c)["narrative"]["state"] == "fresh"
    assert len(_inv(ws)["executive"]["narrative_history"]) == 2


def test_accept_verdict_without_computed_is_400(tmp_path, dashboard_client):
    ws, _ = _ws(tmp_path, {"verdict_status": "supported"})
    c = dashboard_client(ws)
    assert c.post("/api/investigation/inv/accept-verdict", json={"rationale": "x"}).status_code == 400

