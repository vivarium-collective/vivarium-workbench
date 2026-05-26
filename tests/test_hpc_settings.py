"""Unit tests for vivarium_dashboard.lib.hpc_settings.

All tests use tmp_path; no real credentials or hostnames appear anywhere.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vivarium_dashboard.lib.hpc_settings import (
    HpcNotConfiguredError,
    get_hpc_settings,
    load_hpc_settings,
    require_configured,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_env(directory: Path, content: str) -> Path:
    """Write a fake hpc.env under directory/.pbg/ and return its path."""
    pbg = directory / ".pbg"
    pbg.mkdir(parents=True, exist_ok=True)
    env_file = pbg / "hpc.env"
    env_file.write_text(content)
    return env_file


# ---------------------------------------------------------------------------
# load_hpc_settings
# ---------------------------------------------------------------------------

class TestLoadHpcSettings:
    def test_missing_env_file_returns_empty_defaults(self, tmp_path: Path) -> None:
        settings = load_hpc_settings(tmp_path)
        assert settings.slurm_submit_host == ""
        assert settings.slurm_submit_user == ""
        assert settings.slurm_partition == ""
        assert settings.hpc_repo_base_path == ""

    def test_non_sensitive_tunables_have_safe_defaults(self, tmp_path: Path) -> None:
        settings = load_hpc_settings(tmp_path)
        assert settings.singularity_cmd == "apptainer"
        assert settings.timeout_connect == 5

    def test_fields_loaded_from_env_file(self, tmp_path: Path) -> None:
        _write_env(
            tmp_path,
            "SLURM_SUBMIT_HOST=hpc.example.org\n"
            "SLURM_SUBMIT_USER=testuser\n"
            "SLURM_PARTITION=general\n"
            "HPC_REPO_BASE_PATH=/remote/repo\n",
        )
        settings = load_hpc_settings(tmp_path)
        assert settings.slurm_submit_host == "hpc.example.org"
        assert settings.slurm_submit_user == "testuser"
        assert settings.slurm_partition == "general"
        assert settings.hpc_repo_base_path == "/remote/repo"

    def test_optional_fields_loaded(self, tmp_path: Path) -> None:
        _write_env(
            tmp_path,
            "SLURM_QOS=high\n"
            "SLURM_NODE_LIST=node01,node02\n"
            "HPC_IMAGE_BASE_PATH=/images\n"
            "HPC_SIM_BASE_PATH=/sims\n"
            "HPC_LOG_BASE_PATH=/logs\n"
            "SINGULARITY_CMD=singularity\n"
            "TIMEOUT_CONNECT=10\n",
        )
        settings = load_hpc_settings(tmp_path)
        assert settings.slurm_qos == "high"
        assert settings.slurm_node_list == "node01,node02"
        assert settings.hpc_image_base_path == "/images"
        assert settings.hpc_sim_base_path == "/sims"
        assert settings.hpc_log_base_path == "/logs"
        assert settings.singularity_cmd == "singularity"
        assert settings.timeout_connect == 10

    def test_process_env_overrides_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # pydantic-settings precedence: process env > env file
        monkeypatch.setenv("SLURM_SUBMIT_HOST", "from-process-env.example.org")
        _write_env(tmp_path, "SLURM_SUBMIT_HOST=from-file.example.org\n")
        settings = load_hpc_settings(tmp_path)
        assert settings.slurm_submit_host == "from-process-env.example.org"

    def test_process_env_used_when_no_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SLURM_SUBMIT_HOST", "env-only.example.org")
        settings = load_hpc_settings(tmp_path)
        assert settings.slurm_submit_host == "env-only.example.org"

    def test_extra_fields_in_env_file_are_ignored(self, tmp_path: Path) -> None:
        _write_env(tmp_path, "UNKNOWN_FIELD=should_be_ignored\n")
        settings = load_hpc_settings(tmp_path)  # must not raise
        assert not hasattr(settings, "unknown_field")


# ---------------------------------------------------------------------------
# require_configured
# ---------------------------------------------------------------------------

class TestRequireConfigured:
    def test_raises_when_all_required_empty(self, tmp_path: Path) -> None:
        settings = load_hpc_settings(tmp_path)
        with pytest.raises(HpcNotConfiguredError) as exc_info:
            require_configured(settings)
        err = exc_info.value
        assert "slurm_submit_host" in err.missing
        assert "slurm_submit_user" in err.missing
        assert "slurm_partition" in err.missing
        assert "hpc_repo_base_path" in err.missing

    def test_raises_listing_only_empty_fields(self, tmp_path: Path) -> None:
        _write_env(
            tmp_path,
            "SLURM_SUBMIT_HOST=hpc.example.org\n"
            "SLURM_SUBMIT_USER=testuser\n",
        )
        settings = load_hpc_settings(tmp_path)
        with pytest.raises(HpcNotConfiguredError) as exc_info:
            require_configured(settings)
        err = exc_info.value
        assert "slurm_submit_host" not in err.missing
        assert "slurm_submit_user" not in err.missing
        assert "slurm_partition" in err.missing
        assert "hpc_repo_base_path" in err.missing

    def test_passes_when_all_required_set(self, tmp_path: Path) -> None:
        _write_env(
            tmp_path,
            "SLURM_SUBMIT_HOST=hpc.example.org\n"
            "SLURM_SUBMIT_USER=testuser\n"
            "SLURM_PARTITION=general\n"
            "HPC_REPO_BASE_PATH=/remote/repo\n",
        )
        settings = load_hpc_settings(tmp_path)
        require_configured(settings)  # must not raise

    def test_error_message_mentions_env_file(self, tmp_path: Path) -> None:
        settings = load_hpc_settings(tmp_path)
        with pytest.raises(HpcNotConfiguredError) as exc_info:
            require_configured(settings)
        assert "hpc.env" in str(exc_info.value)

    def test_error_exposes_missing_list(self, tmp_path: Path) -> None:
        settings = load_hpc_settings(tmp_path)
        with pytest.raises(HpcNotConfiguredError) as exc_info:
            require_configured(settings)
        assert isinstance(exc_info.value.missing, list)
        assert len(exc_info.value.missing) > 0


# ---------------------------------------------------------------------------
# get_hpc_settings (cached)
# ---------------------------------------------------------------------------

class TestGetHpcSettings:
    def test_returns_settings_for_workspace(self, tmp_path: Path) -> None:
        _write_env(tmp_path, "SLURM_SUBMIT_HOST=cached.example.org\n")
        settings = get_hpc_settings(str(tmp_path))
        assert settings.slurm_submit_host == "cached.example.org"

    def test_cache_returns_same_object(self, tmp_path: Path) -> None:
        _write_env(tmp_path, "SLURM_SUBMIT_HOST=cached.example.org\n")
        s1 = get_hpc_settings(str(tmp_path))
        s2 = get_hpc_settings(str(tmp_path))
        assert s1 is s2

    def test_different_workspaces_independent(
        self, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        ws_a = tmp_path
        ws_b = tmp_path_factory.mktemp("ws_b")
        _write_env(ws_a, "SLURM_SUBMIT_HOST=host-a.example.org\n")
        _write_env(ws_b, "SLURM_SUBMIT_HOST=host-b.example.org\n")
        sa = get_hpc_settings(str(ws_a))
        sb = get_hpc_settings(str(ws_b))
        assert sa.slurm_submit_host == "host-a.example.org"
        assert sb.slurm_submit_host == "host-b.example.org"
        assert sa is not sb


# ---------------------------------------------------------------------------
# HpcNotConfiguredError
# ---------------------------------------------------------------------------

class TestHpcNotConfiguredError:
    def test_is_runtime_error(self) -> None:
        err = HpcNotConfiguredError(["slurm_submit_host"])
        assert isinstance(err, RuntimeError)

    def test_missing_stored(self) -> None:
        missing = ["slurm_submit_host", "slurm_partition"]
        err = HpcNotConfiguredError(missing)
        assert err.missing == missing

    def test_str_contains_field_names(self) -> None:
        err = HpcNotConfiguredError(["slurm_submit_host", "slurm_partition"])
        msg = str(err)
        assert "slurm_submit_host" in msg
        assert "slurm_partition" in msg
