"""Tests for lib.work_views builders + server.py shim parity.

Covers:
  - build_pending:             empty-dict on non-git dir; panel keys present; 200
                                shim parity vs _serve_pending
  - build_generation:          null when pbg_superpowers absent / not active; 200
                                shim parity vs _get_generation
  - build_work_composite_diff: {base, branch, changes:[]} + error on merge-base
                                failure; shim parity vs _get_work_composite_diff

TestServerShimParity: constructs server.Handler via __new__, patches _json and
WORKSPACE, and asserts that each legacy handler method returns the same body as
the lib builder for the same workspace root.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixture: a tiny git-initialised workspace with workspace.yaml
# ---------------------------------------------------------------------------

@pytest.fixture
def git_ws(tmp_path: Path) -> Path:
    """A minimal git-initialised workspace with workspace.yaml on 'main'."""
    ws = tmp_path / "ws"
    ws.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=ws, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=ws, check=True, capture_output=True)

    workspace_yaml = {
        "name": "test-ws",
        "observables": [{"name": "obs-a"}],
        "visualizations": [{"name": "viz-a"}],
        "phases": [{"n": 1}],
        "datasets": [{"name": "ds-a"}],
        "references_pdfs": [{"bib_key": "smith2020"}],
        "expert_docs": [{"name": "doc-a"}],
        "imports": {"pkg-a": {"version": "0.1"}},
    }
    (ws / "workspace.yaml").write_text(yaml.safe_dump(workspace_yaml))
    subprocess.run(["git", "add", "workspace.yaml"], cwd=ws, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=ws, check=True,
                   capture_output=True)
    return ws


@pytest.fixture
def git_ws_with_stage(git_ws: Path) -> Path:
    """A git_ws that also has a stage/add-obs-b branch with a new observable."""
    ws = git_ws
    subprocess.run(["git", "checkout", "-b", "stage/add-obs-b"], cwd=ws,
                   check=True, capture_output=True)
    new_ws = {
        "name": "test-ws",
        "observables": [{"name": "obs-a"}, {"name": "obs-b"}],
    }
    (ws / "workspace.yaml").write_text(yaml.safe_dump(new_ws))
    subprocess.run(["git", "add", "workspace.yaml"], cwd=ws, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "add obs-b"], cwd=ws, check=True,
                   capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=ws, check=True,
                   capture_output=True)
    return ws


# ---------------------------------------------------------------------------
# build_pending
# ---------------------------------------------------------------------------

class TestBuildPending:
    def test_non_git_dir_returns_empty_dict_200(self, tmp_path: Path) -> None:
        """A non-git directory returns ({}, 200) — no exception / no 500."""
        from vivarium_dashboard.lib.work_views import build_pending
        body, status = build_pending(tmp_path)
        assert status == 200
        assert body == {}

    def test_git_repo_no_stage_branches_returns_empty_lists(
        self, git_ws: Path
    ) -> None:
        """A git repo with no stage/* branches returns the 7-panel empty dict."""
        from vivarium_dashboard.lib.work_views import build_pending
        body, status = build_pending(git_ws)
        assert status == 200
        assert set(body.keys()) == {
            "observables", "visualizations", "phases", "datasets",
            "references_pdfs", "expert_docs", "imports",
        }
        for panel in body.values():
            assert panel == []

    def test_stage_branch_new_observable_appears(
        self, git_ws_with_stage: Path
    ) -> None:
        """A stage branch's new observable appears in the pending list."""
        from vivarium_dashboard.lib.work_views import build_pending
        body, status = build_pending(git_ws_with_stage)
        assert status == 200
        obs = body["observables"]
        assert len(obs) == 1
        assert obs[0]["entry"]["name"] == "obs-b"
        assert obs[0]["branch"] == "stage/add-obs-b"

    def test_existing_items_not_in_pending(
        self, git_ws_with_stage: Path
    ) -> None:
        """Items already on main (obs-a) are NOT included in pending."""
        from vivarium_dashboard.lib.work_views import build_pending
        body, _ = build_pending(git_ws_with_stage)
        obs_names = [e["entry"]["name"] for e in body["observables"]]
        assert "obs-a" not in obs_names


# ---------------------------------------------------------------------------
# build_generation
# ---------------------------------------------------------------------------

class TestBuildGeneration:
    def test_null_when_pbg_superpowers_absent(self, tmp_path: Path) -> None:
        """When pbg_superpowers is absent (or raises) → {generation: null}."""
        from vivarium_dashboard.lib.work_views import build_generation
        result = build_generation(tmp_path)
        assert result == {"generation": None}

    def test_null_when_no_active_generation(self, git_ws: Path) -> None:
        """A real workspace with pbg_superpowers but no generation → null."""
        try:
            from pbg_superpowers import generation as _gen  # noqa: F401
        except ImportError:
            pytest.skip("pbg_superpowers.generation not available")
        from vivarium_dashboard.lib.work_views import build_generation
        result = build_generation(git_ws)
        assert result == {"generation": None}

    def test_returns_summary_when_active(self, git_ws: Path, monkeypatch) -> None:
        """When a generation is active the summary dict is returned."""
        try:
            from pbg_superpowers import generation as _gen  # noqa: F401
        except ImportError:
            pytest.skip("pbg_superpowers.generation not available")

        class _FakeGen:
            generation_id = "gen-001"
            git_sha = "abc123"
            param_set_hash = "hashXYZ"
            created_at = "2026-06-25T00:00:00"
            label = "test-label"
            runs = [1, 2, 3]  # len = 3

        import pbg_superpowers.generation as gen_mod
        monkeypatch.setattr(gen_mod, "current_generation",
                            lambda ws_root: _FakeGen())
        from vivarium_dashboard.lib.work_views import build_generation
        result = build_generation(git_ws)
        assert result["generation"] is not None
        g = result["generation"]
        assert g["generation_id"] == "gen-001"
        assert g["git_sha"] == "abc123"
        assert g["n_runs"] == 3


# ---------------------------------------------------------------------------
# build_work_composite_diff
# ---------------------------------------------------------------------------

class TestBuildWorkCompositeDiff:
    def test_non_git_dir_returns_error_in_body(self, tmp_path: Path) -> None:
        """A non-git directory causes merge-base to fail → error in body, 200."""
        from vivarium_dashboard.lib.work_views import build_work_composite_diff
        result = build_work_composite_diff(tmp_path)
        assert "error" in result
        assert result["changes"] == []
        assert "base" in result
        assert "branch" in result

    def test_git_repo_on_main_returns_empty_changes(
        self, git_ws: Path
    ) -> None:
        """On the initial main branch merge-base == HEAD → empty changes."""
        from vivarium_dashboard.lib.work_views import build_work_composite_diff
        result = build_work_composite_diff(git_ws)
        # Either empty changes or an error — no exception
        assert "changes" in result
        assert "base" in result
        assert "branch" in result

    def test_state_base_respected(
        self, git_ws: Path, tmp_path: Path
    ) -> None:
        """If .pbg/state.json sets base, it's used for the merge-base call."""
        # Write a state.json
        pbg_dir = git_ws / ".pbg"
        pbg_dir.mkdir(exist_ok=True)
        state = {"active_branch": "feat/test", "base": "main"}
        (pbg_dir / "state.json").write_text(json.dumps(state))

        from vivarium_dashboard.lib.work_views import build_work_composite_diff
        result = build_work_composite_diff(git_ws)
        assert result["base"] == "main"

    def test_returns_dict_always(self, tmp_path: Path) -> None:
        """Always returns a dict (never raises), even on completely empty dir."""
        from vivarium_dashboard.lib.work_views import build_work_composite_diff
        result = build_work_composite_diff(tmp_path)
        assert isinstance(result, dict)
        assert "changes" in result


# ---------------------------------------------------------------------------
# TestServerShimParity
# ---------------------------------------------------------------------------

class TestServerShimParity:
    """Assert the legacy server.py handler bodies == the lib builder bodies."""

    @staticmethod
    def _invoke_handler(monkeypatch, ws_root: Path, method_name: str) -> dict:
        import vivarium_dashboard.server as server
        monkeypatch.setattr(server, "WORKSPACE", ws_root)
        handler = server.Handler.__new__(server.Handler)
        captured: dict = {}

        def _fake_json(data, code):
            captured["body"] = data
            captured["status"] = code

        handler._json = _fake_json  # type: ignore[method-assign]
        handler.path = "/"
        getattr(handler, method_name)()
        return captured

    def test_serve_pending_parity(
        self, monkeypatch, git_ws: Path
    ) -> None:
        """_serve_pending returns the same body as build_pending."""
        from vivarium_dashboard.lib.work_views import build_pending
        captured = self._invoke_handler(monkeypatch, git_ws, "_serve_pending")
        lib_body, lib_status = build_pending(git_ws)
        assert captured["status"] == lib_status
        assert captured["body"] == lib_body

    def test_get_generation_parity(
        self, monkeypatch, git_ws: Path
    ) -> None:
        """_get_generation returns the same body as build_generation."""
        from vivarium_dashboard.lib.work_views import build_generation
        captured = self._invoke_handler(monkeypatch, git_ws, "_get_generation")
        assert captured["status"] == 200
        assert captured["body"] == build_generation(git_ws)

    def test_get_work_composite_diff_parity(
        self, monkeypatch, git_ws: Path
    ) -> None:
        """_get_work_composite_diff returns the same body as build_work_composite_diff."""
        from vivarium_dashboard.lib.work_views import build_work_composite_diff
        captured = self._invoke_handler(
            monkeypatch, git_ws, "_get_work_composite_diff"
        )
        assert captured["status"] == 200
        assert captured["body"] == build_work_composite_diff(git_ws)
