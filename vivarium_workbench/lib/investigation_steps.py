"""Investigation-as-composite building blocks.

A ``StudyStep`` is a process-bigraph ``Step`` wrapping one study. Prerequisite
edges are expressed as input wires so the engine orders StudySteps by data
dependency (design: docs/superpowers/specs/2026-08-01-investigation-as-composite-design.md).

``update()`` dispatches each study run to the persistent env worker's
``run_study`` capability via the shared pool (same pattern as
``study_run_post.py`` / ``composite_flush.py``'s ``run_study_analyses``
dispatch). Tests may still run the walking-skeleton path — assign a list to
``_RUN_ORDER`` to record execution order instead of hitting a live worker
(used by the hermetic ordering tests in
``tests/test_investigation_steps_skeleton.py``).
"""
from __future__ import annotations

import process_bigraph

# Skeleton test hook: when a list is assigned here, each StudyStep.update()
# appends its slug (proves execution order) and returns a marker instead of
# dispatching to a live env worker. None in production.
_RUN_ORDER: list | None = None


def _run_study_hook(workspace: str, study_slug: str) -> dict:
    """Run one study: skeleton-record order, or dispatch to the env worker.

    Honors ``backends.resolve_run_target`` first (§2A.8 workstream 8 step
    2a). ``run_study`` is a **run entrypoint**, and item 18 made that function
    THE authoritative local-vs-deployment answer for every one of them
    "never by which button happened to be clicked". This path was clicking a
    button: it called the worker unconditionally, so a user who picked a
    materialized remote build — or a deployment that pins remote runs — still
    had studies executed in an env worker. `study_runs.launch_into_study`
    already threads the target into `run_core.invoke_run`; this did not.

    On ``deployment`` it raises rather than dispatching. Dispatching from inside
    a ``process_bigraph`` Step is a real design question — whether the Step
    blocks on a Batch job or the investigation composite itself becomes async —
    and inventing an answer here would bury it. Raising keeps the user's choice
    honored (their work does not silently run in the wrong place) and puts the
    decision where it can be made deliberately.
    """
    if _RUN_ORDER is not None:
        _RUN_ORDER.append(study_slug)
        return {"study": study_slug, "ran": True}

    from pathlib import Path

    from vivarium_workbench.lib import backends

    if backends.resolve_run_target(Path(workspace)) == "deployment":
        raise RuntimeError(
            f"study {study_slug!r} resolves to the 'deployment' run target, so it "
            "must not run in an env worker. Run it through the study-run path "
            "(study_runs.launch_into_study -> run_core.invoke_run), which threads "
            "the target and dispatches to viva-api. Investigation composites do "
            "not dispatch yet — REFACTOR-PLAN §2A.8 workstream 8 step 2a."
        )

    from vivarium_workbench.lib.env_worker_pool import get_pool

    return get_pool().call(workspace, "run_study", {
        "workspace": workspace, "study_slug": study_slug})


class StudyStep(process_bigraph.Step):
    config_schema = {
        "workspace": "string",
        "study_slug": "string",
        "prereqs": {"_type": "list[string]", "_default": []},
    }

    def inputs(self):
        return {f"prereq_{p}": "node" for p in self.config.get("prereqs", [])}

    def outputs(self):
        return {"result": "node"}

    def update(self, state=None):
        result = _run_study_hook(self.config["workspace"], self.config["study_slug"])
        return {"result": result}


# Skeleton test hook (mirrors ``_RUN_ORDER``): when a list is assigned here,
# each InvestigationAnalysisStep.update() appends its ``name`` (proves it runs after its
# wired studies) instead of dispatching to a live env worker. None in
# production.
_ANALYSIS_RUN_ORDER: list | None = None


def _run_analysis_hook(workspace: str, name: str, config: dict, report_dir: str) -> dict:
    """Run one investigation-level analysis: skeleton-record order, or
    dispatch to the env worker's ``run_investigation_analysis`` capability."""
    if _ANALYSIS_RUN_ORDER is not None:
        _ANALYSIS_RUN_ORDER.append(name)
        return {"written": [], "errors": []}

    from vivarium_workbench.lib.env_worker_pool import get_pool

    return get_pool().call(workspace, "run_investigation_analysis", {
        "workspace": workspace, "name": name, "config": config,
        "report_dir": report_dir})


def _extract_study_verdict(res, verdict_analysis: str):
    """Pull the config verdict out of a wired ``StudyStep`` result (the
    ``run_study`` reply): prefer the named per-study analysis's verdict
    (``res["analyses"][verdict_analysis]["verdict"]``, the store data-flow
    refactor's canonical source — design:
    docs/superpowers/specs/2026-08-02-store-dataflow-refactor-design.md §2),
    fall back to the reply's top-level conclusion ``verdict``, and finally
    fall back to the raw result unchanged (older/non-analysis studies)."""
    if isinstance(res, dict):
        analyses = res.get("analyses") or {}
        entry = analyses.get(verdict_analysis) if isinstance(analyses, dict) else None
        verdict = entry.get("verdict") if isinstance(entry, dict) else None
        if verdict is not None:
            return verdict
        if res.get("verdict") is not None:
            return res.get("verdict")
    return res


class InvestigationAnalysisStep(process_bigraph.Step):
    """One investigation-level Analysis (e.g. ``comparison_matrix``), wired to
    every study whose verdict it needs. ``inputs()`` — one ``"node"`` port per
    ``study_slugs`` entry, wired by the generator to that study's
    ``StudyStep`` ``result`` store — both orders this step to run AFTER those
    studies (the engine's ``determine_steps`` dependency ordering, same
    mechanism as ``StudyStep``'s ``prereq_*`` ports) AND delivers each study's
    result dict (which carries its ``verdict``) as this step's wired state.

    ``update()`` assembles ``config_verdicts = {slug: verdict}`` from the wired
    study results via ``_extract_study_verdict`` — each ``config_verdicts[slug]``
    is the actual verdict dict (``res["analyses"][verdict_analysis]["verdict"]``,
    falling back to ``res["verdict"]``, falling back to the raw result), not the
    raw ``run_study`` reply. Merges the result into the static ``params``, and
    dispatches to the ``run_investigation_analysis`` worker capability (design:
    docs/superpowers/specs/2026-08-01-investigation-as-composite-design.md,
    §Architecture 2-3; refined by
    docs/superpowers/specs/2026-08-02-store-dataflow-refactor-design.md §2).
    Bypasses the parquet-coupled ``run_study_analyses`` path entirely — the
    #712 blocker this exists to route around.

    NAME: keep this ``InvestigationAnalysisStep``, NOT ``AnalysisStep`` — the
    latter collides with ``v2ecoli.workflow.analysis.AnalysisStep`` for the
    short link-registry alias in a v2ecoli venv, so ``local:AnalysisStep`` would
    silently resolve to the wrong class. The unique name lets the generator use
    the plain ``local:InvestigationAnalysisStep`` address safely.
    """

    config_schema = {
        "workspace": "string",
        "name": "string",
        "params": {"_type": "map", "_default": {}},
        "study_slugs": {"_type": "list[string]", "_default": []},
        "report_dir": "string",
        "verdict_analysis": {"_type": "string", "_default": "comparison_cards"},
    }

    def inputs(self):
        return {f"study_{slug}": "node" for slug in self.config.get("study_slugs", [])}

    def outputs(self):
        return {"written": "node"}

    def update(self, state=None):
        state = state or {}
        verdict_analysis = self.config.get("verdict_analysis", "comparison_cards")
        config_verdicts = {
            slug: _extract_study_verdict(state.get(f"study_{slug}"), verdict_analysis)
            for slug in self.config.get("study_slugs", [])
        }
        params = dict(self.config.get("params") or {})
        # Thread the workspace into the analysis config: an investigation-level
        # analysis (e.g. comparison_matrix) needs it to locate per-study outputs
        # (verdict files) on disk. config-provided workspace wins over any params.
        analysis_config = {**params, "config_verdicts": config_verdicts,
                           "workspace": self.config["workspace"]}
        reply = _run_analysis_hook(
            self.config["workspace"], self.config["name"], analysis_config,
            self.config["report_dir"])
        return {"written": reply.get("written")}
