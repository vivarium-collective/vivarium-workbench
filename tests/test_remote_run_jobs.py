import time

from vivarium_workbench.lib.remote_run_jobs import RemoteRunManager, STEP_NAMES


def _wait(job, timeout=5.0):
    t0 = time.time()
    while job.status in ("queued", "running") and time.time() - t0 < timeout:
        time.sleep(0.01)


def test_job_has_a_step_per_name_all_pending():
    mgr = RemoteRunManager()
    done = []

    def worker(job):
        done.append(job.job_id)

    job = mgr.submit("study-a", worker)
    _wait(job)
    d = job.to_dict()
    assert [s["name"] for s in d["steps"]] == STEP_NAMES
    assert d["study"] == "study-a"
    assert d["status"] == "done"
    assert mgr.get(job.job_id) is job


def test_worker_exception_marks_job_failed():
    mgr = RemoteRunManager()

    def worker(job):
        job.set_step("push", "running")
        raise RuntimeError("boom")

    job = mgr.submit("s", worker)
    _wait(job)
    d = job.to_dict()
    assert d["status"] == "failed"
    assert "boom" in (d["error"] or "")


def test_set_step_updates_status_and_message():
    mgr = RemoteRunManager()

    def worker(job):
        job.set_step("build", "done", "simulator 15")

    job = mgr.submit("s", worker)
    _wait(job)
    build = next(s for s in job.to_dict()["steps"] if s["name"] == "build")
    assert build["status"] == "done"
    assert build["message"] == "simulator 15"


from pathlib import Path

from vivarium_workbench.lib.remote_run_jobs import PipelineCtx, run_remote_pipeline


class _FakeClient:
    def __init__(self):
        self.calls = []

    def upload_simulator(self, simulator, force=False):
        self.calls.append(("upload", simulator))
        return {"database_id": 15, "status": "running"}

    def simulator_status(self, simulator_id):
        return {"status": "completed"}

    def run_simulation(self, **kw):
        self.calls.append(("run", kw))
        return {"database_id": 50}

    def simulation_status(self, simulation_id):
        return {"status": "completed"}

    def download_data(self, simulation_id, dest_dir):
        p = Path(dest_dir) / f"sim_{simulation_id}.tar.gz"
        p.write_bytes(b"x")
        return p


def _ctx(tmp_path, client, **over):
    landed = {}

    def land(study_dir, **kw):
        landed["kw"] = kw
        return "run_abc"

    base = dict(
        study="s", study_dir=tmp_path, spec_id="v2ecoli.composites.baseline",
        repo_url="https://github.com/x/v2ecoli", branch="main",
        observables=["listeners/mass/cell_mass"], num_generations=1, num_seeds=1,
        run_parca=True, client=client, push_and_sha=lambda: "deadbeef",
        land=land, poll_interval=0.0, poll_timeout=5.0,
    )
    base.update(over)
    return PipelineCtx(**base), landed


def test_pipeline_happy_path(tmp_path):
    from vivarium_workbench.lib.remote_run_jobs import RemoteRunJob

    client = _FakeClient()
    ctx, landed = _ctx(tmp_path, client)
    job = RemoteRunJob("s")
    run_remote_pipeline(job, ctx)
    assert job.error is None
    assert job.run_id == "run_abc"
    assert job.to_dict()["simulation_id"] == 50  # remote sim id surfaced on the job
    assert all(s["status"] == "done" for s in job.steps)
    # observables threaded into run_simulation; commit threaded into upload + land
    run_kw = next(c[1] for c in client.calls if c[0] == "run")
    assert run_kw["observables"] == ["listeners/mass/cell_mass"]
    assert run_kw["simulator_id"] == 15
    assert landed["kw"]["simulation_id"] == 50
    assert landed["kw"]["commit"] == "deadbeef"


def test_pipeline_marks_failed_step_on_error(tmp_path):
    from vivarium_workbench.lib.remote_run_jobs import RemoteRunJob

    client = _FakeClient()

    def boom():
        raise RuntimeError("push rejected")

    ctx, _ = _ctx(tmp_path, client, push_and_sha=boom)
    job = RemoteRunJob("s")
    run_remote_pipeline(job, ctx)
    assert job.error is not None and "push rejected" in job.error
    push = next(s for s in job.steps if s["name"] == "push")
    assert push["status"] == "failed"
    # later steps never ran
    assert next(s for s in job.steps if s["name"] == "build")["status"] == "pending"


def test_pipeline_passes_s3_uri_from_sim_config(tmp_path):
    """When run_simulation returns a config.parca_options.outdir, it's threaded into land()."""
    from vivarium_workbench.lib.remote_run_jobs import RemoteRunJob

    class _FakeClientWithS3(_FakeClient):
        def run_simulation(self, **kw):
            self.calls.append(("run", kw))
            return {
                "database_id": 50,
                "config": {"parca_options": {"outdir": "s3://my-bucket/runs/exp-99/"}},
            }

    client = _FakeClientWithS3()
    ctx, landed = _ctx(tmp_path, client)
    job = RemoteRunJob("s")
    run_remote_pipeline(job, ctx)
    assert job.error is None
    assert landed["kw"]["s3_uri"] == "s3://my-bucket/runs/exp-99/"


def test_pipeline_s3_uri_none_when_no_config(tmp_path):
    """When run_simulation returns no config, s3_uri is None (doesn't raise)."""
    from vivarium_workbench.lib.remote_run_jobs import RemoteRunJob

    client = _FakeClient()  # returns {"database_id": 50} with no config
    ctx, landed = _ctx(tmp_path, client)
    job = RemoteRunJob("s")
    run_remote_pipeline(job, ctx)
    assert job.error is None
    assert landed["kw"]["s3_uri"] is None
