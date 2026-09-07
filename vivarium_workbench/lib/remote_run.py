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

from vivarium_workbench.lib.pbg_export import export_composite_pbg  # noqa: E402 (module-level for patch)

if TYPE_CHECKING:
    from vivarium_workbench.lib.sms_api_client import SmsApiClient

# Default poll interval in seconds
_DEFAULT_POLL_INTERVAL = 10.0
# Hardening (external users): bound the status-poll loop so a stuck remote run can't
# hang the poller forever, and tolerate a few *consecutive* transient errors so a
# single network blip doesn't fail a multi-hour run. Env-tunable for slow links.
_DEFAULT_POLL_TIMEOUT = 7200.0  # 2 h wall-clock ceiling; <= 0 disables the deadline
_MAX_CONSECUTIVE_POLL_ERRORS = 5


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
    # Exclude the `.viv-build.json` provenance stamp: it's bookkeeping the
    # workbench (re)writes on every build-switch, and a stale/regenerated stamp
    # must never block a remote dispatch (#858). The primary fix stamps it
    # before the baseline commit so it's normally clean anyway; this is
    # defense-in-depth for caches materialized by an older workbench.
    status = _git(ws_root, "status", "--porcelain", "--", ".", ":!.viv-build.json")
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
    n_steps: int = 1,
    overrides: "dict | None" = None,
    poll_timeout: float = _DEFAULT_POLL_TIMEOUT,
    skip_preflight: bool = False,
    expected_variant_count: "int | None" = None,
    analysis_options: "dict | None" = None,
) -> Path:
    """Export a composite, submit to sms-api, poll, and land results.tar.gz.

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
        Directory for the landed ``results.tar.gz``.  Defaults to
        ``<ws_root>/.pbg/remote-results/``.
    analysis_options:
        v2ecoli-shaped ``{scale: {name: params}}`` analyses to run server-side
        (composite-auto-results Task 8 — mirrors the study path's
        ``remote_run_views.remote_run_submit`` injection). Forwarded to
        ``client.compose_submit``'s own ``analysis_options`` param.  Default
        ``None`` keeps every pre-Task-8 caller unchanged.
    poll_timeout:
        Wall-clock ceiling (seconds) for the whole poll loop; raises
        :exc:`TimeoutError` if the run hasn't reached a terminal state by then.
        Pass ``<= 0`` to disable (wait indefinitely).  Defaults to 2 h.
    skip_preflight:
        Bypass the pre-spend preflight (:func:`preflight.preflight_composite_run`)
        that validates the composite-id + overrides request LOCALLY before
        dispatch. A preflight failure otherwise raises :exc:`PreflightError` and
        aborts *before* any GovCloud spend. Only skip when the request has
        already been validated.
    expected_variant_count:
        Forwarded to the preflight so a ``variants`` grid must expand to exactly
        this many branches (e.g. 84 for Run-4 pathway-expression).

    Returns
    -------
    Path
        Path to the downloaded ``results.tar.gz``.
    """
    from vivarium_workbench.lib.sms_api_client import SmsApiClient as _SmsApiClient
    from vivarium_workbench.lib.workspace_deps_views import _sms_api_base

    ws_root = Path(ws_root).resolve()

    if client is None:
        client = _SmsApiClient(_sms_api_base())

    if dest is None:
        dest = ws_root / ".pbg" / "remote-results"
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    # Resolve the pip URL for the workspace code, plus any workspace-pinned framework
    # versions (§3.12) so the shared runner image doesn't float to latest PyPI.
    #
    # N3 / option C: on a pinned deployment (VIVARIUM_WORKBENCH_REMOTE_PINNED) the prod
    # pod's /workspace is dirty-by-design — the workbench re-renders reports/ and touches
    # workspace.yaml at runtime — so git_pip_url's clean-tree check can never hold there.
    # Instead derive the commit from sms-api's already-resolved *built* simulator (the same
    # resolver the pinned "Run on remote" study card uses) and ship git+<repo>@<commit>: no
    # local git, pinned by construction to the commit sms-api built + keyed its ParCa cache
    # by. Local dev (unpinned) keeps the clean+pushed git_pip_url path.
    from vivarium_workbench.lib import remote_pinned
    cfg = remote_pinned.pinned_config()
    if cfg is not None:
        resolved = remote_pinned.resolve_pinned_build(client, cfg.repo_url, cfg.branch)
        repo = cfg.repo_url.strip().rstrip("/")
        if repo.endswith(".git"):
            repo = repo[: -len(".git")]
        pip_url = f"git+{repo}.git@{resolved['commit']}"
    else:
        pip_url = git_pip_url(ws_root)
    extra_pip_deps = [pip_url, *workspace_pinned_deps(ws_root)]

    # Pre-spend preflight: validate the composite-id + overrides request LOCALLY
    # before committing any GovCloud spend. Turns ~15 silent wrong-but-successful
    # failure modes (a dropped process swap, a typo'd emit path, an empty variant
    # grid, an incoherent step count) into a loud, aggregated local error. A
    # failure raises PreflightError and aborts here — the dispatch never happens.
    if not skip_preflight:
        from vivarium_workbench.lib.preflight import preflight_composite_run

        report = preflight_composite_run(
            ws_root, composite_id, overrides,
            n_steps=n_steps,
            expected_variant_count=expected_variant_count,
        )
        print(report.summary())

    # Export composite to a temporary .pbg file
    with tempfile.NamedTemporaryFile(suffix=".pbg", delete=False) as tmp:
        pbg_path = Path(tmp.name)

    try:
        export_composite_pbg(ws_root, composite_id, pbg_path, overrides=overrides)
        pbg_bytes = pbg_path.read_bytes()
    finally:
        try:
            pbg_path.unlink()
        except OSError:
            pass

    # Submit. sms-api's `interval_time` query param IS the step count — it sets
    # `end_time_point`, which the runner passes as `run_pbg.py -n <steps>` (§3.5).
    # sms-api hard-rejects `interval_time` outside 0..1000 with a 400
    # (compose.py:121-122), so clamp here at the boundary that owns the contract
    # rather than trusting every caller. Local runs never reach this path, so
    # their step counts stay unbounded.
    steps = max(0, min(int(n_steps), 1000))
    print(f"Submitting composite '{composite_id}' to sms-api ({steps} steps)…")
    # analysis_options is added to the call only when present, so a client test
    # double built against the pre-Task-8 compose_submit(pbg_bytes,
    # extra_pip_deps=, interval_time=) signature (no **kwargs catch-all) keeps
    # working unchanged when there's nothing to inject.
    compose_kwargs: dict = dict(extra_pip_deps=extra_pip_deps, interval_time=float(steps))
    if analysis_options:
        compose_kwargs["analysis_options"] = analysis_options
    sim_id = client.compose_submit(pbg_bytes, **compose_kwargs)
    print(f"Submitted. Simulation id: {sim_id}")

    # Poll until terminal state — bounded by a wall-clock deadline and tolerant of a
    # few consecutive transient errors (see _poll_until_terminal).
    status, status_data = _poll_until_terminal(client, sim_id, poll_interval, poll_timeout)

    if status != "completed":
        raise RuntimeError(
            f"Remote run {sim_id} ended with status '{status}': {status_data}"
        )

    # Download results (results.tar.gz — T5b)
    results_path = client.download_compose_results(sim_id, dest)
    print(f"Results landed at: {results_path}")
    return results_path


# The process-bigraph framework packages a workspace pins in its own uv.lock. We
# forward these pins to the remote runner image (§3.12) so it doesn't float to the
# latest PyPI release at container-build time and silently mismatch the workspace.
_PINNED_FRAMEWORK_PKGS = ("process-bigraph", "bigraph-schema", "pbg-emitters", "pbg-superpowers")


def workspace_pinned_deps(ws_root: "Path | str") -> list[str]:
    """Pip specs for the workspace's own framework pins, read from its ``uv.lock``.

    Best-effort + defensive: returns ``[]`` if the lockfile is absent/unparseable
    or a package isn't pinned (the runner image then floats, as it does today).
    A git-sourced pin becomes ``name @ git+<url>@<sha>``; a PyPI pin becomes
    ``name==version``. Threaded into ``extra_pip_deps`` via the existing mechanism —
    no new pinning concept, no sms-api change.
    """
    try:
        import tomllib

        lock_path = Path(ws_root) / "uv.lock"
        if not lock_path.is_file():
            return []
        data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    deps: list[str] = []
    for pkg in data.get("package", []):
        name = pkg.get("name")
        if name not in _PINNED_FRAMEWORK_PKGS:
            continue
        source = pkg.get("source") or {}
        git = source.get("git")
        if git:
            # uv encodes the resolved commit as a ``#<sha>`` fragment on the git URL.
            url, _, frag = str(git).partition("#")
            sha = frag or ""
            url = url.split("?")[0]  # strip ?branch=… — the sha is authoritative
            deps.append(f"{name} @ git+{url}@{sha}" if sha else f"{name} @ git+{url}")
        elif pkg.get("version"):
            deps.append(f"{name}=={pkg['version']}")
    return deps


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

# Terminal compose statuses that end the poll loop.
_TERMINAL_STATUSES = ("completed", "failed", "error", "cancelled")


def _poll_until_terminal(
    client: "SmsApiClient",
    sim_id: int,
    poll_interval: float,
    poll_timeout: float,
) -> "tuple[str, dict]":
    """Poll ``client.compose_status(sim_id)`` until a terminal status.

    Hardened for external users: bounded by ``poll_timeout`` (wall-clock ceiling;
    ``<= 0`` disables) and tolerant of up to ``_MAX_CONSECUTIVE_POLL_ERRORS``
    *consecutive* transient :class:`SmsApiError`s, so one network blip doesn't fail
    an otherwise-healthy multi-hour run and a stuck run can't hang the poller forever.

    Returns ``(status, status_data)`` for a terminal status. Raises
    :exc:`TimeoutError` on deadline and :exc:`RuntimeError` on persistent polling
    failure.
    """
    from vivarium_workbench.lib.sms_api_client import SmsApiError

    deadline = time.monotonic() + poll_timeout if poll_timeout and poll_timeout > 0 else None
    consecutive_errors = 0
    while True:
        try:
            status_data = client.compose_status(sim_id)
            consecutive_errors = 0
        except SmsApiError as exc:
            consecutive_errors += 1
            if consecutive_errors > _MAX_CONSECUTIVE_POLL_ERRORS:
                raise RuntimeError(
                    f"Remote run {sim_id}: sms-api status polling failed "
                    f"{consecutive_errors} times in a row (last error: {exc}). "
                    f"Is the sms-api endpoint ({client.base_url}) still reachable?"
                ) from exc
            print(
                f"  status: (transient poll error "
                f"{consecutive_errors}/{_MAX_CONSECUTIVE_POLL_ERRORS}: {exc}; retrying)"
            )
            time.sleep(poll_interval)
            continue

        status = status_data.get("status", "unknown")
        print(f"  status: {status}")
        if status in _TERMINAL_STATUSES:
            return status, status_data
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(
                f"Remote run {sim_id} did not reach a terminal state within "
                f"{poll_timeout:.0f}s (last status: '{status}'). The run may still be "
                f"executing on the deployment — check sms-api directly before retrying."
            )
        time.sleep(poll_interval)


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
