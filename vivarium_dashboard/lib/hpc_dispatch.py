"""SSH + SLURM dispatch layer for HPC compute backends (Todo #10).

Uses stdlib subprocess + SSH ControlMaster sockets for connection reuse.
No extra runtime dependencies — no paramiko, no asyncssh.

All public functions call require_configured() before touching the network,
so callers don't need to validate settings.  Sensitive values (key path,
username) are redacted via _mask() before any log emit or error propagation.

Pattern mirrors ~/sms/sms-api/sms_api/simulation/simulation_service.py
(SimulationServiceHpc) and ~/sms/sms-api/sms_api/common/hpc/slurm_service.py
(SlurmService), adapted for synchronous subprocess use:

  submit_build_job  ≈  SimulationServiceHpc.submit_build_image_job
  submit_run_job    ≈  SimulationServiceHpc.submit_parca_job (run-job variant)
  _scp_file         ≈  SlurmService.scp_upload + submit_job (SCP step)

Build pipeline (no Docker anywhere):
  1. rsync workspace to cluster
  2. write sbatch script locally
  3. scp script to cluster     ← _scp_file()
  4. sbatch --parsable          → int job_id
  5. cluster builds SIF via:   apptainer build --fakeroot --force --disable-cache
                                    {sif} Singularity.def
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import uuid
from pathlib import Path
from textwrap import dedent

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
    Returns the CompletedProcess — callers check returncode themselves.
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


def _scp_file(
    settings: HpcSettings,
    local_path: Path,
    remote_path: str,
    *,
    timeout: int = 60,
) -> None:
    """Upload a single file to the cluster via SCP.

    Mirrors SlurmService.scp_upload() + the mkdir step in submit_job()
    from ~/sms/sms-api/sms_api/common/hpc/slurm_service.py.

    Creates the remote parent directory first (via SSH), then SCPs the file.
    Uses the ControlMaster socket when available for connection reuse.
    """
    # Ensure remote parent directory exists (mirrors slurm_service mkdir step).
    remote_dir = str(Path(remote_path).parent)
    r = _ssh(settings, f'mkdir -p "{remote_dir}"', timeout=15)
    if r.returncode != 0:
        raise RuntimeError(
            f"mkdir -p on cluster failed: {_mask(r.stderr[-300:], settings)}"
        )

    target = f"{settings.slurm_submit_user}@{settings.slurm_submit_host}"
    sock = _socket_path(settings)

    scp_args = ["scp"]
    if sock.exists():
        scp_args += ["-o", f"ControlPath={sock}"]
    if settings.slurm_submit_key_path:
        scp_args += ["-i", settings.slurm_submit_key_path]
    if settings.slurm_submit_known_hosts:
        scp_args += ["-o", f"UserKnownHostsFile={settings.slurm_submit_known_hosts}"]
    scp_args += [
        "-o", f"ConnectTimeout={settings.timeout_connect}",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        str(local_path),
        f"{target}:{remote_path}",
    ]

    r2 = subprocess.run(scp_args, capture_output=True, text=True, timeout=timeout)
    if r2.returncode != 0:
        raise RuntimeError(
            f"scp failed (exit {r2.returncode}): {_mask(r2.stderr[-500:], settings)}"
        )


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
    log_file: str,
) -> list[str]:
    """Return #SBATCH directive lines.

    Uses an explicit log file path (``-o``/``-e`` same file) to mirror the
    sms-api pattern where the caller knows the exact log path up front.
    """
    lines = [
        "#!/bin/bash",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --time={time_min}",
        f"#SBATCH --cpus-per-task={cpus}",
        f"#SBATCH --mem={mem_gb}GB",
        f"#SBATCH --partition={settings.slurm_partition}",
        f"#SBATCH --mail-type=ALL",
    ]
    if settings.slurm_qos:
        lines.append(f"#SBATCH --qos={settings.slurm_qos}")
    if settings.slurm_node_list:
        lines.append(f"#SBATCH --nodelist={settings.slurm_node_list}")
    lines += [
        f"#SBATCH -o {log_file}",
        f"#SBATCH -e {log_file}",
        "",
    ]
    return lines


def _singularity_exec_lines(
    settings: HpcSettings,
    remote_ws: str,
    ws_name: str,
    command: str,
) -> list[str]:
    """Return the singularity exec command lines for a run sbatch script.

    Bind-mounts:
      {remote_ws}/results  →  /app/results   (simulation output)
      {remote_ws}/out      →  /app/out        (ParCa cache — must persist across jobs)
    """
    image_base = settings.hpc_image_base_path or remote_ws
    sif_path = f"{image_base}/{ws_name}.sif"
    return [
        "# Prefer apptainer, fall back to singularity (mirrors sms-api runtime detection)",
        "if command -v apptainer &>/dev/null; then",
        "    SIF_CMD=apptainer",
        "elif command -v singularity &>/dev/null; then",
        "    SIF_CMD=singularity",
        "else",
        '    echo "ERROR: neither apptainer nor singularity found"; exit 1',
        "fi",
        "",
        f'mkdir -p "{remote_ws}/results" "{remote_ws}/out"',
        f'cd "{remote_ws}"',
        "",
        '"$SIF_CMD" exec \\',
        f'    -B "{remote_ws}/results:/app/results" \\',
        f'    -B "{remote_ws}/out:/app/out" \\',
        f'    "{sif_path}" \\',
        f'    {command}',
    ]


def build_image_script(
    settings: HpcSettings,
    ws_name: str,
    build_id: str,
) -> str:
    """Return the text of an sbatch script that builds ``ws_name.sif``.

    Mirrors SimulationServiceHpc.submit_build_image_job() sbatch content
    from ~/sms/sms-api/sms_api/simulation/simulation_service.py:

      - apptainer/singularity runtime detection
      - APPTAINER_CACHEDIR + APPTAINER_TMPDIR (avoids NFS issues on compute nodes)
      - skip if SIF already exists
      - repo.tar creation honouring .dockerignore
      - apptainer build --fakeroot --force --disable-cache {sif} Singularity.def

    The workspace is rsync'd to the cluster before this script is submitted,
    so Step 1 of the sms-api pattern (git clone) becomes an existence check.
    """
    remote_ws = f"{settings.hpc_repo_base_path}/{ws_name}"
    image_base = settings.hpc_image_base_path or remote_ws
    log_base = settings.hpc_log_base_path or f"{settings.hpc_repo_base_path}/logs"
    log_dir = f"{log_base}/{ws_name}"
    log_file = f"{log_dir}/vivarium-build-{ws_name}-{build_id}.out"
    sif_path = f"{image_base}/{ws_name}.sif"
    job_name = f"vivarium-build-{ws_name}-{build_id}"
    apptainer_tmpdir = settings.apptainer_tmpdir or "/tmp/apptainer"

    header = _build_sbatch_header(
        settings,
        job_name=job_name,
        cpus=3,
        mem_gb=8,
        time_min=60,
        log_file=log_file,
    )

    body = dedent(f"""\
        set -eu
        env

        # --- Container runtime detection (mirrors sms-api) ---
        if command -v apptainer &>/dev/null; then
            CONTAINER_CMD="apptainer"
            echo "Using apptainer for container build"
        elif command -v singularity &>/dev/null; then
            CONTAINER_CMD="singularity"
            echo "Using singularity for container build"
        else
            echo "ERROR: Neither apptainer nor singularity found in PATH"
            exit 1
        fi

        # TMPDIR: local disk (not NFS) for fast metadata ops during builds.
        # CACHEDIR: shared storage, layer caching across nodes.
        export APPTAINER_CACHEDIR=${{APPTAINER_CACHEDIR:-$HOME/.apptainer/cache}}
        export APPTAINER_TMPDIR="{apptainer_tmpdir}"
        mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"

        # --- Step 1: Confirm workspace exists (rsync ran before submission) ---
        echo "=== Step 1: Verifying workspace ==="
        REPO_PATH="{remote_ws}"
        if [ ! -d "$REPO_PATH" ]; then
            echo "ERROR: Workspace not found at $REPO_PATH"
            echo "       rsync_workspace() should have run before submit_build_job()"
            exit 1
        fi
        echo "Workspace found at $REPO_PATH"

        # If both repo and image already exist, skip the whole build.
        if [ -f "{sif_path}" ]; then
            echo "Image {sif_path} already exists. Skipping build."
            exit 0
        fi

        # --- Step 2: Build Apptainer image ---
        echo "=== Step 2: Building Apptainer image ==="
        mkdir -p "{image_base}"

        echo "Building {ws_name}.sif on $(hostname) from $REPO_PATH ..."
        cd "$REPO_PATH"

        GIT_HASH=$(git rev-parse HEAD 2>/dev/null || echo "no-git")
        GIT_BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "detached")
        TIMESTAMP=$(date '+%Y%m%d.%H%M%S')

        # Create git diff for traceability (mirrors sms-api source-info pattern).
        mkdir -p source-info
        git diff HEAD > source-info/git_diff.txt 2>/dev/null || true

        # Create repo.tar honouring .dockerignore (mirrors sms-api lines 272-296).
        echo "Creating repo tarball..."
        EXCLUDE_PATTERNS=$(mktemp)
        if [ -f .dockerignore ]; then
            grep -v "^#" .dockerignore | grep -v "^$" | grep -v "^!" | while read -r pattern; do
                if [[ "$pattern" == /* ]]; then
                    echo ".${{pattern}}/*" >> "$EXCLUDE_PATTERNS"
                elif [[ "$pattern" == */ ]]; then
                    echo "./${{pattern}}*" >> "$EXCLUDE_PATTERNS"
                else
                    echo "./${{pattern}}" >> "$EXCLUDE_PATTERNS"
                    echo "./${{pattern}}/*" >> "$EXCLUDE_PATTERNS"
                fi
            done
        fi

        FIND_CMD="find . -type f"
        while read -r pattern; do
            FIND_CMD="$FIND_CMD ! -path \\"$pattern\\""
        done < "$EXCLUDE_PATTERNS"

        TEMP_FILE_LIST=$(mktemp)
        eval "$FIND_CMD -print0" > "$TEMP_FILE_LIST"
        tar -cf repo.tar --null -T "$TEMP_FILE_LIST"
        rm -f "$EXCLUDE_PATTERNS" "$TEMP_FILE_LIST"
        echo "Created repo.tar ($(du -sh repo.tar | awk '{{print $1}}'))"

        echo "=== Building Container Image: {sif_path} ==="
        echo "=== git hash $GIT_HASH, git branch $GIT_BRANCH ==="

        # --fakeroot --force --disable-cache mirrors sms-api line 318 exactly.
        # --disable-cache: ensures correct architecture is pulled for multi-arch
        #   base images (avoids wrong-arch cached layers).
        if ! $CONTAINER_CMD build --fakeroot --force --disable-cache \\
            "{sif_path}" \\
            "Singularity.def"; then
            echo "ERROR: Container build failed."
            exit 1
        fi
        echo "Container build successful!"

        rm -f repo.tar
        echo "Build completed. Image saved to {sif_path}."
        """)

    return "\n".join(header) + "\n" + body


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
    """Return the text of an sbatch script that runs ``command`` inside the
    workspace Singularity image on the HPC.

    Mirrors SimulationServiceHpc.submit_parca_job() sbatch pattern:
      - mkdir output dirs
      - skip guard if results already populated
      - singularity exec with bind mounts
      - non-empty guard on completion
    """
    remote_ws = f"{settings.hpc_repo_base_path}/{ws_name}"
    log_base = settings.hpc_log_base_path or f"{settings.hpc_repo_base_path}/logs"
    log_dir = f"{log_base}/{ws_name}"
    log_file = f"{log_dir}/vivarium-{ws_name}-{run_id}.out"
    results_dir = f"{remote_ws}/results"

    header = _build_sbatch_header(
        settings,
        job_name=f"vivarium-{ws_name}-{run_id}",
        cpus=cpus,
        mem_gb=mem_gb,
        time_min=time_min,
        log_file=log_file,
    )

    # Skip-if-populated guard mirrors sms-api parca job (lines 392-396).
    guards_and_exec = dedent(f"""\
        set -e

        mkdir -p "{results_dir}" "{remote_ws}/out"

        # Skip if results directory already populated (mirrors sms-api parca guard).
        if [ "$(ls -A "{results_dir}" 2>/dev/null)" ]; then
            echo "Results directory {results_dir} is not empty. Skipping."
            exit 0
        fi

        echo "Starting run {run_id} on $(hostname) ..."
        """)

    exec_lines = _singularity_exec_lines(settings, remote_ws, ws_name, command)

    # Non-empty guard on exit mirrors sms-api parca job (lines 409-413).
    post_guard = dedent(f"""\

        if [ ! "$(ls -A "{results_dir}" 2>/dev/null)" ]; then
            echo "Results directory {results_dir} is empty after run — job likely failed."
            exit 1
        fi

        echo "Run {run_id} completed. Results at {results_dir}."
        """)

    return "\n".join(header) + "\n" + guards_and_exec + "\n".join(exec_lines) + post_guard


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


def _ssh_opt_str(settings: HpcSettings) -> str:
    """Return the -e ssh option string for rsync."""
    opt = "ssh"
    if settings.slurm_submit_key_path:
        opt += f" -i {settings.slurm_submit_key_path}"
    if settings.slurm_submit_known_hosts:
        opt += f" -o UserKnownHostsFile={settings.slurm_submit_known_hosts}"
    opt += f" -o ConnectTimeout={settings.timeout_connect}"
    return opt


def rsync_workspace(settings: HpcSettings, local_ws: Path) -> None:
    """rsync the local workspace directory to the HPC repo base path.

    Excludes large/transient artefacts that don't belong on the cluster:
    ``.venv/``, ``.git/``, ``__pycache__/``, ``.pbg/runs/``,
    ``.pbg/state.json``, ``.pbg/hpc/``.

    This is the vivarium-dashboard equivalent of sms-api's git-clone-on-cluster
    step: the cluster always has an up-to-date copy of the workspace source.
    """
    require_configured(settings)
    ws_name = local_ws.name
    target = f"{settings.slurm_submit_user}@{settings.slurm_submit_host}"
    remote_dest = f"{target}:{settings.hpc_repo_base_path}/{ws_name}/"
    cmd = [
        "rsync", "-az", "--delete",
        "--no-o", "--no-g", "--omit-dir-times",
        "--exclude=.venv/",
        "--exclude=.git/",
        "--exclude=__pycache__/",
        "--exclude=.pbg/runs/",
        "--exclude=.pbg/state.json",
        "--exclude=.pbg/hpc/",
        "-e", _ssh_opt_str(settings),
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
    """rsync workspace, SCP an sbatch build script, and submit it.

    Mirrors SimulationServiceHpc.submit_build_image_job():
      1. rsync_workspace  (our equivalent of sms-api's git-clone-on-cluster step)
      2. build_image_script → write locally
      3. _scp_file         (≈ SlurmService.scp_upload)
      4. sbatch --parsable (≈ SlurmService.submit_job sbatch step)

    The workspace must contain a ``Singularity.def`` at its root — this is
    the definition file the cluster build script passes to apptainer.

    Returns::

        {"build_id": str, "slurm_job_id": int, "log_path": str}
    """
    require_configured(settings)
    ws_name = local_ws.name

    if not (local_ws / "Singularity.def").exists():
        raise RuntimeError(
            f"Workspace {ws_name} is missing Singularity.def — "
            "cannot submit a build job without a container definition file."
        )

    build_id = uuid.uuid4().hex[:8]

    # Step 1: sync workspace source to cluster.
    rsync_workspace(settings, local_ws)

    # Step 2: generate sbatch script and write it locally (in .pbg/hpc/ for
    # auditability — mirrors sms-api's tempfile approach but persisted).
    script_text = build_image_script(settings, ws_name, build_id)
    hpc_dir = local_ws / ".pbg" / "hpc"
    hpc_dir.mkdir(parents=True, exist_ok=True)
    local_script = hpc_dir / f"build-{build_id}.sbatch"
    local_script.write_text(script_text)

    # Step 3: SCP script to cluster (≈ SlurmService.scp_upload).
    log_base = settings.hpc_log_base_path or f"{settings.hpc_repo_base_path}/logs"
    log_dir = f"{log_base}/{ws_name}"
    remote_script = f"{log_dir}/build-{build_id}.sbatch"
    _scp_file(settings, local_script, remote_script, timeout=30)

    # Step 4: sbatch --parsable → int job_id (≈ SlurmService.submit_job sbatch step).
    r = _ssh(settings, f"sbatch --parsable {remote_script}", timeout=30)
    if r.returncode != 0:
        raise RuntimeError(
            f"sbatch failed: {_mask(r.stderr[-500:], settings)}"
        )
    slurm_job_id = int(r.stdout.strip())
    log_path = f"{log_dir}/vivarium-build-{ws_name}-{build_id}.out"
    return {"build_id": build_id, "slurm_job_id": slurm_job_id, "log_path": log_path}


def get_job_status(settings: HpcSettings, slurm_job_id: int) -> dict:
    """Query SLURM job state.

    Tries ``squeue -j <id>`` first (fast, in-memory); falls back to
    ``scontrol show job`` for jobs that have left the queue (mirrors
    sms-api squeue → scontrol pattern).

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
        # _SQUEUE_FMT = "%i %j %T %R %M"  → job_id name state reason elapsed
        return {
            "job_id": slurm_job_id,
            "state": parts[2] if len(parts) > 2 else "UNKNOWN",
            "reason": parts[3] if len(parts) > 3 else None,
            "start_time": None,
            "elapsed": parts[4] if len(parts) > 4 else None,
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
    """Write an sbatch run script, SCP it to the cluster, and submit.

    Mirrors SimulationServiceHpc.submit_parca_job() flow:
      1. rsync_workspace (keeps cluster copy current)
      2. build_run_script → write locally
      3. _scp_file
      4. sbatch --parsable

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

    # Step 1: sync workspace source to cluster.
    rsync_workspace(settings, local_ws)

    # Step 2: generate sbatch script and write locally.
    script_text = build_run_script(
        settings, ws_name, run_id, command,
        cpus=cpus, mem_gb=mem_gb, time_min=time_min,
    )
    hpc_dir = local_ws / ".pbg" / "hpc"
    hpc_dir.mkdir(parents=True, exist_ok=True)
    local_script = hpc_dir / f"run-{run_id}.sbatch"
    local_script.write_text(script_text)

    # Step 3: SCP script to cluster.
    log_base = settings.hpc_log_base_path or f"{settings.hpc_repo_base_path}/logs"
    log_dir = f"{log_base}/{ws_name}"
    remote_script = f"{log_dir}/run-{run_id}.sbatch"
    _scp_file(settings, local_script, remote_script, timeout=30)

    # Step 4: sbatch --parsable.
    r = _ssh(settings, f"sbatch --parsable {remote_script}", timeout=30)
    if r.returncode != 0:
        raise RuntimeError(
            f"sbatch failed: {_mask(r.stderr[-500:], settings)}"
        )
    slurm_job_id = int(r.stdout.strip())
    log_path = f"{log_dir}/vivarium-{ws_name}-{run_id}.out"
    return {"slurm_job_id": slurm_job_id, "log_path": log_path}
