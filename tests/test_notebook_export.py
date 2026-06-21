"""notebook_export — investigation → self-contained .ipynb + .py.

Builds a minimal fixture workspace (workspace.yaml + investigation + study +
a runs.db recipe) in a tmp dir, runs the exporter, and asserts:

  * both artifacts are written and the .py is syntactically valid;
  * the notebook re-runs via the process-bigraph protocol and discovers the
    run/render entry points by convention (generic fallback vs scripts/);
  * a Process-bigraph composite-structure section is emitted;
  * the generated *text* states parameters/criteria only — planted result
    numbers (conclusion, findings, key-metrics, verdict, test outcomes) never
    leak into Markdown, while parameters and acceptance thresholds are kept.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from vivarium_dashboard.lib.notebook_export import export_investigation_notebook

# Distinctive numbers planted in *result* fields — must NOT appear in the text.
_RESULT_TOKENS = ["9999", "8888", "7777", "6666", "5555"]


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _make_runs_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, "
        "label TEXT, params_json TEXT, started_at REAL, completed_at REAL, "
        "n_steps INTEGER, status TEXT NOT NULL, sim_name TEXT)"
    )
    conn.execute(
        "INSERT INTO runs_meta (run_id, spec_id, n_steps, params_json, status, sim_name) "
        "VALUES (?,?,?,?,?,?)",
        ("demo__main", "demo", 12, json.dumps({"interval": 0.05, "steps": 12}), "complete", "main"),
    )
    conn.commit()
    conn.close()


def _build_workspace(root: Path) -> str:
    """Create a minimal pbg workspace; return the investigation slug."""
    _write(root / "workspace.yaml", {
        "schema_version": 2,
        "name": "demo",
        "package_path": "demo_pkg",
        "layout": {
            "studies": "workspace/studies",
            "investigations": "workspace/investigations",
            "reports": "workspace/reports",
        },
    })
    # composite spec (referenced by path only; not executed at generation time)
    _write(root / "demo_pkg" / "composites" / "demo.composite.yaml", {
        "name": "demo",
        "parameters": {"interval": {"type": "float", "default": 0.05}},
        "state": {"Proc": {"_type": "process", "address": "local:Demo", "config": {"k": 1}}},
    })
    _write(root / "workspace" / "investigations" / "demo-inv" / "investigation.yaml", {
        "schema_version": 2,
        "name": "demo-inv",
        "title": "Demo investigation",
        "question": "Does the demo composite do the thing?",
        "executive": {
            "what_is_this": "A demonstration workspace for the exporter test.",
            "verdict": "It grew to 5555 widgets.",  # RESULT — must be dropped
            "decisions_needed": ["Should we expand the parameter sweep?"],
        },
        "studies": ["demo-study"],
    })
    _write(root / "workspace" / "studies" / "demo-study" / "study.yaml", {
        "schema_version": 3,
        "name": "demo-study",
        "question": "Can parameter K drive the widget count?",
        "objective": "Build the demo composite and run it.",
        "hypothesis": "Higher K yields more widgets.",
        "study_card": {
            "verdict": "demonstrated",
            "key_metrics": [{"label": "widgets", "value": "1 -> 7777", "status": "pass"}],
        },
        "description": "The composite grew to 9999 widgets over the run.",  # RESULT prose
        "variants": [
            {"name": "reference", "params": {"interval": 0.05, "k_param": 2.0, "seed": 7}},
        ],
        "visualizations": [
            {"name": "Widget count", "address": "local:TimeSeriesFromObservables",
             "config": {"observables": ["widgets"], "caption": "Rose to 8888 by the end."}},
        ],
        "behavior_tests": [
            {"name": "grows", "measure": {"kind": "last", "path": "widgets"},
             "pass_if": {"op": "gt", "threshold": 10}, "result": "PASS",
             "notes": "observed 6666 widgets"},  # RESULT — must be dropped
        ],
        "findings": [
            {"id": "F1", "status": "confirms",
             "statement": "The widget count reached 8888.",  # RESULT
             "summary": "Grew to 9999."},
        ],
        "conclusion": "Final widget count was 9999.",  # RESULT
    })
    _make_runs_db(root / "workspace" / "studies" / "demo-study" / "runs.db")
    return "demo-inv"


def _markdown_text(ipynb: dict) -> str:
    return "\n".join(
        "".join(c["source"]) for c in ipynb["cells"] if c["cell_type"] == "markdown"
    )


def _code_text(ipynb: dict) -> str:
    return "\n".join(
        "".join(c["source"]) for c in ipynb["cells"] if c["cell_type"] == "code"
    )


def test_export_writes_valid_artifacts(tmp_path: Path):
    inv = _build_workspace(tmp_path)
    out = export_investigation_notebook(tmp_path, inv)

    assert out["ipynb"].is_file() and out["py"].is_file()
    nb = json.loads(out["ipynb"].read_text())
    assert nb["nbformat"] == 4 and nb["cells"]
    # the generated script must be syntactically valid Python
    compile(out["py"].read_text(), str(out["py"]), "exec")


def test_text_states_parameters_not_results(tmp_path: Path):
    inv = _build_workspace(tmp_path)
    out = export_investigation_notebook(tmp_path, inv)
    md = _markdown_text(json.loads(out["ipynb"].read_text()))

    # design intent + structure sections are present
    assert "Can parameter K drive the widget count?" in md
    assert "### Parameters" in md
    assert "Composite structure (process-bigraph)" in md
    assert "### Acceptance criteria" in md

    # parameters and pre-registered thresholds are kept
    assert "k_param=2.0" in md and "seed=7" in md
    assert "threshold 10" in md          # acceptance criterion (pre-registered)

    # planted RESULT numbers never leak into the prose
    for tok in _RESULT_TOKENS:
        assert tok not in md, f"result number {tok} leaked into notebook text"
    # result-bearing fields/columns are dropped entirely
    assert "Final widget count" not in md      # conclusion prose
    assert "Rose to" not in md                 # result-laden viz caption
    assert "| result |" not in md.lower()      # no outcome column in criteria table


def test_process_bigraph_section_and_recipe(tmp_path: Path):
    inv = _build_workspace(tmp_path)
    out = export_investigation_notebook(tmp_path, inv)
    nb = json.loads(out["ipynb"].read_text())
    code = _code_text(nb)

    # composite-structure cell loads + realizes the process-bigraph document
    assert "build_composite_from_spec" in code
    assert "demo_pkg/composites/demo.composite.yaml" in code
    assert "processes" in code and "ports" in code
    # the run recipe (steps + interval) is wired from runs_meta
    assert "12" in code and "0.05" in code


def test_run_strategy_generic_vs_scripts(tmp_path: Path):
    inv = _build_workspace(tmp_path)

    # no scripts/ → generic process-bigraph run path
    out = export_investigation_notebook(tmp_path, inv)
    code = _code_text(json.loads(out["ipynb"].read_text()))
    assert "comp.run(12)" in code
    assert "run_study(" not in code

    # add the workspace's bespoke runner + renderer → scripts path is discovered
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "run_study_sims.py").write_text("def run_study(*a, **k): ...\n")
    (tmp_path / "scripts" / "render_study_viz.py").write_text("def _render_one(*a, **k): ...\n")
    out2 = export_investigation_notebook(tmp_path, inv)
    code2 = _code_text(json.loads(out2["ipynb"].read_text()))
    assert "from scripts.run_study_sims import run_study" in code2
    assert "run_study(" in code2


def test_viz_cells_use_iframe_isolation(tmp_path):
    """Viz HTML (which embeds Plotly scripts) is shown via an iframe srcdoc, so
    the scripts execute in JupyterLab instead of rendering blank."""
    inv = _build_workspace(tmp_path)
    out = export_investigation_notebook(tmp_path, inv)
    code = _code_text(json.loads(out["ipynb"].read_text()))
    assert "def show_viz(" in code          # setup defines the iframe helper
    assert "<iframe srcdoc=" in code
    assert "show_viz(_render_one(" in code   # viz cells route through it
    assert "display(HTML(_render_one(" not in code  # old, blank-rendering form gone
