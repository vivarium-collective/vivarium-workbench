"""Tests for the deterministic investigation-report generator.

The generator must render one investigation to a single self-contained HTML
document from EXISTING files only (investigation.yaml, study.yaml, loop-trajectory
JSON) — no model call, no invented content. These tests build a tiny fixture
workspace on the fly and assert the assembled data shapes + self-containment.
"""
import json

import yaml

from vivarium_workbench.lib.investigation_report import (
    build_report_data,
    render_html,
    render_investigation_report,
)


def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(obj, sort_keys=False), encoding="utf-8")


def _ws(tmp_path):
    """A minimal flat-layout workspace with a sourcing investigation (incl. an
    adversarial-control trap) and an agentic-build investigation with a trajectory."""
    _write(tmp_path / "workspace.yaml", {"schema_version": 2, "name": "tw", "package_path": "pkg"})

    # --- sourcing investigation ---
    _write(tmp_path / "investigations" / "src" / "investigation.yaml", {
        "name": "src", "title": "Sourcing", "status": "complete",
        "question": "Can an agent source the model well?",
        "executive": {"what_is_this": "audit", "verdict": "graded correctly", "verdict_status": "passed"},
        "catalog": {"mod-a": ["physics_2d", "collision"]},
        "studies": ["reuse-ok", "trap"],
    })
    _write(tmp_path / "studies" / "reuse-ok" / "study.yaml", {
        "name": "reuse-ok", "title": "Reuse OK", "confidence": "Accepted", "gate_status": "passed",
        "requires": ["physics_2d", "collision"], "claim": "reuse mod-a",
        "sourcing": {"decision": "reuse", "modules": ["mod-a"], "rationale": "fits",
                     "audit": {"gate": "pass", "axes": {"source_fit": "within_tol", "reinvention": "within_tol"},
                               "catches_if_wrong": "build-new would trip reinvention"}},
        "baseline": [{"name": "mod-a", "composite": "pkg.composites.reuse_ok", "module": "mod-a", "domain": "2D physics"}],
        "behavior_tests": [{"name": "sourcing-fit", "classification": "primary",
                            "measure": {"kind": "audit-axis", "path": "sourcing.source_fit"},
                            "pass_if": {"op": "==", "value": "within_tol"}}],
        "runs": [{"name": "reuse-ok", "status": "completed",
                  "outcomes": {"SOURCING-GATE": {"result": "PASS", "detail": "gate=pass"}}}],
        "conclusion": "The audit graded this PASS.",
    })
    _write(tmp_path / "studies" / "trap" / "study.yaml", {
        "name": "trap", "title": "Trap", "confidence": "Refuted", "gate_status": "failed",
        "requires": ["physics_2d", "spatial"], "claim": "no clean fit",
        "sourcing": {"decision": "reuse", "modules": ["mod-a"], "rationale": "tempting but wrong",
                     "audit": {"gate": "fail", "axes": {"source_fit": "mismatch", "reinvention": "within_tol"},
                               "catches_if_wrong": "needs spatial, mod-a lacks it"}},
        "baseline": [{"name": "mod-a", "composite": "pkg.composites.trap", "module": "mod-a"}],
        "behavior_tests": [{"name": "sourcing-fit", "measure": {"kind": "audit-axis", "path": "sourcing.source_fit"},
                            "pass_if": {"op": "==", "value": "within_tol"}}],
        "runs": [{"name": "trap", "status": "audit-only",
                  "outcomes": {"SOURCING-GATE": {"result": "FAIL", "detail": "gate=fail"}}}],
        "conclusion": "The audit refused this bad reuse.",
    })

    # --- agentic-build investigation with a trajectory ---
    _write(tmp_path / "investigations" / "build" / "investigation.yaml", {
        "name": "build", "title": "Build challenges", "status": "in_progress",
        "question": "Which tasks require an agent?", "studies": ["tsk"],
    })
    _write(tmp_path / "studies" / "tsk" / "study.yaml", {
        "name": "tsk", "title": "Task", "confidence": "Accepted", "gate_status": "passed",
        "question": "Build a model that passes the tests.",
        "biological_summary": "A Composite: FooProc + BarProc over a/b.",
        "baseline": [{"name": "final", "composite": "pkg.composites.tsk"}],
        "behavior_tests": [{"name": "growth", "description": "grows", "pass_if": {"op": ">=", "value": 0}}],
        "runs": [{"name": "tsk-agent", "status": "completed",
                  "outcomes": {"LOOP-OUTCOME": {"result": "PASS", "detail": "DONE in 1 edit"}}}],
        "conclusion": "## Final model\nFooProc + BarProc.\n\n## Result\nDONE 1/1.",
        "loop_provenance": "tsk-agent",
    })
    traj = {"schema": "agent_build_trajectory/v1", "study": "tsk", "driver": "LLM agent",
            "tests": [{"id": "growth", "label": "grows", "expected": ">= 0", "provenance": "max biomass"}],
            "iterations": [
                {"iteration": 0, "n_pass": 0, "n_hard": 1, "newly_fixed": [],
                 "agent_decision": {"action": "install", "mechanism": "FooProc", "reasoning": "start"},
                 "tests": [{"id": "growth", "verdict": "mismatch", "observed": 0.0, "expected": ">= 0", "margin": -1.0}]},
                {"iteration": 1, "n_pass": 1, "n_hard": 1, "newly_fixed": ["growth"],
                 "agent_decision": {"action": "done", "reasoning": "passes"},
                 "tests": [{"id": "growth", "verdict": "within_tol", "observed": 5.0, "expected": ">= 0", "margin": 5.0}]},
            ],
            "result": {"state": "DONE", "edits": 1, "violations": []}}
    (tmp_path / "investigations" / "build" / "tsk_agent_trajectory.json").write_text(json.dumps(traj))

    # --- spatio-flux-style investigation: DIFFERENT fields, no `title`, an
    #     on-disk figure, purpose/findings/conclusion_logic instead of claim/sourcing ---
    _write(tmp_path / "investigations" / "sf" / "investigation.yaml", {
        "name": "sf", "title": "Scenarios", "status": "complete", "studies": ["sf-scn"],
    })
    _write(tmp_path / "studies" / "sf-scn" / "study.yaml", {
        "name": "sf-scn", "gate_status": "passed",   # note: no title / claim / confidence
        "purpose": {"question": "Does the scenario reproduce its artifacts?",
                    "mechanism": "Runs the pkg.composites.scn composite."},
        "baseline": [{"name": "baseline", "composite": "pkg.composites.scn", "params": {"model": "core"}}],
        "visualizations": [{"name": "plot.svg", "address": "image:visualizations/plot.svg", "chart": "image"}],
        "behavior_tests": [{"name": "SCN-REPRODUCES", "classification": "regression",
                            "measure": {"kind": "artifacts_present", "expected": ["plot.svg", "state.json"]},
                            "pass_if": {"op": "all_exist_and_match", "tolerance": 0.02}}],
        "runs": [{"name": "scn-reproduce", "status": "completed", "result": "PASS",
                  "outcomes": {"SCN-REPRODUCES": {"result": "PASS", "detail": "artifacts match"}}}],
        "findings": [{"id": "F1", "status": "passed", "statement": "reproduces the reference artifacts.",
                      "evidence": {"from_test": "SCN-REPRODUCES"}}],
        "conclusion_logic": {"if_primary_tests_pass": {"implementation_status": "Faithfully reproduced."}},
    })
    svg = (tmp_path / "studies" / "sf-scn" / "visualizations" / "plot.svg")
    svg.parent.mkdir(parents=True, exist_ok=True)
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>')
    return tmp_path


def test_sourcing_data_shape_and_provenance(tmp_path):
    ws = _ws(tmp_path)
    d = build_report_data(ws, "src")
    assert d["title"] == "Sourcing"
    assert [s["slug"] for s in d["studies"]] == ["reuse-ok", "trap"]
    # sourcing block passes through verbatim (the report's actual subject)
    reuse = d["studies"][0]
    assert reuse["sourcing"]["decision"] == "reuse"
    assert reuse["sourcing"]["audit"]["axes"]["source_fit"] == "within_tol"
    # the trap is a real fail (its adversarial-control treatment is derived from this)
    trap = d["studies"][1]
    assert trap["gate_status"] == "failed" and trap["confidence"] == "Refuted"
    assert trap["sourcing"]["audit"]["gate"] == "fail"
    # only known top-level keys — nothing invented
    assert set(d) <= {"slug", "title", "status", "question", "hypothesis", "lead",
                      "executive", "at_a_glance", "acceptance_criteria", "spine",
                      "catalog", "workspace", "provenance", "studies",
                      # provenance surfacing: cited top-level argument + resolved bib
                      "scientific_argument", "key_claims", "competing_frameworks",
                      "references", "inv_references",
                      # narrative front-matter (published-report aesthetic): the
                      # Background / "simulated world" prose, glossary, guidelines,
                      # how-to-read, and the creation date for the masthead.
                      "biological_story", "glossary", "guidelines", "how_to_read",
                      "created",
                      # deterministic cross-study synthesis (finding-first report)
                      "synthesis"}


def test_agentic_trajectory_attached(tmp_path):
    ws = _ws(tmp_path)
    d = build_report_data(ws, "build")
    assert len(d["studies"]) == 1
    s = d["studies"][0]
    assert "agent_trajectory" in s
    assert s["agent_trajectory"]["result"]["state"] == "DONE"
    assert len(s["agent_trajectory"]["iterations"]) == 2
    assert s["behavior_tests"][0]["name"] == "growth"


def test_render_is_self_contained(tmp_path):
    ws = _ws(tmp_path)
    html = render_html(build_report_data(ws, "src"))
    # no unrendered placeholders
    assert "__DATA__" not in html and "__TITLE__" not in html
    # self-contained: no live calls, no external assets
    assert "fetch(" not in html
    assert 'src="http' not in html and 'href="http' not in html
    assert "/api/" not in html
    # real field values are present (rendered from data, not invented)
    assert "Sourcing" in html
    assert "reuse" in html and "mod-a" in html


def test_render_writes_file(tmp_path):
    ws = _ws(tmp_path)
    out = render_investigation_report(ws, "build")
    assert out.name == "investigation-build.html"
    assert out.is_file()
    assert "Build challenges" in out.read_text(encoding="utf-8")


def test_spatioflux_style_fields_and_inlined_figure(tmp_path):
    """A workspace whose studies use purpose/findings/conclusion_logic + on-disk
    figures (no title/claim/sourcing) renders adaptively with the same generator."""
    ws = _ws(tmp_path)
    d = build_report_data(ws, "sf")
    s = d["studies"][0]
    # title falls back to a humanized slug when the study has none
    assert s["title"] == "Sf Scn"
    assert s["purpose"]["question"].startswith("Does the scenario")
    assert s["findings"][0]["statement"].startswith("reproduces")
    assert s["conclusion_logic"]["if_primary_tests_pass"]["implementation_status"]
    # the on-disk figure is inlined as a data URI (self-contained)
    figs = s["figures_embedded"]
    assert figs and figs[0]["data_uri"].startswith("data:image/svg+xml;base64,")
    html = render_html(d)
    assert "Sf Scn" in html and "data:image/svg+xml;base64," in html
    # still self-contained even with an embedded image
    assert "fetch(" not in html and "/api/" not in html


def test_oversized_figure_is_skipped_not_read(tmp_path):
    """A figure past the per-file cap is reported as skipped, not embedded."""
    import vivarium_workbench.lib.investigation_report as mod
    ws = _ws(tmp_path)
    big = ws / "studies" / "sf-scn" / "visualizations" / "plot.svg"
    big.write_text("<svg/>" + " " * (mod._IMG_PER_FILE_MAX + 10))
    d = build_report_data(ws, "sf")
    figs = d["studies"][0]["figures_embedded"]
    assert figs and figs[0].get("skipped") is True and "data_uri" not in figs[0]


def test_spine_verdict_dag_acceptance_and_matrix(tmp_path):
    """The report data carries the computed spine: a verdict DAG (nodes + edges
    from parent_studies + 5-state verdicts), the acceptance roll-up, and the
    AC→study gating matrix (with the unlinked-criterion gap flagged)."""
    ws = tmp_path
    _write(ws / "workspace.yaml", {"schema_version": 2, "name": "tw", "package_path": "pkg"})
    _write(ws / "investigations" / "sp" / "investigation.yaml", {
        "name": "sp", "title": "Spine", "studies": ["a", "b"],
        "acceptance_criteria": [{"study": "a", "behavior": "beh-a"}, {"behavior": "unlinked"}],
    })
    _write(ws / "studies" / "a" / "study.yaml", {
        "name": "a", "title": "Study A", "gate_status": "passed",
        "behavior_tests": [{"name": "beh-a"}],
        "runs": [{"name": "r", "status": "completed", "outcomes": {"beh-a": {"result": "PASS"}}}]})
    _write(ws / "studies" / "b" / "study.yaml", {
        "name": "b", "title": "Study B", "gate_status": "failed", "parent_studies": [{"study": "a"}],
        "runs": [{"name": "r", "status": "completed", "outcomes": {"beh-b": {"result": "FAIL"}}}]})

    d = build_report_data(ws, "sp")
    sp = d["spine"]
    # verdict DAG: nodes carry 5-state verdicts; edge a→b from parent_studies; b deeper
    nodes = {n["slug"]: n for n in sp["verdict_dag"]["nodes"]}
    assert nodes["a"]["verdict"] == "passed" and nodes["b"]["verdict"] == "failing"
    assert nodes["b"]["depth"] > nodes["a"]["depth"]
    assert {"from": "a", "to": "b"} in sp["verdict_dag"]["edges"]
    # acceptance roll-up present with the linked criterion resolved
    assert any(c.get("study") == "a" for c in (sp["acceptance"] or {}).get("criteria", []))
    # AC gating matrix flags the unlinked criterion as a gap
    gaps = [c for c in (sp["ac_matrix"] or {}).get("criteria", []) if c.get("gap")]
    assert any(c["behavior"] == "unlinked" for c in gaps)
    # the rendered report is still self-contained
    html = render_html(d)
    assert "fetch(" not in html and "/api/" not in html


def test_missing_investigation_raises(tmp_path):
    ws = _ws(tmp_path)
    try:
        build_report_data(ws, "nope")
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError for a missing investigation")


# --- Model-section loom view (bigraph topology) --------------------------------

def test_compact_bigraph_extracts_processes_and_nested_stores():
    """_compact_bigraph splits a resolved composite state into the flat process
    list (name/address/ports) and the nested store tree (name/type/children)."""
    from vivarium_workbench.lib.investigation_report import _compact_bigraph
    state = {
        "_type": "composite",
        "metabolism": {"_type": "process", "address": "local:Foo",
                       "doc": "FBA metabolism contract.\nsecond line ignored",
                       "inputs": {"nutrients": ["cell", "env", "n"]},
                       "outputs": {"biomass": ["cell", "physiology", "m"]}},
        "cell": {"biomass": {"_type": "mass"},
                 "sub": {"x": {"_type": "concentration"}}},
        "viz": {"_type": "step", "address": "local:Plot", "inputs": {}, "outputs": {}},
    }
    topo = _compact_bigraph(state)
    assert {p["name"] for p in topo["processes"]} == {"metabolism", "viz"}
    foo = next(p for p in topo["processes"] if p["name"] == "metabolism")
    assert foo["address"] == "local:Foo"
    assert foo["inputs"] == ["nutrients"] and foo["outputs"] == ["biomass"]
    # first line of the describe() doc is carried as the concise contract summary
    assert foo["doc"] == "FBA metabolism contract."
    # a 3+ segment path resolves its compartment to the second segment
    assert set(foo["compartments"]) == {"env", "physiology"}
    cell = next(st for st in topo["stores"] if st["name"] == "cell")
    assert any(c["name"] == "biomass" and c["type"] == "mass" for c in cell["children"])
    sub = next(c for c in cell["children"] if c["name"] == "sub")
    assert any(gc["name"] == "x" and gc["type"] == "concentration" for gc in sub["children"])
    # process↔compartment wiring edges are emitted for the interactive loom
    assert {(e["p"], e["c"]) for e in topo["edges"]} == {
        ("metabolism", "env"), ("metabolism", "physiology")}


def test_model_topology_is_fully_guarded():
    """_model_topology returns None (never raises) when there is no baseline
    composite to resolve — the report must render even without a resolvable model."""
    from vivarium_workbench.lib.investigation_report import _model_topology
    assert _model_topology("/no/such/ws", {}) is None
    assert _model_topology("/no/such/ws", {"baseline": [{"name": "x"}]}) is None


def test_template_renders_loom_view_and_light_markdown():
    """The Model section prefers the resolved loom view, and authored conclusions
    are rendered through the light-markdown helper (no raw ## headers)."""
    from pathlib import Path
    import vivarium_workbench
    tpl = (Path(vivarium_workbench.__file__).parent
           / "templates" / "investigation-report.html").read_text(encoding="utf-8")
    assert "function loomTopoSVG(" in tpl
    assert "s.model_topology ? loomTopoSVG(s.model_topology)" in tpl
    assert "function mdLite(" in tpl
    assert "mdLite(conclusion)" in tpl


def test_real_loom_embed_is_opt_in_and_self_contained_by_default(tmp_path):
    """The Model section embeds the REAL bigraph-loom only when the publisher
    passes ``loom_embed`` (the bundle ships the loom + composite-state beside the
    report). Without it the report stays fully self-contained — no iframe, no
    ``/api/`` reference — so a standalone download still opens offline."""
    from vivarium_workbench.lib.investigation_report import build_report_data, render_html
    ws = _ws(tmp_path)
    # default: no loom_embed → self-contained, no embed payload
    data = build_report_data(ws, "src")
    assert "loom_embed" not in data
    html = render_html(data)
    assert "/api/" not in html and 'src="http' not in html
    # publisher path: loom_embed present → carried into the embedded data payload
    le = {"loom": "../bigraph-loom/index.html", "state": "../api/composite-state/"}
    data2 = build_report_data(ws, "src", loom_embed=le)
    assert data2["loom_embed"] == le
    html2 = render_html(data2)
    assert '"loom_embed"' in html2  # the client reads INV.loom_embed to build the iframe


def test_template_wires_real_loom_embed():
    """The template carries the real-loom embed renderer + its lazy mounter, and
    builds the loom URL from the DATA (INV.loom_embed) — never a hardcoded
    ``/api/`` path, which would break the self-contained default."""
    from pathlib import Path
    import vivarium_workbench
    tpl = (Path(vivarium_workbench.__file__).parent
           / "templates" / "investigation-report.html").read_text(encoding="utf-8")
    assert "function loomRealEmbed(" in tpl
    assert "function setupRealLooms(" in tpl
    assert "setupRealLooms();" in tpl
    assert "INV.loom_embed" in tpl
    assert "?embed=1&static=1&stateUrl=" in tpl
    # the template itself must contain no /api/ literal (self-contained default)
    assert "/api/" not in tpl


def test_template_wires_interactive_loom():
    """The Model section prefers the lightweight interactive loom when the
    resolved topology carries wiring edges, and the interaction helpers are
    present so the embedded SVG pans/zooms/highlights without a server."""
    from pathlib import Path
    import vivarium_workbench
    tpl = (Path(vivarium_workbench.__file__).parent
           / "templates" / "investigation-report.html").read_text(encoding="utf-8")
    assert "function loomInteractive(" in tpl
    # the interactive view is chosen first, falling back to the static renders
    assert "loomInteractive(s.model_topology)" in tpl
    # interaction handlers wired into the embedded SVG
    for fn in ("_iloomHi", "_iloomClear", "_iloomTip", "_iloomExpand",
               "setupInteractiveLooms"):
        assert f"function {fn}(" in tpl, fn
    assert "setupInteractiveLooms();" in tpl


def test_model_loom_png_embedded_in_report(tmp_path):
    """A study that saved viz/model-loom.png gets a self-contained data-URI
    Model view (`model_loom`), preferred over the live loom iframe."""
    _write(tmp_path / "workspace.yaml",
           {"schema_version": 2, "name": "tw", "package_path": "pkg"})
    _write(tmp_path / "investigations" / "inv" / "investigation.yaml", {
        "name": "inv", "title": "Inv", "status": "complete",
        "question": "?", "studies": ["s1"],
    })
    _write(tmp_path / "studies" / "s1" / "study.yaml", {
        "name": "s1", "title": "S1",
        "baseline": [{"name": "baseline", "composite": "pkg.composites.demo.demo"}],
    })
    # a tiny (valid) PNG saved by `render-loom`
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000d4944415478da63f8cfc0f01f0005000155a3f4610"
        "000000049454e44ae426082")
    loom = tmp_path / "studies" / "s1" / "viz" / "model-loom.png"
    loom.parent.mkdir(parents=True, exist_ok=True)
    loom.write_bytes(png)

    data = build_report_data(tmp_path, "inv")
    assert len(data["studies"]) == 1
    s1 = data["studies"][0]
    assert s1.get("model_loom", "").startswith("data:image/png;base64,")

    html = render_html(data)
    assert s1["model_loom"] in html  # self-contained (no external fetch)


def test_report_renders_studies_from_members_only_investigation(tmp_path):
    """The generated report's study list must come from `members ∪ studies` —
    a post-migration investigation that carries only `members:` (the L0-audit
    canonical key) must still render its studies, not an empty list."""
    _write(tmp_path / "workspace.yaml", {"schema_version": 2, "name": "tw", "package_path": "pkg"})
    _write(tmp_path / "investigations" / "inv" / "investigation.yaml", {
        "name": "inv", "title": "Members-only", "status": "complete",
        "question": "Do members render?",
        "members": ["only-study"],          # NOTE: members:, no studies:
    })
    _write(tmp_path / "studies" / "only-study" / "study.yaml", {
        "name": "only-study", "title": "Only Study", "confidence": "Accepted",
        "gate_status": "passed", "claim": "renders",
        "baseline": [{"name": "b", "composite": "pkg.composites.b"}],
    })
    data = build_report_data(tmp_path, "inv")
    slugs = [s.get("slug") for s in data.get("studies", [])]
    assert slugs == ["only-study"], f"members-only investigation rendered {slugs!r}, expected the member"


def test_bib_label_and_reference_parsing(tmp_path):
    """The provenance feature resolves papers.bib bibkeys → short 'Author Year'
    labels so citation chips read meaningfully and link to the References list."""
    from vivarium_workbench.lib.investigation_report import _bib_label, _load_references
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    # _bib_label: first-author surname + year, from a BibTeX author field
    assert _bib_label("kelley2001offaxis",
                      {"author": "Kelley, Deborah S. and others", "year": "2001"}) == "Kelley 2001"
    assert _bib_label("x", {"author": "Russell, Michael J.", "year": "1997"}) == "Russell 1997"
    assert _bib_label("bare2020", {}) == "bare2020"  # no fields → fall back to key
    # _load_references: parse a minimal bib under <ws>/references/papers.bib
    inv_dir = tmp_path / "investigations"
    inv_dir.mkdir(parents=True)
    refs_dir = tmp_path / "references"
    refs_dir.mkdir()
    (refs_dir / "papers.bib").write_text(
        "@article{preiner2020hydrogen,\n  author = {Preiner, Martina},\n"
        "  year = {2020},\n  title = {A hydrogen-dependent geochemistry},\n}\n",
        encoding="utf-8")
    # A BibTeX/LaTeX accent escape must decode to Unicode in the chip label,
    # author and title — not leak the raw escape (e.g. Segr{\`e} -> Segrè).
    (refs_dir / "papers.bib").write_text(
        "@article{preiner2020hydrogen,\n  author = {Preiner, Martina},\n"
        "  year = {2020},\n  title = {A hydrogen-dependent geochemistry},\n}\n"
        "@article{segre2000compositional,\n"
        "  author = {Segr{\\`e}, Daniel and Ben-Eli, Dafna and Lancet, Doron},\n"
        "  year = {2000},\n  title = {Compositional genomes},\n}\n",
        encoding="utf-8")
    wp = WorkspacePaths.load(tmp_path)
    refs = _load_references(tmp_path, wp)
    assert "preiner2020hydrogen" in refs
    assert refs["preiner2020hydrogen"]["label"] == "Preiner 2020"
    assert "hydrogen-dependent" in refs["preiner2020hydrogen"]["title"]
    seg = refs["segre2000compositional"]
    assert seg["label"] == "Segrè 2000", seg["label"]
    assert seg["author"].startswith("Segrè, Daniel")
    assert "{" not in seg["author"] and "\\" not in seg["author"]


def test_build_report_data_surfaces_plan_board(tmp_path):
    """The honest A→E gate ladder (plan_board) is surfaced through the report
    spine so the report can lead the findings with where the campaign stands."""
    _write(tmp_path / "workspace.yaml", {"schema_version": 2, "name": "tw", "package_path": "pkg"})
    _write(tmp_path / "investigations" / "camp" / "investigation.yaml", {
        "name": "camp", "title": "Campaign", "status": "in_progress", "question": "Q?",
        "studies": ["s1"],
        "plan_gates": [{"id": "A", "name": "Gate A", "member_studies": ["s1"],
                        "criterion": "s1 passes", "criterion_findings": []}],
    })
    _write(tmp_path / "studies" / "s1" / "study.yaml", {
        "name": "s1", "title": "S1", "gate_status": "passed",
        "baseline": [{"name": "b", "composite": "pkg.composites.c"}],
        "findings": [{"id": "f1", "status": "passed", "statement": "ok"}],
    })
    data = build_report_data(tmp_path, "camp")
    pb = (data.get("spine") or {}).get("plan_board")
    assert isinstance(pb, list) and pb, "plan_board should be surfaced in the report spine"
    assert pb[0]["id"] == "A"


def test_render_report_pdf_missing_playwright_raises_guidance(monkeypatch):
    """PDF export degrades cleanly: with Playwright unavailable, render_report_pdf
    raises ReportPdfError carrying install guidance (so the route returns a 503
    message, not a 500) — deterministic regardless of whether Playwright is
    installed in the test environment."""
    import builtins
    from vivarium_workbench.lib import investigation_report as ir

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.split(".")[0] == "playwright":
            raise ImportError("playwright not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        ir.render_report_pdf("<!doctype html><html><body>x</body></html>")
    except ir.ReportPdfError as e:
        assert "playwright" in str(e).lower()
    else:
        raise AssertionError("expected ReportPdfError when Playwright is missing")


def test_synthesis_separates_status_and_classifies_evidence(tmp_path):
    """The deterministic synthesis separates run status from evidence grade, and a
    computational pass/refute never silently becomes a proven/unproven claim."""
    d = build_report_data(_ws(tmp_path), "src")
    syn = d["synthesis"]
    assert syn["counts"]["total"] == 2
    grades = {r["slug"]: r["grade"] for r in syn["studies_index"]}
    assert grades["reuse-ok"] == "supported"
    assert grades["trap"] == "refuted"
    # the refuted study surfaces as a prominent negative-result contradiction
    negs = [c for c in syn["contradictions"] if c["kind"] == "negative-result"]
    assert any(c["studies"] and c["studies"][0]["slug"] == "trap" for c in negs)
    # claim status is derived, not asserted: refuted -> unsupported, supported -> supported
    claim = {r["slug"]: r["claim_status"] for r in syn["studies_index"]}
    assert claim["trap"] == "unsupported" and claim["reuse-ok"] == "supported"


def test_report_studies_are_cards_in_main_no_supplement():
    """Studies render as collapsible cards in a single main 'Studies' section
    (renderStudies → renderStudy), and the Technical supplement is removed, so
    each study appears exactly once."""
    from pathlib import Path as _P
    import vivarium_workbench as _vw
    tpl = (_P(_vw.__file__).parent / "templates" / "investigation-report.html").read_text(encoding="utf-8")
    for fn in ("function execSummary", "function renderStudies", "function renderStudy",
               "function claimEvidenceMap"):
        assert fn in tpl, fn
    # the Technical supplement wrapper is gone (studies are not duplicated there)
    assert '<details class="supplement"' not in tpl
    assert 'id="supplement"' not in tpl
    # the main render pipeline renders the Studies section (cards), exactly once
    i = tpl.index("function render(inv)")
    body = tpl[i:i + 2000]
    assert "renderStudies(inv)" in body
    assert body.count("renderStudies(inv)") == 1
    assert 'id="studies"' in tpl
