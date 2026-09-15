# tests/test_simulations_matched_tools.py
"""Simulations DB "compatible analysis tools" annotation.

Mirrors tests/test_simulations_capabilities.py's style: exercise the
lib/simulations_index.py helpers directly with a mocked tools list, so these
stay fast and independent of a real workspace / installed viewers.
"""
from vivarium_workbench.lib import simulations_index as si


def _fake_tools():
    return [
        {"id": "parsimony-viewer", "title": "Parsimony Viewer", "kind": "embed-3d",
         "requires": ["3d_pack"],
         "matched": [{"ref": "ecoli-3d", "label": "ecoli-3d"}]},
    ]


def test_row_with_3d_pack_capability_gets_matched_tool(monkeypatch):
    monkeypatch.setattr(
        "vivarium_workbench.lib.analysis_tools.build_analysis_tools",
        lambda ws: _fake_tools(),
    )
    rows = [{"run_id": "r1", "study_slug": "ecoli-3d", "capabilities": ["3d_pack"]}]
    si._attach_matched_tools(rows, "/ws")
    matched = rows[0]["matched_tools"]
    assert len(matched) == 1
    assert matched[0]["id"] == "parsimony-viewer"
    assert matched[0]["kind"] == "embed-3d"
    assert matched[0]["launch_url"]


def test_row_without_matching_capability_gets_empty_matched_tools(monkeypatch):
    monkeypatch.setattr(
        "vivarium_workbench.lib.analysis_tools.build_analysis_tools",
        lambda ws: _fake_tools(),
    )
    rows = [{"run_id": "r2", "study_slug": "showcase", "capabilities": []}]
    si._attach_matched_tools(rows, "/ws")
    assert rows[0]["matched_tools"] == []


def test_a_broken_tools_build_never_breaks_the_row(monkeypatch):
    def _boom(ws):
        raise RuntimeError("broken viewer")

    monkeypatch.setattr(
        "vivarium_workbench.lib.analysis_tools.build_analysis_tools", _boom,
    )
    rows = [{"run_id": "r1", "study_slug": "ecoli-3d", "capabilities": ["3d_pack"]}]
    si._attach_matched_tools(rows, "/ws")  # must not raise
    assert rows[0]["matched_tools"] == []


def test_3d_launch_url_prefers_hosted_viewer_url():
    tool = {"id": "parsimony-viewer", "kind": "embed-3d", "requires": ["3d_pack"],
            "matched": [{"ref": "ecoli-3d", "viewer_url": "https://example.com/hosted"}]}
    row = {"run_id": "r1", "study_slug": "ecoli-3d"}
    assert si._launch_url_for_matched_tool(tool, row) == "https://example.com/hosted"


def test_3d_launch_url_falls_back_to_bundled_models_manifest():
    tool = {"id": "parsimony-viewer", "kind": "embed-3d", "requires": ["3d_pack"],
            "matched": [{"ref": "ecoli-3d"}]}
    row = {"run_id": "r1", "study_slug": "ecoli-3d"}
    url = si._launch_url_for_matched_tool(tool, row)
    assert url.startswith("/parsimony-viewer/index.html?models=")
    assert "ecoli-3d" in url


def test_3d_tool_omitted_when_studys_own_pack_not_in_matched():
    # The tool matches by CAPABILITY (row carries 3d_pack) but this row's study
    # isn't one of the tool's matched pack candidates — no dead link.
    tool = {"id": "parsimony-viewer", "kind": "embed-3d", "requires": ["3d_pack"],
            "matched": [{"ref": "other-study"}]}
    row = {"run_id": "r1", "study_slug": "ecoli-3d"}
    assert si._launch_url_for_matched_tool(tool, row) is None


def test_launcher_tool_builds_actuation_url():
    tool = {"uid": "pkg::demo", "id": "demo", "title": "Demo", "kind": "launcher",
            "requires": ["observables"]}
    row = {"run_id": "r1", "study_slug": "showcase"}
    url = si._launch_url_for_matched_tool(tool, row)
    assert url == "/api/analysis-viewer/pkg%3A%3Ademo/launch?study=showcase&run=r1"


def test_bare_embed_contributed_viewer_is_omitted():
    # A contributed viewer of kind "embed" (a custom mounted mini-app) has no
    # href/launch semantics this layer can resolve to one URL.
    tool = {"uid": "pkg::custom", "id": "custom", "kind": "embed", "requires": ["mass"]}
    row = {"run_id": "r1", "study_slug": "showcase"}
    assert si._launch_url_for_matched_tool(tool, row) is None


def test_attach_matched_tools_does_not_recurse_forever(monkeypatch, tmp_path):
    # analysis_tools.build_analysis_tools computes its run candidates via
    # build_simulations_data (lib/analysis_tools.py:_run_candidates) — calling
    # it FROM build_simulations_data must not recurse without the reentrancy
    # guard in _attach_matched_tools.
    calls = {"n": 0}

    def fake_build_analysis_tools(ws):
        calls["n"] += 1
        si.build_simulations_data(ws)  # mirrors the real dependency
        return _fake_tools()

    monkeypatch.setattr(
        "vivarium_workbench.lib.analysis_tools.build_analysis_tools",
        fake_build_analysis_tools,
    )
    rows = [{"run_id": "r1", "study_slug": "ecoli-3d", "capabilities": ["3d_pack"]}]
    si._attach_matched_tools(rows, tmp_path)
    assert calls["n"] == 1  # not called again from the nested build_simulations_data
    assert rows[0]["matched_tools"]


def test_build_simulations_data_attaches_matched_tools_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "vivarium_workbench.lib.analysis_tools.build_analysis_tools",
        lambda ws: _fake_tools(),
    )
    monkeypatch.setattr(si, "backfill_index_into_jsonl", lambda ws: 0)
    monkeypatch.setattr(si.run_log, "fold_runs_jsonl", lambda ws: {
        "r1": {"run_id": "r1", "study_slug": "ecoli-3d", "status": "completed",
               "capabilities": ["3d_pack"]},
    })
    monkeypatch.setattr(si, "_append_remote_simulations", lambda sims, ws, **kw: sims)
    data = si.build_simulations_data(tmp_path)
    row = data["simulations"][0]
    assert row["matched_tools"][0]["id"] == "parsimony-viewer"


def _hosted_3d_tools():
    """embed-3d tool for study 's01' carrying a hosted viewer_url (as
    analysis_tools_3d attaches from ui.viz_viewer_urls)."""
    return [
        {"id": "parsimony-viewer", "title": "Parsimony Viewer", "kind": "embed-3d",
         "requires": ["3d_pack"],
         "matched": [{"ref": "s01", "label": "s01",
                      "viewer_url": "https://host.example/s01/index.html"}]},
    ]


def test_capabilityless_run_of_3d_study_gets_hosted_viewer_tool(monkeypatch):
    """A run whose STUDY owns a 3D pack gets the Parsimony Viewer chip even
    though the run itself carries NO emitter capabilities — 3d_pack is a study
    property, not a run one. The chip links the study's hosted viewer_url. This
    is what lets a Simulations-DB row open its 3D viewer directly."""
    monkeypatch.setattr(
        "vivarium_workbench.lib.analysis_tools.build_analysis_tools",
        lambda ws: _hosted_3d_tools(),
    )
    rows = [{"run_id": "r3", "study_slug": "s01", "capabilities": []}]
    si._attach_matched_tools(rows, "/ws")
    matched = rows[0]["matched_tools"]
    assert len(matched) == 1
    assert matched[0]["kind"] == "embed-3d"
    assert matched[0]["launch_url"] == "https://host.example/s01/index.html"


def test_study_scoped_3d_match_does_not_leak_across_studies(monkeypatch):
    """The study-scoped embed-3d match must not attach to a capability-less run
    of a DIFFERENT study (no 3D pack of its own)."""
    monkeypatch.setattr(
        "vivarium_workbench.lib.analysis_tools.build_analysis_tools",
        lambda ws: _hosted_3d_tools(),
    )
    rows = [{"run_id": "r4", "study_slug": "some-other-study", "capabilities": []}]
    si._attach_matched_tools(rows, "/ws")
    assert rows[0]["matched_tools"] == []
