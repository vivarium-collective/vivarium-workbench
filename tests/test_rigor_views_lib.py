"""Unit + parity tests for vivarium_dashboard.lib.rigor_views.

Covers the rigor builder ported in Phase A, Batch 3 (backed by the
run-merging ``lib.study_spec.load_study_detail_spec``):

- ``build_investigation_rigor`` happy + error paths,
- ``TestServerShimParity`` — the legacy stdlib rigor handler delegates to this
  builder and must produce byte-identical bodies + status codes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from vivarium_dashboard.lib import rigor_views


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_runs_db(path: Path, slug: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE runs_meta (run_id TEXT, spec_id TEXT, label TEXT, "
        "params_json TEXT, started_at REAL, completed_at REAL, n_steps INTEGER, "
        "status TEXT, sim_name TEXT, generation_id TEXT)"
    )
    conn.execute(
        "INSERT INTO runs_meta VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("db-run-1", slug, "DB Run", '{"seed": 0}', 1700000000.0, 1700000010.0,
         100, "completed", "baseline", None),
    )
    conn.commit()
    conn.close()


def _make_workspace(tmp_path: Path) -> Path:
    """An investigation with one member study that has a runs.db run."""
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

    inv_dir = tmp_path / "investigations" / "my-inv"
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(
        yaml.dump({
            "name": "my-inv",
            "title": "My Investigation",
            "studies": ["my-study"],
        }),
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# build_investigation_rigor
# ---------------------------------------------------------------------------

class TestBuildInvestigationRigor:
    def test_happy_path(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        out = rigor_views.build_investigation_rigor(ws, "my-inv")
        assert isinstance(out, dict)
        assert "error" not in out

    def test_missing_raises_400(self, tmp_path: Path) -> None:
        with pytest.raises(rigor_views.RigorViewError) as exc:
            rigor_views.build_investigation_rigor(tmp_path, None)
        assert exc.value.status == 400
        assert exc.value.body == {"error": "missing ?investigation="}

    def test_not_found_raises_404(self, tmp_path: Path) -> None:
        with pytest.raises(rigor_views.RigorViewError) as exc:
            rigor_views.build_investigation_rigor(tmp_path, "nope")
        assert exc.value.status == 404
        assert exc.value.body == {"error": "investigation not found"}

    def test_unreadable_yaml_returns_200_error(self, tmp_path: Path) -> None:
        """A malformed investigation.yaml is a 200-shaped error, not a raise."""
        inv_dir = tmp_path / "investigations" / "bad"
        inv_dir.mkdir(parents=True)
        (inv_dir / "investigation.yaml").write_bytes(b": invalid: yaml: {{{")
        out = rigor_views.build_investigation_rigor(tmp_path, "bad")
        assert "error" in out
        assert out["error"].startswith("unreadable investigation.yaml")


# ---------------------------------------------------------------------------
# RigorViewError
# ---------------------------------------------------------------------------

class TestRigorViewError:
    def test_body_and_status(self) -> None:
        err = rigor_views.RigorViewError({"error": "oops"}, 404)
        assert err.status == 404
        assert err.body == {"error": "oops"}
        assert str(err) == "oops"


# ---------------------------------------------------------------------------
# TestServerShimParity: legacy handler body == lib-builder body
# ---------------------------------------------------------------------------

class TestServerShimParity:
    @staticmethod
    def _invoke(monkeypatch: Any, ws_root: Path, method_name: str, path: str) -> dict:
        import vivarium_dashboard.server as server
        monkeypatch.setattr(server, "WORKSPACE", ws_root)
        server._WP_CACHE.clear()
        handler = server.Handler.__new__(server.Handler)
        captured: dict = {}

        def _fake_json(data: dict, code: int) -> None:
            captured["body"] = data
            captured["status"] = code

        handler._json = _fake_json  # type: ignore[method-assign]
        handler.path = path
        getattr(handler, method_name)()
        return captured

    # --- investigation-rigor ---

    def test_investigation_rigor_200_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        captured = self._invoke(
            monkeypatch, ws, "_get_investigation_rigor",
            "/api/investigation-rigor?investigation=my-inv")
        lib_body = rigor_views.build_investigation_rigor(ws, "my-inv")
        assert captured["status"] == 200
        assert captured["body"] == lib_body

    def test_investigation_rigor_400_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        captured = self._invoke(
            monkeypatch, tmp_path, "_get_investigation_rigor", "/api/investigation-rigor")
        assert captured["status"] == 400
        assert captured["body"] == {"error": "missing ?investigation="}

    def test_investigation_rigor_404_parity(self, monkeypatch: Any, tmp_path: Path) -> None:
        captured = self._invoke(
            monkeypatch, tmp_path, "_get_investigation_rigor",
            "/api/investigation-rigor?investigation=nope")
        assert captured["status"] == 404
        assert captured["body"] == {"error": "investigation not found"}
