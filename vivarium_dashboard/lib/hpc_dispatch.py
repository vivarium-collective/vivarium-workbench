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

Build pipeline (GHCR-pull — no Docker locally, no --fakeroot):
  1. Infer GHCR image ref from git remote origin
       github.com/org/repo → ghcr.io/org/repo
       (override via GHCR_IMAGE in .pbg/hpc.env)
  2. Write sbatch script locally
  3. SCP script to cluster          ← _scp_file()
  4. sbatch --parsable               → int job_id
  5. Cluster builds SIF via:
       apptainer build docker://ghcr.io/org/repo:sha-<hash> ws.sif
     No --fakeroot required: OCI registry pull → SIF conversion uses no
     user-namespace mapping.  Docker image is built by GitHub Actions
     (.github/workflows/build-and-push.yml) on every push.

Run pipeline (unchanged):
  1. rsync workspace to cluster (bind-mount targets: out/, results/)
  2. Write sbatch run script locally
  3. SCP + sbatch --parsable
  4. singularity exec -B out:/app/out -B results:/app/results {sif} <cmd>
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


def _build_tag_list(local_sha: str, branch_tag: str) -> str:
    """Return a space-separated bash word list of GHCR tags to try in order.

    Order: sha-<sha> → <branch-tag> → latest.
    Only non-empty, distinct tags are included.
    """
    seen: set[str] = set()
    tags: list[str] = []
    for raw in (f"sha-{local_sha}" if local_sha else "", branch_tag, "latest"):
        t = raw.strip()
        if t and t not in seen:
            seen.add(t)
            tags.append(t)
    return " ".join(tags)


def _infer_ghcr_image(local_ws: Path) -> str | None:
    """Derive a GHCR image ref from the workspace's git remote origin.

    Converts GitHub SSH/HTTPS remote URLs to a ``ghcr.io`` image reference::

        https://github.com/vivarium-collective/v2ecoli.git
        git@github.com:vivarium-collective/v2ecoli.git
          → ghcr.io/vivarium-collective/v2ecoli

    Returns ``None`` if the workspace has no GitHub remote or git is not
    available.  The caller should fall back to ``HpcSettings.ghcr_image``
    or raise a descriptive error.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(local_ws), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        m = re.match(
            r"(?:https://github\.com/|git@github\.com:)([^/]+)/([^/.]+?)(?:\.git)?$",
            r.stdout.strip(),
        )
        if m:
            return f"ghcr.io/{m.group(1).lower()}/{m.group(2).lower()}"
    except Exception:
        pass
    return None


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
    # Embed the user command inside a bash -c single-quoted string.
    # Single quotes in the command itself would break the outer quoting, so
    # escape them with the standard "exit-quote, add literal quote,
    # re-enter-quote" trick: ' → '\'' — safe for all POSIX shells.
    _cmd = command.replace("uv run ", "", 1).replace("'", "'\\''")
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
        "",
        # Prepend /app/.venv/bin to PATH so the container's compiled entry
        # points (v2ecoli-parca, v2ecoli-colony, etc.) are found directly,
        # without going through `uv run` which tries to canonicalize Python
        # symlinks and fails when they resolve into /root/.local/ (only
        # accessible to root inside the container).
        # `uv run CMD` → `CMD` resolved via /app/.venv/bin on PATH.
        '"$SIF_CMD" exec \\',
        f'    -B "{remote_ws}/results:/app/results" \\',
        f'    -B "{remote_ws}/out:/app/out" \\',
        f'    "{sif_path}" \\',
        f'    bash -c \'export PATH=/app/.venv/bin:"$PATH"; exec {_cmd}\'',
    ]


def build_image_script(
    settings: HpcSettings,
    ws_name: str,
    build_id: str,
    ghcr_image: str,
    *,
    local_sha: str = "",
    branch_tag: str = "",
) -> str:
    """Return an sbatch script that pulls ``ws_name`` from GHCR and converts to SIF.

    No ``--fakeroot`` required: ``apptainer build docker://...`` is a pure OCI
    registry pull + SIF conversion — it uses Apptainer's own HTTP client, not
    the Docker daemon, and requires no user-namespace mapping.

    The Docker image is built by GitHub Actions
    (``.github/workflows/build-and-push.yml``) on every push and tagged as
    ``sha-<7-char-sha>`` (pinned), ``<branch-name>`` (rolling), and ``:latest``
    (only on the default branch).

    Tag strategy (tried in order, baked in at submit time from local workspace):
      1. ``sha-<short-sha>`` — pinned to exact commit; best reproducibility.
      2. ``<branch-tag>``    — branch name tag (slash → hyphen); available on
                               every push including non-default branches.
      3. ``:latest``         — rolling default-branch tag; fallback when no
                               sha or branch tag is available.
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

        # --- Container runtime detection ---
        if command -v apptainer &>/dev/null; then
            CONTAINER_CMD="apptainer"
            echo "Using apptainer"
        elif command -v singularity &>/dev/null; then
            CONTAINER_CMD="singularity"
            echo "Using singularity"
        else
            echo "ERROR: Neither apptainer nor singularity found in PATH"
            exit 1
        fi

        export APPTAINER_CACHEDIR=${{APPTAINER_CACHEDIR:-$HOME/.apptainer/cache}}
        export APPTAINER_TMPDIR="{apptainer_tmpdir}"
        mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"

        if [ -f "{sif_path}" ]; then
            echo "Image {sif_path} already exists. Skipping build."
            exit 0
        fi

        mkdir -p "{image_base}"

        # Tag order baked in at submit time from the local workspace:
        #   1. sha-<short-sha>  — pinned; best reproducibility
        #   2. <branch-tag>     — branch push tag (slash → hyphen); works on non-default branches
        #   3. latest           — default-branch rolling tag; last resort
        GHCR_IMAGE="{ghcr_image}"
        TAGS=({_build_tag_list(local_sha, branch_tag)})
        SUCCESS=0
        for TAG in "${{TAGS[@]}}"; do
            echo "=== Trying $GHCR_IMAGE:$TAG → {sif_path} ==="
            if $CONTAINER_CMD build --force --disable-cache \\
                "{sif_path}" \\
                "docker://$GHCR_IMAGE:$TAG" 2>&1; then
                SUCCESS=1
                break
            else
                echo "Tag $TAG unavailable, trying next..."
            fi
        done
        if [ "$SUCCESS" -eq 0 ]; then
            echo "ERROR: all tags exhausted: ${{TAGS[*]}}"
            exit 1
        fi

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

    # Non-empty guard: accept output in results/ OR out/sim_data/ so both
    # ParCa (writes out/sim_data/parca_state.pkl) and Colony (writes results/)
    # exit 0 on success.  Mirrors sms-api parca job (lines 409-413) but
    # generalised for the two-step v2ecoli dispatch.
    parca_out_dir = f"{remote_ws}/out/sim_data"
    post_guard = dedent(f"""\

        if [ ! "$(ls -A "{results_dir}" 2>/dev/null)" ] && [ ! "$(ls -A "{parca_out_dir}" 2>/dev/null)" ]; then
            echo "Neither {results_dir} nor {parca_out_dir} has output after run — job likely failed."
            exit 1
        fi

        echo "Run {run_id} completed."
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
        # Never clobber cluster-generated outputs: ParCa cache lives in out/
        # and colony results accumulate in results/. Both are bind-mounted into
        # the container; rsync must not delete them between runs.
        "--exclude=out/",
        "--exclude=results/",
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


def rsync_workspace_back(
    settings: HpcSettings,
    local_ws: Path,
    remote_path: str | None = None,
) -> dict:
    """rsync the HPC remote ``results/`` and ``out/`` directories back to the local workspace.

    This is the reverse of ``rsync_workspace()``: after a SLURM job completes
    on the cluster, call this to pull generated artifacts (simulation output,
    ParCa cache, colony results) back to the local machine.

    Uses ``--partial --inplace`` so a re-pull after a partial failure is cheap.

    Args:
        settings: HPC cluster connection settings.
        local_ws: Local workspace directory (must exist).
        remote_path: Override the remote path.  Defaults to
            ``{hpc_repo_base_path}/{ws_name}``.

    Returns:
        A dict with keys:

        - ``state``: ``"ok"`` if rsync succeeded, ``"partial"`` if it returned
          a non-warning exit code that still transferred some data
          (exit 23 or 24).
        - ``bytes``: total bytes transferred as reported by ``--info=stats2``
          (int, 0 if parsing fails).
        - ``duration_s``: wall-clock seconds for the rsync call (float).
        - ``dirs``: list of dirs pulled (``["results", "out"]``).

    Raises:
        RuntimeError: rsync failed with an exit code that does not indicate
            partial transfer (anything other than 0, 23, 24).
    """
    import time

    require_configured(settings)
    ws_name = local_ws.name
    target = f"{settings.slurm_submit_user}@{settings.slurm_submit_host}"
    remote_base = remote_path or f"{settings.hpc_repo_base_path}/{ws_name}"
    remote_root = f"{target}:{remote_base}"

    t0 = time.monotonic()
    dirs = ["results", "out"]
    total_bytes = 0
    final_state = "ok"

    for d in dirs:
        local_dest = local_ws / d
        local_dest.mkdir(parents=True, exist_ok=True)
        cmd = [
            "rsync", "-az", "--partial", "--inplace",
            "--no-o", "--no-g", "--omit-dir-times",
            "--info=stats2",
            "-e", _ssh_opt_str(settings),
            f"{remote_root}/{d}/",
            str(local_dest) + "/",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            # Parse total bytes from stats line like:
            # "Total transferred file size: 12345678 bytes"
            for line in r.stdout.splitlines():
                m = re.search(r"Total transferred file size:\s+(\d+)", line)
                if m:
                    total_bytes += int(m.group(1))
                    break
        elif r.returncode in (23, 24):
            # 23 = partial transfer due to error, 24 = partial transfer
            # due to vanished source files — still useful, mark partial
            final_state = "partial"
            for line in r.stdout.splitlines():
                m = re.search(r"Total transferred file size:\s+(\d+)", line)
                if m:
                    total_bytes += int(m.group(1))
                    break
        else:
            raise RuntimeError(
                f"rsync pullback for {d}/ failed (exit {r.returncode}): "
                f"{_mask(r.stderr[-1000:], settings)}"
            )

    duration_s = time.monotonic() - t0
    return {
        "state": final_state,
        "bytes": total_bytes,
        "duration_s": round(duration_s, 2),
        "dirs": dirs,
    }


def submit_build_job(settings: HpcSettings, local_ws: Path) -> dict:
    """Infer GHCR image, SCP an sbatch build script, and submit it.

    GHCR-pull build pipeline (no Docker locally, no --fakeroot on cluster):
      1. Infer GHCR image ref from git remote origin
           (override via GHCR_IMAGE in workspace/.pbg/hpc.env)
      2. build_image_script → write sbatch locally
      3. _scp_file         (≈ SlurmService.scp_upload)
      4. sbatch --parsable  → int job_id

    No ``rsync_workspace()`` call: the code lives in the GHCR image (built by
    GitHub Actions on every push) — no workspace files are needed on the cluster
    for the build step.  ``rsync_workspace()`` is still called by
    ``submit_run_job()`` to place bind-mount targets (``out/``, ``results/``)
    before the first simulation run.

    Returns::

        {"build_id": str, "slurm_job_id": int, "log_path": str, "ghcr_image": str}
    """
    require_configured(settings)
    ws_name = local_ws.name

    # Explicit setting takes priority; fall back to git-remote inference.
    ghcr_image = settings.ghcr_image or _infer_ghcr_image(local_ws)
    if not ghcr_image:
        raise RuntimeError(
            f"Cannot determine GHCR image for workspace '{ws_name}'. "
            "Either set GHCR_IMAGE in workspace/.pbg/hpc.env, or ensure the "
            "workspace has a GitHub remote (git remote get-url origin)."
        )

    build_id = uuid.uuid4().hex[:8]

    # Gather git metadata from the LOCAL workspace at submit time so the
    # sbatch script has baked-in tags and never relies on the remote workspace
    # path existing (which it doesn't until the first rsync).
    def _git_local(cmd: list[str]) -> str:
        try:
            return subprocess.run(
                ["git", "-C", str(local_ws)] + cmd,
                capture_output=True, text=True, timeout=5, check=False,
            ).stdout.strip()
        except Exception:
            return ""

    local_sha = _git_local(["rev-parse", "--short", "HEAD"])
    # GitHub Actions tags branch names with '/' replaced by '-'.
    raw_branch = _git_local(["rev-parse", "--abbrev-ref", "HEAD"])
    branch_tag = raw_branch.replace("/", "-") if raw_branch else ""

    # Step 1: generate sbatch script and write it locally.
    script_text = build_image_script(
        settings, ws_name, build_id, ghcr_image,
        local_sha=local_sha, branch_tag=branch_tag,
    )
    hpc_dir = local_ws / ".pbg" / "hpc"
    hpc_dir.mkdir(parents=True, exist_ok=True)
    local_script = hpc_dir / f"build-{build_id}.sbatch"
    local_script.write_text(script_text)

    # Step 2: SCP script to cluster (≈ SlurmService.scp_upload).
    log_base = settings.hpc_log_base_path or f"{settings.hpc_repo_base_path}/logs"
    log_dir = f"{log_base}/{ws_name}"
    remote_script = f"{log_dir}/build-{build_id}.sbatch"
    _scp_file(settings, local_script, remote_script, timeout=30)

    # Step 3: sbatch --parsable → int job_id.
    r = _ssh(settings, f"sbatch --parsable {remote_script}", timeout=30)
    if r.returncode != 0:
        raise RuntimeError(
            f"sbatch failed: {_mask(r.stderr[-500:], settings)}"
        )
    slurm_job_id = int(r.stdout.strip())
    log_path = f"{log_dir}/vivarium-build-{ws_name}-{build_id}.out"
    return {
        "build_id": build_id,
        "slurm_job_id": slurm_job_id,
        "log_path": log_path,
        "ghcr_image": ghcr_image,
    }


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
    return {"slurm_job_id": slurm_job_id, "log_path": log_path, "run_id": run_id}
