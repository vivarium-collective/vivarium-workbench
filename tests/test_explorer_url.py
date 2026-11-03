"""The molecule Explorer as a tool in the Explore tab (launch-on-demand).

It is a built-in Analysis Tool (``build_analysis_tools``) opened full-window via
``GET /api/explorer-open``. The happy path launches a real ``v2ecoli.browser``
process (needs a ParCa cache + workspace venv — not available in CI), so here we
assert the tool is surfaced only when a cache exists and that the open route
returns the structured no-cache message (not a redirect to nothing).
"""

from pathlib import Path

from vivarium_workbench.lib.analysis_tools import build_analysis_tools


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n", encoding="utf-8")
    return tmp_path


def _add_cache(ws: Path) -> None:
    cache = ws / "out" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "simData.cPickle").write_bytes(b"")  # presence is all build_analysis_tools checks


def _tool_ids(ws: Path) -> set[str]:
    return {t.get("id") for t in build_analysis_tools(ws)}


def test_explorer_tool_hidden_without_a_cache(tmp_path):
    ws = _ws(tmp_path)
    assert "molecule-explorer" not in _tool_ids(ws)


def test_explorer_tool_surfaced_with_a_cache(tmp_path):
    ws = _ws(tmp_path)
    _add_cache(ws)
    tools = build_analysis_tools(ws)
    me = [t for t in tools if t.get("id") == "molecule-explorer"]
    assert len(me) == 1, tools
    t = me[0]
    assert t["kind"] == "launcher"
    # one openable item pointing at the launch-on-demand redirect
    assert (t.get("matched") or t.get("targets"))[0]["href"] == "/api/explorer-open"


def test_explorer_tool_excludable(tmp_path):
    ws = _ws(tmp_path)
    _add_cache(ws)
    (ws / "workspace.yaml").write_text(
        "name: test-ws\nui:\n  analysis_tools_exclude: [molecule-explorer]\n",
        encoding="utf-8",
    )
    assert "molecule-explorer" not in _tool_ids(ws)


def test_explorer_open_reports_no_cache(inproc_client, tmp_path):
    client = inproc_client(workspace=_ws(tmp_path))
    r = client.get("/api/explorer-open")
    assert r.status_code == 503
    assert "cache" in r.text.lower()
