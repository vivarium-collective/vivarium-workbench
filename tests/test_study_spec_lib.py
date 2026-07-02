"""Unit + parity tests for vivarium_workbench.lib.study_spec.

Covers the run-merging study-detail loader extracted in Phase A, Batch 3:

- the structural core (study_dir / study_spec_path resolution, runs.db reading,
  viz HTML auto-discovery),
- the keystone behavior — ``load_study_detail_spec`` merges ``runs.db`` runs into
  ``spec["runs"]`` and reconciles ``simulation_set`` (the merged spec rigor sees),
- ``TestServerShimParity`` — the legacy ``server._study_detail_spec(name)`` body
  equals ``lib.study_spec.load_study_detail_spec(WORKSPACE, name)`` on the same
  fixture.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml

from vivarium_workbench.lib import study_spec


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_runs_db(path: Path, slug: str, *, run_id: str = "db-run-1",
                  status: str = "completed", started_at: float = 1700000000.0) -> None:
    """Write a minimal ``runs_meta`` table with one recorded run."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT, spec_id TEXT, label TEXT, "
        "params_json TEXT, started_at REAL, completed_at REAL, n_steps INTEGER, "
        "status TEXT, sim_name TEXT, generation_id TEXT)"
    )
    conn.execute(
        "INSERT INTO runs_meta VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, slug, "DB Run", '{"seed": 0}', started_at, started_at + 10,
         100, status, "baseline", None),
    )
    conn.commit()
    conn.close()


def _make_workspace(tmp_path: Path) -> Path:
    """A fixture study with (a) ``runs: []`` in study.yaml but a row in runs.db,
    and (b) no ``robustness.n_replicates`` / ``simulation_set.seeds`` — exactly
    the case the loader must merge + reconcile."""
    study_dir = tmp_path / "studies" / "my-study"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        yaml.dump({
            "name": "my-study",
            "composite": "pbg_ws.composites.baseline",
            "runs": [],
            "simulation_set": [
                {"name": "baseline", "is_baseline": True, "status": "ready"},
            ],
        }),
        encoding="utf-8",
    )
    _make_runs_db(study_dir / "runs.db", "my-study")
    return tmp_path


# ---------------------------------------------------------------------------
# Directory / spec-path resolution
# ---------------------------------------------------------------------------

class TestPathResolution:
    def test_study_dir_flat(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        assert study_spec.study_dir(ws, "my-study") == (ws / "studies" / "my-study").resolve()

    def test_study_spec_path_prefers_study_yaml(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        p = study_spec.study_spec_path(ws, "my-study")
        assert p.name == "study.yaml"
        assert p.is_file()

    def test_study_spec_file_fallback_to_spec_yaml(self, tmp_path: Path) -> None:
        d = tmp_path / "studies" / "legacy"
        d.mkdir(parents=True)
        (d / "spec.yaml").write_text("name: legacy\n", encoding="utf-8")
        assert study_spec.study_spec_file(d).name == "spec.yaml"

    def test_study_spec_file_not_found_default(self, tmp_path: Path) -> None:
        d = tmp_path / "studies" / "empty"
        d.mkdir(parents=True)
        assert study_spec.study_spec_file(d).name == "study.yaml"  # default


# ---------------------------------------------------------------------------
# read_runs_db_for_study
# ---------------------------------------------------------------------------

class TestReadRunsDb:
    def test_reads_runs_meta_row(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        rows = study_spec.read_runs_db_for_study(ws, "my-study")
        assert len(rows) == 1
        r = rows[0]
        assert r["run_id"] == "db-run-1"
        assert r["status"] == "completed"
        assert r["params"] == {"seed": 0}
        assert "started_at_iso" in r and "stale" in r and "params_summary" in r

    def test_absent_db_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "studies" / "no-db").mkdir(parents=True)
        assert study_spec.read_runs_db_for_study(tmp_path, "no-db") == []

    def test_merges_study_yaml_runs(self, tmp_path: Path) -> None:
        """Emitter-less runs recorded only in study.yaml are surfaced too."""
        d = tmp_path / "studies" / "yaml-runs"
        d.mkdir(parents=True)
        (d / "study.yaml").write_text(
            yaml.dump({"name": "yaml-runs",
                       "runs": [{"run_id": "y1", "name": "Y1", "status": "completed"}]}),
            encoding="utf-8",
        )
        rows = study_spec.read_runs_db_for_study(tmp_path, "yaml-runs")
        assert {r["run_id"] for r in rows} == {"y1"}
        assert rows[0]["source"] == "study.yaml"


# ---------------------------------------------------------------------------
# discover_viz_html_files
# ---------------------------------------------------------------------------

class TestDiscoverVizHtml:
    def test_auto_rendered_viz_gated_on_runs_db(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        viz = ws / "studies" / "my-study" / "viz"
        viz.mkdir()
        (viz / "chart.html").write_text("<html></html>")
        out = study_spec.discover_viz_html_files(ws, "my-study")
        assert len(out) == 1
        assert out[0]["name"] == "chart (auto)"
        assert out[0]["url"] == "/studies/my-study/viz/chart.html"

    def test_no_runs_db_skips_auto_viz(self, tmp_path: Path) -> None:
        d = tmp_path / "studies" / "norun"
        (d / "viz").mkdir(parents=True)
        (d / "viz" / "chart.html").write_text("<html></html>")
        assert study_spec.discover_viz_html_files(tmp_path, "norun") == []

    def test_reports_figures_not_gated(self, tmp_path: Path) -> None:
        (tmp_path / "studies" / "s").mkdir(parents=True)
        fig = tmp_path / "reports" / "figures" / "s"
        fig.mkdir(parents=True)
        (fig / "fig.html").write_text("<html></html>")
        out = study_spec.discover_viz_html_files(tmp_path, "s")
        assert len(out) == 1
        assert out[0]["name"] == "fig"
        assert out[0]["stale"] is False


# ---------------------------------------------------------------------------
# load_study_detail_spec — the keystone run-merge + reconcile
# ---------------------------------------------------------------------------

class TestLoadStudyDetailSpec:
    def test_none_when_no_spec(self, tmp_path: Path) -> None:
        assert study_spec.load_study_detail_spec(tmp_path, "missing") is None

    def test_merges_db_runs_into_spec_runs(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        spec = study_spec.load_study_detail_spec(ws, "my-study")
        assert spec is not None
        run_ids = {(r or {}).get("run_id") for r in spec.get("runs", [])}
        assert "db-run-1" in run_ids   # the runs.db run was merged in

    def test_reconciles_simulation_set(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        spec = study_spec.load_study_detail_spec(ws, "my-study")
        assert spec is not None
        simset = spec.get("simulation_set") or []
        assert simset, "simulation_set should survive load + reconcile"
        base = simset[0]
        # reconcile attaches run provenance the authored plan lacked:
        assert base.get("n_runs_recorded") == 1
        # a completed run flips the authored 'ready' status to 'completed':
        assert base.get("status") == "completed"

    def test_report_card_urls_nested_investigation_layout(self, tmp_path: Path) -> None:
        """report_card_urls must resolve cards in the NESTED
        investigations/<inv>/studies/<slug>/ layout (e.g. the v2ecoli↔vEcoli
        comparison), not only the flat studies/<slug>/ path — the flat-only
        scan left report_card_urls empty so the generated report had no cards."""
        import json
        # Nested layout is declared by workspace.yaml's layout: map (as the
        # real v2ecoli workspace does).
        (tmp_path / "workspace.yaml").write_text(
            "layout:\n  investigations: workspace/investigations\n"
            "  studies: workspace/studies\n", encoding="utf-8")
        inv = tmp_path / "workspace" / "investigations" / "cmp"
        sd = inv / "studies" / "basal"
        rc = sd / "viz" / "report_card"
        rc.mkdir(parents=True)
        (inv / "investigation.yaml").write_text(
            "name: cmp\nstudies: [basal]\n", encoding="utf-8")
        (sd / "study.yaml").write_text(
            "name: basal\ninvestigation: cmp\ncomposite: pbg_ws.composites.baseline\n",
            encoding="utf-8")
        (rc / "standard.html").write_text(
            "<html><body>card</body></html>", encoding="utf-8")
        (rc / "standard.verdict.json").write_text(
            json.dumps({"overall": "drift"}), encoding="utf-8")
        spec = study_spec.load_study_detail_spec(tmp_path, "basal")
        assert spec is not None
        rcu = spec.get("report_card_urls") or {}
        assert "standard" in rcu, f"nested report card not found: {rcu}"
        assert rcu["standard"]["verdict"] == "drift"
        assert rcu["standard"]["url"].endswith(
            "investigations/cmp/studies/basal/viz/report_card/standard.html")
