"""Compile a workspace investigation into a **runnable** process-bigraph
composite — the execution substrate for "investigation as a composite".

Distinct from ``investigation_composite.py`` (which builds a pull-or-compute
*trigger document*): this module builds an executable ``Composite`` state dict
whose ``StudyStep``s run the member studies and whose ``InvestigationAnalysisStep``s
run investigation-level analyses, ordered by the process-bigraph scheduler via
input/output store wiring. Design:
``docs/superpowers/specs/2026-08-01-investigation-as-composite-design.md`` (§3).

Pure dict construction — deliberately imports NO ``process_bigraph`` (the Step
classes are referenced only by ``local:<Class>`` string address), so this module
+ its shape tests stay import-light.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml

# Guards the module-level StudyStep/InvestigationAnalysisStep hook swap in
# ``run_investigation_composite`` (see there).
_HOOK_SWAP_LOCK = threading.Lock()

from vivarium_workbench.lib.investigation_members import (
    investigation_member_slugs,
    member_slug,
)
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _study_upstreams(ws: WorkspacePaths, slug: str) -> list[str]:
    """Studies ``slug`` must run after — the canonical union of
    ``pipeline_gate.prerequisites`` and ``inputs[].from``.

    A thin delegation to :func:`run_jobs.study_upstreams`, the one relation the
    run_jobs "Run" path and ``rerun`` also read, so the composite wiring built
    here can never order studies differently from how they are run or replayed.
    Kept as a module-level name because this module's own readers refer to it.

    Total: a malformed study.yaml yields no edges rather than raising (see
    :func:`run_jobs.study_upstreams`), so one bad file cannot take down the
    ordering of every other study.
    """
    from vivarium_workbench.lib.run_jobs import study_upstreams

    return study_upstreams(ws, slug)


def build_investigation_composite(ws_root: Path | str, inv_slug: str) -> dict:
    """Compile ``investigations/<inv_slug>/investigation.yaml`` into a composite
    state dict: one ``StudyStep`` per member study (prerequisite-ordered, real or
    synthetic-serial) + one ``InvestigationAnalysisStep`` per ``analyses:`` entry
    wired to every member study's result store.

    Ordering has two mechanisms, both expressed purely as input wires (no explicit
    scheduling code — ``process_bigraph``'s ``determine_steps`` reads the wires):

    1. **Real upstreams** — a study's :func:`run_jobs.study_upstreams`
       (``pipeline_gate.prerequisites`` ∪ ``inputs[].from``), filtered to entries
       that are members of THIS investigation.
    2. **Synthetic serial edges** — confirmed necessary in Task 1: independent
       steps (no wiring between them) run in NONDETERMINISTIC engine order, not
       declared order. A study with no real prerequisite is wired instead to the
       immediately-preceding declared member, turning a whole no-prerequisite
       investigation into a deterministic declared-order chain. (v1 is serial;
       dropping these edges for genuinely-independent studies is a later
       parallelism pass.)

    Returns a plain state dict (NOT a running ``Composite``) — callers wrap it as
    ``Composite({"state": build_investigation_composite(...),
    "run_steps_on_init": True}, core)``.
    """
    ws = WorkspacePaths.load(ws_root)
    ws_root_str = str(ws_root)
    inv_path = ws.investigations / inv_slug / "investigation.yaml"
    spec = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}

    members: list[str] = []
    for entry in investigation_member_slugs(spec):
        slug = member_slug(entry)
        if slug:
            members.append(slug)
    member_set = set(members)

    state: dict[str, Any] = {}

    for i, slug in enumerate(members):
        real_prereqs = [
            p for p in _study_upstreams(ws, slug) if p in member_set and p != slug
        ]
        if real_prereqs:
            prereqs = real_prereqs
        elif i > 0:
            # No real prerequisite: synthetic serial edge to the
            # immediately-preceding declared member (declared-order determinism
            # — see function docstring).
            prereqs = [members[i - 1]]
        else:
            prereqs = []

        state[slug] = {
            "_type": "step",
            "address": "local:StudyStep",
            "config": {
                "workspace": ws_root_str,
                "study_slug": slug,
                "prereqs": prereqs,
            },
            "inputs": {f"prereq_{p}": [f"study_{p}_result"] for p in prereqs},
            "outputs": {"result": [f"study_{slug}_result"]},
        }
        state[f"study_{slug}_result"] = {}

    report_dir_str = str(ws.report_dir(inv_slug))
    for entry in (spec.get("analyses") or []):
        name = entry.get("name") if isinstance(entry, dict) else entry
        if not name:
            continue
        params = (entry.get("params") if isinstance(entry, dict) else None) or {}

        state[f"analysis_{name}"] = {
            "_type": "step",
            "address": "local:InvestigationAnalysisStep",
            "config": {
                "workspace": ws_root_str,
                "name": name,
                "params": params,
                "study_slugs": list(members),
                "report_dir": report_dir_str,
            },
            "inputs": {
                f"study_{slug}": [f"study_{slug}_result"] for slug in members
            },
            "outputs": {"written": [f"analysis_{name}_written"]},
        }
        state[f"analysis_{name}_written"] = {}

    return state


def run_investigation_composite(ws_root: Path | str, inv_slug: str, *,
                                 run_study_fn=None) -> dict:
    """Build ``inv_slug``'s ``InvestigationComposite`` and RUN it: one
    ``process_bigraph.Composite`` whose ``StudyStep``s/``InvestigationAnalysisStep``s
    execute in the order the scheduler derives from
    ``build_investigation_composite``'s wiring (real
    ``pipeline_gate.prerequisites`` edges, else the synthetic-serial chain that
    preserves declared order — see that function's docstring). This is the
    single execution entry point ``prepare_investigation`` delegates to for a
    full run (design: docs/superpowers/specs/2026-08-01-investigation-as-composite-design.md,
    §Architecture 4).

    ``run_study_fn`` (optional, **tests only**): ``(workspace, study_slug) ->
    reply dict`` — when given, temporarily substitutes for the live
    ``env_worker_pool`` dispatch so a run can be exercised hermetically with no
    process-bigraph worker/pool. Implementation note: rather than monkeypatching
    ``env_worker_pool.get_pool`` (what the existing skeleton/dispatch tests do)
    or requiring callers to juggle ``investigation_steps._RUN_ORDER`` directly,
    this function temporarily swaps ``investigation_steps._run_study_hook`` /
    ``_run_analysis_hook`` for wrappers that (a) delegate to ``run_study_fn``
    when given, else the original hook (so production dispatch — including the
    ``_RUN_ORDER`` skeleton path other tests use — is unchanged), and (b)
    record execution order + the full reply for this function's summary. This
    is necessary regardless of ``run_study_fn``: the ``InvestigationAnalysisStep``
    output store only ever holds ``reply["written"]`` (see
    ``investigation_steps.InvestigationAnalysisStep.update``), never
    ``reply["errors"]`` — capturing at the hook is the only way to surface
    analysis errors here. The swap is restored in a ``finally`` before this
    function returns, including on a raised exception (fail-loud policy below).

    **Failure policy (v1):** fail-loud. If a ``StudyStep``'s worker call raises
    (rather than returning an error in its reply — the ``run_study``/
    ``run_investigation_analysis`` capabilities themselves never raise, but the
    engine or a stubbed ``run_study_fn`` might), it propagates out of
    ``Composite(...)`` uncaught and this function does not catch it — the
    scheduler aborts mid-graph and the caller (a detached run) is responsible
    for recording the failure. Errors reported *within* a reply (the
    ``errors`` list ``run_study``/``run_investigation_analysis`` return) are
    captured into this summary's ``errors`` without aborting — that distinction
    (in-reply error vs raised exception) is the only partial-failure handling
    v1 does. Continuing independent branches after a raised failure is a
    follow-up (not implemented).

    Returns ``{"investigation": inv_slug, "studies_ran": [...],
    "analyses": [...], "errors": [...], "study_results": {slug: reply}}`` —
    ``studies_ran``/``analyses`` are slugs/names in the order the scheduler ran
    them; ``study_results`` (additive, not in the design's minimal shape) maps
    each study slug to its full ``run_study`` reply, for callers (like
    ``prepare_investigation``) that need the run refs, not just the order.
    """
    from bigraph_schema import allocate_core
    from process_bigraph import Composite

    from vivarium_workbench.lib import investigation_steps as _steps
    from vivarium_workbench.lib.investigation_steps import (
        InvestigationAnalysisStep, StudyStep)

    state = build_investigation_composite(ws_root, inv_slug)
    core = allocate_core(top={
        "StudyStep": StudyStep, "InvestigationAnalysisStep": InvestigationAnalysisStep})

    studies_ran: list[str] = []
    analyses_ran: list[str] = []
    study_results: dict[str, dict] = {}
    errors: list[dict] = []

    _orig_study_hook = _steps._run_study_hook
    _orig_analysis_hook = _steps._run_analysis_hook

    def _study_hook(workspace, study_slug):
        studies_ran.append(study_slug)
        reply = (run_study_fn(workspace, study_slug) if run_study_fn is not None
                  else _orig_study_hook(workspace, study_slug))
        study_results[study_slug] = reply
        for err in (reply.get("errors") or []) if isinstance(reply, dict) else []:
            errors.append({"study": study_slug, "error": err})
        return reply

    def _analysis_hook(workspace, name, config, report_dir):
        analyses_ran.append(name)
        reply = _orig_analysis_hook(workspace, name, config, report_dir)
        for err in (reply.get("errors") or []) if isinstance(reply, dict) else []:
            errors.append({"analysis": name, "error": err})
        return reply

    # Serialize the module-level hook swap: two concurrent in-process runs would
    # otherwise clobber each other's hooks mid-flight. v1 is serial so this only
    # enforces mutual exclusion (no behavior change for the single-caller path),
    # closing the re-entrancy window if an in-process concurrent caller is added.
    with _HOOK_SWAP_LOCK:
        _steps._run_study_hook = _study_hook
        _steps._run_analysis_hook = _analysis_hook
        try:
            Composite({"state": state, "run_steps_on_init": True}, core=core)
        finally:
            _steps._run_study_hook = _orig_study_hook
            _steps._run_analysis_hook = _orig_analysis_hook

    return {
        "investigation": inv_slug,
        "studies_ran": studies_ran,
        "analyses": analyses_ran,
        "errors": errors,
        "study_results": study_results,
    }
