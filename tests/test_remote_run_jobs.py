import time

from vivarium_dashboard.lib.remote_run_jobs import RemoteRunManager, STEP_NAMES


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
