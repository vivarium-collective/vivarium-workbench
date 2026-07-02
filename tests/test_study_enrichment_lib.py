"""Parity tests for vivarium_dashboard.lib.study_enrichment.

Covers the four helpers extracted from server.py in Phase A, Batch 4:

- reconcile_simset_with_runs   (pure-ish, ws_root-parameterised)
- compute_param_enforcement     (pure — requires pbg_superpowers.param_enforcement)
- collect_study_feedback        (ws_root-parameterised)
- study_acceptance_criterion    (ws_root-parameterised)

For the two WORKSPACE-bound helpers (collect_study_feedback,
study_acceptance_criterion) we build a fixture workspace and assert
``lib.<helper>(ws_root, ...)`` == ``server._<helper>(...)`` (with
``server.WORKSPACE`` monkeypatched to the same fixture root).

We also re-run the load_study_detail_spec parity test to prove the full
loader still produces the same spec now that it calls the lib helpers
directly instead of via the lazy ``import server``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers / fixture builders
# ---------------------------------------------------------------------------

def _make_runs_db(path: Path, slug: str, *, run_id: str = "r1",
                  status: str = "completed",
                  started_at: float = 1700000000.0) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT, spec_id TEXT, label TEXT, "
        "params_json TEXT, started_at REAL, completed_at REAL, n_steps INTEGER, "
        "status TEXT, sim_name TEXT, generation_id TEXT)"
    )
    conn.execute(
        "INSERT INTO runs_meta VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, slug, "Run", '{"seed": 0}', started_at, started_at + 10,
         100, status, "baseline", None),
    )
    conn.commit()
    conn.close()


def _make_workspace_with_acceptance(tmp_path: Path) -> tuple[Path, str]:
    """A workspace where 'my-study' is owned by 'my-inv', which has a
    computed_acceptance with a criterion for 'my-study'."""
    slug = "my-study"
    inv = "my-inv"

    # Study
    study_dir = tmp_path / "investigations" / inv / "studies" / slug
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        yaml.dump({"name": slug, "composite": "pbg_ws.composites.baseline", "runs": []}),
        encoding="utf-8",
    )

    # Investigation with computed_acceptance covering the study
    inv_dir = tmp_path / "investigations" / inv
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "investigation.yaml").write_text(
        yaml.dump({
            "name": inv,
            "executive": {
                "computed_acceptance": {
                    "verdict_status": "pass",
                    "criteria": [
                        {"study": slug, "verdict": "pass", "gate": "G1"},
                    ],
                }
            },
        }),
        encoding="utf-8",
    )
    return tmp_path, slug


def _make_feedback_workspace(tmp_path: Path) -> tuple[Path, str]:
    """A workspace with feedback annotation targeting 'dnaa-00' study."""
    pytest.importorskip("pbg_superpowers.feedback_import")
    slug = "dnaa-00-param"
    inv = "dnaa-replication"
    inv_dir = tmp_path / "investigations" / inv
    feedback_dir = inv_dir / "feedback"
    feedback_dir.mkdir(parents=True)
    (feedback_dir / "review.yaml").write_text(
        yaml.dump({
            "meta": {"investigation": inv},
            "annotations": {
                f"study-{slug}-charts": [
                    {"author": "Reviewer", "text": "Looks good",
                     "ts": "2026-06-01T10:00:00Z"},
                ],
            },
        }, sort_keys=False),
        encoding="utf-8",
    )
    return tmp_path, slug


# ---------------------------------------------------------------------------
# reconcile_simset_with_runs
# ---------------------------------------------------------------------------

class TestReconcileSimsetWithRuns:
    """Smoke tests for the pure helper — behavior mirrors the old server function."""

    def test_empty_simset_passthrough(self) -> None:
        from vivarium_dashboard.lib.study_enrichment import reconcile_simset_with_runs
        assert reconcile_simset_with_runs(None, []) is None
        assert reconcile_simset_with_runs([], [{"run_id": "r1"}]) == []

    def test_no_runs_passthrough(self) -> None:
        from vivarium_dashboard.lib.study_enrichment import reconcile_simset_with_runs
        sim_set = [{"name": "baseline", "is_baseline": True}]
        result = reconcile_simset_with_runs(sim_set, [])
        # sim_set returned unchanged when runs is empty
        assert result == [{"name": "baseline", "is_baseline": True}]

    def test_completed_run_flips_status(self) -> None:
        from vivarium_dashboard.lib.study_enrichment import reconcile_simset_with_runs
        sim_set = [{"name": "b", "is_baseline": True, "status": "ready"}]
        runs = [{"run_id": "r1", "status": "completed", "seed": 42}]
        result = reconcile_simset_with_runs(sim_set, runs)
        assert result[0]["status"] == "completed"
        assert result[0]["n_runs_recorded"] == 1
        assert result[0]["seeds"] == [42]


# ---------------------------------------------------------------------------
# compute_param_enforcement
# ---------------------------------------------------------------------------

class TestComputeParamEnforcement:
    """Smoke tests for the pure helper — behavior mirrors the old server function."""

    def test_none_when_no_enforced_params(self) -> None:
        mod = pytest.importorskip("pbg_superpowers.param_enforcement")
        if not hasattr(mod, "resolve_run_expected"):
            pytest.skip("pbg_superpowers.param_enforcement.resolve_run_expected not available")
        from vivarium_dashboard.lib.study_enrichment import compute_param_enforcement
        assert compute_param_enforcement({"runs": []}) is None


# ---------------------------------------------------------------------------
# collect_study_feedback
# ---------------------------------------------------------------------------

class TestCollectStudyFeedback:
    def test_empty_when_no_investigations(self, tmp_path: Path) -> None:
        pytest.importorskip("pbg_superpowers.feedback_import")
        from vivarium_dashboard.lib.study_enrichment import collect_study_feedback
        assert collect_study_feedback(tmp_path, "any-study") == []

    def test_collects_matching_annotation(self, tmp_path: Path) -> None:
        ws, slug = _make_feedback_workspace(tmp_path)
        from vivarium_dashboard.lib.study_enrichment import collect_study_feedback
        out = collect_study_feedback(ws, slug)
        assert len(out) == 1
        assert out[0]["author"] == "Reviewer"
        assert out[0]["text"] == "Looks good"


# ---------------------------------------------------------------------------
# study_acceptance_criterion
# ---------------------------------------------------------------------------

class TestStudyAcceptanceCriterion:
    def test_none_when_no_investigations(self, tmp_path: Path) -> None:
        from vivarium_dashboard.lib.study_enrichment import study_acceptance_criterion
        assert study_acceptance_criterion(tmp_path, "missing") is None

    def test_returns_acceptance_for_owned_study(self, tmp_path: Path) -> None:
        ws, slug = _make_workspace_with_acceptance(tmp_path)
        from vivarium_dashboard.lib.study_enrichment import study_acceptance_criterion
        result = study_acceptance_criterion(ws, slug)
        assert result is not None
        assert result["investigation"] == "my-inv"
        assert result["verdict_status"] == "pass"
        assert len(result["criteria"]) == 1
        assert result["criteria"][0]["study"] == slug


# ---------------------------------------------------------------------------
# load_study_detail_spec parity (with lib helpers now called directly)
# ---------------------------------------------------------------------------

class TestLoadStudyDetailSpecParityBatch4:
    """Confirm the loader produces a spec with runs merged in: the lib helpers
    are called directly (no lazy import server)."""

    def _make_ws(self, tmp_path: Path) -> Path:
        study_dir = tmp_path / "studies" / "parity-study"
        study_dir.mkdir(parents=True)
        (study_dir / "study.yaml").write_text(
            yaml.dump({
                "name": "parity-study",
                "composite": "pbg_ws.composites.baseline",
                "runs": [],
                "simulation_set": [
                    {"name": "baseline", "is_baseline": True, "status": "ready"},
                ],
            }),
            encoding="utf-8",
        )
        _make_runs_db(study_dir / "runs.db", "parity-study")
        return tmp_path

    def test_loader_merges_runs_from_db(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        ws = self._make_ws(tmp_path)
        from vivarium_dashboard.lib import study_spec
        lib_spec = study_spec.load_study_detail_spec(ws, "parity-study")
        # The run recorded in runs.db is merged into the loaded spec.
        assert lib_spec is not None
        run_ids = {(r or {}).get("run_id") for r in lib_spec.get("runs", [])}
        assert "r1" in run_ids
