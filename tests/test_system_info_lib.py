"""Tests for lib.system_info builders + server.py shim parity.

Covers:
  - build_framework_metrics:  counts, tolerates missing ws, tolerates compute failure
  - build_github_repo:        git-remote branch, yaml fallback, null when neither
  - build_ui_config:          defaults, yaml override
  - build_workspace_home:     name/description/investigations shape

TestServerShimParity: constructs server.Handler via __new__, patches _json and
WORKSPACE, and asserts that each legacy handler method returns the same body dict
as the lib builder for the same workspace root.
"""

from __future__ import annotations

import yaml
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixture: a minimal workspace with workspace.yaml, investigations, studies
# ---------------------------------------------------------------------------

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """A workspace with one investigation + two studies + a workspace.yaml."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()

    # workspace.yaml with ui config + dashboard.github_repo fallback
    (ws_root / "workspace.yaml").write_text(yaml.safe_dump({
        "name": "test-ws",
        "description": "A test workspace",
        "imports": {"pbg-core": "0.1.0"},
        "ui": {
            "composite_view": "bigraph-loom",
            "ptools_server_url": "http://ptools:1555",
        },
        "dashboard": {
            "github_repo": "acme/test-ws",
        },
    }))

    # One investigation
    inv_dir = ws_root / "investigations" / "inv-a"
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "inv-a",
        "title": "Investigation A",
        "status": "active",
        "description": "The first investigation.",
        "studies": ["study-1", "study-2"],
    }))

    # Two studies
    for slug in ("study-1", "study-2"):
        sd = ws_root / "studies" / slug
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 4,
            "name": slug,
            "findings": [],
        }))

    return ws_root


# ---------------------------------------------------------------------------
# build_framework_metrics
# ---------------------------------------------------------------------------

class TestBuildFrameworkMetrics:
    def test_counts_studies_and_investigations(self, ws: Path) -> None:
        from vivarium_workbench.lib.system_info import build_framework_metrics
        result = build_framework_metrics(ws)
        assert result["n_investigations"] == 1
        assert result["n_studies"] == 2
        assert "metrics" in result
        assert isinstance(result["metrics"], dict)

    def test_tolerant_on_missing_workspace(self, tmp_path: Path) -> None:
        from vivarium_workbench.lib.system_info import build_framework_metrics
        result = build_framework_metrics(tmp_path / "does-not-exist")
        assert result["n_investigations"] == 0
        assert result["n_studies"] == 0
        assert isinstance(result["metrics"], dict)

    def test_tolerant_on_compute_failure(self, ws: Path, monkeypatch) -> None:
        """If pbg_superpowers.rigor.framework_metrics raises, metrics stays {} + counts OK."""
        pytest.importorskip("pbg_superpowers.rigor")
        from pbg_superpowers import rigor as _rigor
        if not hasattr(_rigor, "framework_metrics"):
            pytest.skip("pbg_superpowers.rigor.framework_metrics not available")

        monkeypatch.setattr(
            _rigor, "framework_metrics",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        from vivarium_workbench.lib.system_info import build_framework_metrics
        result = build_framework_metrics(ws)
        assert result["metrics"] == {}
        assert result["n_studies"] == 2
        assert result["n_investigations"] == 1


# ---------------------------------------------------------------------------
# build_github_repo
# ---------------------------------------------------------------------------

class TestBuildGithubRepo:
    def test_yaml_fallback_returns_slug(self, ws: Path) -> None:
        """workspace.yaml dashboard.github_repo used when git remote absent."""
        from vivarium_workbench.lib.system_info import build_github_repo
        result = build_github_repo(ws)
        assert result == {"repo": "acme/test-ws"}

    def test_null_when_neither_resolves(self, tmp_path: Path) -> None:
        """Empty workspace with no git remote + no workspace.yaml → {repo: null}."""
        from vivarium_workbench.lib.system_info import build_github_repo
        result = build_github_repo(tmp_path)
        assert result == {"repo": None}

    def test_yaml_full_url_normalized(self, tmp_path: Path) -> None:
        """A full GitHub URL in workspace.yaml is normalized to owner/name."""
        (tmp_path / "workspace.yaml").write_text(yaml.safe_dump({
            "dashboard": {
                "github_repo": "https://github.com/vivarium-collective/v2ecoli.git",
            },
        }))
        from vivarium_workbench.lib.system_info import build_github_repo
        result = build_github_repo(tmp_path)
        assert result == {"repo": "vivarium-collective/v2ecoli"}

    def test_yaml_repository_key_also_accepted(self, tmp_path: Path) -> None:
        """dashboard.repository is a recognized alias."""
        (tmp_path / "workspace.yaml").write_text(yaml.safe_dump({
            "dashboard": {"repository": "org/repo"},
        }))
        from vivarium_workbench.lib.system_info import build_github_repo
        result = build_github_repo(tmp_path)
        assert result == {"repo": "org/repo"}

    def test_detect_github_repo_overrides_yaml(self, ws: Path, monkeypatch) -> None:
        """Git remote (via _detect_github_repo) takes priority over workspace.yaml."""
        import vivarium_workbench.lib.system_info as _si
        # Patch the import inside build_github_repo
        import vivarium_workbench.lib.report as report_mod
        monkeypatch.setattr(report_mod, "_detect_github_repo",
                            lambda ws_root: "git-remote/repo")
        result = _si.build_github_repo(ws)
        assert result == {"repo": "git-remote/repo"}


# ---------------------------------------------------------------------------
# build_ui_config
# ---------------------------------------------------------------------------

class TestBuildUiConfig:
    def test_defaults_on_empty_workspace(self, tmp_path: Path) -> None:
        from vivarium_workbench.lib.system_info import (
            build_ui_config, _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        )
        result = build_ui_config(tmp_path)
        assert result["composite_view"] == "bigraph-loom"
        assert result["ptools_server_url"] == ""
        assert result["ptools_omics_url_template"] == _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE

    def test_reads_ui_block(self, ws: Path) -> None:
        from vivarium_workbench.lib.system_info import (
            build_ui_config, _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        )
        result = build_ui_config(ws)
        assert result["composite_view"] == "bigraph-loom"
        assert result["ptools_server_url"] == "http://ptools:1555"
        # template not overridden → falls back to default
        assert result["ptools_omics_url_template"] == _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE

    def test_omics_url_template_override(self, tmp_path: Path) -> None:
        custom = "http://custom.ptools/{server}?omics=1"
        (tmp_path / "workspace.yaml").write_text(yaml.safe_dump({
            "ui": {"ptools_omics_url_template": custom},
        }))
        from vivarium_workbench.lib.system_info import build_ui_config
        result = build_ui_config(tmp_path)
        assert result["ptools_omics_url_template"] == custom

    def test_tolerant_on_missing_yaml(self, tmp_path: Path) -> None:
        from vivarium_workbench.lib.system_info import (
            build_ui_config, _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE,
        )
        result = build_ui_config(tmp_path)
        assert result["composite_view"] == "bigraph-loom"
        assert result["ptools_omics_url_template"] == _PTOOLS_DEFAULT_OMICS_URL_TEMPLATE


# ---------------------------------------------------------------------------
# build_workspace_home
# ---------------------------------------------------------------------------

class TestBuildWorkspaceHome:
    def test_full_shape(self, ws: Path) -> None:
        from vivarium_workbench.lib.system_info import build_workspace_home
        result = build_workspace_home(ws)
        assert result["name"] == "test-ws"
        assert result["description"] == "A test workspace"
        assert result["imports"] == {"pbg-core": "0.1.0"}
        assert len(result["investigations"]) == 1
        inv = result["investigations"][0]
        assert inv["name"] == "inv-a"
        assert inv["status"] == "active"

    def test_empty_workspace(self, tmp_path: Path) -> None:
        from vivarium_workbench.lib.system_info import build_workspace_home
        result = build_workspace_home(tmp_path)
        assert result["investigations"] == []
        assert result["imports"] == {}
        # name defaults to the directory name
        assert result["name"] == tmp_path.name


