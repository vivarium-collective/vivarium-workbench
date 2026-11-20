"""The SMS composite card's cloud dispatch and its dispatch preflight (step P2 Builds B2, docs/backend-adapters.md §2.5).

Moved verbatim from the core's ``composite_test_run_views`` (the card) and ``/api/remote-dispatch-preflight``
(``api/app.py``): a registered build's PRE-BUILT image run through viva-api's ``run_simulation`` (plan B / #1101),
the config pick keyed on the composite (#1113), the one typed Cloud-vs-local decision (:func:`resolve_cloud_target`,
c1 / #1107) and the opt-in compose path (``VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH``). The core reaches it through
``SmsLegacyRuns.dispatch_composite`` / ``dispatch_preflight`` and ``SmsLegacyUnrouted.dispatch_composite`` (the
Environment picker's explicit cloud run, when no backend is selected), which import this module lazily.

Core names are read through their modules at call time (``backends.resolve_run_target``,
``remote_run.remote_dispatch_preflight``), and so are the plugin's own (``remote_pinned.<name>``,
``sms_api_client.<name>``), so every patch target stays the one it was.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from vivarium_workbench.lib import remote_run
from vivarium_workbench_sms import remote_pinned, sms_api_client

if TYPE_CHECKING:
    from vivarium_workbench.lib.backends.provider import Detached


# The repo's whole-cell default config. In the vEcoli lineage it runs the
# standard whole-cell E. coli model (the canonical whole-cell baseline), so it is
# a legitimate fallback when the build carries no config named after the requested
# composite — but NEVER the alphabetically-first arbitrary file (#1113).
_WHOLE_CELL_DEFAULT_CONFIG = "api_simulation_default.json"


def _config_for_spec(spec_id: str, cfgs: "list[str]") -> "str | None":
    """Pick the discovered config that matches the requested composite (#1113).

    ``spec_id`` is a dotted composite id (e.g.
    ``v2ecoli.composites.ecoli_baseline``); its last dotted segment is the
    composite name. A vEcoli-lineage config file's name (its ``experiment_id``
    stem) identifies the simulation it runs, and discovery exposes only those
    filenames — so config selection keys off the filename, and must NEVER fall
    back to the repo's alphabetically-first file, which bears no relation to the
    requested composite.

    Resolution, first match wins:

    1. a config whose filename stem equals the composite name (case-insensitive)
       — the config named after the composite, the strongest ``spec_id`` match;
    2. ``api_simulation_default.json`` — the whole-cell default — when present,
       as the fallback for a whole-cell composite the repo ships no named config
       for (this preserves the working v2ecoli ``ecoli_baseline`` path).

    Returns the chosen filename, or ``None`` when nothing matches — the caller
    then FAILS CLOSED rather than silently dispatch an unrelated config.
    """
    leaf = (spec_id or "").rsplit(".", 1)[-1].strip().lower()
    by_stem: dict[str, str] = {}
    for c in cfgs:
        stem = c[:-5] if c.lower().endswith(".json") else c
        by_stem.setdefault(stem.lower(), c)
    if leaf and leaf in by_stem:
        return by_stem[leaf]
    if _WHOLE_CELL_DEFAULT_CONFIG in cfgs:
        return _WHOLE_CELL_DEFAULT_CONFIG
    return None


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
    overrides = overrides or {}
    client = sms_api_client.SmsApiClient(sms_api_client.sms_api_base())
    # Resolve a real config for this build. sms-api defaults to
    # 'api_simulation_default.json', which only exists in the vEcoli-lineage
    # repos (e.g. v2ecoli) — a build whose repo lacks it (e.g. the sms-ecoli fork,
    # which carries only CD-specific configs) 404s when the config is omitted. So
    # when the caller didn't pin one, ask discovery and pick the config that
    # actually matches the requested composite (``spec_id``).
    #
    # #1113: config selection MUST consult ``spec_id``. It used to only (a) prefer
    # 'api_simulation_default.json' regardless of which composite was requested, or
    # (b) fall back to the build's alphabetically-first file (cfgs[0]). ``spec_id``
    # — the composite actually requested — never fed the pick, only the human-
    # readable description, so a dispatch could run an UNRELATED simulation while
    # its description named the right composite (e.g. every ecoli_baseline card-run
    # against build #211, which lacks the whole-cell default, silently resolved to
    # fss_pathway_oe_native_oe_carina and burned real Batch/ParCa compute before
    # failing later for an unrelated reason). Now key off ``spec_id`` via
    # :func:`_config_for_spec`, and FAIL CLOSED when nothing matches — a wrong-config
    # dispatch that happens to succeed would attribute a real result to the wrong
    # composite. The caller must then pick a config explicitly.
    if not config_filename:
        cfgs: list = []
        try:
            disc = client._get("/api/v1/simulations/discovery",
                               params={"simulator_id": int(simulator_id)})
            cfgs = disc.get("config_filenames") or []
        except Exception:  # noqa: BLE001 — discovery is best-effort; treated as "none discovered"
            cfgs = []
        config_filename = _config_for_spec(spec_id, cfgs)
        if not config_filename:
            _avail = ", ".join(cfgs) if cfgs else "(none discovered)"
            return {
                "error": (f"Cloud build #{simulator_id} has no config matching "
                          f"composite '{spec_id}'. Pick a config explicitly before "
                          f"dispatching — configs available on this build: {_avail}."),
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
    except sms_api_client.SmsApiError as e:
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
    from vivarium_workbench.lib import backends

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
    if backends.resolve_run_target(ws_root) != "deployment":
        return None  # local run — unchanged

    # (3) Deployment workspace: dispatch the resolved build's image. Session build
    #     first (the picker's switched build), else the deployment-wide pin.
    session_build = remote_pinned.resolved_from_session_build(ws_root)
    if session_build:
        return CloudTarget(int(session_build["simulator_id"]),
                           str(session_build.get("repo_url") or ""),
                           str(session_build.get("commit") or ""), "session-build")

    client = sms_api_client.SmsApiClient(sms_api_client.sms_api_base())
    # resolve_pinned_simulator_id handles the frozen PinnedConfig + unreachable
    # sms-api correctly (returns None), so no `.get("simulator_id")` on a dataclass.
    sid = remote_pinned.resolve_pinned_simulator_id(client, ws_root)
    if sid is not None:
        cfg = remote_pinned.pinned_config()
        return CloudTarget(int(sid), str(cfg.repo_url if cfg else ""), "", "pinned")
    return _no_image(None, reason="no-build")


def dispatch_composite(ws_root: Path, body: dict, *, spec_id: str, overrides: dict,
                       emit_paths: list, steps: float) -> "tuple[dict, int] | Detached | None":
    """The composite card's SMS launch (``runs.dispatch_composite`` / ``unrouted.dispatch_composite``), moved
    verbatim from ``composite_test_run`` (step P2 Builds B2).

    :func:`resolve_cloud_target` is the ONE typed decision for "Cloud image, local, or dead-end". A
    :class:`CloudTarget` dispatches the build's PRE-BUILT image (plan B / #1101 — needs only simulator_id, no local
    push and no compose allow-list entry). ``None`` is a local run. A ``(dict, 409)`` is an actionable dead-end (no
    cloud image) carrying an ``actions[]`` list, returned verbatim instead of silently falling into the dead
    compose/git-install path, unless an operator opted into it: then the git preflight, and :class:`Detached` with
    the selected build as the run request's ``build_ref``.

    Item 18 context: a pinned/materialized workspace resolves to "deployment" WITHOUT the loom sending run_target
    (it only sends it when the Environment scope is toggled to Cloud); resolve_cloud_target routes both the explicit
    and the pinned case to the same image dispatch, so they can never drift.
    """
    from vivarium_workbench.lib.backends.provider import Detached

    cloud = resolve_cloud_target(ws_root, body)
    if isinstance(cloud, CloudTarget):
        cfg_fn = (body.get("config_filename") or "").strip() or None
        return _dispatch_build_image_run(
            cloud.simulator_id, overrides, emit_paths, cfg_fn, spec_id)
    if cloud is None:
        return None  # a local run
    # No cloud image resolved for a Cloud-scoped run. The compose/git-install
    # path is DEAD for almost every repo (sms-api's allow-list 403s it), so it
    # is unreachable unless an operator opts in with
    # VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH=1. Without the opt-in, return
    # the actionable 409 (Build on cloud / Switch to Local) rather than
    # dispatch a run that can only fail in the run log.
    if not _compose_dispatch_allowed():
        return cloud
    build_ref = _explicit_build_ref(body)
    # Opt-in compose path: the workspace is installed FROM GIT on the
    # deployment, so it must be clean + pushed AND the repo must be on
    # sms-api's compose allow-list. Warn up front on the git side; the
    # allow-list is enforced server-side (a 403 in the run log).
    pf = remote_run.remote_dispatch_preflight(ws_root)
    if not pf.get("ok"):
        return {"error": pf.get("message", "workspace not ready to run remotely"),
                "reason": pf.get("reason"), "preflight": pf,
                "run_target": "deployment",
                "actions": cloud_build_actions(pf.get("sha"))}, 409
    return Detached(build_ref)


def dispatch_preflight(ws_root: Path) -> dict:
    """``/api/remote-dispatch-preflight`` for an SMS run, moved verbatim from ``api/app.py`` (step P2 Builds B2).

    Cloud runs execute a registered build's PRE-BUILT image (plan B / #1101), so the question is "does a cloud image
    resolve for this workspace" — the SAME decision :func:`resolve_cloud_target` makes at dispatch. When one does,
    it's ready (no push needed). When none does, return the actionable ``actions[]`` shape (Build on cloud / Switch
    to Local) so the card renders buttons, not prose. The dead compose/git-install path (which needs a clean+pushed
    tree AND the repo on sms-api's compose allow-list) is only surfaced when an operator opts in via
    ``VIVARIUM_WORKBENCH_ALLOW_COMPOSE_DISPATCH=1``.
    """
    cloud = resolve_cloud_target(ws_root, {})
    if isinstance(cloud, CloudTarget):
        return {
            "target": "deployment", "ok": True, "reason": "image",
            "dispatch": "image", "simulator_id": cloud.simulator_id,
            "message": f"Cloud image (build #{cloud.simulator_id}) is ready — "
                       "runs execute its pre-built image, no push needed."}
    if not _compose_dispatch_allowed():
        # No cloud image and compose is not opted into → actionable dead-end.
        payload, _status = cloud if isinstance(cloud, tuple) else _no_image(None, reason="no-build")
        payload = dict(payload)
        payload.update(target="deployment", ok=False)
        return payload
    # Opt-in compose path: the git-install preflight, plus the real requirement.
    pf = remote_run.remote_dispatch_preflight(ws_root)
    pf["target"] = "deployment"
    pf["actions"] = cloud_build_actions(pf.get("sha"))
    pf["message"] = (pf.get("message", "") + " The compose path installs your "
                     "repo from git on the deployment and requires the repo to be "
                     "on sms-api's compose allow-list.").strip()
    return pf
