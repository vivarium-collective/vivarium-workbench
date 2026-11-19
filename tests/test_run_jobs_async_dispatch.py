"""The job layer understands an async dispatch — §A2' of
`docs/run-orchestration-consolidation.md`.

On a deployment target `study_runs.run_study_baseline` returns
`remote_run_views.remote_run_submit` verbatim: **202** with a `simulation_id`,
already non-blocking. The job worker accepted only HTTP 200, so every successful
Batch dispatch was recorded `failed` with the error text "HTTP 202" — and the
`simulation_id` was thrown away, leaving nothing to poll.
"""
import pytest

from vivarium_workbench.lib import run_jobs
from vivarium_workbench.lib.run_jobs import RunJob, TERMINAL_STATUSES


def _job(*items):
    return RunJob("inv", [dict(it) for it in items])


# --- what the worker records -------------------------------------------------

def test_submitted_is_not_terminal():
    """The distinction the whole change rests on: dispatched is not finished."""
    assert "submitted" not in TERMINAL_STATUSES
    assert TERMINAL_STATUSES == {"done", "failed", "skipped"}


def test_progress_counts_submitted_separately():
    job = _job({"status": "submitted"}, {"status": "done"}, {"status": "queued"})
    p = job.to_dict()["progress"]
    assert (p["total"], p["done"], p["submitted"]) == (3, 1, 1)


# --- resolving against viva-api ----------------------------------------------

class _Client:
    """Records the ids asked for, so the ONE-call property is testable."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def compose_status_batch(self, ids):
        self.calls.append(list(ids))
        return self.rows


def test_refresh_resolves_completed_and_failed_in_one_call():
    job = _job(
        {"status": "submitted", "simulation_id": 1},
        {"status": "submitted", "simulation_id": 2},
        {"status": "submitted", "simulation_id": 3},
    )
    client = _Client([
        {"sim_id": 1, "status": "completed"},
        {"sim_id": 2, "status": "failed", "error_message": "OOMKilled"},
        {"sim_id": 3, "status": "running"},
    ])
    run_jobs.refresh_submitted(job, client=client)

    items = job.to_dict()["items"]
    assert items[0]["status"] == "done"
    assert items[1]["status"] == "failed"
    assert items[1]["error"] == "OOMKilled"
    assert items[2]["status"] == "submitted", "still running upstream"
    # one call for all three, not one call each — the point of the batch endpoint
    assert client.calls == [[1, 2, 3]]


@pytest.mark.parametrize("upstream", ["waiting", "queued", "running", "pending",
                                      "suspended", "unknown", ""])
def test_non_terminal_upstream_states_stay_submitted(upstream):
    """An unknown state is not a finished one."""
    job = _job({"status": "submitted", "simulation_id": 7})
    run_jobs.refresh_submitted(job, client=_Client([{"sim_id": 7, "status": upstream}]))
    assert job.to_dict()["items"][0]["status"] == "submitted"


def test_unreachable_upstream_leaves_items_submitted():
    """Losing the poll must not turn a running campaign into a failed one."""
    class _Down:
        def compose_status_batch(self, ids):
            raise RuntimeError("sms-api unreachable")

    job = _job({"status": "submitted", "simulation_id": 7})
    run_jobs.refresh_submitted(job, client=_Down())
    assert job.to_dict()["items"][0]["status"] == "submitted"


def test_job_completes_once_every_item_resolves():
    job = _job({"status": "submitted", "simulation_id": 1},
               {"status": "submitted", "simulation_id": 2})
    run_jobs.refresh_submitted(job, client=_Client([
        {"sim_id": 1, "status": "completed"},
        {"sim_id": 2, "status": "completed"},
    ]))
    d = job.to_dict()
    assert d["status"] == "done"
    assert d["completed_at"] is not None


def test_refresh_is_a_noop_without_submitted_items():
    """No network call when there is nothing in flight."""
    client = _Client([])
    run_jobs.refresh_submitted(_job({"status": "done"}), client=client)
    assert client.calls == []


# --- the defect itself, at the worker -----------------------------------------

def _capture_worker(tmp_path, monkeypatch):
    """Build a real `_worker` closure via the public entry, as the app does.

    Layout matches `test_run_unblocked_views_lib._make_ws`: investigations and
    studies sit FLAT at the workspace root, which is where `WorkspacePaths`
    resolves them when workspace.yaml declares no `layout:`. Getting this wrong
    makes `investigation_run_unblocked` return 404 and the worker never exist —
    a test that looks like it exercises the dispatch but does not.
    """
    from vivarium_workbench.lib import run_unblocked_views as ruv
    inv_dir = tmp_path / "investigations" / "inv-a"
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(
        "studies:\n  - study-a\n", encoding="utf-8")
    sdir = tmp_path / "studies" / "study-a"
    sdir.mkdir(parents=True)
    (sdir / "study.yaml").write_text("baseline: []\n", encoding="utf-8")

    monkeypatch.setattr(ruv, "enumerate_unblocked", lambda spec: (
        [{"study": "study-a", "variant": "baseline", "kind": "baseline",
          "status": "queued"}], []))
    grabbed = {}

    class _J:
        job_id = "J1"

    monkeypatch.setattr(ruv.manager, "submit",
                        lambda i, it, w: (grabbed.__setitem__("w", w), _J())[1])
    monkeypatch.setattr(ruv.comparative_runs,
                        "render_investigation_comparative_visualisations",
                        lambda *a, **k: None)
    body, code = ruv.investigation_run_unblocked(tmp_path, {"investigation": "inv-a"})
    assert code == 202, f"fixture did not reach the dispatch path: {body}"
    return ruv, grabbed["w"]


def test_worker_records_a_202_as_submitted_not_failed(tmp_path, monkeypatch):
    """The defect: a successful Batch dispatch was recorded `failed` with the
    error text "HTTP 202", and the simulation_id was discarded."""
    ruv, worker = _capture_worker(tmp_path, monkeypatch)
    called = []

    def _submit_stub(ws, body):
        called.append(body)
        return {"simulation_id": 4242, "phase": "running"}, 202

    monkeypatch.setattr(ruv.study_runs, "run_study_baseline", _submit_stub)

    job = _job({"study": "study-a", "variant": "baseline",
                "kind": "baseline", "status": "queued"})
    worker(job)

    # Prove the path was actually exercised before trusting the outcome — a
    # mis-built fixture otherwise yields a passing test that ran nothing.
    assert called, "run_study_baseline was never reached"

    item = job.to_dict()["items"][0]
    assert item["status"] == "submitted", "202 is a dispatch, not a failure"
    assert item["simulation_id"] == 4242, "the handle must be kept, or nothing can poll"
    assert "error" not in item


def test_worker_still_marks_a_real_failure_failed(tmp_path, monkeypatch):
    ruv, worker = _capture_worker(tmp_path, monkeypatch)
    called = []

    def _fail_stub(ws, body):
        called.append(body)
        return {"error": "nope"}, 409

    monkeypatch.setattr(ruv.study_runs, "run_study_baseline", _fail_stub)
    job = _job({"study": "study-a", "variant": "baseline",
                "kind": "baseline", "status": "queued"})
    worker(job)
    assert called, "run_study_baseline was never reached"
    item = job.to_dict()["items"][0]
    assert (item["status"], item["error"]) == ("failed", "nope")


def test_a_200_still_completes_the_item(tmp_path, monkeypatch):
    """The local path is untouched: a synchronous run still finishes the item."""
    ruv, worker = _capture_worker(tmp_path, monkeypatch)
    monkeypatch.setattr(ruv.study_runs, "run_study_baseline",
                        lambda ws, body: ({"run_id": "r-1"}, 200))
    job = _job({"study": "study-a", "variant": "baseline",
                "kind": "baseline", "status": "queued"})
    worker(job)
    item = job.to_dict()["items"][0]
    assert (item["status"], item["run_id"]) == ("done", "r-1")


def test_worker_keeps_a_viva_v1_dispatchs_run_id_as_the_handle(tmp_path, monkeypatch):
    """A /viva/v1 dispatch answers 202 with the backend's ``run_id`` (no integer simulation id); that id is the
    only thing that can be polled or landed later, so it must be kept."""
    ruv, worker = _capture_worker(tmp_path, monkeypatch)
    monkeypatch.setattr(ruv.study_runs, "run_study_baseline",
                        lambda ws, body: ({"run_id": "simulation-cDA421C", "phase": "running", "backend": "viva-v1"}, 202))
    job = _job({"study": "study-a", "variant": "baseline", "kind": "baseline", "status": "queued"})
    worker(job)
    item = job.to_dict()["items"][0]
    assert (item["status"], item["simulation_id"]) == ("submitted", "simulation-cDA421C")


# --- items dispatched to a document backend (viva-core / compose-api) ---------
#
# A named backend that runs documents answers a study run with 202 + its own run id (``simulation-...`` from
# viva-core, ``compose-<n>`` from compose-api) and no integer simulation id. Those items used to be asked of the SMS
# host's integer-only batch route, which raised on the string id, so they stayed ``submitted`` forever.

class _NoSms:
    """An SMS client that must not be asked: the items are not SMS simulations."""

    def __init__(self):
        self.calls = []

    def compose_status_batch(self, ids):
        self.calls.append(list(ids))
        raise AssertionError(f"asked the SMS host about {ids}")


def _patch_status(monkeypatch, module, answers):
    asked = []

    def _status(run_id):
        asked.append(run_id)
        return answers[run_id]

    monkeypatch.setattr(module, "status", _status)
    return asked


def test_viva_core_and_compose_items_settle_against_their_own_backend(monkeypatch):
    from vivarium_workbench.lib import remote_run_compose, remote_run_viva_v1

    viva = _patch_status(monkeypatch, remote_run_viva_v1, {
        "simulation-cDA421C": ({"kind": "run", "phase": "done", "raw_status": "completed", "error": None,
                                "run_id": "simulation-cDA421C"}, 200)})
    comp = _patch_status(monkeypatch, remote_run_compose, {
        "compose-4706": ({"kind": "run", "phase": "failed", "raw_status": "out_of_memory", "error": "OOMKilled",
                          "run_id": "compose-4706"}, 200)})
    sms = _NoSms()
    job = _job({"status": "submitted", "simulation_id": "simulation-cDA421C"},
               {"status": "submitted", "simulation_id": "compose-4706"})
    run_jobs.refresh_submitted(job, client=sms)

    d = job.to_dict()
    assert d["items"][0]["status"] == "done"
    assert (d["items"][1]["status"], d["items"][1]["error"]) == ("failed", "OOMKilled")
    assert d["status"] == "failed" and d["completed_at"] is not None
    assert (viva, comp, sms.calls) == (["simulation-cDA421C"], ["compose-4706"], [])


def test_a_failed_document_run_without_a_message_names_its_upstream_state(monkeypatch):
    from vivarium_workbench.lib import remote_run_viva_v1

    _patch_status(monkeypatch, remote_run_viva_v1, {
        "simulation-X": ({"kind": "run", "phase": "failed", "raw_status": "cancelled", "error": None,
                          "run_id": "simulation-X"}, 200)})
    job = _job({"status": "submitted", "simulation_id": "simulation-X"})
    run_jobs.refresh_submitted(job, client=_NoSms())
    item = job.to_dict()["items"][0]
    assert (item["status"], item["error"]) == ("failed", "upstream: cancelled")


def test_a_running_document_run_stays_submitted_with_its_phase(monkeypatch):
    from vivarium_workbench.lib import remote_run_compose

    _patch_status(monkeypatch, remote_run_compose, {
        "compose-9": ({"kind": "run", "phase": "queued", "raw_status": "submitting", "error": None,
                       "run_id": "compose-9"}, 200)})
    job = _job({"status": "submitted", "simulation_id": "compose-9"})
    run_jobs.refresh_submitted(job, client=_NoSms())
    item = job.to_dict()["items"][0]
    assert (item["status"], item["phase"]) == ("submitted", "submitting")
    assert job.to_dict()["status"] != "done"


@pytest.mark.parametrize("answer", [
    ({"error": "unreachable", "reachable": False, "run_id": "simulation-Y"}, 502),
    ({"phase": "not_found", "error": "no such run", "run_id": "simulation-Y"}, 404),
])
def test_a_document_backend_that_does_not_answer_leaves_the_item_submitted(monkeypatch, answer):
    """Losing the poll must not turn a running run into a failed one -- on any backend."""
    from vivarium_workbench.lib import remote_run_viva_v1

    _patch_status(monkeypatch, remote_run_viva_v1, {"simulation-Y": answer})
    job = _job({"status": "submitted", "simulation_id": "simulation-Y"})
    run_jobs.refresh_submitted(job, client=_NoSms())
    assert job.to_dict()["items"][0]["status"] == "submitted"


def test_a_raising_backend_does_not_stop_the_others(monkeypatch):
    from vivarium_workbench.lib import remote_run_compose, remote_run_viva_v1

    def _boom(run_id):
        raise RuntimeError("down")

    monkeypatch.setattr(remote_run_viva_v1, "status", _boom)
    _patch_status(monkeypatch, remote_run_compose, {
        "compose-1": ({"kind": "run", "phase": "done", "raw_status": "completed", "error": None,
                       "run_id": "compose-1"}, 200)})
    client = _Client([{"sim_id": 5, "status": "completed"}])
    job = _job({"status": "submitted", "simulation_id": "simulation-Z"},
               {"status": "submitted", "simulation_id": "compose-1"},
               {"status": "submitted", "simulation_id": 5})
    run_jobs.refresh_submitted(job, client=client)
    items = job.to_dict()["items"]
    assert [it["status"] for it in items] == ["submitted", "done", "done"]
    assert client.calls == [[5]], "the SMS batch call carries only the SMS ids, as before"


def test_an_unreachable_backend_is_asked_once_per_refresh(monkeypatch):
    """Each ask is a synchronous request inside the status GET: a down host is not asked again for every item."""
    from vivarium_workbench.lib import remote_run_compose

    asked = _patch_status(monkeypatch, remote_run_compose, {
        f"compose-{n}": ({"error": "unreachable", "reachable": False}, 502) for n in (1, 2, 3)})
    job = _job(*({"status": "submitted", "simulation_id": f"compose-{n}"} for n in (1, 2, 3)))
    run_jobs.refresh_submitted(job, client=_NoSms())
    assert asked == ["compose-1"]
    assert all(it["status"] == "submitted" for it in job.to_dict()["items"])
