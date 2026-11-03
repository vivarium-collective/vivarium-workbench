"""The /api/explorer-url route (molecule Explorer Explore tab, launch-on-demand).

The happy path launches a real ``v2ecoli.browser`` process, which needs a ParCa
cache and the workspace venv — not available in CI — so here we assert the
route is wired and returns the structured ``no-cache`` status for a workspace with
no cache (the branch the frontend shows as a message instead of a blank iframe).
"""

from pathlib import Path


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n", encoding="utf-8")
    return tmp_path


def test_explorer_url_reports_no_cache(inproc_client, tmp_path):
    client = inproc_client(workspace=_ws(tmp_path))
    r = client.get("/api/explorer-url")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "no-cache"
    assert body["url"] is None
    assert "simData.cPickle" in body["message"]
