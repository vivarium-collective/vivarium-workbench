"""The SMS deployment run of a workspace composite: export a ``.pbg``, submit it to sms-api (``/compose/v1``, or a
viva-core ``/viva/v1/composites`` document run), poll until it ends, and download its results archive.

Moved verbatim from the core's ``remote_run.run_remote`` in step P2 (Runs b, docs/backend-adapters.md §2.5). The
core keeps ``remote_run.run_remote`` as a backend-blind dispatcher; it reaches :func:`run` through
``SmsLegacyRuns.run_composite`` / ``SmsLegacyUnrouted.run_composite`` and, with ``via_viva_v1=True``,
``VivaCoreRuns.run_composite``. The plugin's CLI ``run-remote`` calls :func:`run` directly with its own client.

The core names this module reads (``git_pip_url``, ``workspace_pinned_deps``, ``backend_environment``, the poll
defaults) are read through ``remote_run.<name>`` at call time, and the client's through ``sms_api_client.<name>``,
so each keeps exactly one patch target. ``export_composite_pbg`` and ``time`` are this module's own globals (tests
patch them here).
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Callable

from vivarium_workbench.lib import remote_run
from vivarium_workbench.lib.pbg_export import export_composite_pbg  # module-level for patch
from vivarium_workbench_sms import remote_pinned, remote_run_viva_v1, sms_api_client

# Hardening (external users): tolerate a few *consecutive* transient errors so a single network blip doesn't fail
# a multi-hour run (the wall-clock ceiling is the core's ``remote_run._DEFAULT_POLL_TIMEOUT``).
_MAX_CONSECUTIVE_POLL_ERRORS = 5


def run(
    ws_root: "Path | str",
    composite_id: str,
    client: "sms_api_client.SmsApiClient | None" = None,
    poll_interval: float = remote_run._DEFAULT_POLL_INTERVAL,
    dest: "Path | None" = None,
    n_steps: float = 1,
    overrides: "dict | None" = None,
    poll_timeout: float = remote_run._DEFAULT_POLL_TIMEOUT,
    skip_preflight: bool = False,
    expected_variant_count: "int | None" = None,
    analysis_options: "dict | None" = None,
    build_ref: "dict | None" = None,
    emit_paths: "list[str] | None" = None,
    *,
    via_viva_v1: bool = False,
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
        client is constructed (``sms_api_client.sms_api_base()``).
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
    emit_paths:
        Unused here (compose-api's emitter selection); accepted so every backend's run takes the same arguments.
    via_viva_v1:
        Submit as a ``/viva/v1/composites`` document run (the viva-core backend) instead of ``/compose/v1``.

    Returns
    -------
    Path
        Path to the downloaded ``results.tar.gz``.
    """
    ws_root = Path(ws_root).resolve()

    if dest is None:
        dest = ws_root / ".pbg" / "remote-results"
    if client is None:
        client = sms_api_client.SmsApiClient(sms_api_client.sms_api_base())

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
    cfg = remote_pinned.pinned_config()
    if build_ref and build_ref.get("commit") and build_ref.get("repo_url"):
        # Explicit build target: a Cloud Run against a SELECTED registered build
        # (the Environment picker's chosen build). Run against THAT build's
        # already-pushed commit — NOT the local working tree — so no clean/pushed
        # check on ws_root (git_pip_url is skipped entirely). This is what lets a
        # Cloud run fire without first pushing session-latest: build N's code is
        # already on GitHub at build_ref['commit'] and already built by sms-api.
        repo = str(build_ref["repo_url"]).strip().rstrip("/")
        if repo.endswith(".git"):
            repo = repo[: -len(".git")]
        pip_url = f"git+{repo}.git@{build_ref['commit']}"
    elif cfg is not None:
        resolved = remote_pinned.resolve_pinned_build(client, cfg.repo_url, cfg.branch)
        repo = cfg.repo_url.strip().rstrip("/")
        if repo.endswith(".git"):
            repo = repo[: -len(".git")]
        pip_url = f"git+{repo}.git@{resolved['commit']}"
    else:
        pip_url = remote_run.git_pip_url(ws_root)
    extra_pip_deps = [pip_url, *remote_run.workspace_pinned_deps(ws_root)]

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
    requested = int(n_steps)
    steps = max(0, min(requested, 1000))
    clamped = f", clamped from {requested} to {steps}" if steps != requested else ""
    print(f"Submitting composite '{composite_id}' to sms-api ({steps} steps{clamped})…")
    # analysis_options is added to the call only when present, so a client test
    # double built against the pre-Task-8 compose_submit(pbg_bytes,
    # extra_pip_deps=, interval_time=) signature (no **kwargs catch-all) keeps
    # working unchanged when there's nothing to inject.
    status_of: "Callable[[int | str], dict] | None"
    if via_viva_v1:
        run = client.create_composite_run(
            environment=remote_run.backend_environment(),
            document=json.loads(pbg_bytes),
            execution={"options": remote_run_viva_v1.viva_v1_document_options(
                remote_run.backend_environment(), steps, extra_pip_deps, analysis_options)},
            label=f"workbench: {composite_id}",
        )
        sim_id = run["id"]
        print(f"Submitted to /viva/v1/composites. Run id: {sim_id}")
        status_of = client.composite_run_status
    else:
        compose_kwargs: dict = dict(extra_pip_deps=extra_pip_deps, interval_time=float(steps))
        if analysis_options:
            compose_kwargs["analysis_options"] = analysis_options
        sim_id = client.compose_submit(pbg_bytes, **compose_kwargs)
        print(f"Submitted. Simulation id: {sim_id}")
        status_of = None

    # Poll until terminal state — bounded by a wall-clock deadline and tolerant of a
    # few consecutive transient errors (see _poll_until_terminal).
    status, status_data = _poll_until_terminal(
        client, sim_id, poll_interval, poll_timeout, **({"status_of": status_of} if status_of else {}))

    if status != "completed":
        raise RuntimeError(
            f"Remote run {sim_id} ended with status '{status}': {status_data}"
        )

    # Download results (results.tar.gz — T5b). A /viva/v1 run's output is the compose
    # simulation it became (``job_id`` = ``compose:<n>``).
    results_path = client.download_compose_results(
        remote_run_viva_v1.compose_simulation_id(client, run) if via_viva_v1 else sim_id, dest)
    print(f"Results landed at: {results_path}")
    return results_path


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

# Terminal compose statuses that end the poll loop.
_TERMINAL_STATUSES = ("completed", "failed", "error", "cancelled")


def _poll_until_terminal(
    client: "sms_api_client.SmsApiClient",
    sim_id: "int | str",
    poll_interval: float,
    poll_timeout: float,
    status_of: "Callable[[int | str], dict] | None" = None,
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
    deadline = time.monotonic() + poll_timeout if poll_timeout and poll_timeout > 0 else None
    consecutive_errors = 0
    while True:
        try:
            status_data = (status_of or client.compose_status)(sim_id)
            consecutive_errors = 0
        except sms_api_client.SmsApiError as exc:
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
