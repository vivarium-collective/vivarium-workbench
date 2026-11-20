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

@pytest.fixture
def sms_client(monkeypatch):
    """Stand in for the SMS plugin's viva-api client: ``sms_legacy.status_batch`` (``SMS_LEGACY.runs.status_many``)
    builds ``sms_legacy.SmsApiClient(...)`` at call time, so the fake answers the batch. Returns the setter."""
    from vivarium_workbench_sms import sms_legacy

    def use(fake):
        monkeypatch.setattr(sms_legacy, "SmsApiClient", lambda *a, **k: fake)
        return fake

    return use

class _Client:
    """Records the ids asked for, so the ONE-call property is testable."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def compose_status_batch(self, ids):
        self.calls.append(list(ids))
        return self.rows


@pytest.mark.plugin
def test_refresh_resolves_completed_and_failed_in_one_call(sms_client):
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
    sms_client(client)
    run_jobs.refresh_submitted(job)

    items = job.to_dict()["items"]
    assert items[0]["status"] == "done"
    assert items[1]["status"] == "failed"
    assert items[1]["error"] == "OOMKilled"
    assert items[2]["status"] == "submitted", "still running upstream"
    # one call for all three, not one call each — the point of the batch endpoint
    assert client.calls == [[1, 2, 3]]


@pytest.mark.plugin
@pytest.mark.parametrize("upstream", ["waiting", "queued", "running", "pending",
                                      "suspended", "unknown", ""])
def test_non_terminal_upstream_states_stay_submitted(upstream, sms_client):
    """An unknown state is not a finished one."""
    job = _job({"status": "submitted", "simulation_id": 7})
    sms_client(_Client([{"sim_id": 7, "status": upstream}]))
    run_jobs.refresh_submitted(job)
    assert job.to_dict()["items"][0]["status"] == "submitted"


@pytest.mark.plugin
def test_unreachable_upstream_leaves_items_submitted(sms_client):
    """Losing the poll must not turn a running campaign into a failed one."""
    class _Down:
        def compose_status_batch(self, ids):
            raise RuntimeError("sms-api unreachable")

    job = _job({"status": "submitted", "simulation_id": 7})
    sms_client(_Down())
    run_jobs.refresh_submitted(job)
    assert job.to_dict()["items"][0]["status"] == "submitted"


@pytest.mark.plugin
def test_job_completes_once_every_item_resolves(sms_client):
    job = _job({"status": "submitted", "simulation_id": 1},
               {"status": "submitted", "simulation_id": 2})
    sms_client(_Client([
        {"sim_id": 1, "status": "completed"},
        {"sim_id": 2, "status": "completed"},
    ]))
    run_jobs.refresh_submitted(job)
    d = job.to_dict()
    assert d["status"] == "done"
    assert d["completed_at"] is not None


@pytest.mark.plugin
def test_refresh_is_a_noop_without_submitted_items(sms_client):
    """No network call when there is nothing in flight."""
    client = _Client([])
    sms_client(client)
    run_jobs.refresh_submitted(_job({"status": "done"}))
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


@pytest.mark.plugin
def test_viva_core_and_compose_items_settle_against_their_own_backend(monkeypatch, sms_client):
    from vivarium_workbench.lib import remote_run_compose
    from vivarium_workbench_sms import remote_run_viva_v1

    viva = _patch_status(monkeypatch, remote_run_viva_v1, {
        "simulation-cDA421C": ({"kind": "run", "phase": "done", "raw_status": "completed", "error": None,
                                "run_id": "simulation-cDA421C"}, 200)})
    comp = _patch_status(monkeypatch, remote_run_compose, {
        "compose-4706": ({"kind": "run", "phase": "failed", "raw_status": "out_of_memory", "error": "OOMKilled",
                          "run_id": "compose-4706"}, 200)})
    sms = _NoSms()
    job = _job({"status": "submitted", "simulation_id": "simulation-cDA421C"},
               {"status": "submitted", "simulation_id": "compose-4706"})
    sms_client(sms)
    run_jobs.refresh_submitted(job)

    d = job.to_dict()
    assert d["items"][0]["status"] == "done"
    assert (d["items"][1]["status"], d["items"][1]["error"]) == ("failed", "OOMKilled")
    assert d["status"] == "failed" and d["completed_at"] is not None
    assert (viva, comp, sms.calls) == (["simulation-cDA421C"], ["compose-4706"], [])


@pytest.mark.plugin
def test_a_failed_document_run_without_a_message_names_its_upstream_state(monkeypatch, sms_client):
    from vivarium_workbench_sms import remote_run_viva_v1

    _patch_status(monkeypatch, remote_run_viva_v1, {
        "simulation-X": ({"kind": "run", "phase": "failed", "raw_status": "cancelled", "error": None,
                          "run_id": "simulation-X"}, 200)})
    job = _job({"status": "submitted", "simulation_id": "simulation-X"})
    sms_client(_NoSms())
    run_jobs.refresh_submitted(job)
    item = job.to_dict()["items"][0]
    assert (item["status"], item["error"]) == ("failed", "upstream: cancelled")


@pytest.mark.plugin
def test_a_running_document_run_stays_submitted_with_its_phase(monkeypatch, sms_client):
    from vivarium_workbench.lib import remote_run_compose

    _patch_status(monkeypatch, remote_run_compose, {
        "compose-9": ({"kind": "run", "phase": "queued", "raw_status": "submitting", "error": None,
                       "run_id": "compose-9"}, 200)})
    job = _job({"status": "submitted", "simulation_id": "compose-9"})
    sms_client(_NoSms())
    run_jobs.refresh_submitted(job)
    item = job.to_dict()["items"][0]
    assert (item["status"], item["phase"]) == ("submitted", "submitting")
    assert job.to_dict()["status"] != "done"


@pytest.mark.plugin
@pytest.mark.parametrize("answer", [
    ({"error": "unreachable", "reachable": False, "run_id": "simulation-Y"}, 502),
    ({"phase": "not_found", "error": "no such run", "run_id": "simulation-Y"}, 404),
])
def test_a_document_backend_that_does_not_answer_leaves_the_item_submitted(monkeypatch, answer, sms_client):
    """Losing the poll must not turn a running run into a failed one -- on any backend."""
    from vivarium_workbench_sms import remote_run_viva_v1

    _patch_status(monkeypatch, remote_run_viva_v1, {"simulation-Y": answer})
    job = _job({"status": "submitted", "simulation_id": "simulation-Y"})
    sms_client(_NoSms())
    run_jobs.refresh_submitted(job)
    assert job.to_dict()["items"][0]["status"] == "submitted"


@pytest.mark.plugin
def test_a_raising_backend_does_not_stop_the_others(monkeypatch, sms_client):
    from vivarium_workbench.lib import remote_run_compose
    from vivarium_workbench_sms import remote_run_viva_v1

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
    sms_client(client)
    run_jobs.refresh_submitted(job)
    items = job.to_dict()["items"]
    assert [it["status"] for it in items] == ["submitted", "done", "done"]
    assert client.calls == [[5]], "the SMS batch call carries only the SMS ids, as before"


@pytest.mark.plugin
def test_an_unreachable_backend_is_asked_once_per_refresh(monkeypatch, sms_client):
    """Each ask is a synchronous request inside the status GET: a down host is not asked again for every item."""
    from vivarium_workbench.lib import remote_run_compose

    asked = _patch_status(monkeypatch, remote_run_compose, {
        f"compose-{n}": ({"error": "unreachable", "reachable": False}, 502) for n in (1, 2, 3)})
    job = _job(*({"status": "submitted", "simulation_id": f"compose-{n}"} for n in (1, 2, 3)))
    sms_client(_NoSms())
    run_jobs.refresh_submitted(job)
    assert asked == ["compose-1"]
    assert all(it["status"] == "submitted" for it in job.to_dict()["items"])


@pytest.mark.plugin
def test_one_stale_viva_core_run_does_not_block_the_others(monkeypatch, sms_client):
    """Review of #1661: viva-core's adapter answers a 404 for an unknown run as 502 "unreachable", carrying the
    server's own status. A deleted run (or a reset server) answers about that run only; the backend is not down,
    so the next item is still asked and settles."""
    from vivarium_workbench_sms import remote_run_viva_v1

    asked = _patch_status(monkeypatch, remote_run_viva_v1, {
        "simulation-GONE": ({"phase": "unreachable", "reachable": False, "reason": "backend unreachable",
                             "status": 404, "error": "GET .../simulation-GONE/status -> 404"}, 502),
        "simulation-OK": ({"kind": "run", "phase": "done", "raw_status": "completed", "error": None,
                           "run_id": "simulation-OK"}, 200)})
    job = _job({"status": "submitted", "simulation_id": "simulation-GONE"},
               {"status": "submitted", "simulation_id": "simulation-OK"})
    sms_client(_NoSms())
    run_jobs.refresh_submitted(job)
    items = job.to_dict()["items"]
    assert asked == ["simulation-GONE", "simulation-OK"]
    assert (items[0]["status"], items[1]["status"]) == ("submitted", "done")


@pytest.mark.plugin
def test_a_viva_core_server_error_still_skips_the_backend(monkeypatch, sms_client):
    """A real 5xx (or no HTTP status at all: a refused connection) still means the backend did not answer."""
    from vivarium_workbench_sms import remote_run_viva_v1

    asked = _patch_status(monkeypatch, remote_run_viva_v1, {
        f"simulation-{n}": ({"phase": "unreachable", "reachable": False, "status": s, "error": "x"}, 502)
        for n, s in ((1, 503), (2, None))})
    job = _job(*({"status": "submitted", "simulation_id": f"simulation-{n}"} for n in (1, 2)))
    sms_client(_NoSms())
    run_jobs.refresh_submitted(job)
    assert asked == ["simulation-1"]


# --- step P2 Runs c2: the SMS batch lives in the plugin (SMS_LEGACY.runs.status_many) ------------------------------
#
# Each row is one upstream answer for a single submitted item; the expected item update and job status are what
# ``refresh_submitted`` produced on the base commit (e4658420: ``_refresh_sms`` + ``_refresh_one``), pasted here.
# One item per case on purpose: with several rows, a non-text status used to fail only the rows after it (a
# partial update) and now fails the whole batch -- that one declared difference is pinned separately below.

_BASE_ITEM = {"study": "s", "status": "submitted", "simulation_id": 7}

_MAPPING = [
    ("completed", [{"sim_id": 7, "status": "completed"}], {"status": "done"}, "done"),
    ("COMPLETED", [{"sim_id": 7, "status": "COMPLETED"}], {"status": "done"}, "done"),
    ("failed-msg", [{"sim_id": 7, "status": "failed", "error_message": "OOMKilled"}],
     {"status": "failed", "error": "OOMKilled"}, "failed"),
    ("failed", [{"sim_id": 7, "status": "failed"}], {"status": "failed", "error": "upstream: failed"}, "failed"),
    ("cancelled", [{"sim_id": 7, "status": "cancelled"}],
     {"status": "failed", "error": "upstream: cancelled"}, "failed"),
    ("out_of_memory", [{"sim_id": 7, "status": "out_of_memory", "error_message": ""}],
     {"status": "failed", "error": "upstream: out_of_memory"}, "failed"),
    ("timeout", [{"sim_id": 7, "status": "timeout", "error_message": None}],
     {"status": "failed", "error": "upstream: timeout"}, "failed"),
    ("running", [{"sim_id": 7, "status": "running"}], {"phase": "running"}, "queued"),
    ("Queued", [{"sim_id": 7, "status": "Queued"}], {"phase": "queued"}, "queued"),
    ("empty", [{"sim_id": 7, "status": ""}], {"phase": "running"}, "queued"),
    ("none", [{"sim_id": 7, "status": None}], {"phase": "running"}, "queued"),
    ("absent", [{"sim_id": 7}], {"phase": "running"}, "queued"),
    ("missing-row", [{"sim_id": 8, "status": "completed"}], {}, "queued"),
    ("non-dict-row", ["completed"], {}, "queued"),
    ("non-text-status", [{"sim_id": 7, "status": 5}], {}, "queued"),
]


@pytest.mark.plugin
@pytest.mark.parametrize("rows,update,job_status", [m[1:] for m in _MAPPING], ids=[m[0] for m in _MAPPING])
def test_sms_batch_mapping_matches_the_base(sms_client, rows, update, job_status):
    client = sms_client(_Client(rows))
    job = _job(_BASE_ITEM)
    run_jobs.refresh_submitted(job)
    d = job.to_dict()
    assert d["items"][0] == {**_BASE_ITEM, **update}
    assert d["status"] == job_status
    assert client.calls == [[7]]


@pytest.mark.plugin
def test_a_non_text_status_now_drops_the_whole_batch(sms_client):
    """Declared change (Runs c2): the plugin builds every body before the core applies any, so a status that is not
    text (``.lower()`` raises) leaves EVERY item of that batch ``submitted``. Before, the rows ahead of it had
    already been applied (a partial update)."""
    sms_client(_Client([{"sim_id": 1, "status": "completed"}, {"sim_id": 2, "status": 5}]))
    job = _job({"status": "submitted", "simulation_id": 1}, {"status": "submitted", "simulation_id": 2})
    run_jobs.refresh_submitted(job)
    assert [it["status"] for it in job.to_dict()["items"]] == ["submitted", "submitted"]


@pytest.mark.plugin
def test_an_id_stored_as_text_settles(sms_client):
    """Declared fix (Runs c2): an item whose id was stored as the text ``"12"`` is asked as ``12`` and now matches
    the batch's integer ``sim_id`` (before, ``by_sim.get("12")`` never matched and the item stayed submitted)."""
    client = sms_client(_Client([{"sim_id": 12, "status": "completed"}]))
    job = _job({"status": "submitted", "simulation_id": "12"})
    run_jobs.refresh_submitted(job)
    assert job.to_dict()["items"][0]["status"] == "done"
    assert client.calls == [[12]]


@pytest.mark.plugin
def test_a_batch_answer_that_is_not_a_mapping_leaves_items_submitted_and_still_settles(monkeypatch):
    """``_refresh_many`` is total like the ``_refresh_sms`` it replaced: a ``status_many`` answering a non-mapping
    leaves its items submitted, and the job still rolls up once every item has resolved."""
    from vivarium_workbench.lib import backends, remote_run_compose

    monkeypatch.setattr(type(backends.SMS_LEGACY.runs), "status_many", lambda self, ids: ["not", "a", "dict"])
    _patch_status(monkeypatch, remote_run_compose, {
        "compose-1": ({"kind": "run", "phase": "failed", "raw_status": "failed", "error": "x",
                       "run_id": "compose-1"}, 200)})
    job = _job({"status": "submitted", "simulation_id": 3}, {"status": "submitted", "simulation_id": "compose-1"})
    run_jobs.refresh_submitted(job)
    assert [it["status"] for it in job.to_dict()["items"]] == ["submitted", "failed"]

    # ... and once the batch answers, the roll-up still runs after it.
    monkeypatch.setattr(type(backends.SMS_LEGACY.runs), "status_many",
                        lambda self, ids: {"3": {"phase": "done", "raw_status": "completed", "error": None}})
    run_jobs.refresh_submitted(job)
    d = job.to_dict()
    assert [it["status"] for it in d["items"]] == ["done", "failed"]
    assert d["status"] == "failed" and d["completed_at"] is not None


@pytest.fixture
def providers_off(monkeypatch):
    from vivarium_workbench.lib import backends
    from vivarium_workbench.lib.backends import provider as provider_mod

    monkeypatch.setenv(provider_mod.PROVIDERS_ENV, "none")
    backends.reset_registry()
    yield
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()


def _providers_off_refresh(monkeypatch):
    """Refresh a job of an integer item, a viva-core-style item and a compose-api item with providers off; return
    the job's dict and the compose-api ids asked."""
    from vivarium_workbench.lib import remote_run_compose

    asked = _patch_status(monkeypatch, remote_run_compose, {
        "compose-1": ({"kind": "run", "phase": "done", "raw_status": "completed", "error": None,
                       "run_id": "compose-1"}, 200)})
    job = _job({"status": "submitted", "simulation_id": 4},
               {"status": "submitted", "simulation_id": "simulation-Q"},
               {"status": "submitted", "simulation_id": "compose-1"})
    run_jobs.refresh_submitted(job)
    return job.to_dict(), asked


def test_providers_off_integer_items_are_not_asked(monkeypatch, providers_off):
    """Declared change (Runs c2), providers off only: no active backend owns an integer id, so SMS is not asked
    (before, ``owner is None`` fell through to the SMS batch) and the item stays ``submitted``. A compose-api item
    still settles, and a viva-core-style id no longer poisons an SMS batch either. (Core: also true with the plugin
    absent; the SMS client being left alone is pinned below, where the plugin is there to ask.)"""
    d, asked = _providers_off_refresh(monkeypatch)
    assert [it["status"] for it in d["items"]] == ["submitted", "submitted", "done"]
    assert asked == ["compose-1"]
    assert d["status"] != "done"


@pytest.mark.plugin
def test_providers_off_the_installed_sms_client_is_not_asked(monkeypatch, providers_off):
    """The same refresh with the plugin installed but providers off: its SMS client is never built."""
    from vivarium_workbench_sms import sms_legacy

    sms_asked = []  # recorded, not raised: refresh_submitted swallows every exception, so a raise would prove nothing

    def _refuse(*a, **k):
        sms_asked.append(a)
        raise AssertionError("asked SMS with providers off")

    monkeypatch.setattr(sms_legacy, "SmsApiClient", _refuse)
    d, asked = _providers_off_refresh(monkeypatch)
    assert [it["status"] for it in d["items"]] == ["submitted", "submitted", "done"]
    assert asked == ["compose-1"]
    assert sms_asked == []
    assert d["status"] != "done"


def test_run_jobs_binds_no_sms_seam():
    """Runs c2 moved the SMS batch status into the plugin: the core keeps no SMS name ..."""
    import inspect

    for name in ("_refresh_sms", "_UPSTREAM_DONE", "_UPSTREAM_FAILED"):
        assert not hasattr(run_jobs, name), name
    assert "client" not in inspect.signature(run_jobs.refresh_submitted).parameters


@pytest.mark.plugin
def test_the_plugin_holds_the_sms_batch_status():
    """... and the plugin holds them."""
    from vivarium_workbench_sms import sms_legacy

    assert sms_legacy._UPSTREAM_DONE == frozenset({"completed"})
    assert sms_legacy._UPSTREAM_FAILED == frozenset({"failed", "cancelled", "out_of_memory", "timeout"})
    assert callable(sms_legacy.status_batch)
