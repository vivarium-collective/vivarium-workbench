"""Pure builder for the ``POST /api/composite-test-run`` route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_composite_test_run``.  The handler starts a DETACHED
composite run: it writes a run-request JSON file + a ``runs_meta`` row, spawns
the ``run-composite`` CLI detached via :func:`run_registry.spawn_detached`, and
returns ``202 {run_id, status: "running"}`` immediately (the browser then polls
``/api/composite-run/<id>/status``).

The builder returns ``(body, status)`` so the FastAPI route wraps every path in
``JSONResponse`` (preserving the non-200 codes — 400 / 429 / 500 — verbatim).
No ``import server`` here.

``composite_runs`` (as ``cr``) and ``run_registry`` are bound at module level so
tests monkeypatch ``cr.generate_run_id`` (a fixed id), ``run_registry.
count_running`` (0 / ≥ cap), and ``run_registry.spawn_detached`` (a fake pid, or
to raise) and never spawn a real subprocess.

The workspace root is threaded explicitly as ``ws_root`` (replacing the server
``WORKSPACE`` global / ``workspace_paths()`` helper) so the module stays
importable standalone and flip-ready.  ``_ws_add_to_sys_path`` is replicated
inline (the workspace's own ``pbg_<slug>`` package must be importable when the
detached CLI is later spawned).  The legacy server.py handler keeps its inline
logic for now — the dedup happens at the flip.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import run_registry
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Make the workspace's own Python package(s) importable.

    Replicates ``server._ws_add_to_sys_path`` (which uses the ``WORKSPACE``
    global) with the root threaded explicitly: insert ``ws_root`` on ``sys.path``
    so the workspace package (e.g. ``pbg_chromosome_rep1``) resolves as a
    top-level package.
    """
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


# Step count above which a run gets a non-blocking "this may be heavy" warning
# in the launch response (full-state snapshots per step add up).
_HEAVY_RUN_STEPS = 250


def _dispatch_build_image_run(simulator_id, overrides, emit_paths, config_filename, spec_id):
    """Dispatch a registered build's PRE-BUILT image via sms-api run_simulation
    (plan B) and return the composite-test-run response tuple.

    Used for both composite-card Cloud paths — an explicit run_target=deployment
    + build, and a pinned/materialized-build workspace. Skips the compose
    git-install path entirely: run_simulation runs the build's committed code
    from its image, so no compose allow-list entry (and no local push) is needed.
    Async — returns a synthetic ``remote-sim-<id>`` run_id the loom polls; the run
    surfaces in the Simulations/Runs tab via remote_simulations.
    """
    from vivarium_workbench.lib import remote_run_views as _rrv
    from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError

    overrides = overrides or {}
    client = SmsApiClient(_rrv._sms_api_base())
    # Resolve a real config for this build. sms-api defaults to
    # 'api_simulation_default.json', which only exists in the vEcoli-lineage
    # repos (e.g. v2ecoli) — a build whose repo lacks it (e.g. the sms-ecoli fork,
    # which carries only CD-specific configs) 404s when the config is omitted. So
    # when the caller didn't pin one, ask discovery and prefer the whole-cell
    # default.
    #
    # #1113: do NOT fall back to the build's first available config (cfgs[0]).
    # ``spec_id`` (the composite actually requested) never mapped to that pick, so
    # cfgs[0] — the alphabetically-first file in the build's repo — silently ran an
    # UNRELATED simulation (e.g. every ecoli_baseline card-run against build #211,
    # which lacks the whole-cell default, resolved to fss_pathway_oe_native_oe_carina
    # and burned real Batch/ParCa compute before failing later for an unrelated
    # reason). Fail CLOSED instead: a wrong-config dispatch that happens to succeed
    # would attribute a real result to the wrong composite. The caller must pick a
    # config explicitly when the build has no whole-cell default.
    if not config_filename:
        cfgs: list = []
        try:
            disc = client._get("/api/v1/simulations/discovery",
                               params={"simulator_id": int(simulator_id)})
            cfgs = disc.get("config_filenames") or []
        except Exception:  # noqa: BLE001 — discovery is best-effort; treated as "none discovered"
            cfgs = []
        if "api_simulation_default.json" in cfgs:
            config_filename = "api_simulation_default.json"
        else:
            _avail = ", ".join(cfgs) if cfgs else "(none discovered)"
            return {
                "error": (f"Cloud build #{simulator_id} has no default config for "
                          f"'{spec_id}'. Pick a config explicitly before dispatching "
                          f"— configs available on this build: {_avail}."),
                "reason": "no-config-for-composite",
                "run_target": "deployment",
                "spec_id": spec_id,
                "available_configs": cfgs,
            }, 409
    try:
        sim = client.run_simulation(
            simulator_id=int(simulator_id),
            num_generations=int(overrides.get("n_generations") or 1),
            num_seeds=int(overrides.get("n_seeds") or 1),
            run_parca=True,
            observables=list(emit_paths or []),
            config_filename=config_filename,
            description=f"composite-card cloud run: {spec_id}",
        )
    except SmsApiError as e:
        return {"error": f"cloud dispatch failed: {e}",
                "reason": "dispatch-failed", "run_target": "deployment"}, 502
    sim_db_id = sim.get("database_id") or sim.get("simulation_id")
    return {"run_id": f"remote-sim-{sim_db_id}", "status": "running",
            "remote": True, "simulation_id": sim_db_id,
            "experiment_id": sim.get("experiment_id")}, 202


@dataclass(frozen=True)
class CloudTarget:
    """A resolved Cloud image-dispatch target for a composite Cloud run.

    ``source`` records which of the three resolution routes produced it:
      * ``"explicit"``      — Environment picker → Cloud + a selected build
                              (``run_target="deployment"`` + ``build``),
      * ``"session-build"`` — this session's materialized ``.viv-build.json``,
      * ``"pinned"``        — the deployment-wide ``VIVARIUM_WORKBENCH_REMOTE_PINNED`` pin.

    All three dispatch the same way: :func:`_dispatch_build_image_run` runs the
    build's PRE-BUILT image via sms-api ``run_simulation`` (plan B / #1101), which
    needs only ``simulator_id`` — no local push, no compose allow-list entry.
    """

    simulator_id: int
    repo_url: str
    commit: str
    source: str


_COMPOSE_TRUTHY = {"1", "true", "yes", "on"}


def _compose_dispatch_allowed() -> bool:
    """Whether the DEAD compose/git-install Cloud path is opted into.

    The compose path (``invoke_run(target="deployment")`` → ``run_remote`` →
    sms-api ``/compose/v1``) installs the workspace FROM GIT on the deployment
    and 403s for any repo not on sms-api's compose allow-list — which is nearly
    all of them. So it is unreachable from the Cloud Run path unless an operator
    explicitly sets ``VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH=1``.
    """
    from vivarium_workbench.lib.env_compat import get_env

    return (get_env("ALLOW_COMPOSE_DISPATCH", "") or "").strip().lower() in _COMPOSE_TRUTHY


def cloud_build_actions(commit: "str | None") -> list[dict]:
    """The actionable buttons a 'no cloud image' response offers the card.

    Rendered as buttons by the composite card / loom ``ExploreRunBar``: "Build on
    cloud" opens the Builds panel pre-filled with the commit (#1093), "Switch
    Environment to Local" flips the run scope so a local run needs no image.
    """
    return [
        {"label": "Build on cloud", "panel": "builds", "commit": (commit or None)},
        {"label": "Switch Environment to Local", "panel": "environment"},
    ]


def _no_image(commit: "str | None", *, reason: str) -> tuple[dict, int]:
    """The actionable 409 for a Cloud-scoped run with no cloud image to run."""
    c = str(commit or "")
    return {
        "error": ("No cloud image exists for this workspace"
                  + (f" at commit {c[:7]}" if c else "")
                  + ". Cloud runs execute a registered build's pre-built image — "
                    "build one on the cloud, or switch the Environment to Local."),
        "reason": reason,
        "run_target": "deployment",
        "actions": cloud_build_actions(c),
    }, 409


def _explicit_build_ref(body: dict) -> "dict | None":
    """The selected build to install code from on the (opt-in) compose path.

    Shaped as the run-request ``build_ref`` (``{simulator_id, repo_url, commit}``)
    when the body carries a build with a commit + repo_url, else ``None`` (the
    stock pinned compose path resolves its own commit inside ``run_remote``).
    """
    build = body.get("build") or None
    if isinstance(build, dict) and build.get("commit") and build.get("repo_url"):
        return {"simulator_id": build.get("simulator_id"),
                "repo_url": build.get("repo_url"),
                "commit": build.get("commit")}
    return None


def resolve_cloud_target(
    ws_root: Path, body: dict
) -> "CloudTarget | tuple[dict, int] | None":
    """The ONE typed Cloud-vs-local dispatch decision for a composite-test-run.

    Returns exactly one of:
      * :class:`CloudTarget` — dispatch the registered build's pre-built image
        (:func:`_dispatch_build_image_run`);
      * ``None`` — a local run (the workspace resolves to ``local`` and no
        explicit Cloud run was requested); the caller runs locally, unchanged;
      * ``(error_dict, 409)`` — a Cloud run was requested/implied but no cloud
        image exists for it. The payload carries an actionable ``actions[]`` list
        ("Build on cloud" / "Switch Environment to Local") instead of silently
        falling into the dead compose/git-install path.

    This collapses the three fall-throughs the composite-card Run path used to
    have — an explicit deployment build lacking a ``simulator_id``, a pinned
    workspace where no build resolves, and the frozen-``PinnedConfig`` ``.get``
    ``AttributeError`` — into one decision with an explicit outcome.
    """
    from vivarium_workbench.lib import remote_pinned

    build = body.get("build") or {}
    if not isinstance(build, dict):
        build = {}

    # (1) Explicit Cloud run against a SELECTED build (Environment picker → Cloud).
    if str(body.get("run_target") or "").strip() == "deployment":
        if build.get("simulator_id"):
            return CloudTarget(int(build["simulator_id"]),
                               str(build.get("repo_url") or ""),
                               str(build.get("commit") or ""), "explicit")
        return _no_image(build.get("commit"), reason="no-build")

    # (2) No explicit Cloud request: the workspace's own resolved target decides.
    #     A pinned/materialized workspace resolves to "deployment" WITHOUT the
    #     loom sending run_target (it only sends it when the scope is toggled).
    if remote_pinned.resolve_run_target(ws_root) != "deployment":
        return None  # local run — unchanged

    # (3) Deployment workspace: dispatch the resolved build's image. Session build
    #     first (the picker's switched build), else the deployment-wide pin.
    session_build = remote_pinned.resolved_from_session_build(ws_root)
    if session_build:
        return CloudTarget(int(session_build["simulator_id"]),
                           str(session_build.get("repo_url") or ""),
                           str(session_build.get("commit") or ""), "session-build")

    from vivarium_workbench.lib import remote_run_views as _rrv
    from vivarium_workbench.lib.sms_api_client import SmsApiClient

    client = SmsApiClient(_rrv._sms_api_base())
    # resolve_pinned_simulator_id handles the frozen PinnedConfig + unreachable
    # sms-api correctly (returns None), so no `.get("simulator_id")` on a dataclass.
    sid = remote_pinned.resolve_pinned_simulator_id(client, ws_root)
    if sid is not None:
        cfg = remote_pinned.pinned_config()
        return CloudTarget(int(sid), str(cfg.repo_url if cfg else ""), "", "pinned")
    return _no_image(None, reason="no-build")


def composite_test_run(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Start a detached composite run. Returns ``(response_dict, status_code)``.

    Behaviour-preserving port of ``_post_composite_test_run`` (body
    ``{id, overrides?, steps?, label?, emit_paths?}``):

      * missing ``id``                 → ``({"error": "missing id"}, 400)``
      * at concurrency cap             → ``({"error": "too many runs in
        progress — wait for one to finish"}, 429)``
      * spawn failure                  → ``({"error": f"spawn failed: {e}",
        "run_id": run_id}, 500)`` (after ``complete_metadata(status="failed")``)
      * happy path                     → ``({"run_id": run_id,
        "status": "running"}, 202)``
    """
    _ws_add_to_sys_path(ws_root)
    from vivarium_workbench.lib.composite_runs import auto_label

    spec_id = (body.get("id") or "").strip()
    overrides = body.get("overrides") or {}
    # float: a temporal composite may run a fractional duration (e.g. 2.5); an
    # int here truncated it. A whole number stays whole (JSON 5 → 5.0 → runs 5).
    steps = float(body.get("steps") or 5)
    label = (body.get("label") or "").strip() or auto_label(overrides)
    emit_paths = body.get("emit_paths") or []
    if not isinstance(emit_paths, list):
        emit_paths = []
    # Loom save-point fork: a full captured state to start this run FROM.
    seed_state = body.get("seed_state") or {}
    if not isinstance(seed_state, dict):
        seed_state = {}
    # Config declaration surface: a composite-run body may declare analyses
    # and/or visualizations to auto-run on flush (study-shaped, possibly
    # scale-grouped -- NOT flattened here; ephemeral_study.merge_declarations
    # does that downstream). The documented shape is a scale-grouped DICT
    # (e.g. {"single": [...], "multigeneration": [...]}), which
    # merge_declarations._flatten_analyses already accepts directly -- so
    # both a list AND a dict must pass through unchanged here. Only a
    # genuinely invalid type (string, number, etc.) degrades to the empty
    # shape, so the Task 5 merge is a no-op.
    declared_analyses = body.get("analyses") or []
    if not isinstance(declared_analyses, (list, dict)):
        declared_analyses = []
    declared_visualizations = body.get("visualizations") or []
    if not isinstance(declared_visualizations, (list, dict)):
        declared_visualizations = []
    declared_results = {
        "analyses": declared_analyses,
        "visualizations": declared_visualizations,
    }
    if not spec_id:
        return {"error": "missing id"}, 400
    # A scaffolded study.yaml carries ``composite: replace_me.composites.placeholder``
    # (scaffold_yaml.py) as a sentinel the author is meant to REPLACE with a real
    # composite id. It never names a runnable composite: dispatching it spawns a
    # detached run that can only fail to build, and until it does the run shows as
    # a phantom "running" row with no backing model (the placeholder-run bug).
    # Refuse it here, at the entry point and BEFORE any runs_meta row is written,
    # so a placeholder can never become a run at all.
    if spec_id.startswith("replace_me."):
        return {
            "error": (
                f"spec_id {spec_id!r} is the scaffold placeholder, not a real "
                "composite -- replace it with a registered composite id before "
                "running."
            )
        }, 400

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    pkg = ws_data.get("package_path") or (
        "pbg_" + ws_data.get("name", "").replace("-", "_"))
    db_file = str(WorkspacePaths.load(ws_root).pbg / "composite-runs.db")

    if run_registry.count_running(db_file) >= run_registry.CONCURRENCY_CAP:
        return (
            {"error": "too many runs in progress — wait for one to finish"},
            429,
        )

    from vivarium_workbench.lib import run_core

    # Run-path decision (c1): resolve_cloud_target is the ONE typed decision for
    # "Cloud image, local, or dead-end". A CloudTarget dispatches the build's
    # PRE-BUILT image (plan B / #1101 — needs only simulator_id, no local push and
    # no compose allow-list entry). None is a local run. A (dict, 409) is an
    # actionable dead-end (no cloud image) carrying an actions[] list, returned
    # verbatim instead of silently falling into the dead compose/git-install path.
    #
    # Item 18 context: a pinned/materialized workspace resolves to "deployment"
    # WITHOUT the loom sending run_target (it only sends it when the Environment
    # scope is toggled to Cloud); resolve_cloud_target routes both the explicit
    # and the pinned case to the same image dispatch, so they can never drift.
    build_ref = None
    target = "local"
    cloud = resolve_cloud_target(ws_root, body)
    if isinstance(cloud, CloudTarget):
        cfg_fn = (body.get("config_filename") or "").strip() or None
        return _dispatch_build_image_run(
            cloud.simulator_id, overrides, emit_paths, cfg_fn, spec_id)
    if isinstance(cloud, tuple):
        # No cloud image resolved for a Cloud-scoped run. The compose/git-install
        # path is DEAD for almost every repo (sms-api's allow-list 403s it), so it
        # is unreachable unless an operator opts in with
        # VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH=1. Without the opt-in, return
        # the actionable 409 (Build on cloud / Switch to Local) rather than
        # dispatch a run that can only fail in the run log.
        if not _compose_dispatch_allowed():
            return cloud
        target = "deployment"
        build_ref = _explicit_build_ref(body)
        # Opt-in compose path: the workspace is installed FROM GIT on the
        # deployment, so it must be clean + pushed AND the repo must be on
        # sms-api's compose allow-list. Warn up front on the git side; the
        # allow-list is enforced server-side (a 403 in the run log).
        from vivarium_workbench.lib import remote_run as _remote_run
        pf = _remote_run.remote_dispatch_preflight(ws_root)
        if not pf.get("ok"):
            return {"error": pf.get("message", "workspace not ready to run remotely"),
                    "reason": pf.get("reason"), "preflight": pf,
                    "run_target": "deployment",
                    "actions": cloud_build_actions(pf.get("sha"))}, 409
    # else: cloud is None → a local run (target stays "local").
    try:
        plan = run_core.invoke_run(ws_root, spec_id=spec_id, config=overrides,
                                   db_path=db_file, label=label, n_steps=steps,
                                   target=target)
    except run_core.RunTargetUnavailable as e:
        return {"error": str(e)}, 409
    run_id = plan.run_id
    run_dir = WorkspacePaths.load(ws_root).pbg / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_rel = str((run_dir / "run.log").relative_to(ws_root))
    request_path = run_dir / "request.json"
    request_path.write_text(json.dumps({
        "run_id": run_id,
        "spec_id": spec_id,
        "pkg": pkg,
        "workspace": str(ws_root),
        "overrides": overrides,
        "steps": steps,
        "emit_paths": emit_paths,
        "seed_state": seed_state,
        "declared_results": declared_results,
        "db_file": db_file,
        "log_path": log_rel,
        # SP-D2: which target the detached runner dispatches to (local subprocess
        # vs. sms-api /compose/v1). `run_target_for` stamps 'deployment' for a
        # materialized remote build (.viv-build.json), 'local' otherwise.
        "target": plan.target,
        # Cloud-run-against-build: the selected build to install code from
        # (git+repo_url@commit) instead of the local tree. None on every other path.
        "build_ref": build_ref,
    }), encoding="utf-8")

    # Reproducibility manifest (spec Part A): the composite path's full replay
    # record — params here IS the full effective config for this launch (no
    # separate baseline layer at this call site), so it doubles as both the
    # override-delta (params_json, unchanged) and the manifest's full params.
    # emitter is not yet resolved at launch time (that happens later, inside
    # the detached run) so it's recorded None here; runtime has no study
    # block on the composite path.
    manifest = cr.build_run_manifest(
        origin="composite", spec_id=spec_id, params=overrides, n_steps=steps,
        emitter=None, emit_paths=emit_paths, runtime={}, pkg=pkg,
        ws_root=ws_root,
    )

    conn = cr.connect(db_file)
    try:
        # SP-B: runs are durable — no prune-to-20 eviction. Deletion is an
        # explicit Sim-DB action (composite_runs.delete_run), not auto-eviction.
        cr.save_metadata(conn, spec_id=spec_id, run_id=run_id,
                         params=overrides, label=label,
                         started_at=time.time(), n_steps=steps,
                         log_path=log_rel, workspace=ws_root, manifest=manifest)
        try:
            pid = run_registry.spawn_detached(
                request_path, workspace=ws_root,
                log_path=run_dir / "run.log")
        except Exception as e:  # noqa: BLE001 — surface the spawn failure
            cr.complete_metadata(conn, run_id=run_id, n_steps=0,
                                 status="failed", workspace=ws_root)
            return {"error": f"spawn failed: {e}", "run_id": run_id}, 500
        cr.set_pid(conn, run_id=run_id, pid=pid)
    finally:
        conn.close()

    resp = {"run_id": run_id, "status": "running"}
    # Non-blocking cost heads-up: the loom writes a full-state snapshot per step,
    # so a long run can produce a large snapshot DB and a slow trajectory load.
    # The run still starts — snapshots are decimated for display and the run
    # self-terminates if the snapshot DB exceeds its 1 GiB budget — but a warning
    # lets the user cut the run short or pass explicit emit_paths first.
    if steps > _HEAVY_RUN_STEPS:
        resp["warning"] = (
            f"Long run ({int(steps)} steps): the loom captures a full-state snapshot "
            f"per step, so the snapshot DB can grow large and loading the full "
            f"trajectory is slower. Frames are decimated for display and the run "
            f"self-terminates if snapshots exceed 1 GiB. For a lighter, faster run use "
            f"fewer steps or pass explicit emit_paths."
        )
    return resp, 202
