"""Background orchestration for remote (smsvpctest) simulation runs.

Mirrors lib/run_jobs.py but models ONE multi-step pipeline per job (push →
build → run → poll → download → land) rather than many items. The pipeline
worker is injected (see run_remote_pipeline) so it can be unit-tested with
fakes — this module has no network/git/sms-api knowledge itself.
"""

from __future__ import annotations

import tempfile
import threading
import time
import traceback
import uuid
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from vivarium_dashboard.lib.models import RemoteRunJob as RemoteRunJobModel

STEP_NAMES = ["push", "build", "run", "poll", "download", "land"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class RemoteRunJob:
    def __init__(self, study: str) -> None:
        self.job_id = uuid.uuid4().hex[:12]
        self.study = study
        self.status = "queued"
        self.steps = [{"name": n, "status": "pending", "message": ""} for n in STEP_NAMES]
        self.run_id: str | None = None
        self.simulation_id: int | None = None
        self.error: str | None = None
        self.started_at = _now()
        self.completed_at: str | None = None
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def set_step(self, name: str, status: str, message: str = "") -> None:
        with self._lock:
            for s in self.steps:
                if s["name"] == name:
                    s["status"] = status
                    if message:
                        s["message"] = message
                    break

    def to_dict(self) -> dict:
        with self._lock:
            raw = {
                "job_id": self.job_id,
                "study": self.study,
                "status": self.status,
                "steps": [dict(s) for s in self.steps],
                "run_id": self.run_id,
                "simulation_id": self.simulation_id,
                "error": self.error,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
            }
        # Validate/normalize through the typed model (single source of truth).
        # Identical JSON for well-formed jobs; an unexpected job warns and falls
        # back to the legacy dict rather than breaking the status endpoint.
        try:
            return RemoteRunJobModel.model_validate(raw).model_dump()
        except ValidationError as e:
            warnings.warn(
                f"remote_run_jobs: job {raw.get('job_id')!r} failed "
                f"RemoteRunJob validation: {e}"
            )
            return raw


class RemoteRunManager:
    def __init__(self) -> None:
        self._jobs: dict[str, RemoteRunJob] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def submit(self, study: str, worker_fn: Callable[[RemoteRunJob], None]) -> RemoteRunJob:
        job = RemoteRunJob(study)
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)

        def _run() -> None:
            job.status = "running"
            try:
                worker_fn(job)
                job.status = "failed" if job.error else "done"
            except Exception:  # noqa: BLE001 — record any worker failure on the job
                job.error = traceback.format_exc(limit=4)
                job.status = "failed"
            finally:
                job.completed_at = _now()

        t = threading.Thread(target=_run, daemon=True)
        job._worker = t
        t.start()
        return job

    def get(self, job_id: str) -> RemoteRunJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, n: int = 10) -> list[dict]:
        with self._lock:
            ids = self._order[-n:][::-1]
            jobs = [self._jobs[i] for i in ids]
        return [j.to_dict() for j in jobs]


manager = RemoteRunManager()


@dataclass
class PipelineCtx:
    study: str
    study_dir: Path
    spec_id: str
    repo_url: str
    branch: str
    observables: list[str]
    num_generations: int
    num_seeds: int
    run_parca: bool
    client: object
    push_and_sha: Callable[[], str]
    land: Callable[..., str]
    poll_interval: float = 5.0
    poll_timeout: float = 3600.0


def _poll(get_status: Callable[[], dict], terminal: set[str], interval: float, timeout: float) -> dict:
    t0 = time.time()
    while True:
        st = get_status()
        if str(st.get("status", "")).lower() in terminal:
            return st
        if time.time() - t0 > timeout:
            raise TimeoutError(f"polling timed out after {timeout}s (last status {st.get('status')!r})")
        time.sleep(interval)


_TERMINAL_OK = {"completed", "done", "succeeded"}
_TERMINAL_BAD = {"failed", "cancelled", "error"}


def run_remote_pipeline(job: RemoteRunJob, ctx: PipelineCtx) -> None:
    """Run push→build→run→poll→download→land, updating job. Records job.error on failure."""
    try:
        # 1. push → commit SHA
        job.set_step("push", "running")
        commit = ctx.push_and_sha()
        job.set_step("push", "done", commit[:12])

        # 2. build simulator from the pushed commit
        job.set_step("build", "running")
        simulator = {"git_commit_hash": commit, "git_repo_url": ctx.repo_url, "git_branch": ctx.branch}
        uploaded = ctx.client.upload_simulator(simulator)
        simulator_id = uploaded["database_id"]
        build = _poll(
            lambda: ctx.client.simulator_status(simulator_id),
            _TERMINAL_OK | _TERMINAL_BAD, ctx.poll_interval, ctx.poll_timeout,
        )
        if str(build.get("status", "")).lower() in _TERMINAL_BAD:
            raise RuntimeError(f"simulator build failed: {build.get('error_message') or build.get('status')}")
        job.set_step("build", "done", f"simulator {simulator_id}")

        # 3. run simulation (Ray auto-selected for v2ecoli)
        job.set_step("run", "running")
        sim = ctx.client.run_simulation(
            simulator_id=simulator_id, num_generations=ctx.num_generations,
            num_seeds=ctx.num_seeds, run_parca=ctx.run_parca, observables=ctx.observables,
        )
        simulation_id = sim["database_id"]
        job.simulation_id = simulation_id
        experiment_id = sim.get("experiment_id") or f"sim{simulator_id}-{ctx.study}"
        s3_uri = ((sim.get("config") or {}).get("parca_options") or {}).get("outdir")
        job.set_step("run", "done", f"simulation {simulation_id}")

        # 4. poll to terminal
        job.set_step("poll", "running")
        st = _poll(
            lambda: ctx.client.simulation_status(simulation_id),
            _TERMINAL_OK | _TERMINAL_BAD, ctx.poll_interval, ctx.poll_timeout,
        )
        if str(st.get("status", "")).lower() in _TERMINAL_BAD:
            raise RuntimeError(f"simulation failed: {st.get('error_message') or st.get('status')}")
        job.set_step("poll", "done")

        # 5. download native store
        job.set_step("download", "running")
        with tempfile.TemporaryDirectory() as td:
            tar_path = ctx.client.download_data(simulation_id, Path(td))
            job.set_step("download", "done")

            # 6. land as a study run
            job.set_step("land", "running")
            run_id = ctx.land(
                ctx.study_dir, spec_id=ctx.spec_id, simulation_id=simulation_id,
                experiment_id=experiment_id, commit=commit, tar_path=tar_path,
                s3_uri=s3_uri,
            )
        job.run_id = run_id
        job.set_step("land", "done", run_id)
    except Exception as e:  # noqa: BLE001 — surface any step failure on the job
        job.error = f"{type(e).__name__}: {e}"
        for s in job.steps:
            if s["status"] == "running":
                job.set_step(s["name"], "failed", str(e))
