# tests/test_analysis_tools.py
from vivarium_workbench.lib import analysis_tools as at

def test_match_requires_subset():
    cands = [
        {"ref": "r1", "capabilities": ["observables", "fluxes"]},
        {"ref": "r2", "capabilities": ["observables"]},
        {"ref": "r3", "capabilities": []},
    ]
    got = at.match(["observables"], cands)
    assert {m["ref"] for m in got} == {"r1", "r2"}
    got2 = at.match(["fluxes"], cands)
    assert {m["ref"] for m in got2} == {"r1"}

def test_builtin_tools_have_no_parsimony():
    ids = {t["id"] for t in at.builtin_tools()}
    # The 3D (parsimony) viewer is NOT a core built-in any more — it is a
    # contributed module (a package's workbench_viewers). Data Explorer was also
    # removed from the core built-ins. Every remaining built-in still declares
    # its capability requirement.
    assert "parsimony-viewer" not in ids
    assert "data-explorer" not in ids
    assert all(t.get("requires") for t in at.builtin_tools())

def test_contributed_3d_viewer_matches_pack_capability(monkeypatch):
    # A contributed embed-3d viewer (requires ["3d_pack"]) is matched against the
    # pack candidates — exactly how the old built-in was — so it surfaces
    # wherever a study has a 3D pack. A no-requires contributed viewer keeps its
    # own targets verbatim.
    monkeypatch.setattr(at, "viewers_public",
        lambda ws: [{"id": "ecoli-3d-viewer", "title": "3D E. coli viewer",
                     "kind": "embed-3d", "requires": ["3d_pack"],
                     "bundle_package": "viva_parsimony"},
                    {"id": "omics", "title": "Omics", "requires": [],
                     "targets": [{"study": "s1", "label": "s1"}]}])
    monkeypatch.setattr(at, "_run_candidates",
        lambda ws: [{"ref": "run1", "label": "run1", "capabilities": ["observables"]}])
    monkeypatch.setattr(at, "_pack_candidates",
        lambda ws: [{"ref": "ecoli-3d", "label": "ecoli-3d", "capabilities": ["3d_pack"]}])
    tools = {t["id"]: t for t in at.build_analysis_tools("/ws")}
    assert {m["ref"] for m in tools["ecoli-3d-viewer"]["matched"]} == {"ecoli-3d"}
    assert tools["omics"]["matched"] == [] and tools["omics"]["targets"]

def test_run_candidates_reads_simulations_key(monkeypatch):
    monkeypatch.setattr(at, "build_simulations_data", lambda ws: {
        "simulations": [
            {"run_id": "r1", "label": "run one", "emitter_type": "XArray",
             "capabilities": ["observables", "fluxes"]},
            {"run_id": "r2", "label": "run two", "emitter_type": "SQLite",
             "capabilities": []},
        ],
        "current": None,
    })
    cands = at._run_candidates("/ws")
    assert {c["ref"] for c in cands} == {"r1", "r2"}
    assert {c["ref"] for c in cands if "observables" in c["capabilities"]} == {"r1"}

def test_contributed_viewer_skipped_when_capability_absent(monkeypatch):
    # A contributed viewer whose required capability isn't present in the
    # workspace (here: no 3d_pack) is skipped entirely, not shown as a dead
    # "No compatible runs" card — so domain-specific tools don't clutter
    # unrelated workspaces.
    monkeypatch.setattr(at, "viewers_public",
        lambda ws: [{"id": "ecoli-3d-viewer", "requires": ["3d_pack"],
                     "kind": "embed-3d", "bundle_package": "viva_parsimony"}])
    monkeypatch.setattr(at, "build_simulations_data", lambda ws: {
        "simulations": [{"run_id": "r1", "label": "run one",
                         "emitter_type": "XArray", "capabilities": ["observables"]}],
        "current": None})
    monkeypatch.setattr(at, "_pack_candidates", lambda ws: [])  # no 3d pack
    tools = {t["id"] for t in at.build_analysis_tools("/ws")}
    assert "ecoli-3d-viewer" not in tools  # skipped: no 3d_pack in this workspace

def test_non_3d_contributed_viewer_kept_alongside_3d(monkeypatch):
    # A non-3D contributed viewer (Omics, no requires) and a contributed 3D
    # viewer (requires 3d_pack) coexist — there is no dedup; the contributed 3D
    # viewer is THE 3D card.
    monkeypatch.setattr(at, "viewers_public", lambda ws: [
        {"id": "ecoli-3d-viewer", "requires": ["3d_pack"], "kind": "embed-3d",
         "bundle_package": "viva_parsimony"},
        {"id": "pathway-tools", "title": "Omics", "requires": [],
         "targets": [{"study": "showcase", "label": "showcase"}]},
    ])
    monkeypatch.setattr(at, "_run_candidates", lambda ws: [])
    monkeypatch.setattr(at, "_pack_candidates",
        lambda ws: [{"ref": "ecoli-3d", "label": "ecoli-3d", "capabilities": ["3d_pack"]}])
    ids = [t["id"] for t in at.build_analysis_tools("/ws")]
    assert "pathway-tools" in ids
    assert "ecoli-3d-viewer" in ids

def test_workspace_can_exclude_a_contributed_viewer(monkeypatch):
    # A workspace opts a tool out via ui.analysis_tools_exclude — the suppressed
    # contributed viewer is dropped even when it otherwise matches (here the 3D
    # viewer, despite a 3d_pack).
    monkeypatch.setattr(at, "_excluded_tool_ids",
                        lambda ws: {"ecoli-3d-viewer", "omics"})
    monkeypatch.setattr(at, "viewers_public",
        lambda ws: [{"id": "ecoli-3d-viewer", "requires": ["3d_pack"],
                     "kind": "embed-3d", "bundle_package": "viva_parsimony"},
                    {"id": "omics", "title": "Omics", "requires": [],
                     "targets": [{"study": "s1", "label": "s1"}]},
                    {"id": "pathway-tools", "title": "PTools", "requires": [],
                     "targets": [{"study": "s1", "label": "s1"}]}])
    monkeypatch.setattr(at, "_run_candidates", lambda ws: [])
    monkeypatch.setattr(at, "_pack_candidates",
        lambda ws: [{"ref": "ecoli-3d", "label": "ecoli-3d", "capabilities": ["3d_pack"]}])
    ids = {t["id"] for t in at.build_analysis_tools("/ws")}
    assert "ecoli-3d-viewer" not in ids   # contributed 3D viewer suppressed despite a pack
    assert "omics" not in ids              # contributed viewer suppressed
    assert "pathway-tools" in ids          # a non-excluded viewer still shows

def test_no_exclude_by_default(monkeypatch, tmp_path):
    # Missing/blank ui.analysis_tools_exclude → nothing suppressed (default).
    (tmp_path / "workspace.yaml").write_text("name: x\nui: {}\n")
    assert at._excluded_tool_ids(tmp_path) == set()
    assert at._excluded_tool_ids(tmp_path / "nonexistent") == set()

def test_run_label_strips_module_prefix():
    # a run's dropdown label should be the concise, unique id — not the verbose
    # stored param-dump label.
    assert at._run_label(
        {"run_id": "v2ecoli.composites.baseline__1784747701__b026df",
         "label": "cache_dir=out/cache, config_overrides={}, emitter=parquet"}
    ) == "baseline__1784747701__b026df"
    assert at._run_label({"run_id": "phase0-multigen-xarray"}) == "phase0-multigen-xarray"
    assert at._run_label({"sim_name": "s"}) == "s"
