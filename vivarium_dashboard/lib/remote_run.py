"""Remote-run orchestration for the vivarium-dashboard CLI.

Provides:
- ``git_pip_url(ws_root)`` — validates the git working tree is clean + pushed
  and returns a ``git+<origin>@<sha>`` pip-installable URL.
- ``run_remote(ws_root, composite_id, ...)`` — export the composite to a .pbg,
  submit to sms-api, poll until completion, and land results.zip.

The dashboard CLI's ``run-remote`` subcommand calls ``run_remote``.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from vivarium_dashboard.lib.pbg_export import export_composite_pbg  # noqa: E402 (module-level for patch)

if TYPE_CHECKING:
    from vivarium_dashboard.lib.sms_api_client import SmsApiClient

# Default poll interval in seconds
_DEFAULT_POLL_INTERVAL = 10.0


def git_pip_url(ws_root: "Path | str") -> str:
    """Return ``git+<origin>@<sha>`` for the workspace repo.

    Guards
    ------
    - Raises :exc:`RuntimeError` if the working tree has uncommitted changes
      or untracked files (``git status --porcelain`` is non-empty).
    - Raises :exc:`RuntimeError` if HEAD is not present on any configured
      remote (i.e. there are unpushed commits).

    Returns
    -------
    str
        A pip-installable VCS URL, e.g.
        ``"git+https://github.com/org/ws.git@abc1234"``.
    """
    ws_root = Path(ws_root).resolve()

    # --- dirty tree check ---
    status = _git(ws_root, "status", "--porcelain")
    if status.strip():
        raise RuntimeError(
            f"Workspace at {ws_root} has uncommitted or untracked changes "
            f"(git status --porcelain returned output).\n"
            f"Please commit and push all changes before running remotely.\n"
            f"Dirty files:\n{status}"
        )

    # --- get HEAD sha ---
    sha = _git(ws_root, "rev-parse", "HEAD").strip()

    # --- unpushed check: HEAD must be reachable from at least one remote ref ---
    # `git branch -r --contains <sha>` lists remotes that contain the commit.
    remote_refs = _git(ws_root, "branch", "-r", "--contains", sha).strip()
    if not remote_refs:
        raise RuntimeError(
            f"HEAD commit {sha[:8]} is not pushed to any remote branch.\n"
            f"Please push before running remotely."
        )

    # --- get origin URL ---
    try:
        origin_url = _git(ws_root, "remote", "get-url", "origin").strip()
    except RuntimeError:
        # Fall back to listing all remotes and taking the first
        remotes = _git(ws_root, "remote").strip().split()
        if not remotes:
            raise RuntimeError(
                f"Workspace at {ws_root} has no git remotes configured."
            )
        origin_url = _git(ws_root, "remote", "get-url", remotes[0]).strip()

    # Normalise: file:// absolute paths are valid pip VCS URLs
    return f"git+{origin_url}@{sha}"


def run_remote(
    ws_root: "Path | str",
    composite_id: str,
    client: "SmsApiClient | None" = None,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    dest: "Path | None" = None,
) -> Path:
    """Export a composite, submit to sms-api, poll, and land results.zip.

    Parameters
    ----------
    ws_root:
        Workspace root directory.
    composite_id:
        Composite spec id (e.g. ``"pbg_my_ws.composites.my_composite"``).
    client:
        ``SmsApiClient`` pointed at the sms-api tunnel.  If *None*, a default
        client is constructed (``http://localhost:8080``).
    poll_interval:
        Seconds between status polls.
    dest:
        Directory for the landed ``results.zip``.  Defaults to
        ``<ws_root>/.pbg/remote-results/``.

    Returns
    -------
    Path
        Path to the downloaded ``results.zip``.
    """
    from vivarium_dashboard.lib.sms_api_client import SmsApiClient as _SmsApiClient
    from vivarium_dashboard.lib.workspace_deps_views import _sms_api_base

    ws_root = Path(ws_root).resolve()

    if client is None:
        client = _SmsApiClient(_sms_api_base())

    if dest is None:
        dest = ws_root / ".pbg" / "remote-results"
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    # Get git pip URL (validates clean + pushed state)
    pip_url = git_pip_url(ws_root)

    # Export composite to a temporary .pbg file
    with tempfile.NamedTemporaryFile(suffix=".pbg", delete=False) as tmp:
        pbg_path = Path(tmp.name)

    try:
        export_composite_pbg(ws_root, composite_id, pbg_path)
        pbg_bytes = pbg_path.read_bytes()
    finally:
        try:
            pbg_path.unlink()
        except OSError:
            pass

    # Submit
    print(f"Submitting composite '{composite_id}' to sms-api…")
    sim_id = client.compose_submit(pbg_bytes, extra_pip_deps=[pip_url])
    print(f"Submitted. Simulation id: {sim_id}")

    # Poll until terminal state
    while True:
        status_data = client.compose_status(sim_id)
        status = status_data.get("status", "unknown")
        print(f"  status: {status}")
        if status in ("completed", "failed", "error", "cancelled"):
            break
        time.sleep(poll_interval)

    if status != "completed":
        raise RuntimeError(
            f"Remote run {sim_id} ended with status '{status}': {status_data}"
        )

    # Download results
    results_path = client.download_compose_results(sim_id, dest)
    print(f"Results landed at: {results_path}")
    return results_path


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> str:
    """Run a git command in *cwd*, return stdout. Raises RuntimeError on failure."""
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (rc={result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout
