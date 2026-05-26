"""SSH + SLURM dispatch layer for HPC compute backends (Todo #10).

Uses stdlib subprocess + SSH ControlMaster sockets for connection reuse.
No extra runtime dependencies — no paramiko, no asyncssh.

All public functions call require_configured() before touching the network,
so callers don't need to validate settings.  Sensitive values (key path,
username) are redacted via _mask() before any log emit or error propagation.

Pattern mirrors ~/sms/sms-api/sms_api/common/hpc/ (squeue → scontrol
fallback, sbatch submission) adapted for synchronous subprocess use.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import uuid
from pathlib import Path

from .hpc_settings import HpcSettings, require_configured

log = logging.getLogger(__name__)

# ControlMaster sockets: ~/.pbg/hpc/sockets/<user>@<host>.sock
_SOCKET_DIR = Path.home() / ".pbg" / "hpc" / "sockets"

# squeue format: job_id, name, state, reason, elapsed
_SQUEUE_FMT = "%i %j %T %R %M"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _socket_path(settings: HpcSettings) -> Path:
    slug = re.sub(
        r"[^A-Za-z0-9@._-]", "_",
        f"{settings.slurm_submit_user}@{settings.slurm_submit_host}",
    )
    return _SOCKET_DIR / f"{slug}.sock"


def _mask(text: str, settings: HpcSettings) -> str:
    """Redact sensitive settings values from ``text`` before logging."""
    if settings.slurm_submit_key_path:
        text = text.replace(settings.slurm_submit_key_path, "<key_path>")
    # Only mask user when at least 3 chars (avoids over-redaction of short words).
    if settings.slurm_submit_user and len(settings.slurm_submit_user) >= 3:
        text = text.replace(settings.slurm_submit_user, "<user>")
    return text


def _base_ssh_opts(settings: HpcSettings) -> list[str]:
    opts: list[str] = [
        "-o", f"ConnectTimeout={settings.timeout_connect}",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
    ]
    if settings.slurm_submit_key_path:
        opts += ["-i", settings.slurm_submit_key_path]
    if settings.slurm_submit_known_hosts:
        opts += ["-o", f"UserKnownHostsFile={settings.slurm_submit_known_hosts}"]
    return opts


def open_socket(settings: HpcSettings) -> Path:
    """Ensure a live ControlMaster socket exists and return its path.

    Safe to call multiple times — checks liveness before opening.
    Raises RuntimeError if SSH connection fails.
    """
    require_configured(settings)
    _SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    sock = _socket_path(settings)
    target = f"{settings.slurm_submit_user}@{settings.slurm_submit_host}"

    if sock.exists():
        chk = subprocess.run(
            ["ssh", "-S", str(sock), "-O", "check", target],
            capture_output=True, timeout=5,
        )
        if chk.returncode == 0:
            return sock
        sock.unlink(missing_ok=True)

    args = (
        ["ssh"]
        + _base_ssh_opts(settings)
        + ["-N", "-f", "-M", "-S", str(sock),
           "-o", "ControlPersist=300",
           target]
    )
    r = subprocess.run(
        args, capture_output=True,
        timeout=settings.timeout_connect + 10,
    )
    if r.returncode != 0:
        raise RuntimeError(
            _mask(r.stderr.decode(errors="replace").strip(), settings)
        )
    return sock


def _ssh(
    settings: HpcSettings,
    command: str,
    *,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """Run ``command`` on the HPC login node.

    Uses the ControlMaster socket when alive; falls back to a direct
    connection if the socket is absent or stale (returncode 255).
    """
    sock = _socket_path(settings)
    target = f"{settings.slurm_submit_user}@{settings.slurm_submit_host}"

    if sock.exists():
        r = subprocess.run(
            ["ssh", "-S", str(sock), target, command],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 255:
            return r
        # Stale socket — clean up and fall through to direct connection.
        sock.unlink(missing_ok=True)
        log.debug("ControlMaster socket was stale; falling back to direct SSH")

    args = ["ssh"] + _base_ssh_opts(settings) + [target, command]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# sbatch script builders
# ---------------------------------------------------------------------------


def _build_sbatch_header(
    settings: HpcSettings,
    *,
    job_name: str,
    cpus: int,
    mem_gb: int,
    time_min: int,
    log_dir: str,
) -> list[str]:
    lines = [
        "#!/bin/bash",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={settings.slurm_partition}",
    ]
    if settings.slurm_qos:
        lines.append(f"#SBATCH --qos={settings.slurm_qos}")
    if settings.slurm_node_list:
        lines.append(f"#SBATCH --nodelist={settings.slurm_node_list}")
    lines += [
        f"#SBATCH --cpus-per-task={cpus}",
        f"#SBATCH --mem={mem_gb}G",
        f"#SBATCH --time={time_min}",
        f"#SBATCH --output={log_dir}/%x-%j.out",
        f"#SBATCH --error={log_dir}/%x-%j.err",
        "",
        "set -euo pipefail",
    ]
    return lines


def _singularity_exec_lines(
    settings: HpcSettings,
    remote_ws: str,
    ws_name: str,
    command: str,
) -> list[str]:
    """Return the Singularity exec command lines for a run script."""
    sing = settings.singularity_cmd or "apptainer"
    return [
        # Try preferred singularity_cmd; fall back to apptainer/singularity.
        f'SIF_CMD=$(command -v {sing} 2>/dev/null '
        f'|| command -v apptainer 2>/dev/null '
        f'|| command -v singularity)',
        f'cd "{remote_ws}"',
        '"$SIF_CMD" exec \\',
        f'    --bind "{remote_ws}/results:/app/results" \\',
        f'    "{ws_name}.sif" \\',
        f'    {command}',
    ]


def build_run_script(
    settings: HpcSettings,
    ws_name: str,
    run_id: str,
    command: str,
    *,
    cpus: int = 1,
    mem_gb: int = 4,
    time_min: int = 60,
) -> str:
    """Return the text of an sbatch script that executes ``command`` inside
    the workspace Singularity image on the HPC."""
    remote_ws = f"{settings.hpc_repo_base_path}/{ws_name}"
    log_base = settings.hpc_log_base_path or f"{settings.hpc_repo_base_path}/logs"
    log_dir = f"{log_base}/{ws_name}"
    header = _build_sbatch_header(
        settings,
        job_name=f"vivarium-{ws_name}-{run_id}",
        cpus=cpus, mem_gb=mem_gb, time_min=time_min,
        log_dir=log_dir,
    )
    body = _singularity_exec_lines(settings, remote_ws, ws_name, command)
    return "\n".join(header + body) + "\n"


def build_image_script(
    settings: HpcSettings,
    ws_name: str,
    build_id: str,
) -> str:
    """Return the text of an sbatch script that builds ``ws_name.sif`` from
    the ``Singularity.def`` in the rsynced workspace."""
    remote_ws = f"{settings.hpc_repo_base_path}/{ws_name}"
    image_base = settings.hpc_image_base_path or remote_ws
    log_base = settings.hpc_log_base_path or f"{settings.hpc_repo_base_path}/logs"
    log_dir = f"{log_base}/{ws_name}"
    sing = settings.singularity_cmd or "apptainer"
    header = _build_sbatch_header(
        settings,
        job_name=f"vivarium-build-{ws_name}-{build_id}",
        cpus=4, mem_gb=8, time_min=60,
        log_dir=log_dir,
    )
    body = [
        f'SIF_CMD=$(command -v {sing} 2>/dev/null '
        f'|| command -v apptainer 2>/dev/null '
        f'|| command -v singularity)',
        f'cd "{remote_ws}"',
        '"$SIF_CMD" build --fakeroot \\',
        f'    "{image_base}/{ws_name}.sif" \\',
        '    Singularity.def',
    ]
    return "\n".join(header + body) + "\n"


# ---------------------------------------------------------------------------
# Public dispatch API
# ---------------------------------------------------------------------------


def check_connectivity(settings: HpcSettings) -> dict:
    """Probe SSH reachability and Singularity/Apptainer availability.

    Returns::

        {
          "reachable": bool,
          "singularity_available": bool,
          "singularity_cmd": str | None,
          "message": str,
        }
    """
    require_configured(settings)
    try:
        r = _ssh(
            settings,
            "which apptainer 2>/dev/null || which singularity 2>/dev/null || true",
            timeout=settings.timeout_connect + 5,
        )
    except Exception as exc:
        return {
            "reachable": False,
            "singularity_available": False,
            "singularity_cmd": None,
            "message": _mask(str(exc), settings),
        }

    if r.returncode != 0:
        return {
            "reachable": False,
            "singularity_available": False,
            "singularity_cmd": None,
            "message": _mask(r.stderr.strip() or "SSH connection failed", settings),
        }

    sing_path = r.stdout.strip()
    sing_cmd = os.path.basename(sing_path) if sing_path else None
    return {
        "reachable": True,
        "singularity_available": bool(sing_path),
        "singularity_cmd": sing_cmd,
        "message": (
            "ok" if sing_path
            else "SSH reachable; apptainer/singularity not found on PATH"
        ),
    }


def check_slurm(settings: HpcSettings) -> dict:
    """Query running jobs and available partitions.

    Returns::

        {
          "partitions": list[str],
          "jobs": list[dict],
          "error": str | None,
        }
    """
    require_configured(settings)
    try:
        jobs_r = _ssh(
            settings,
            f"squeue -u {settings.slurm_submit_user} --noheader "
            f"--format='{_SQUEUE_FMT}'",
        )
        parts_r = _ssh(
            settings,
            "sinfo --noheader -o '%P %a %l %D %t' 2>/dev/null || true",
        )
    except Exception as exc:
        return {"partitions": [], "jobs": [], "error": _mask(str(exc), settings)}

    jobs: list[dict] = []
    for line in jobs_r.stdout.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) >= 3:
            jobs.append({
                "job_id": parts[0],
                "name": parts[1],
                "state": parts[2],
                "reason": parts[3] if len(parts) > 3 else None,
                "elapsed": parts[4] if len(parts) > 4 else None,
            })

    partitions: list[str] = []
    for line in parts_r.stdout.splitlines():
        s = line.strip()
        if s:
            partitions.append(s.split()[0].rstrip("*"))

    return {
        "partitions": partitions,
        "jobs": jobs,
        "error": (
            _mask(jobs_r.stderr.strip(), settings)
            if jobs_r.returncode != 0 else None
        ),
    }


def rsync_workspace(settings: HpcSettings, local_ws: Path) -> None:
    """rsync the local workspace directory to the HPC repo base path.

    Excludes large/transient artefacts: ``.venv/``, ``.git/``,
    ``__pycache__/``, ``.pbg/runs/``, ``.pbg/state.json``.
    """
    require_configured(settings)
    ws_name = local_ws.name
    target = f"{settings.slurm_submit_user}@{settings.slurm_submit_host}"
    remote_dest = f"{target}:{settings.hpc_repo_base_path}/{ws_name}/"

    ssh_opt = "ssh"
    if settings.slurm_submit_key_path:
        ssh_opt += f" -i {settings.slurm_submit_key_path}"
    if settings.slurm_submit_known_hosts:
        ssh_opt += f" -o UserKnownHostsFile={settings.slurm_submit_known_hosts}"
    ssh_opt += f" -o ConnectTimeout={settings.timeout_connect}"

    cmd = [
        "rsync", "-az", "--delete",
        "--no-o", "--no-g", "--omit-dir-times",
        "--exclude=.venv/",
        "--exclude=.git/",
        "--exclude=__pycache__/",
        "--exclude=.pbg/runs/",
        "--exclude=.pbg/state.json",
        "-e", ssh_opt,
        str(local_ws) + "/",
        remote_dest,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(
            f"rsync failed (exit {r.returncode}): "
            f"{_mask(r.stderr[-1000:], settings)}"
        )


def submit_build_job(settings: HpcSettings, local_ws: Path) -> dict:
    """rsync workspace, write a build sbatch script, and submit it.

    Returns::

        {"build_id": str, "slurm_job_id": int, "log_path": str}
    """
    require_configured(settings)
    ws_name = local_ws.name
    build_id = uuid.uuid4().hex[:8]

    # Write sbatch script locally so the second rsync carries it to the HPC.
    hpc_dir = local_ws / ".pbg" / "hpc"
    hpc_dir.mkdir(parents=True, exist_ok=True)
    script_local = hpc_dir / f"build-{build_id}.sh"
    script_local.write_text(build_image_script(settings, ws_name, build_id))

    # Two rsyncs: first syncs workspace content, second carries the new script.
    rsync_workspace(settings, local_ws)

    remote_ws = f"{settings.hpc_repo_base_path}/{ws_name}"
    remote_script = f"{remote_ws}/.pbg/hpc/build-{build_id}.sh"
    r = _ssh(settings, f"sbatch {remote_script}", timeout=30)
    if r.returncode != 0:
        raise RuntimeError(
            f"sbatch failed: {_mask(r.stderr[-500:], settings)}"
        )
    # sbatch stdout: "Submitted batch job <id>"
    slurm_job_id = int(r.stdout.strip().split()[-1])
    log_base = settings.hpc_log_base_path or f"{settings.hpc_repo_base_path}/logs"
    log_path = f"{log_base}/{ws_name}/vivarium-build-{ws_name}-{build_id}-{slurm_job_id}.out"
    return {"build_id": build_id, "slurm_job_id": slurm_job_id, "log_path": log_path}


def get_job_status(settings: HpcSettings, slurm_job_id: int) -> dict:
    """Query SLURM job state.

    Tries ``squeue -j <id>`` first (fast, in-memory); falls back to
    ``scontrol show job`` for jobs that have left the queue (mirrors
    sms-api squeue → scontrol pattern; sacct not used).

    Returns::

        {
          "job_id": int,
          "state": str,
          "reason": str | None,
          "start_time": str | None,
          "elapsed": str | None,
        }
    """
    require_configured(settings)
    r = _ssh(
        settings,
        f"squeue -j {slurm_job_id} --noheader --format='{_SQUEUE_FMT}'",
        timeout=30,
    )
    if r.returncode == 0 and r.stdout.strip():
        parts = r.stdout.strip().split(None, 4)
        return {
            "job_id": slurm_job_id,
            "state": parts[0] if parts else "UNKNOWN",
            "reason": parts[1] if len(parts) > 1 else None,
            "start_time": parts[2] if len(parts) > 2 else None,
            "elapsed": parts[3] if len(parts) > 3 else None,
        }

    # Job not in squeue — try scontrol (mirrors sms-api fallback).
    r2 = _ssh(
        settings,
        f"scontrol show job {slurm_job_id} --oneliner 2>/dev/null || true",
        timeout=30,
    )
    state = "UNKNOWN"
    if r2.returncode == 0 and r2.stdout.strip():
        m = re.search(r"JobState=(\S+)", r2.stdout)
        if m:
            state = m.group(1)
    return {
        "job_id": slurm_job_id,
        "state": state,
        "reason": None,
        "start_time": None,
        "elapsed": None,
    }


def cancel_job(settings: HpcSettings, slurm_job_id: int) -> None:
    """Cancel a SLURM job via ``scancel``."""
    require_configured(settings)
    r = _ssh(settings, f"scancel {slurm_job_id}", timeout=30)
    if r.returncode != 0:
        raise RuntimeError(
            f"scancel failed: {_mask(r.stderr[-500:], settings)}"
        )


def submit_run_job(
    settings: HpcSettings,
    local_ws: Path,
    command: str,
    resources: dict,
) -> dict:
    """Write an sbatch run script, rsync the workspace, and submit.

    ``resources`` keys (all optional):
        cpus (int): CPUs per task (default 1).
        mem_gb (int): Memory in GB (default 4).
        time_min (int): Time limit in minutes (default 60).

    Returns::

        {"slurm_job_id": int, "log_path": str}
    """
    require_configured(settings)
    ws_name = local_ws.name
    run_id = uuid.uuid4().hex[:8]
    cpus = int(resources.get("cpus", 1))
    mem_gb = int(resources.get("mem_gb", 4))
    time_min = int(resources.get("time_min", 60))

    hpc_dir = local_ws / ".pbg" / "hpc"
    hpc_dir.mkdir(parents=True, exist_ok=True)
    script_local = hpc_dir / f"run-{run_id}.sh"
    script_local.write_text(
        build_run_script(
            settings, ws_name, run_id, command,
            cpus=cpus, mem_gb=mem_gb, time_min=time_min,
        )
    )

    rsync_workspace(settings, local_ws)

    remote_ws = f"{settings.hpc_repo_base_path}/{ws_name}"
    remote_script = f"{remote_ws}/.pbg/hpc/run-{run_id}.sh"
    r = _ssh(settings, f"sbatch {remote_script}", timeout=30)
    if r.returncode != 0:
        raise RuntimeError(
            f"sbatch failed: {_mask(r.stderr[-500:], settings)}"
        )
    slurm_job_id = int(r.stdout.strip().split()[-1])
    log_base = settings.hpc_log_base_path or f"{settings.hpc_repo_base_path}/logs"
    log_path = (
        f"{log_base}/{ws_name}/"
        f"vivarium-{ws_name}-{run_id}-{slurm_job_id}.out"
    )
    return {"slurm_job_id": slurm_job_id, "log_path": log_path}
