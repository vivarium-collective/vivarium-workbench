"""Which env-worker methods may run in a worker, and which are jobs.

Step 1 of REFACTOR-PLAN §2A.8 workstream 8 (design:
`docs/env-worker-routing.md`). §2A.7 already draws this line — the worker answers
**interactive** queries; simulations and heavy analyses are **jobs** — and the
distinction it draws is *method-shaped* (`registry_catalog` vs `run_study`), not
call-site-shaped. This module is that line, written down.

**Why classify by method rather than by call site.** `WorkerPool.call()` is the
single choke point all 25 call sites pass through, and it already receives the
method name. Classifying here is robust to an incomplete audit: a call site nobody
has traced still cannot route a simulation into a worker pod sized for
interaction. (Of the eight job-class call sites, four have been traced;
`investigation_steps` dispatches `run_study` with no run-target gate at all.)

**Viz is deliberately NOT classified.** §2A.7: *"Viz straddles — light preview may
stay an env-worker query, heavy post-run rendering moves into the job; split it
**as it comes, don't pre-design**."* So `render_viz_doc` / `viz_preview` /
`validate_generated_visualization` stay interactive until something real forces
the split. Pre-classifying them would be exactly the guess this design rules out.
"""
from __future__ import annotations

__all__ = [
    "JOB_CLASS_METHODS", "is_job_class",
    "CATALOG_CLASS_METHODS", "is_catalog_class",
]

#: Methods that *execute the science* rather than answer a question about it.
#: These run a composite: a simulation, a post-run analysis pass, or an
#: investigation's study graph. Cost is unbounded by anything this layer can see
#: — a real study on this system declares 1000 seeds x 10 generations — so they
#: must not be routed to a worker sized for interactive queries.
JOB_CLASS_METHODS = frozenset({
    "run_study",                  # a study's simulation(s)
    "run_study_analyses",         # the post-run v2ecoli analysis pass
    "run_investigation_analysis",  # an investigation's analysis step
})
# `run_process` was here and is NOT job-class: `env_worker._run_process` builds one
# class from the registry, fills its input ports and runs a SINGLE `update()`,
# degrading to `{ok: False, stage, error}` rather than raising. That is a probe —
# the Composite Explorer's "try this process" button — not a job. It was
# classified on the `run_` prefix rather than on what it does, which is the
# mistake this comment exists to stop being repeated.


#: Methods that are a *run entrypoint*, where `resolve_run_target` (item 18) is
#: authoritative. Its own docstring names exactly this class: "every dashboard run
#: entrypoint (Composites tab, Study tab, CLI, batch worker, rerun)".
#:
#: Deliberately NARROWER than JOB_CLASS_METHODS. `run_study_analyses` and
#: `run_investigation_analysis` are post-run analysis over output that already
#: exists. They can be heavy — but heaviness is the SCALE axis (step 2b), not a
#: question of where a *run* executes. Gating them on the run target would stop
#: post-run analysis outright on any pinned deployment, for no principled reason.
RUN_ENTRYPOINT_METHODS = frozenset({"run_study"})


def is_run_entrypoint(method: str) -> bool:
    """Whether ``remote_pinned.resolve_run_target`` governs this method."""
    return method in RUN_ENTRYPOINT_METHODS


def is_job_class(method: str) -> bool:
    """True when ``method`` executes science rather than answering about it.

    Unknown methods are treated as **interactive**, which is the safe default for
    a *lookup* and the reason ``tests/test_env_worker_routing.py`` asserts every
    declared capability is accounted for: a newly added heavy method should fail
    that test rather than silently inherit the interactive path.
    """
    return method in JOB_CLASS_METHODS


#: Interactive queries that are nonetheless EXPENSIVE the first time in a fresh
#: worker: they force the full ``@composite_generator`` / process-module import
#: walk (materializing a ``LazyLinkRegistry`` of hundreds of pending modules) to
#: answer. On a large venv (~99 installed process packages) that cold walk runs
#: for minutes — far past the 60s default socket ``call_timeout`` — so the pool
#: would kill the worker mid-import and respawn cold on the next call, making
#: EVERY call re-pay the cold cost (the RENCI /api/registry ~480s / 504). They
#: are NOT job-class (they answer a question, they don't run a simulation), but
#: they need the long timeout job-class work would otherwise monopolize. The pool
#: gives these a separate, larger ``catalog_timeout`` so the one cold build
#: completes and the worker stays warm (subsequent calls are ~ms).
CATALOG_CLASS_METHODS = frozenset({
    "registry_catalog",     # GET /api/registry (+ /api/catalog, /api/marketplace)
    "composites_full",      # GET /api/composites
    "discover_composites",  # generator discovery for the Composites tab
})


def is_catalog_class(method: str) -> bool:
    """True when ``method`` is an interactive query whose FIRST call in a fresh
    worker triggers the heavy process-module import walk, so it needs the pool's
    longer ``catalog_timeout`` rather than the interactive default."""
    return method in CATALOG_CLASS_METHODS
