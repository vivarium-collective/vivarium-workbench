"""Background-thread job manager for investigation-wide multi-variant runs.

The ``/api/investigation-run-unblocked`` endpoint kicks off a sequence
of variant runs (potentially tens of minutes total). HTTP requests can
not block that long, so the work is queued onto a background thread
and tracked here. Clients poll
``/api/investigation-run-unblocked-status?job_id=...`` for progress.

A "job" is one investigation-wide run sequence. It holds:

  job_id       opaque short id (caller polls with this)
  status       queued | running | done | failed
  investigation slug
  items        list of variant-level sub-jobs, each:
                 {study, variant, status, run_id?, error?}
  started_at   ISO8601
  completed_at ISO8601 (set when status reaches done/failed)
  worker       Thread instance (not serialised)

Jobs live in-process; restarting the dashboard loses them. The runs
themselves write to ``studies/<slug>/runs.db`` and that persistence is
the durable artefact — the in-memory job is just progress signalling.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from datetime import datetime, timezone
from typing import Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: Item statuses that mean "this item will not change again".
#:
#: ``submitted`` is deliberately NOT here: it means the work was dispatched to
#: viva-api and is running on Batch, carrying a ``simulation_id`` that can still
#: resolve to done or failed. Treating it as terminal is what made a successful
#: async dispatch look finished (or, before this, look *failed* — the worker only
#: accepted HTTP 200 and `remote_run_submit` answers 202).
TERMINAL_STATUSES = frozenset({"done", "failed", "skipped"})

#: An item held back because a prerequisite study has not finished (plan §A3′,
#: option (c)). Like ``submitted`` it is deliberately NOT terminal — it is work
#: that still has to happen.
#:
#: ``waiting`` exists because ordering alone does not sequence a DEPLOYMENT
#: target: A2′ made dispatch return ``submitted`` immediately, so without a gate
#: a dependent starts while its prerequisite is still running on Batch. The two
#: rejected alternatives are worth recording — blocking the worker until prereqs
#: settle reinstates the hours-long held thread A0b identified as the original
#: defect, and releasing inside the status GET makes progress depend on somebody
#: keeping a browser tab open. This option keeps the worker short-lived and makes
#: the release an explicit, observable act (:meth:`RunJobManager.redrive`).
WAITING = "waiting"


class RunJob:
    def __init__(self, investigation: str, items: list[dict]):
        self.job_id = uuid.uuid4().hex[:12]
        self.investigation = investigation
        self.items: list[dict] = items  # mutated as work progresses
        self.status = "queued"
        self.started_at = _now()
        self.completed_at: str | None = None
        self.error: str | None = None
        self._worker: threading.Thread | None = None
        #: The worker callable, kept so RunJobManager.redrive can re-run it.
        self._worker_fn: Callable[[RunJob], None] | None = None
        self._lock = threading.Lock()

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "job_id":        self.job_id,
                "investigation": self.investigation,
                "status":        self.status,
                "items":         [dict(it) for it in self.items],
                "started_at":    self.started_at,
                "completed_at":  self.completed_at,
                "error":         self.error,
                "progress":      self._progress_locked(),
            }

    def _progress_locked(self) -> dict:
        n = len(self.items)
        done = sum(1 for it in self.items if it.get("status") in TERMINAL_STATUSES)
        running = sum(1 for it in self.items if it.get("status") == "running")
        submitted = sum(1 for it in self.items if it.get("status") == "submitted")
        waiting = sum(1 for it in self.items if it.get("status") == WAITING)
        return {"total": n, "done": done, "running": running,
                "submitted": submitted, "waiting": waiting}

    def update_item(self, idx: int, **fields) -> None:
        with self._lock:
            if 0 <= idx < len(self.items):
                self.items[idx].update(fields)


class RunJobManager:
    """In-process registry of background run-jobs."""

    def __init__(self):
        self._jobs: dict[str, RunJob] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        investigation: str,
        items: list[dict],
        worker_fn: Callable[[RunJob], None],
    ) -> RunJob:
        """Create a RunJob, start its background worker, return the handle."""
        job = RunJob(investigation, items)
        job._worker_fn = worker_fn   # kept so redrive() can run it again
        with self._lock:
            self._jobs[job.job_id] = job
        self._start(job, worker_fn)
        return job

    def redrive(self, job_id: str) -> dict:
        """Re-run a job's worker to release items whose prerequisites finished.

        This is option (c) of §A3′: the worker marks a gated item ``waiting``
        and returns instead of blocking on a Batch job, so *something* has to
        come back and start it once the prerequisite lands. That something is an
        explicit call — a route the UI hits, or an operator — rather than a
        thread parked for hours (rejected: A0b's defect) or a side effect of a
        status GET (rejected: progress would depend on a browser tab being open).

        Idempotent and safe to call at any time: the worker only touches items in
        ``queued``/``waiting``, and a still-running worker is left alone rather
        than duplicated, so a caller that polls this cannot double-dispatch.

        Returns ``{redriven: bool, reason: str, waiting: int}`` — ``redriven``
        false is an ordinary outcome (nothing was waiting, or a worker is already
        going), not an error.
        """
        job = self.get(job_id)
        if job is None:
            return {"redriven": False, "reason": "no such job", "waiting": 0}
        with job._lock:
            waiting = sum(1 for it in job.items if it.get("status") == WAITING)
            alive = job._worker is not None and job._worker.is_alive()
            worker_fn = getattr(job, "_worker_fn", None)
        if alive:
            return {"redriven": False, "reason": "worker already running",
                    "waiting": waiting}
        if not waiting:
            return {"redriven": False, "reason": "nothing waiting", "waiting": 0}
        if worker_fn is None:
            # Pre-redrive jobs (or a hand-built stand-in) have no stored worker.
            return {"redriven": False, "reason": "job has no re-runnable worker",
                    "waiting": waiting}
        self._start(job, worker_fn)
        return {"redriven": True, "reason": f"released {waiting} waiting item(s)",
                "waiting": waiting}

    def _start(self, job: RunJob, worker_fn: Callable[[RunJob], None]) -> None:
        """Run ``worker_fn`` against ``job`` on a fresh daemon thread.

        Extracted from :meth:`submit` so :meth:`redrive` starts a worker the
        same way rather than growing a second, subtly-different copy of the
        status accounting below.
        """

        def _run():
            try:
                with job._lock:
                    job.status = "running"
                worker_fn(job)
                with job._lock:
                    if all(it.get("status") in TERMINAL_STATUSES for it in job.items):
                        any_failed = any(it.get("status") == "failed" for it in job.items)
                        job.status = "failed" if any_failed else "done"
                    elif any(it.get("status") == WAITING for it in job.items):
                        # Items are gated behind a prerequisite that has not
                        # finished. The job is emphatically NOT done — it needs a
                        # redrive() once those prerequisites land, and saying
                        # "done" here would strand them silently.
                        job.status = WAITING
                    elif any(it.get("status") == "submitted" for it in job.items):
                        # The worker is finished dispatching, but work it handed to
                        # Batch is still running. The job is NOT done — its items
                        # resolve when their simulation_ids do. Reporting "done"
                        # here (as the old else-branch did unconditionally) would
                        # tell the UI a 10,000-sim campaign had completed the moment
                        # it was submitted.
                        job.status = "submitted"
                    else:
                        job.status = "done"
            except BaseException as e:  # noqa: BLE001
                with job._lock:
                    job.status = "failed"
                    job.error = f"worker crashed: {e}\n{traceback.format_exc()[-2000:]}"
            finally:
                with job._lock:
                    job.completed_at = _now()

        t = threading.Thread(target=_run, daemon=True, name=f"runjob-{job.job_id}")
        job._worker = t
        t.start()

    def get(self, job_id: str) -> RunJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        # Most recent first
        jobs.sort(key=lambda j: j.started_at, reverse=True)
        return [j.to_dict() for j in jobs[:limit]]


# Module-level singleton — one manager per dashboard process.
manager = RunJobManager()


# ---------------------------------------------------------------------------
# Investigation-level "run unblocked" planner
# ---------------------------------------------------------------------------

#: viva-api ``ComposeJobStatus`` values that mean the run is over.
#: Anything not listed — waiting/queued/running/pending/suspended, or an absent
#: status — leaves the item ``submitted``: an unknown state is not a finished one.
_UPSTREAM_DONE = frozenset({"completed"})
_UPSTREAM_FAILED = frozenset({"failed", "cancelled", "out_of_memory", "timeout"})


def refresh_submitted(job, client=None) -> None:
    """Resolve a job's ``submitted`` items against viva-api, in ONE call.

    ``submitted`` means the work was dispatched to Batch and viva-api holds the
    truth about it, keyed by ``simulation_id``. This asks for all of them at once
    (``GET /compose/v1/simulations/status/batch``) rather than per item, and runs
    on **status read** rather than from a polling thread — so nothing is held
    open on the workbench side while Batch works.

    Uses only the job's PUBLIC surface (``items`` + ``update_item``), because
    jobs here are duck-typed: the suite drives workers with a minimal stand-in
    that has neither ``_lock`` nor job-level status, and the status route calls
    this on whatever the manager returns.

    Total by design — it never raises. If viva-api is unreachable the items stay
    ``submitted``, which is what they are. Losing the poll must not turn a
    running campaign into a failed one, nor 500 a status page.
    """
    try:
        items = list(getattr(job, "items", None) or [])
        pending = [(i, it.get("simulation_id")) for i, it in enumerate(items)
                   if it.get("status") == "submitted" and it.get("simulation_id")]
        if not pending:
            return
        if client is None:
            from vivarium_workbench.lib.sms_api_client import SmsApiClient
            from vivarium_workbench.lib.workspace_deps_views import _sms_api_base
            client = SmsApiClient(_sms_api_base())
        rows = client.compose_status_batch([sid for _, sid in pending])
        by_sim = {r.get("sim_id"): r for r in rows if isinstance(r, dict)}
        for idx, sid in pending:
            row = by_sim.get(sid)
            if not row:
                continue
            upstream = (row.get("status") or "").lower()
            if upstream in _UPSTREAM_DONE:
                job.update_item(idx, status="done")
            elif upstream in _UPSTREAM_FAILED:
                job.update_item(idx, status="failed",
                                error=row.get("error_message") or f"upstream: {upstream}")
            else:
                job.update_item(idx, phase=upstream or "running")
        _settle_if_complete(job)
    except Exception:  # noqa: BLE001 — a poll must never fail a running job
        return


def _settle_if_complete(job) -> None:
    """Roll a job up to terminal once every item has resolved.

    Silently skips a job that carries no job-level status — again, duck typing:
    the test stand-ins have ``items`` and ``update_item`` and nothing else.
    """
    items = list(getattr(job, "items", None) or [])
    if not items or not all(it.get("status") in TERMINAL_STATUSES for it in items):
        return
    if not hasattr(job, "status"):
        return
    job.status = "failed" if any(it.get("status") == "failed" for it in items) else "done"
    if getattr(job, "completed_at", None) is None:
        job.completed_at = _now()


def study_prereqs(ws, slug: str) -> list[str]:
    """Study slugs ``slug`` must run after, read STRICTLY from
    ``pipeline_gate.prerequisites`` — never the legacy ``parent_studies``.

    Each entry is ``{study: X, ...}`` or a bare string ``X``. Keying strictly on
    this field makes a study.yaml with no ``pipeline_gate`` a no-op (empty list)
    rather than silently picking up legacy ``parent_studies`` edges.

    Lifted here (plan §A3′) from ``investigation_execution._study_prereqs``,
    which still calls it, so the pbg-composite path and the ``run_jobs`` path
    read prerequisites through ONE function. They had no reason to diverge and
    every reason not to: an investigation whose composite orders A before B, but
    whose "Run" button does not, is the kind of disagreement this plan exists to
    remove.

    ``ws`` is a ``WorkspacePaths``. Total: an unreadable or malformed study.yaml
    yields no edges rather than raising, because a missing prerequisite must not
    take down the enumeration of every OTHER study in the investigation.
    """
    import yaml

    p = ws.studies / slug / "study.yaml"
    if not p.exists():
        return []
    try:
        spec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — see docstring: never break enumeration
        return []
    gate = spec.get("pipeline_gate") or {}
    out: list[str] = []
    for e in gate.get("prerequisites") or []:
        if isinstance(e, dict) and e.get("study"):
            out.append(e["study"])
        elif isinstance(e, str) and e:
            out.append(e)
    return out


def order_items_by_prereqs(items: list[dict], ws) -> list[dict]:
    """Stable topological sort of run items so a study's prerequisites come first.

    Items carry a ``study`` slug; several items (baseline + variants) can share
    one. Edges come from :func:`study_prereqs`, **filtered to studies actually
    present in this batch** — a prerequisite outside the investigation (or
    excluded by the request's ``studies`` filter) cannot be waited for here, so
    it is not an edge. That mirrors ``build_investigation_composite``, which
    filters prereqs to ``member_set`` for the same reason.

    Stable: studies with no ordering constraint between them keep their declared
    order, which is the investigation's own member order and the only
    deterministic tiebreak available. Items within one study keep their relative
    order (baseline before its variants).

    A prerequisite CYCLE is not an error here — it is left in declared order and
    reported by the caller's own means. Refusing to run an investigation because
    two studies name each other would convert a metadata mistake into an outage,
    and the pbg path does not refuse either.

    **This orders; it does not gate.** On a local target that is sufficient,
    because each run blocks until it finishes. On a deployment target it is NOT:
    an item dispatched to Batch returns ``submitted`` immediately, so a
    dependent still starts while its prerequisite is mid-flight. Closing that
    needs a release-on-completion mechanism — see the plan's §A3′ open question.
    """
    if not items:
        return items
    present = {it.get("study") for it in items if it.get("study")}
    order: dict[str, int] = {}
    for i, it in enumerate(items):
        order.setdefault(it.get("study"), i)
    deps = {
        s: [p for p in study_prereqs(ws, s) if p in present and p != s]
        for s in present
    }

    ranked: list[str] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(s: str) -> None:
        if s in seen or s in visiting:
            return  # already placed, or a cycle — leave declared order to win
        visiting.add(s)
        for d in sorted(deps.get(s, ()), key=lambda x: order.get(x, 0)):
            visit(d)
        visiting.discard(s)
        seen.add(s)
        ranked.append(s)

    for s in sorted(present, key=lambda x: order.get(x, 0)):
        visit(s)

    rank = {s: i for i, s in enumerate(ranked)}
    return sorted(items, key=lambda it: rank.get(it.get("study"), len(rank)))


def enumerate_unblocked(spec: dict) -> tuple[list[dict], list[dict]]:
    """Return (runnable_items, blocked_items) for one study's spec.

    A variant is **blocked** if the study has any ``conditions.model_settings``
    (or legacy ``expert_inputs``) entry with ``gate: required-before-run``
    whose ``current`` is null / missing. The variant is **runnable**
    otherwise.

    Items have shape ``{study, variant, base_composite?, params?, kind}``
    where kind ∈ {"baseline", "variant"}.

    A study with no variants surfaces its baseline as a single runnable item.
    """
    cond = spec.get("conditions") or {}
    # Backward-compat: accept either model_settings or the legacy alias.
    settings = cond.get("model_settings") or cond.get("expert_inputs") or []
    pending_required = [
        s for s in settings
        if isinstance(s, dict)
        and s.get("gate") == "required-before-run"
        and (s.get("current") is None or s.get("current") == "")
    ]
    blocked_reason = None
    if pending_required:
        names = ", ".join(s.get("name", "?") for s in pending_required)
        blocked_reason = f"required-before-run settings unset: {names}"

    study_slug = spec.get("name") or "(unnamed)"
    runnable: list[dict] = []
    blocked: list[dict] = []

    # Baseline as the implicit first item.
    baseline = cond.get("baseline") or {}
    variants = cond.get("variants") or []
    if not baseline.get("composite"):
        # The top-level form (``baseline: [{name, composite, params}, ...]``, ``variants: [...]``): the study
        # runner reads it the same way (study_runs.run_study_baseline runs ``baseline[0]``).
        top = spec.get("baseline")
        first = top[0] if isinstance(top, list) and top else top
        if isinstance(first, dict) and first.get("composite"):
            baseline = first
            variants = spec.get("variants") or []
    if baseline.get("composite"):
        item = {
            "study":          study_slug,
            "variant":        "baseline",
            "kind":           "baseline",
            "composite":      baseline.get("composite"),
            "params":         dict(baseline.get("params") or {}),
            "status":         "queued",
        }
        if blocked_reason:
            item["status"] = "blocked"
            item["error"] = blocked_reason
            blocked.append(item)
        else:
            runnable.append(item)

    for v in variants:
        if not isinstance(v, dict):
            continue
        item = {
            "study":          study_slug,
            "variant":        v.get("name", "?"),
            "kind":           "variant",
            "composite":      v.get("composite") or v.get("base_composite") or baseline.get("composite"),
            "params":         dict(v.get("parameter_overrides") or v.get("params") or {}),
            "status":         "queued",
        }
        if blocked_reason:
            item["status"] = "blocked"
            item["error"] = blocked_reason
            blocked.append(item)
        else:
            runnable.append(item)

    return runnable, blocked
