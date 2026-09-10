import io
import json
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from vivarium_workbench.lib.sms_api_client import SmsApiClient, SmsApiError


class _Resp(io.BytesIO):
    status = 200

    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@contextmanager
def _patch_urlopen(monkeypatch, capture, payload, status=200):
    def fake_urlopen(req, timeout=None):
        capture["url"] = req.full_url
        capture["method"] = req.get_method()
        capture["body"] = req.data
        if status != 200:
            from urllib.error import HTTPError

            raise HTTPError(req.full_url, status, "err", {}, io.BytesIO(b"boom"))
        return _Resp(payload, status)

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    yield


def test_latest_simulator_builds_query(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"git_commit_hash": "abc123"}):
        c = SmsApiClient("http://h:8080")
        out = c.latest_simulator("https://github.com/x/v2ecoli", "master")
    assert out["git_commit_hash"] == "abc123"
    assert cap["url"].startswith("http://h:8080/core/v1/simulator/latest?")
    assert "git_branch=master" in cap["url"]
    assert "git_repo_url=https%3A%2F%2Fgithub.com%2Fx%2Fv2ecoli" in cap["url"]


def test_observables_repeats_names_param(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"time": [0.0], "series": {"mass": [1.0]}}):
        c = SmsApiClient("http://h:8080")
        out = c.observables(49, ["mass", "volume"], seed=0)
    assert out["series"]["mass"] == [1.0]
    assert "/api/v1/simulations/49/observables?" in cap["url"]
    assert "names=mass%2Cvolume" in cap["url"]
    assert "seed=0" in cap["url"]


def test_non_200_raises(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {}, status=404):
        c = SmsApiClient("http://h:8080")
        with pytest.raises(SmsApiError):
            c.simulation_status(999)


def _http_error(status, body):
    from urllib.error import HTTPError
    return HTTPError("http://h:8080/x", status, "err", {}, io.BytesIO(body))


def test_http_error_detail_summarises_html_gateway_page():
    """A 502/504 from a proxy is an HTML page, not JSON — summarise it to its
    <title> instead of echoing the whole markup into the user's alert/logs."""
    from vivarium_workbench.lib.sms_api_client import _http_error_detail
    html = (b"<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n"
            b"<body>\r\n<center><h1>502 Bad Gateway</h1></center>\r\n</body>\r\n</html>")
    out = _http_error_detail(_http_error(502, html))
    assert out == ": 502 Bad Gateway"
    assert "<html" not in out and "<h1>" not in out


def test_http_error_detail_keeps_json_detail():
    """A FastAPI {"detail": ...} body still surfaces the useful message."""
    from vivarium_workbench.lib.sms_api_client import _http_error_detail
    out = _http_error_detail(_http_error(422, b'{"detail": "missing field foo"}'))
    assert out == ": missing field foo"


def test_http_error_detail_caps_length():
    from vivarium_workbench.lib.sms_api_client import _http_error_detail
    out = _http_error_detail(_http_error(500, b"x" * 5000))
    assert len(out) <= 210 and out.endswith("…")


def test_run_simulation_query_and_repeated_observables(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"database_id": 50}):
        c = SmsApiClient("http://h:8080")
        out = c.run_simulation(
            simulator_id=15, num_generations=1, num_seeds=1, run_parca=True,
            observables=["mass", "volume"], experiment_id="exp1",
        )
    assert out["database_id"] == 50
    assert cap["method"] == "POST"
    qs = parse_qs(urlsplit(cap["url"]).query)
    assert qs["simulator_id"] == ["15"]
    assert qs["num_generations"] == ["1"]
    assert qs["run_parca"] == ["True"]
    assert qs["observables"] == ["mass", "volume"]  # repeated key, not comma-joined
    assert qs["experiment_id"] == ["exp1"]


def test_run_simulation_sends_analysis_options_as_json_body(monkeypatch):
    """analysis_options is a Pydantic model with no Query()/Body() wrapper on
    the sms-api route — FastAPI reads a bare model param from the JSON
    request body, not the query string (unlike every other run_simulation
    param above)."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"database_id": 51}):
        c = SmsApiClient("http://h:8080")
        out = c.run_simulation(
            simulator_id=15, num_generations=1, num_seeds=1, run_parca=True,
            observables=["mass"], analysis_options={"multiseed": {"ecocyc_table": {}}},
        )
    assert out["database_id"] == 51
    assert json.loads(cap["body"].decode()) == {
        "analysis_options": {"multiseed": {"ecocyc_table": {}}}
    }
    qs = parse_qs(urlsplit(cap["url"]).query)
    assert "analysis_options" not in qs  # never in the query string


def test_run_simulation_omits_json_body_when_no_analysis_options(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"database_id": 52}):
        c = SmsApiClient("http://h:8080")
        c.run_simulation(
            simulator_id=15, num_generations=1, num_seeds=1, run_parca=True,
            observables=["mass"],
        )
    assert cap["body"] is None


def test_run_simulation_sends_config_filename_query_param(monkeypatch):
    """sms-api's own Query() default (api_simulation_default.json) only
    exists in the public vEcoli-lineage repos — a repo without it (e.g.
    sms-ecoli) 404s unless the caller supplies a real filename explicitly.
    Real name confirmed against sms-api's own route: `simulation_config_filename`
    (viva_api/api/routers/sms.py), a query param like every other scalar
    run_simulation() field, never a JSON-body key."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"database_id": 53}):
        c = SmsApiClient("http://h:8080")
        c.run_simulation(
            simulator_id=15, num_generations=1, num_seeds=1, run_parca=True,
            observables=["mass"], config_filename="mecillinam_wellmixed.json",
        )
    qs = parse_qs(urlsplit(cap["url"]).query)
    assert qs["simulation_config_filename"] == ["mecillinam_wellmixed.json"]


def test_run_simulation_omits_config_filename_when_absent(monkeypatch):
    """Unset by default — a caller with no opinion gets exactly the same
    request as before this field existed (sms-api's own default still
    applies server-side for repos that do have it)."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"database_id": 54}):
        c = SmsApiClient("http://h:8080")
        c.run_simulation(
            simulator_id=15, num_generations=1, num_seeds=1, run_parca=True,
            observables=["mass"],
        )
    qs = parse_qs(urlsplit(cap["url"]).query)
    assert "simulation_config_filename" not in qs


def test_get_simulation_returns_full_record(monkeypatch):
    """GET /api/v1/simulations/{id} -- the full record (config, simulator_id,
    num_seeds), used by P1-11's execution-provenance lookup. Distinct from
    simulation_status(), which only returns id/status/error_message."""
    cap = {}
    payload = {
        "database_id": 199,
        "simulator_id": 66,
        "config": {"generations": 2, "n_init_sims": 4},
        "num_seeds": 4,
        "experiment_id": "exp-199",
    }
    with _patch_urlopen(monkeypatch, cap, payload):
        c = SmsApiClient("http://h:8080")
        out = c.get_simulation(199)
    assert out == payload
    assert cap["url"] == "http://h:8080/api/v1/simulations/199"
    assert cap["method"] == "GET"


def test_simulator_commit_resolves_by_database_id(monkeypatch):
    """simulator_commit looks the simulator_id up in sms-api's OWN registry
    (GET /core/v1/simulator/versions) -- the server-sourced commit, never the
    landing laptop's local checkout."""
    cap = {}
    payload = {"versions": [
        {"database_id": 65, "git_commit_hash": "aaa1111"},
        {"database_id": 66, "git_commit_hash": "bbb2222"},
    ]}
    with _patch_urlopen(monkeypatch, cap, payload):
        c = SmsApiClient("http://h:8080")
        assert c.simulator_commit(66) == "bbb2222"
        assert c.simulator_commit(999) is None


def test_simulator_commit_returns_none_when_unreachable(monkeypatch):
    """Best-effort provenance: an unreachable/erroring sms-api must never
    raise out of simulator_commit -- landing must still proceed."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {}, status=502):
        c = SmsApiClient("http://h:8080")
        assert c.simulator_commit(66) is None


def test_upload_simulator_sends_json_body(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"database_id": 16, "status": "running"}):
        c = SmsApiClient("http://h:8080")
        out = c.upload_simulator({"git_commit_hash": "abc", "git_repo_url": "u", "git_branch": "b"}, force=True)
    assert out["database_id"] == 16
    assert cap["method"] == "POST"
    assert json.loads(cap["body"].decode())["git_commit_hash"] == "abc"
    assert "force=true" in cap["url"]


def test_run_analysis_sends_modules_as_query_json(monkeypatch):
    """modules is read via a query param on sms-api's endpoint (?modules=<json>),
    not a request body -- unlike run_simulation's analysis_options."""
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"job_id": "ana-exp1", "analysis_name": "analysis-exp1-ab12"}):
        c = SmsApiClient("http://h:8080")
        out = c.run_analysis(115, {"multiseed": {"doubling_time_distribution": {}}})
    assert out["job_id"] == "ana-exp1"
    assert cap["method"] == "POST"
    assert cap["body"] is None
    assert "/api/v1/simulations/115/analysis?" in cap["url"]
    qs = parse_qs(urlsplit(cap["url"]).query)
    assert json.loads(qs["modules"][0]) == {"multiseed": {"doubling_time_distribution": {}}}


def test_composite_resolve_posts_to_simulator_route(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"name": "c", "parameters": {}, "state": {}}):
        c = SmsApiClient("http://x")
        out = c.composite_resolve(66, "pkg.composites.cell", {"k": 5})
    assert cap["url"] == "http://x/core/v1/simulator/66/composite-resolve"
    assert cap["method"] == "POST"
    assert json.loads(cap["body"]) == {"composite_ref": "pkg.composites.cell", "overrides": {"k": 5}}
    assert out["name"] == "c"


def test_download_data_streams_to_file(monkeypatch, tmp_path):
    cap = {}
    payload = b"\x1f\x8b\x08fake-gzip-bytes"

    class _RawResp:
        status = 200

        def __init__(self):
            self._b = io.BytesIO(payload)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._b.close()

        def read(self, n=-1):
            return self._b.read(n)

    def fake_urlopen(req, timeout=None):
        cap["url"] = req.full_url
        cap["method"] = req.get_method()
        return _RawResp()

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080")
    out = c.download_data(49, tmp_path)
    assert out == tmp_path / "sim_49.tar.gz"
    assert out.read_bytes() == payload
    assert cap["method"] == "POST"
    assert cap["url"] == "http://h:8080/api/v1/simulations/49/data"


def test_non_200_surfaces_server_error_body(monkeypatch):
    """CD2 pipeline audit §3.12: a FastAPI 422/500's JSON ``detail`` must reach
    the raised SmsApiError, not just the bare status code."""
    cap = {}

    def fake_urlopen(req, timeout=None):
        from urllib.error import HTTPError

        cap["url"] = req.full_url
        body = json.dumps({"detail": "num_generations must be >= 1"}).encode()
        raise HTTPError(req.full_url, 422, "Unprocessable Entity", {}, io.BytesIO(body))

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080")
    with pytest.raises(SmsApiError) as exc_info:
        c.simulation_status(999)
    assert "422" in str(exc_info.value)
    assert "num_generations must be >= 1" in str(exc_info.value)
    assert exc_info.value.status == 422


def test_post_error_surfaces_server_error_body(monkeypatch):
    def fake_urlopen(req, timeout=None):
        from urllib.error import HTTPError

        body = json.dumps({"detail": {"loc": ["body", "commit"], "msg": "field required"}}).encode()
        raise HTTPError(req.full_url, 422, "Unprocessable Entity", {}, io.BytesIO(body))

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080")
    with pytest.raises(SmsApiError) as exc_info:
        c.upload_simulator({"git_commit_hash": "abc"})
    assert "field required" in str(exc_info.value)


def test_error_body_read_failure_does_not_mask_original_error(monkeypatch):
    """A body that can't be read/decoded must not prevent the original error
    from being raised (§3.12 fix must be strictly additive)."""

    class _UnreadableHTTPError(Exception):
        pass

    def fake_urlopen(req, timeout=None):
        from urllib.error import HTTPError

        class _BrokenBody:
            def read(self):
                raise OSError("body already consumed")

        e = HTTPError(req.full_url, 500, "err", {}, None)
        e.fp = _BrokenBody()
        raise e

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080")
    with pytest.raises(SmsApiError) as exc_info:
        c._get("/x")
    assert "500" in str(exc_info.value)


def test_get_retries_on_5xx_then_succeeds(monkeypatch):
    """GET is idempotent -- a transient 5xx should be retried, not raised
    immediately."""
    from urllib.error import HTTPError

    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b""))
        return _Resp({"status": "completed"})

    class _FakeTime:
        """Stand-in for the ``time`` module, scoped to this module's own
        name binding rather than the real stdlib module.

        ``monkeypatch.setattr(".../sms_api_client.time.sleep", ...)`` looks
        like it patches only this module, but ``sms_api_client.time`` IS the
        process-wide ``time`` module object (there is only one in
        ``sys.modules``) -- setting an attribute on it mutates ``time.sleep``
        for every thread in the process for the duration of the test. A
        leftover daemon thread from an earlier test's polling loop (e.g.
        ``run_jobs``/``remote_run_jobs``) that is still spinning on
        ``time.sleep(interval)`` would then have its sleep calls silently
        become no-ops and get counted into this test's ``sleeps`` list,
        which is how a real run produced 1125 recorded sleeps instead of 2.
        Rebinding the module-level ``time`` *name* inside
        ``sms_api_client`` instead leaves the real stdlib module (and any
        other thread using it) untouched.
        """

        def sleep(self, s):
            sleeps.append(s)

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.time", _FakeTime())
    c = SmsApiClient("http://h:8080")
    out = c.simulation_status(1)
    assert out["status"] == "completed"
    assert calls["n"] == 3
    assert len(sleeps) == 2  # two retries before success


def test_get_gives_up_after_max_retries(monkeypatch):
    """After the retry budget is exhausted, the last error is raised -- it
    must not retry forever."""
    from urllib.error import HTTPError

    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b'{"detail": "db down"}'))

    class _FakeTime:
        def sleep(self, s):
            pass

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.time", _FakeTime())
    c = SmsApiClient("http://h:8080")
    with pytest.raises(SmsApiError) as exc_info:
        c.simulation_status(1)
    assert calls["n"] == 3  # default retry budget, not unbounded
    assert "db down" in str(exc_info.value)


def test_get_does_not_retry_on_4xx(monkeypatch):
    """A 4xx is a client error, not transient -- retrying it would just waste
    time and could not possibly succeed."""
    from urllib.error import HTTPError

    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080")
    with pytest.raises(SmsApiError):
        c.simulation_status(1)
    assert calls["n"] == 1  # no retry


def test_post_is_never_retried_on_5xx(monkeypatch):
    """POST (submit/dispatch) must NOT be auto-retried -- a retried submit
    could double-run a simulation."""
    from urllib.error import HTTPError

    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b""))

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080")
    with pytest.raises(SmsApiError):
        c.upload_simulator({"git_commit_hash": "abc"})
    assert calls["n"] == 1  # single attempt, no retry


def test_download_data_uses_generous_default_timeout(monkeypatch, tmp_path):
    """Multi-GB native-store downloads must not inherit the 30s status-call
    default (§3.12)."""
    from vivarium_workbench.lib.sms_api_client import DOWNLOAD_TIMEOUT

    cap = {}

    class _RawResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, n=-1):
            return b""

    def fake_urlopen(req, timeout=None):
        cap["timeout"] = timeout
        return _RawResp()

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080", timeout=30.0)
    c.download_data(49, tmp_path)
    assert cap["timeout"] == DOWNLOAD_TIMEOUT
    assert DOWNLOAD_TIMEOUT > c.timeout


def test_download_data_explicit_timeout_overrides_default(monkeypatch, tmp_path):
    cap = {}

    class _RawResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, n=-1):
            return b""

    def fake_urlopen(req, timeout=None):
        cap["timeout"] = timeout
        return _RawResp()

    monkeypatch.setattr("vivarium_workbench.lib.sms_api_client.urlopen", fake_urlopen)
    c = SmsApiClient("http://h:8080")
    c.download_data(49, tmp_path, timeout=7200.0)
    assert cap["timeout"] == 7200.0


def test_analysis_status_gets_by_database_id(monkeypatch):
    cap = {}
    with _patch_urlopen(monkeypatch, cap, {"id": 7, "status": "completed", "error_log": None}):
        c = SmsApiClient("http://h:8080")
        out = c.analysis_status(7)
    assert out["status"] == "completed"
    assert cap["method"] == "GET"
    assert cap["url"] == "http://h:8080/analyses/7/status"
