"""Workspace-scoped HPC / SLURM configuration.

Loaded from workspace/.pbg/hpc.env (gitignored — never committed).
All sensitive fields default to "" so the dashboard boots cleanly on
workspaces that have no HPC backend configured; dispatch code calls
require_configured() before opening any SSH connection.

Pattern mirrors ~/sms/sms-api/sms_api/config.py.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class HpcSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # SSH connection — all sensitive, no real defaults
    slurm_submit_host: str = ""       # login node hostname or IP
    slurm_submit_user: str = ""       # SSH username on the HPC
    slurm_submit_key_path: str = ""   # path to private key file
    slurm_submit_known_hosts: str = ""  # path to known_hosts file
                                        # generate: ssh-keyscan <host> > that file

    # SLURM job submission
    slurm_partition: str = ""
    slurm_qos: str = ""
    slurm_node_list: str = ""         # optional --nodelist constraint

    # Remote filesystem base paths (all on the HPC, sensitive)
    hpc_image_base_path: str = ""     # where .sif images are stored
    hpc_sim_base_path: str = ""       # where simulation outputs land
    hpc_log_base_path: str = ""       # where sbatch --output logs go
    hpc_repo_base_path: str = ""      # rsync target for workspace files

    # Non-sensitive tunables with safe defaults
    singularity_cmd: str = "apptainer"  # or "singularity"
    timeout_connect: int = 5            # SSH ConnectTimeout seconds
    apptainer_tmpdir: str = "/tmp/apptainer"  # noqa: S108 — intentional /tmp for fast
                                              # metadata ops during builds (avoids NFS).
                                              # mirrors sms_api/config.py:apptainer_tmpdir


class HpcNotConfiguredError(RuntimeError):
    """Raised when required HPC settings are missing."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(
            f"HPC settings not configured: {missing}. "
            "Fill in workspace/.pbg/hpc.env — see .pbg/hpc.env.example"
        )


_REQUIRED_FIELDS: tuple[str, ...] = (
    "slurm_submit_host",
    "slurm_submit_user",
    "slurm_partition",
    "hpc_repo_base_path",
)


def require_configured(settings: HpcSettings) -> None:
    """Raise HpcNotConfiguredError if any required field is empty."""
    missing = [f for f in _REQUIRED_FIELDS if not getattr(settings, f)]
    if missing:
        raise HpcNotConfiguredError(missing)


def load_hpc_settings(workspace: Path) -> HpcSettings:
    """Load HpcSettings from workspace/.pbg/hpc.env.

    Falls back to process environment variables when the file is absent,
    which lets CI and container deployments inject config via env without
    needing a file on disk.
    """
    env_file = workspace / ".pbg" / "hpc.env"
    if env_file.is_file():
        return HpcSettings(_env_file=str(env_file))
    return HpcSettings()


@lru_cache(maxsize=8)
def get_hpc_settings(workspace_str: str) -> HpcSettings:
    """Cached HpcSettings for the given workspace path string.

    Keyed by string so the lru_cache works (Path is not hashable by value
    across identical-content objects in all Python versions).
    """
    return load_hpc_settings(Path(workspace_str))
