"""The two trace routes' dispatch table, pinned answer for answer (step P2, Runs b2).

Every expected row below was computed on the commit BEFORE Runs b2 (main @ 777cac76), when ``lib/remote_trace.py``
still held viva-api's capability check, ``simulation_trace``, ``composite_run_trace`` and the ``compose_id``
correlation itself. Runs b2 moved them behind ``SMS_LEGACY.runs.trace`` / ``VIVA_CORE.runs.trace`` and the SMS
``unrouted`` facet; with providers on, nothing here may change.

Each row is a request to ``/api/remote-run-trace`` (or ``/api/remote-run-trace-support``) and records the status, the
body (the bytes, or the JSON), ``Content-Disposition``, ``X-Trace-Events`` and every call made to a backend: the SMS
client's methods (with the client's base URL) and compose-api's trace. Only seams that exist on both sides are
patched -- the SMS client's class attributes, ``remote_run_compose.trace``, the named backend's profile and the
records' identity lookups -- never a name of ``remote_trace``, so the table runs unchanged on the base and after the
move. The network is a tripwire: ``sms_api_client.urlopen`` and ``backend_config``'s ``urlopen`` raise.

The workspace holds four records: a compose-api run ``compose-6`` and a viva-core run ``simulation-V`` and an
sms-legacy run ``44`` on this workbench's backend, and an sms-legacy run ``45`` of another deployment. Any other id
has no record.
"""
from __future__ import annotations

import json

import pytest

from vivarium_workbench.lib import backend_config
from vivarium_workbench.lib import composite_runs as cr
from vivarium_workbench.lib import remote_run_compose
from vivarium_workbench.lib import server_capabilities as sc
from vivarium_workbench.lib.backends import records

HERE = "http://api:8000"
ELSEWHERE = "http://other:8000"
TRACE = json.dumps({"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, "pid": 1, "tid": 1}]}).encode()
EMPTY = json.dumps({"traceEvents": [], "displayTimeUnit": "ms"}).encode()
COMPOSE_TRACE = b'{"traceEvents": [{"ph": "M"}, {"ph": "X", "ts": 1}]}'

PROFILES = {
    None: None,
    "compose": {"reachable": True, "dispatch": sc.DISPATCH_COMPOSE, "error": None},
    "viva": {"reachable": True, "dispatch": sc.DISPATCH_DOCUMENT, "error": None},
    "legacy": {"reachable": True, "dispatch": sc.DISPATCH_LEGACY, "error": None},
}


class _Net(Exception):
    pass


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    root = tmp_path_factory.mktemp("trace-table") / "ws"
    (root / "studies" / "s").mkdir(parents=True)
    (root / "workspace.yaml").write_text("name: ws\n")
    for rid, kind, base, dep in (("compose-6", "compose-api", HERE, "smsvpctest"),
                                 ("simulation-V", "viva-core", HERE, "smsvpctest"),
                                 ("44", "sms-legacy", HERE, "smsvpctest"),
                                 ("45", "sms-legacy", ELSEWHERE, "prod-elsewhere")):
        conn = cr.connect(root / "studies" / "s" / "runs.db")
        try:
            cr.save_metadata(conn, spec_id="c", run_id=f"remote-pending-{rid}", label=None, started_at=0.0,
                             n_steps=0, params={"source": dep, "simulation_id": rid, "backend": "ray",
                                                "remote": {"kind": kind, "id": rid, "base_url": base,
                                                           "deployment": dep, "server_id": None}})
        finally:
            conn.close()
    return root


@pytest.fixture(scope="module")
def app():
    from vivarium_workbench.api.app import create_app

    return create_app()


@pytest.fixture
def world(ws, app, monkeypatch):
    """The table's world: providers on, the SMS client's class patched where the plugin is installed. Without it
    (the core-only job) there is no SMS client to patch or to call, and the cases the core answers alone -- the
    unmarked rows -- still match the golden; the rest are marked ``plugin``."""
    from fastapi.testclient import TestClient

    from vivarium_workbench.api.app import get_workspace
    try:
        from vivarium_workbench_sms import sms_api_client as sac
    except ImportError:   # the core-only job
        sac = None

    for v in (*backend_config.BACKEND_BASE_ENV_VARS, "VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT",
              "VIVARIUM_WORKBENCH_BACKEND_PROVIDERS"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", HERE)  # this workbench's one backend
    monkeypatch.setenv("VIVARIUM_WORKBENCH_REMOTE_DEPLOYMENT", "smsvpctest")
    monkeypatch.setenv("VIVARIUM_WORKBENCH_PERFETTO_UI", "off")
    monkeypatch.setattr(records, "fetch_server_id", lambda url, **_: None)
    monkeypatch.setattr(records, "refresh_server_id", lambda url, **_: None)
    sc._PROFILE_CACHE.clear()

    def net(*a, **k):
        raise _Net("network call in the trace table")

    if sac is not None:
        monkeypatch.setattr(sac, "urlopen", net)
    monkeypatch.setattr(backend_config, "urlopen", net)

    state: dict = {"mode": "ok", "calls": []}
    calls = state["calls"]

    def capabilities(self):
        calls.append(("capabilities", self.base_url))
        mode = state["mode"]
        if mode == "unreachable":
            raise sac.SmsApiError("tunnel down")
        if mode == "pre-capabilities":
            raise sac.SmsApiError("nope", status=404)
        caps = [] if mode == "capability-absent" else [sac.CAPABILITY_VIVA_V1_TRACE]
        return {"version": "0.9.999", "capabilities": caps}

    def _answer(name, arg, base_url):
        calls.append((name, base_url, arg))
        mode = state["mode"]
        if mode.startswith("upstream-"):
            code = mode.split("-", 1)[1]
            raise sac.SmsApiError("upstream says no", status=None if code == "none" else int(code))
        return {"empty": EMPTY, "not-json": b"not json"}.get(mode, TRACE)

    def simulation_trace(self, simulation_id):
        return _answer("simulation_trace", simulation_id, self.base_url)

    def composite_run_trace(self, run_id):
        return _answer("composite_run_trace", run_id, self.base_url)

    def compose_status(self, compose_id):
        calls.append(("compose_status", self.base_url, compose_id))
        return {"status": "completed", "correlation_id": None if state["mode"] == "no-correlation" else "corr-1"}

    if sac is not None:
        for name, fn in (("capabilities", capabilities), ("simulation_trace", simulation_trace),
                         ("composite_run_trace", composite_run_trace), ("compose_status", compose_status)):
            monkeypatch.setattr(sac.SmsApiClient, name, fn)

    def compose_trace(run_id):
        calls.append(("compose.trace", run_id))
        return COMPOSE_TRACE, 200, f"compose-{run_id}.json"

    monkeypatch.setattr(remote_run_compose, "trace", compose_trace)
    app.dependency_overrides[get_workspace] = lambda: ws
    client = TestClient(app)

    def ask(path, params, *, named=None, mode="ok"):
        monkeypatch.setattr(sc, "explicit_backend_profile", lambda: PROFILES[named])
        state["mode"] = mode
        del calls[:]
        r = client.get(path, params=params)
        ctype = r.headers.get("content-type", "")
        body = r.json() if ctype.startswith("application/json") and r.headers.get(
            "content-disposition") is None else {"bytes": r.content.decode("utf-8", "replace")}
        return {"status": r.status_code, "body": body, "order": list(body),
                "disposition": r.headers.get("content-disposition"),
                "events": r.headers.get("x-trace-events"),
                "calls": [list(c) for c in calls]}

    yield ask
    app.dependency_overrides.clear()
    sc._PROFILE_CACHE.clear()


def _fetch_cases() -> "list[tuple[str, dict, str | None, str]]":
    """``(case id, query, named profile, client mode)``."""
    out = []
    ids = ["12", " 12 ", "abc", "0", "-3", "simulation-N", "compose-7", "44", " 44 ", "45", "simulation-V",
           "compose-6"]
    for key in ("simulation_id", "composite_run_id", "compose_id"):
        for rid in ids:
            out.append((f"{key}={rid!r}", {key: rid}, None, "ok"))
    pairs = [
        {"simulation_id": "44", "composite_run_id": "simulation-V"},
        {"simulation_id": "44", "compose_id": "7"},
        {"simulation_id": "simulation-V", "compose_id": "7"},
        {"simulation_id": "simulation-V", "composite_run_id": "r"},
        {"simulation_id": "simulation-V", "composite_run_id": "compose-6"},
        {"simulation_id": "compose-6", "compose_id": "5"},
        {"simulation_id": "12", "composite_run_id": "r"},
        {"simulation_id": "12", "compose_id": "7"},
        {"simulation_id": "45", "composite_run_id": "compose-6"},
        {"simulation_id": "45", "composite_run_id": "r"},
        {"simulation_id": "abc", "composite_run_id": "45"},
        {"composite_run_id": "simulation-V", "compose_id": "7"},
        {"composite_run_id": "45", "compose_id": "7"},
        {"simulation_id": "", "composite_run_id": "r"},
        {"simulation_id": "12", "composite_run_id": "", "compose_id": ""},
        {"simulation_id": "simulation-V", "composite_run_id": "", "compose_id": ""},
        {"simulation_id": "44", "composite_run_id": ""},
        {"simulation_id": "45", "compose_id": ""},
        {"simulation_id": "", "composite_run_id": "simulation-V"},
        {"composite_run_id": " compose-6 "},
        {},
    ]
    for q in pairs:
        out.append(("pair " + "&".join(f"{k}={v!r}" for k, v in q.items()), q, None, "ok"))
    for q in ({"simulation_id": "44", "deployment": "prod-elsewhere"},
              {"simulation_id": "44", "deployment": "nowhere"},
              {"simulation_id": "45", "deployment": "prod-elsewhere"},
              {"simulation_id": "12", "deployment": "smsvpctest"},
              {"simulation_id": "simulation-V", "study": "s"},
              {"simulation_id": "44", "study": "missing"}):
        out.append(("where " + "&".join(f"{k}={v!r}" for k, v in q.items()), q, None, "ok"))
    modes = ("capability-absent", "unreachable", "pre-capabilities", "upstream-404", "upstream-409",
             "upstream-500", "upstream-none", "no-correlation", "empty", "not-json")
    for q in ({"simulation_id": "12"}, {"simulation_id": "44"}, {"simulation_id": "simulation-V"},
              {"composite_run_id": "r1"}, {"compose_id": "7"}):
        for mode in modes:
            out.append((f"mode {mode} " + "&".join(f"{k}={v!r}" for k, v in q.items()), q, None, mode))
    for named in ("compose", "viva", "legacy"):
        for q in ({"simulation_id": "12"}, {"simulation_id": "compose-7"}, {"simulation_id": "simulation-N"},
                  {"simulation_id": "simulation-V"}, {"composite_run_id": "r1"}, {"compose_id": "7"}):
            out.append((f"named {named} " + "&".join(f"{k}={v!r}" for k, v in q.items()), q, named, "ok"))
    return out


FETCH_CASES = _fetch_cases()
SUPPORT_CASES = [
    ("support nothing named", None, "ok"),
    ("support compose named", "compose", "ok"),
    ("support compose named, viva-api unreachable", "compose", "unreachable"),
    ("support viva-core named", "viva", "ok"),
    ("support viva-core named, capability absent", "viva", "capability-absent"),
    ("support legacy named", "legacy", "ok"),
    ("support unreachable", None, "unreachable"),
    ("support capability absent", None, "capability-absent"),
    ("support pre-capabilities", None, "pre-capabilities"),
]

#: The rows the core answers alone, the same with the plugin absent (step P3, K2c): compose-api ids, the ambiguous
#: pairs refused before any backend is asked, a deployment that is not this workbench's, and the compose-api half of
#: the support route. Every other row needs the SMS plugin and is marked ``plugin``.
CORE_CASES = frozenset({
    "simulation_id='compose-7'",
    "simulation_id='compose-6'",
    "composite_run_id='compose-7'",
    "composite_run_id='compose-6'",
    "pair simulation_id='compose-6'&compose_id='5'",
    "pair simulation_id='12'&composite_run_id='r'",
    "pair simulation_id='12'&compose_id='7'",
    "pair composite_run_id=' compose-6 '",
    "pair ",
    "where simulation_id='44'&deployment='prod-elsewhere'",
    "where simulation_id='44'&deployment='nowhere'",
    "named compose simulation_id='compose-7'",
    "named viva simulation_id='compose-7'",
    "named legacy simulation_id='compose-7'",
    "support compose named",
    "support compose named, viva-api unreachable",
})


def _params(cases):
    """Each case as a param with its id, marked ``plugin`` unless the core answers it alone."""
    return [pytest.param(*c, id=c[0], marks=() if c[0] in CORE_CASES else pytest.mark.plugin) for c in cases]


@pytest.mark.parametrize("case,query,named,mode", _params(FETCH_CASES))
def test_the_trace_route_answers_as_on_the_base(world, case, query, named, mode):
    assert world("/api/remote-run-trace", query, named=named, mode=mode) == GOLDEN[case]


@pytest.mark.parametrize("case,named,mode", _params(SUPPORT_CASES))
def test_the_trace_support_route_answers_as_on_the_base(world, case, named, mode):
    assert world("/api/remote-run-trace-support", {}, named=named, mode=mode) == GOLDEN[case]


def test_the_table_covers_every_case():
    assert set(GOLDEN) == {c[0] for c in FETCH_CASES} | {c[0] for c in SUPPORT_CASES}
    assert CORE_CASES <= set(GOLDEN)


GOLDEN: dict = {"simulation_id='12'": {'status': 200,
                        'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, "pid": '
                                          '1, "tid": 1}]}'},
                        'order': ['bytes'],
                        'disposition': 'inline; filename="simulation-12-trace.json"',
                        'events': '1',
                        'calls': [['capabilities', 'http://api:8000'], ['simulation_trace', 'http://api:8000', 12]]},
 "simulation_id=' 12 '": {'status': 200,
                          'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, '
                                            '"pid": 1, "tid": 1}]}'},
                          'order': ['bytes'],
                          'disposition': 'inline; filename="simulation-12-trace.json"',
                          'events': '1',
                          'calls': [['capabilities', 'http://api:8000'],
                                    ['simulation_trace', 'http://api:8000', 12]]},
 "simulation_id='abc'": {'status': 400,
                         'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                         'order': ['error'],
                         'disposition': None,
                         'events': None,
                         'calls': []},
 "simulation_id='0'": {'status': 400,
                       'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                       'order': ['error'],
                       'disposition': None,
                       'events': None,
                       'calls': []},
 "simulation_id='-3'": {'status': 400,
                        'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                        'order': ['error'],
                        'disposition': None,
                        'events': None,
                        'calls': []},
 "simulation_id='simulation-N'": {'status': 400,
                                  'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                                  'order': ['error'],
                                  'disposition': None,
                                  'events': None,
                                  'calls': []},
 "simulation_id='compose-7'": {'status': 200,
                               'body': {'bytes': '{"traceEvents": [{"ph": "M"}, {"ph": "X", "ts": 1}]}'},
                               'order': ['bytes'],
                               'disposition': 'inline; filename="compose-compose-7.json"',
                               'events': '1',
                               'calls': [['compose.trace', 'compose-7']]},
 "simulation_id='44'": {'status': 200,
                        'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, "pid": '
                                          '1, "tid": 1}]}'},
                        'order': ['bytes'],
                        'disposition': 'inline; filename="simulation-44-trace.json"',
                        'events': '1',
                        'calls': [['capabilities', 'http://api:8000'], ['simulation_trace', 'http://api:8000', 44]]},
 "simulation_id=' 44 '": {'status': 200,
                          'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, '
                                            '"pid": 1, "tid": 1}]}'},
                          'order': ['bytes'],
                          'disposition': 'inline; filename="simulation-44-trace.json"',
                          'events': '1',
                          'calls': [['capabilities', 'http://api:8000'],
                                    ['simulation_trace', 'http://api:8000', 44]]},
 "simulation_id='45'": {'status': 409,
                        'body': {'error': 'this run was dispatched to prod-elsewhere (http://other:8000); this '
                                          'workbench is configured for http://api:8000 (a different URL)',
                                 'reason': 'other-backend',
                                 'phase': 'other-backend',
                                 'terminal': True,
                                 'reachable': False,
                                 'simulation_id': '45',
                                 'run': '45',
                                 'dispatched_to': 'prod-elsewhere'},
                        'order': ['error',
                                  'reason',
                                  'phase',
                                  'terminal',
                                  'reachable',
                                  'simulation_id',
                                  'run',
                                  'dispatched_to'],
                        'disposition': None,
                        'events': None,
                        'calls': []},
 "simulation_id='simulation-V'": {'status': 200,
                                  'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": '
                                                    '1000, "pid": 1, "tid": 1}]}'},
                                  'order': ['bytes'],
                                  'disposition': 'inline; filename="composite-simulation-V-trace.json"',
                                  'events': '1',
                                  'calls': [['capabilities', 'http://api:8000'],
                                            ['composite_run_trace', 'http://api:8000', 'simulation-V']]},
 "simulation_id='compose-6'": {'status': 200,
                               'body': {'bytes': '{"traceEvents": [{"ph": "M"}, {"ph": "X", "ts": 1}]}'},
                               'order': ['bytes'],
                               'disposition': 'inline; filename="compose-compose-6.json"',
                               'events': '1',
                               'calls': [['compose.trace', 'compose-6']]},
 "composite_run_id='12'": {'status': 200,
                           'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, '
                                             '"pid": 1, "tid": 1}]}'},
                           'order': ['bytes'],
                           'disposition': 'inline; filename="composite-12-trace.json"',
                           'events': '1',
                           'calls': [['capabilities', 'http://api:8000'],
                                     ['composite_run_trace', 'http://api:8000', '12']]},
 "composite_run_id=' 12 '": {'status': 200,
                             'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, '
                                               '"pid": 1, "tid": 1}]}'},
                             'order': ['bytes'],
                             'disposition': 'inline; filename="composite-12-trace.json"',
                             'events': '1',
                             'calls': [['capabilities', 'http://api:8000'],
                                       ['composite_run_trace', 'http://api:8000', '12']]},
 "composite_run_id='abc'": {'status': 200,
                            'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, '
                                              '"pid": 1, "tid": 1}]}'},
                            'order': ['bytes'],
                            'disposition': 'inline; filename="composite-abc-trace.json"',
                            'events': '1',
                            'calls': [['capabilities', 'http://api:8000'],
                                      ['composite_run_trace', 'http://api:8000', 'abc']]},
 "composite_run_id='0'": {'status': 200,
                          'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, '
                                            '"pid": 1, "tid": 1}]}'},
                          'order': ['bytes'],
                          'disposition': 'inline; filename="composite-0-trace.json"',
                          'events': '1',
                          'calls': [['capabilities', 'http://api:8000'],
                                    ['composite_run_trace', 'http://api:8000', '0']]},
 "composite_run_id='-3'": {'status': 200,
                           'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, '
                                             '"pid": 1, "tid": 1}]}'},
                           'order': ['bytes'],
                           'disposition': 'inline; filename="composite--3-trace.json"',
                           'events': '1',
                           'calls': [['capabilities', 'http://api:8000'],
                                     ['composite_run_trace', 'http://api:8000', '-3']]},
 "composite_run_id='simulation-N'": {'status': 200,
                                     'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": '
                                                       '1000, "pid": 1, "tid": 1}]}'},
                                     'order': ['bytes'],
                                     'disposition': 'inline; filename="composite-simulation-N-trace.json"',
                                     'events': '1',
                                     'calls': [['capabilities', 'http://api:8000'],
                                               ['composite_run_trace', 'http://api:8000', 'simulation-N']]},
 "composite_run_id='compose-7'": {'status': 200,
                                  'body': {'bytes': '{"traceEvents": [{"ph": "M"}, {"ph": "X", "ts": 1}]}'},
                                  'order': ['bytes'],
                                  'disposition': 'inline; filename="compose-compose-7.json"',
                                  'events': '1',
                                  'calls': [['compose.trace', 'compose-7']]},
 "composite_run_id='44'": {'status': 200,
                           'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, '
                                             '"pid": 1, "tid": 1}]}'},
                           'order': ['bytes'],
                           'disposition': 'inline; filename="composite-44-trace.json"',
                           'events': '1',
                           'calls': [['capabilities', 'http://api:8000'],
                                     ['composite_run_trace', 'http://api:8000', '44']]},
 "composite_run_id=' 44 '": {'status': 200,
                             'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, '
                                               '"pid": 1, "tid": 1}]}'},
                             'order': ['bytes'],
                             'disposition': 'inline; filename="composite-44-trace.json"',
                             'events': '1',
                             'calls': [['capabilities', 'http://api:8000'],
                                       ['composite_run_trace', 'http://api:8000', '44']]},
 "composite_run_id='45'": {'status': 409,
                           'body': {'error': 'this run was dispatched to prod-elsewhere (http://other:8000); this '
                                             'workbench is configured for http://api:8000 (a different URL)',
                                    'reason': 'other-backend',
                                    'phase': 'other-backend',
                                    'terminal': True,
                                    'reachable': False,
                                    'simulation_id': '45',
                                    'run': '45',
                                    'dispatched_to': 'prod-elsewhere'},
                           'order': ['error',
                                     'reason',
                                     'phase',
                                     'terminal',
                                     'reachable',
                                     'simulation_id',
                                     'run',
                                     'dispatched_to'],
                           'disposition': None,
                           'events': None,
                           'calls': []},
 "composite_run_id='simulation-V'": {'status': 200,
                                     'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": '
                                                       '1000, "pid": 1, "tid": 1}]}'},
                                     'order': ['bytes'],
                                     'disposition': 'inline; filename="composite-simulation-V-trace.json"',
                                     'events': '1',
                                     'calls': [['capabilities', 'http://api:8000'],
                                               ['composite_run_trace', 'http://api:8000', 'simulation-V']]},
 "composite_run_id='compose-6'": {'status': 200,
                                  'body': {'bytes': '{"traceEvents": [{"ph": "M"}, {"ph": "X", "ts": 1}]}'},
                                  'order': ['bytes'],
                                  'disposition': 'inline; filename="compose-compose-6.json"',
                                  'events': '1',
                                  'calls': [['compose.trace', 'compose-6']]},
 "compose_id='12'": {'status': 200,
                     'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, "pid": 1, '
                                       '"tid": 1}]}'},
                     'order': ['bytes'],
                     'disposition': 'inline; filename="composite-corr-1-trace.json"',
                     'events': '1',
                     'calls': [['capabilities', 'http://api:8000'],
                               ['compose_status', 'http://api:8000', 12],
                               ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "compose_id=' 12 '": {'status': 200,
                       'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, "pid": '
                                         '1, "tid": 1}]}'},
                       'order': ['bytes'],
                       'disposition': 'inline; filename="composite-corr-1-trace.json"',
                       'events': '1',
                       'calls': [['capabilities', 'http://api:8000'],
                                 ['compose_status', 'http://api:8000', 12],
                                 ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "compose_id='abc'": {'status': 400,
                      'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                      'order': ['error'],
                      'disposition': None,
                      'events': None,
                      'calls': []},
 "compose_id='0'": {'status': 400,
                    'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                    'order': ['error'],
                    'disposition': None,
                    'events': None,
                    'calls': []},
 "compose_id='-3'": {'status': 400,
                     'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                     'order': ['error'],
                     'disposition': None,
                     'events': None,
                     'calls': []},
 "compose_id='simulation-N'": {'status': 400,
                               'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                               'order': ['error'],
                               'disposition': None,
                               'events': None,
                               'calls': []},
 "compose_id='compose-7'": {'status': 400,
                            'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                            'order': ['error'],
                            'disposition': None,
                            'events': None,
                            'calls': []},
 "compose_id='44'": {'status': 200,
                     'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, "pid": 1, '
                                       '"tid": 1}]}'},
                     'order': ['bytes'],
                     'disposition': 'inline; filename="composite-corr-1-trace.json"',
                     'events': '1',
                     'calls': [['capabilities', 'http://api:8000'],
                               ['compose_status', 'http://api:8000', 44],
                               ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "compose_id=' 44 '": {'status': 200,
                       'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, "pid": '
                                         '1, "tid": 1}]}'},
                       'order': ['bytes'],
                       'disposition': 'inline; filename="composite-corr-1-trace.json"',
                       'events': '1',
                       'calls': [['capabilities', 'http://api:8000'],
                                 ['compose_status', 'http://api:8000', 44],
                                 ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "compose_id='45'": {'status': 200,
                     'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, "pid": 1, '
                                       '"tid": 1}]}'},
                     'order': ['bytes'],
                     'disposition': 'inline; filename="composite-corr-1-trace.json"',
                     'events': '1',
                     'calls': [['capabilities', 'http://api:8000'],
                               ['compose_status', 'http://api:8000', 45],
                               ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "compose_id='simulation-V'": {'status': 400,
                               'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                               'order': ['error'],
                               'disposition': None,
                               'events': None,
                               'calls': []},
 "compose_id='compose-6'": {'status': 400,
                            'body': {'error': 'simulation_id / compose_id must be a positive integer'},
                            'order': ['error'],
                            'disposition': None,
                            'events': None,
                            'calls': []},
 "pair simulation_id='44'&composite_run_id='simulation-V'": {'status': 400,
                                                             'body': {'error': 'give exactly one of simulation_id, '
                                                                               'composite_run_id, compose_id'},
                                                             'order': ['error'],
                                                             'disposition': None,
                                                             'events': None,
                                                             'calls': []},
 "pair simulation_id='44'&compose_id='7'": {'status': 400,
                                            'body': {'error': 'give exactly one of simulation_id, composite_run_id, '
                                                              'compose_id'},
                                            'order': ['error'],
                                            'disposition': None,
                                            'events': None,
                                            'calls': []},
 "pair simulation_id='simulation-V'&compose_id='7'": {'status': 400,
                                                      'body': {'error': 'give exactly one of simulation_id, '
                                                                        'composite_run_id, compose_id'},
                                                      'order': ['error'],
                                                      'disposition': None,
                                                      'events': None,
                                                      'calls': []},
 "pair simulation_id='simulation-V'&composite_run_id='r'": {'status': 400,
                                                            'body': {'error': 'give exactly one of simulation_id, '
                                                                              'composite_run_id, compose_id'},
                                                            'order': ['error'],
                                                            'disposition': None,
                                                            'events': None,
                                                            'calls': []},
 "pair simulation_id='simulation-V'&composite_run_id='compose-6'": {'status': 200,
                                                                    'body': {'bytes': '{"traceEvents": [{"ph": "M"}, '
                                                                                      '{"ph": "X", "ts": 1}]}'},
                                                                    'order': ['bytes'],
                                                                    'disposition': 'inline; '
                                                                                   'filename="compose-compose-6.json"',
                                                                    'events': '1',
                                                                    'calls': [['compose.trace', 'compose-6']]},
 "pair simulation_id='compose-6'&compose_id='5'": {'status': 200,
                                                   'body': {'bytes': '{"traceEvents": [{"ph": "M"}, {"ph": "X", '
                                                                     '"ts": 1}]}'},
                                                   'order': ['bytes'],
                                                   'disposition': 'inline; filename="compose-compose-6.json"',
                                                   'events': '1',
                                                   'calls': [['compose.trace', 'compose-6']]},
 "pair simulation_id='12'&composite_run_id='r'": {'status': 400,
                                                  'body': {'error': 'give exactly one of simulation_id, '
                                                                    'composite_run_id, compose_id'},
                                                  'order': ['error'],
                                                  'disposition': None,
                                                  'events': None,
                                                  'calls': []},
 "pair simulation_id='12'&compose_id='7'": {'status': 400,
                                            'body': {'error': 'give exactly one of simulation_id, composite_run_id, '
                                                              'compose_id'},
                                            'order': ['error'],
                                            'disposition': None,
                                            'events': None,
                                            'calls': []},
 "pair simulation_id='45'&composite_run_id='compose-6'": {'status': 409,
                                                          'body': {'error': 'this run was dispatched to '
                                                                            'prod-elsewhere (http://other:8000); '
                                                                            'this workbench is configured for '
                                                                            'http://api:8000 (a different URL)',
                                                                   'reason': 'other-backend',
                                                                   'phase': 'other-backend',
                                                                   'terminal': True,
                                                                   'reachable': False,
                                                                   'simulation_id': '45',
                                                                   'run': '45',
                                                                   'dispatched_to': 'prod-elsewhere'},
                                                          'order': ['error',
                                                                    'reason',
                                                                    'phase',
                                                                    'terminal',
                                                                    'reachable',
                                                                    'simulation_id',
                                                                    'run',
                                                                    'dispatched_to'],
                                                          'disposition': None,
                                                          'events': None,
                                                          'calls': []},
 "pair simulation_id='45'&composite_run_id='r'": {'status': 409,
                                                  'body': {'error': 'this run was dispatched to prod-elsewhere '
                                                                    '(http://other:8000); this workbench is '
                                                                    'configured for http://api:8000 (a different '
                                                                    'URL)',
                                                           'reason': 'other-backend',
                                                           'phase': 'other-backend',
                                                           'terminal': True,
                                                           'reachable': False,
                                                           'simulation_id': '45',
                                                           'run': '45',
                                                           'dispatched_to': 'prod-elsewhere'},
                                                  'order': ['error',
                                                            'reason',
                                                            'phase',
                                                            'terminal',
                                                            'reachable',
                                                            'simulation_id',
                                                            'run',
                                                            'dispatched_to'],
                                                  'disposition': None,
                                                  'events': None,
                                                  'calls': []},
 "pair simulation_id='abc'&composite_run_id='45'": {'status': 409,
                                                    'body': {'error': 'this run was dispatched to prod-elsewhere '
                                                                      '(http://other:8000); this workbench is '
                                                                      'configured for http://api:8000 (a different '
                                                                      'URL)',
                                                             'reason': 'other-backend',
                                                             'phase': 'other-backend',
                                                             'terminal': True,
                                                             'reachable': False,
                                                             'simulation_id': '45',
                                                             'run': '45',
                                                             'dispatched_to': 'prod-elsewhere'},
                                                    'order': ['error',
                                                              'reason',
                                                              'phase',
                                                              'terminal',
                                                              'reachable',
                                                              'simulation_id',
                                                              'run',
                                                              'dispatched_to'],
                                                    'disposition': None,
                                                    'events': None,
                                                    'calls': []},
 "pair composite_run_id='simulation-V'&compose_id='7'": {'status': 400,
                                                         'body': {'error': 'give exactly one of simulation_id, '
                                                                           'composite_run_id, compose_id'},
                                                         'order': ['error'],
                                                         'disposition': None,
                                                         'events': None,
                                                         'calls': []},
 "pair composite_run_id='45'&compose_id='7'": {'status': 409,
                                               'body': {'error': 'this run was dispatched to prod-elsewhere '
                                                                 '(http://other:8000); this workbench is configured '
                                                                 'for http://api:8000 (a different URL)',
                                                        'reason': 'other-backend',
                                                        'phase': 'other-backend',
                                                        'terminal': True,
                                                        'reachable': False,
                                                        'simulation_id': '45',
                                                        'run': '45',
                                                        'dispatched_to': 'prod-elsewhere'},
                                               'order': ['error',
                                                         'reason',
                                                         'phase',
                                                         'terminal',
                                                         'reachable',
                                                         'simulation_id',
                                                         'run',
                                                         'dispatched_to'],
                                               'disposition': None,
                                               'events': None,
                                               'calls': []},
 "pair simulation_id=''&composite_run_id='r'": {'status': 200,
                                                'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": '
                                                                  '0, "dur": 1000, "pid": 1, "tid": 1}]}'},
                                                'order': ['bytes'],
                                                'disposition': 'inline; filename="composite-r-trace.json"',
                                                'events': '1',
                                                'calls': [['capabilities', 'http://api:8000'],
                                                          ['composite_run_trace', 'http://api:8000', 'r']]},
 "pair simulation_id='12'&composite_run_id=''&compose_id=''": {'status': 200,
                                                               'body': {'bytes': '{"traceEvents": [{"name": "run", '
                                                                                 '"ph": "X", "ts": 0, "dur": 1000, '
                                                                                 '"pid": 1, "tid": 1}]}'},
                                                               'order': ['bytes'],
                                                               'disposition': 'inline; '
                                                                              'filename="simulation-12-trace.json"',
                                                               'events': '1',
                                                               'calls': [['capabilities', 'http://api:8000'],
                                                                         ['simulation_trace',
                                                                          'http://api:8000',
                                                                          12]]},
 "pair simulation_id='simulation-V'&composite_run_id=''&compose_id=''": {'status': 200,
                                                                         'body': {'bytes': '{"traceEvents": '
                                                                                           '[{"name": "run", "ph": '
                                                                                           '"X", "ts": 0, "dur": '
                                                                                           '1000, "pid": 1, "tid": '
                                                                                           '1}]}'},
                                                                         'order': ['bytes'],
                                                                         'disposition': 'inline; '
                                                                                        'filename="composite-simulation-V-trace.json"',
                                                                         'events': '1',
                                                                         'calls': [['capabilities',
                                                                                    'http://api:8000'],
                                                                                   ['composite_run_trace',
                                                                                    'http://api:8000',
                                                                                    'simulation-V']]},
 "pair simulation_id='44'&composite_run_id=''": {'status': 200,
                                                 'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", '
                                                                   '"ts": 0, "dur": 1000, "pid": 1, "tid": 1}]}'},
                                                 'order': ['bytes'],
                                                 'disposition': 'inline; filename="simulation-44-trace.json"',
                                                 'events': '1',
                                                 'calls': [['capabilities', 'http://api:8000'],
                                                           ['simulation_trace', 'http://api:8000', 44]]},
 "pair simulation_id='45'&compose_id=''": {'status': 409,
                                           'body': {'error': 'this run was dispatched to prod-elsewhere '
                                                             '(http://other:8000); this workbench is configured for '
                                                             'http://api:8000 (a different URL)',
                                                    'reason': 'other-backend',
                                                    'phase': 'other-backend',
                                                    'terminal': True,
                                                    'reachable': False,
                                                    'simulation_id': '45',
                                                    'run': '45',
                                                    'dispatched_to': 'prod-elsewhere'},
                                           'order': ['error',
                                                     'reason',
                                                     'phase',
                                                     'terminal',
                                                     'reachable',
                                                     'simulation_id',
                                                     'run',
                                                     'dispatched_to'],
                                           'disposition': None,
                                           'events': None,
                                           'calls': []},
 "pair simulation_id=''&composite_run_id='simulation-V'": {'status': 200,
                                                           'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": '
                                                                             '"X", "ts": 0, "dur": 1000, "pid": 1, '
                                                                             '"tid": 1}]}'},
                                                           'order': ['bytes'],
                                                           'disposition': 'inline; '
                                                                          'filename="composite-simulation-V-trace.json"',
                                                           'events': '1',
                                                           'calls': [['capabilities', 'http://api:8000'],
                                                                     ['composite_run_trace',
                                                                      'http://api:8000',
                                                                      'simulation-V']]},
 "pair composite_run_id=' compose-6 '": {'status': 200,
                                         'body': {'bytes': '{"traceEvents": [{"ph": "M"}, {"ph": "X", "ts": 1}]}'},
                                         'order': ['bytes'],
                                         'disposition': 'inline; filename="compose-compose-6.json"',
                                         'events': '1',
                                         'calls': [['compose.trace', 'compose-6']]},
 'pair ': {'status': 400,
           'body': {'error': 'give exactly one of simulation_id, composite_run_id, compose_id'},
           'order': ['error'],
           'disposition': None,
           'events': None,
           'calls': []},
 "where simulation_id='44'&deployment='prod-elsewhere'": {'status': 409,
                                                          'body': {'error': 'this run was dispatched to '
                                                                            'prod-elsewhere; this workbench is '
                                                                            'configured for smsvpctest',
                                                                   'reason': 'other-backend',
                                                                   'phase': 'other-backend',
                                                                   'terminal': True,
                                                                   'reachable': False,
                                                                   'simulation_id': '44',
                                                                   'run': '44',
                                                                   'dispatched_to': 'prod-elsewhere'},
                                                          'order': ['error',
                                                                    'reason',
                                                                    'phase',
                                                                    'terminal',
                                                                    'reachable',
                                                                    'simulation_id',
                                                                    'run',
                                                                    'dispatched_to'],
                                                          'disposition': None,
                                                          'events': None,
                                                          'calls': []},
 "where simulation_id='44'&deployment='nowhere'": {'status': 409,
                                                   'body': {'error': 'this run was dispatched to nowhere; this '
                                                                     'workbench is configured for smsvpctest',
                                                            'reason': 'other-backend',
                                                            'phase': 'other-backend',
                                                            'terminal': True,
                                                            'reachable': False,
                                                            'simulation_id': '44',
                                                            'run': '44',
                                                            'dispatched_to': 'nowhere'},
                                                   'order': ['error',
                                                             'reason',
                                                             'phase',
                                                             'terminal',
                                                             'reachable',
                                                             'simulation_id',
                                                             'run',
                                                             'dispatched_to'],
                                                   'disposition': None,
                                                   'events': None,
                                                   'calls': []},
 "where simulation_id='45'&deployment='prod-elsewhere'": {'status': 409,
                                                          'body': {'error': 'this run was dispatched to '
                                                                            'prod-elsewhere (http://other:8000); '
                                                                            'this workbench is configured for '
                                                                            'http://api:8000 (a different URL)',
                                                                   'reason': 'other-backend',
                                                                   'phase': 'other-backend',
                                                                   'terminal': True,
                                                                   'reachable': False,
                                                                   'simulation_id': '45',
                                                                   'run': '45',
                                                                   'dispatched_to': 'prod-elsewhere'},
                                                          'order': ['error',
                                                                    'reason',
                                                                    'phase',
                                                                    'terminal',
                                                                    'reachable',
                                                                    'simulation_id',
                                                                    'run',
                                                                    'dispatched_to'],
                                                          'disposition': None,
                                                          'events': None,
                                                          'calls': []},
 "where simulation_id='12'&deployment='smsvpctest'": {'status': 200,
                                                      'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", '
                                                                        '"ts": 0, "dur": 1000, "pid": 1, "tid": '
                                                                        '1}]}'},
                                                      'order': ['bytes'],
                                                      'disposition': 'inline; filename="simulation-12-trace.json"',
                                                      'events': '1',
                                                      'calls': [['capabilities', 'http://api:8000'],
                                                                ['simulation_trace', 'http://api:8000', 12]]},
 "where simulation_id='simulation-V'&study='s'": {'status': 200,
                                                  'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", '
                                                                    '"ts": 0, "dur": 1000, "pid": 1, "tid": 1}]}'},
                                                  'order': ['bytes'],
                                                  'disposition': 'inline; '
                                                                 'filename="composite-simulation-V-trace.json"',
                                                  'events': '1',
                                                  'calls': [['capabilities', 'http://api:8000'],
                                                            ['composite_run_trace',
                                                             'http://api:8000',
                                                             'simulation-V']]},
 "where simulation_id='44'&study='missing'": {'status': 200,
                                              'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": '
                                                                '0, "dur": 1000, "pid": 1, "tid": 1}]}'},
                                              'order': ['bytes'],
                                              'disposition': 'inline; filename="simulation-44-trace.json"',
                                              'events': '1',
                                              'calls': [['capabilities', 'http://api:8000'],
                                                        ['simulation_trace', 'http://api:8000', 44]]},
 "mode capability-absent simulation_id='12'": {'status': 409,
                                               'body': {'error': 'this viva-api deployment does not support: '
                                                                 'viva-v1-trace (server version: 0.9.999) — '
                                                                 'upgrade/redeploy viva-api, or use a deployment '
                                                                 'that advertises this capability',
                                                        'missing': ['viva-v1-trace'],
                                                        'server_version': '0.9.999'},
                                               'order': ['error', 'missing', 'server_version'],
                                               'disposition': None,
                                               'events': None,
                                               'calls': [['capabilities', 'http://api:8000']]},
 "mode unreachable simulation_id='12'": {'status': 502,
                                         'body': {'error': 'viva-api unreachable: tunnel down'},
                                         'order': ['error'],
                                         'disposition': None,
                                         'events': None,
                                         'calls': [['capabilities', 'http://api:8000']]},
 "mode pre-capabilities simulation_id='12'": {'status': 409,
                                              'body': {'error': 'this viva-api deployment does not support: '
                                                                'viva-v1-trace (server version: pre-capabilities '
                                                                '(endpoint absent)) — upgrade/redeploy viva-api, or '
                                                                'use a deployment that advertises this capability',
                                                       'missing': ['viva-v1-trace'],
                                                       'server_version': 'pre-capabilities (endpoint absent)'},
                                              'order': ['error', 'missing', 'server_version'],
                                              'disposition': None,
                                              'events': None,
                                              'calls': [['capabilities', 'http://api:8000']]},
 "mode upstream-404 simulation_id='12'": {'status': 404,
                                          'body': {'error': 'upstream says no'},
                                          'order': ['error'],
                                          'disposition': None,
                                          'events': None,
                                          'calls': [['capabilities', 'http://api:8000'],
                                                    ['simulation_trace', 'http://api:8000', 12]]},
 "mode upstream-409 simulation_id='12'": {'status': 409,
                                          'body': {'error': 'upstream says no'},
                                          'order': ['error'],
                                          'disposition': None,
                                          'events': None,
                                          'calls': [['capabilities', 'http://api:8000'],
                                                    ['simulation_trace', 'http://api:8000', 12]]},
 "mode upstream-500 simulation_id='12'": {'status': 502,
                                          'body': {'error': 'upstream says no'},
                                          'order': ['error'],
                                          'disposition': None,
                                          'events': None,
                                          'calls': [['capabilities', 'http://api:8000'],
                                                    ['simulation_trace', 'http://api:8000', 12]]},
 "mode upstream-none simulation_id='12'": {'status': 502,
                                           'body': {'error': 'upstream says no'},
                                           'order': ['error'],
                                           'disposition': None,
                                           'events': None,
                                           'calls': [['capabilities', 'http://api:8000'],
                                                     ['simulation_trace', 'http://api:8000', 12]]},
 "mode no-correlation simulation_id='12'": {'status': 200,
                                            'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, '
                                                              '"dur": 1000, "pid": 1, "tid": 1}]}'},
                                            'order': ['bytes'],
                                            'disposition': 'inline; filename="simulation-12-trace.json"',
                                            'events': '1',
                                            'calls': [['capabilities', 'http://api:8000'],
                                                      ['simulation_trace', 'http://api:8000', 12]]},
 "mode empty simulation_id='12'": {'status': 200,
                                   'body': {'bytes': '{"traceEvents": [], "displayTimeUnit": "ms"}'},
                                   'order': ['bytes'],
                                   'disposition': 'inline; filename="simulation-12-trace.json"',
                                   'events': '0',
                                   'calls': [['capabilities', 'http://api:8000'],
                                             ['simulation_trace', 'http://api:8000', 12]]},
 "mode not-json simulation_id='12'": {'status': 200,
                                      'body': {'bytes': 'not json'},
                                      'order': ['bytes'],
                                      'disposition': 'inline; filename="simulation-12-trace.json"',
                                      'events': None,
                                      'calls': [['capabilities', 'http://api:8000'],
                                                ['simulation_trace', 'http://api:8000', 12]]},
 "mode capability-absent simulation_id='44'": {'status': 409,
                                               'body': {'error': 'this viva-api deployment does not support: '
                                                                 'viva-v1-trace (server version: 0.9.999) — '
                                                                 'upgrade/redeploy viva-api, or use a deployment '
                                                                 'that advertises this capability',
                                                        'missing': ['viva-v1-trace'],
                                                        'server_version': '0.9.999'},
                                               'order': ['error', 'missing', 'server_version'],
                                               'disposition': None,
                                               'events': None,
                                               'calls': [['capabilities', 'http://api:8000']]},
 "mode unreachable simulation_id='44'": {'status': 502,
                                         'body': {'error': 'viva-api unreachable: tunnel down'},
                                         'order': ['error'],
                                         'disposition': None,
                                         'events': None,
                                         'calls': [['capabilities', 'http://api:8000']]},
 "mode pre-capabilities simulation_id='44'": {'status': 409,
                                              'body': {'error': 'this viva-api deployment does not support: '
                                                                'viva-v1-trace (server version: pre-capabilities '
                                                                '(endpoint absent)) — upgrade/redeploy viva-api, or '
                                                                'use a deployment that advertises this capability',
                                                       'missing': ['viva-v1-trace'],
                                                       'server_version': 'pre-capabilities (endpoint absent)'},
                                              'order': ['error', 'missing', 'server_version'],
                                              'disposition': None,
                                              'events': None,
                                              'calls': [['capabilities', 'http://api:8000']]},
 "mode upstream-404 simulation_id='44'": {'status': 404,
                                          'body': {'error': 'upstream says no'},
                                          'order': ['error'],
                                          'disposition': None,
                                          'events': None,
                                          'calls': [['capabilities', 'http://api:8000'],
                                                    ['simulation_trace', 'http://api:8000', 44]]},
 "mode upstream-409 simulation_id='44'": {'status': 409,
                                          'body': {'error': 'upstream says no'},
                                          'order': ['error'],
                                          'disposition': None,
                                          'events': None,
                                          'calls': [['capabilities', 'http://api:8000'],
                                                    ['simulation_trace', 'http://api:8000', 44]]},
 "mode upstream-500 simulation_id='44'": {'status': 502,
                                          'body': {'error': 'upstream says no'},
                                          'order': ['error'],
                                          'disposition': None,
                                          'events': None,
                                          'calls': [['capabilities', 'http://api:8000'],
                                                    ['simulation_trace', 'http://api:8000', 44]]},
 "mode upstream-none simulation_id='44'": {'status': 502,
                                           'body': {'error': 'upstream says no'},
                                           'order': ['error'],
                                           'disposition': None,
                                           'events': None,
                                           'calls': [['capabilities', 'http://api:8000'],
                                                     ['simulation_trace', 'http://api:8000', 44]]},
 "mode no-correlation simulation_id='44'": {'status': 200,
                                            'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, '
                                                              '"dur": 1000, "pid": 1, "tid": 1}]}'},
                                            'order': ['bytes'],
                                            'disposition': 'inline; filename="simulation-44-trace.json"',
                                            'events': '1',
                                            'calls': [['capabilities', 'http://api:8000'],
                                                      ['simulation_trace', 'http://api:8000', 44]]},
 "mode empty simulation_id='44'": {'status': 200,
                                   'body': {'bytes': '{"traceEvents": [], "displayTimeUnit": "ms"}'},
                                   'order': ['bytes'],
                                   'disposition': 'inline; filename="simulation-44-trace.json"',
                                   'events': '0',
                                   'calls': [['capabilities', 'http://api:8000'],
                                             ['simulation_trace', 'http://api:8000', 44]]},
 "mode not-json simulation_id='44'": {'status': 200,
                                      'body': {'bytes': 'not json'},
                                      'order': ['bytes'],
                                      'disposition': 'inline; filename="simulation-44-trace.json"',
                                      'events': None,
                                      'calls': [['capabilities', 'http://api:8000'],
                                                ['simulation_trace', 'http://api:8000', 44]]},
 "mode capability-absent simulation_id='simulation-V'": {'status': 409,
                                                         'body': {'error': 'this viva-api deployment does not '
                                                                           'support: viva-v1-trace (server version: '
                                                                           '0.9.999) — upgrade/redeploy viva-api, or '
                                                                           'use a deployment that advertises this '
                                                                           'capability',
                                                                  'missing': ['viva-v1-trace'],
                                                                  'server_version': '0.9.999'},
                                                         'order': ['error', 'missing', 'server_version'],
                                                         'disposition': None,
                                                         'events': None,
                                                         'calls': [['capabilities', 'http://api:8000']]},
 "mode unreachable simulation_id='simulation-V'": {'status': 502,
                                                   'body': {'error': 'viva-api unreachable: tunnel down'},
                                                   'order': ['error'],
                                                   'disposition': None,
                                                   'events': None,
                                                   'calls': [['capabilities', 'http://api:8000']]},
 "mode pre-capabilities simulation_id='simulation-V'": {'status': 409,
                                                        'body': {'error': 'this viva-api deployment does not '
                                                                          'support: viva-v1-trace (server version: '
                                                                          'pre-capabilities (endpoint absent)) — '
                                                                          'upgrade/redeploy viva-api, or use a '
                                                                          'deployment that advertises this '
                                                                          'capability',
                                                                 'missing': ['viva-v1-trace'],
                                                                 'server_version': 'pre-capabilities (endpoint '
                                                                                   'absent)'},
                                                        'order': ['error', 'missing', 'server_version'],
                                                        'disposition': None,
                                                        'events': None,
                                                        'calls': [['capabilities', 'http://api:8000']]},
 "mode upstream-404 simulation_id='simulation-V'": {'status': 404,
                                                    'body': {'error': 'upstream says no'},
                                                    'order': ['error'],
                                                    'disposition': None,
                                                    'events': None,
                                                    'calls': [['capabilities', 'http://api:8000'],
                                                              ['composite_run_trace',
                                                               'http://api:8000',
                                                               'simulation-V']]},
 "mode upstream-409 simulation_id='simulation-V'": {'status': 409,
                                                    'body': {'error': 'upstream says no'},
                                                    'order': ['error'],
                                                    'disposition': None,
                                                    'events': None,
                                                    'calls': [['capabilities', 'http://api:8000'],
                                                              ['composite_run_trace',
                                                               'http://api:8000',
                                                               'simulation-V']]},
 "mode upstream-500 simulation_id='simulation-V'": {'status': 502,
                                                    'body': {'error': 'upstream says no'},
                                                    'order': ['error'],
                                                    'disposition': None,
                                                    'events': None,
                                                    'calls': [['capabilities', 'http://api:8000'],
                                                              ['composite_run_trace',
                                                               'http://api:8000',
                                                               'simulation-V']]},
 "mode upstream-none simulation_id='simulation-V'": {'status': 502,
                                                     'body': {'error': 'upstream says no'},
                                                     'order': ['error'],
                                                     'disposition': None,
                                                     'events': None,
                                                     'calls': [['capabilities', 'http://api:8000'],
                                                               ['composite_run_trace',
                                                                'http://api:8000',
                                                                'simulation-V']]},
 "mode no-correlation simulation_id='simulation-V'": {'status': 200,
                                                      'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", '
                                                                        '"ts": 0, "dur": 1000, "pid": 1, "tid": '
                                                                        '1}]}'},
                                                      'order': ['bytes'],
                                                      'disposition': 'inline; '
                                                                     'filename="composite-simulation-V-trace.json"',
                                                      'events': '1',
                                                      'calls': [['capabilities', 'http://api:8000'],
                                                                ['composite_run_trace',
                                                                 'http://api:8000',
                                                                 'simulation-V']]},
 "mode empty simulation_id='simulation-V'": {'status': 200,
                                             'body': {'bytes': '{"traceEvents": [], "displayTimeUnit": "ms"}'},
                                             'order': ['bytes'],
                                             'disposition': 'inline; filename="composite-simulation-V-trace.json"',
                                             'events': '0',
                                             'calls': [['capabilities', 'http://api:8000'],
                                                       ['composite_run_trace', 'http://api:8000', 'simulation-V']]},
 "mode not-json simulation_id='simulation-V'": {'status': 200,
                                                'body': {'bytes': 'not json'},
                                                'order': ['bytes'],
                                                'disposition': 'inline; filename="composite-simulation-V-trace.json"',
                                                'events': None,
                                                'calls': [['capabilities', 'http://api:8000'],
                                                          ['composite_run_trace',
                                                           'http://api:8000',
                                                           'simulation-V']]},
 "mode capability-absent composite_run_id='r1'": {'status': 409,
                                                  'body': {'error': 'this viva-api deployment does not support: '
                                                                    'viva-v1-trace (server version: 0.9.999) — '
                                                                    'upgrade/redeploy viva-api, or use a deployment '
                                                                    'that advertises this capability',
                                                           'missing': ['viva-v1-trace'],
                                                           'server_version': '0.9.999'},
                                                  'order': ['error', 'missing', 'server_version'],
                                                  'disposition': None,
                                                  'events': None,
                                                  'calls': [['capabilities', 'http://api:8000']]},
 "mode unreachable composite_run_id='r1'": {'status': 502,
                                            'body': {'error': 'viva-api unreachable: tunnel down'},
                                            'order': ['error'],
                                            'disposition': None,
                                            'events': None,
                                            'calls': [['capabilities', 'http://api:8000']]},
 "mode pre-capabilities composite_run_id='r1'": {'status': 409,
                                                 'body': {'error': 'this viva-api deployment does not support: '
                                                                   'viva-v1-trace (server version: pre-capabilities '
                                                                   '(endpoint absent)) — upgrade/redeploy viva-api, '
                                                                   'or use a deployment that advertises this '
                                                                   'capability',
                                                          'missing': ['viva-v1-trace'],
                                                          'server_version': 'pre-capabilities (endpoint absent)'},
                                                 'order': ['error', 'missing', 'server_version'],
                                                 'disposition': None,
                                                 'events': None,
                                                 'calls': [['capabilities', 'http://api:8000']]},
 "mode upstream-404 composite_run_id='r1'": {'status': 404,
                                             'body': {'error': 'upstream says no'},
                                             'order': ['error'],
                                             'disposition': None,
                                             'events': None,
                                             'calls': [['capabilities', 'http://api:8000'],
                                                       ['composite_run_trace', 'http://api:8000', 'r1']]},
 "mode upstream-409 composite_run_id='r1'": {'status': 409,
                                             'body': {'error': 'upstream says no'},
                                             'order': ['error'],
                                             'disposition': None,
                                             'events': None,
                                             'calls': [['capabilities', 'http://api:8000'],
                                                       ['composite_run_trace', 'http://api:8000', 'r1']]},
 "mode upstream-500 composite_run_id='r1'": {'status': 502,
                                             'body': {'error': 'upstream says no'},
                                             'order': ['error'],
                                             'disposition': None,
                                             'events': None,
                                             'calls': [['capabilities', 'http://api:8000'],
                                                       ['composite_run_trace', 'http://api:8000', 'r1']]},
 "mode upstream-none composite_run_id='r1'": {'status': 502,
                                              'body': {'error': 'upstream says no'},
                                              'order': ['error'],
                                              'disposition': None,
                                              'events': None,
                                              'calls': [['capabilities', 'http://api:8000'],
                                                        ['composite_run_trace', 'http://api:8000', 'r1']]},
 "mode no-correlation composite_run_id='r1'": {'status': 200,
                                               'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": '
                                                                 '0, "dur": 1000, "pid": 1, "tid": 1}]}'},
                                               'order': ['bytes'],
                                               'disposition': 'inline; filename="composite-r1-trace.json"',
                                               'events': '1',
                                               'calls': [['capabilities', 'http://api:8000'],
                                                         ['composite_run_trace', 'http://api:8000', 'r1']]},
 "mode empty composite_run_id='r1'": {'status': 200,
                                      'body': {'bytes': '{"traceEvents": [], "displayTimeUnit": "ms"}'},
                                      'order': ['bytes'],
                                      'disposition': 'inline; filename="composite-r1-trace.json"',
                                      'events': '0',
                                      'calls': [['capabilities', 'http://api:8000'],
                                                ['composite_run_trace', 'http://api:8000', 'r1']]},
 "mode not-json composite_run_id='r1'": {'status': 200,
                                         'body': {'bytes': 'not json'},
                                         'order': ['bytes'],
                                         'disposition': 'inline; filename="composite-r1-trace.json"',
                                         'events': None,
                                         'calls': [['capabilities', 'http://api:8000'],
                                                   ['composite_run_trace', 'http://api:8000', 'r1']]},
 "mode capability-absent compose_id='7'": {'status': 409,
                                           'body': {'error': 'this viva-api deployment does not support: '
                                                             'viva-v1-trace (server version: 0.9.999) — '
                                                             'upgrade/redeploy viva-api, or use a deployment that '
                                                             'advertises this capability',
                                                    'missing': ['viva-v1-trace'],
                                                    'server_version': '0.9.999'},
                                           'order': ['error', 'missing', 'server_version'],
                                           'disposition': None,
                                           'events': None,
                                           'calls': [['capabilities', 'http://api:8000']]},
 "mode unreachable compose_id='7'": {'status': 502,
                                     'body': {'error': 'viva-api unreachable: tunnel down'},
                                     'order': ['error'],
                                     'disposition': None,
                                     'events': None,
                                     'calls': [['capabilities', 'http://api:8000']]},
 "mode pre-capabilities compose_id='7'": {'status': 409,
                                          'body': {'error': 'this viva-api deployment does not support: '
                                                            'viva-v1-trace (server version: pre-capabilities '
                                                            '(endpoint absent)) — upgrade/redeploy viva-api, or use '
                                                            'a deployment that advertises this capability',
                                                   'missing': ['viva-v1-trace'],
                                                   'server_version': 'pre-capabilities (endpoint absent)'},
                                          'order': ['error', 'missing', 'server_version'],
                                          'disposition': None,
                                          'events': None,
                                          'calls': [['capabilities', 'http://api:8000']]},
 "mode upstream-404 compose_id='7'": {'status': 404,
                                      'body': {'error': 'upstream says no'},
                                      'order': ['error'],
                                      'disposition': None,
                                      'events': None,
                                      'calls': [['capabilities', 'http://api:8000'],
                                                ['compose_status', 'http://api:8000', 7],
                                                ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "mode upstream-409 compose_id='7'": {'status': 409,
                                      'body': {'error': 'upstream says no'},
                                      'order': ['error'],
                                      'disposition': None,
                                      'events': None,
                                      'calls': [['capabilities', 'http://api:8000'],
                                                ['compose_status', 'http://api:8000', 7],
                                                ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "mode upstream-500 compose_id='7'": {'status': 502,
                                      'body': {'error': 'upstream says no'},
                                      'order': ['error'],
                                      'disposition': None,
                                      'events': None,
                                      'calls': [['capabilities', 'http://api:8000'],
                                                ['compose_status', 'http://api:8000', 7],
                                                ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "mode upstream-none compose_id='7'": {'status': 502,
                                       'body': {'error': 'upstream says no'},
                                       'order': ['error'],
                                       'disposition': None,
                                       'events': None,
                                       'calls': [['capabilities', 'http://api:8000'],
                                                 ['compose_status', 'http://api:8000', 7],
                                                 ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "mode no-correlation compose_id='7'": {'status': 404,
                                        'body': {'error': 'compose run 7 has no correlation_id to find its trace by'},
                                        'order': ['error'],
                                        'disposition': None,
                                        'events': None,
                                        'calls': [['capabilities', 'http://api:8000'],
                                                  ['compose_status', 'http://api:8000', 7]]},
 "mode empty compose_id='7'": {'status': 200,
                               'body': {'bytes': '{"traceEvents": [], "displayTimeUnit": "ms"}'},
                               'order': ['bytes'],
                               'disposition': 'inline; filename="composite-corr-1-trace.json"',
                               'events': '0',
                               'calls': [['capabilities', 'http://api:8000'],
                                         ['compose_status', 'http://api:8000', 7],
                                         ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "mode not-json compose_id='7'": {'status': 200,
                                  'body': {'bytes': 'not json'},
                                  'order': ['bytes'],
                                  'disposition': 'inline; filename="composite-corr-1-trace.json"',
                                  'events': None,
                                  'calls': [['capabilities', 'http://api:8000'],
                                            ['compose_status', 'http://api:8000', 7],
                                            ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "named compose simulation_id='12'": {'status': 200,
                                      'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": '
                                                        '1000, "pid": 1, "tid": 1}]}'},
                                      'order': ['bytes'],
                                      'disposition': 'inline; filename="simulation-12-trace.json"',
                                      'events': '1',
                                      'calls': [['capabilities', 'http://api:8000'],
                                                ['simulation_trace', 'http://api:8000', 12]]},
 "named compose simulation_id='compose-7'": {'status': 200,
                                             'body': {'bytes': '{"traceEvents": [{"ph": "M"}, {"ph": "X", "ts": '
                                                               '1}]}'},
                                             'order': ['bytes'],
                                             'disposition': 'inline; filename="compose-compose-7.json"',
                                             'events': '1',
                                             'calls': [['compose.trace', 'compose-7']]},
 "named compose simulation_id='simulation-N'": {'status': 400,
                                                'body': {'error': 'simulation_id / compose_id must be a positive '
                                                                  'integer'},
                                                'order': ['error'],
                                                'disposition': None,
                                                'events': None,
                                                'calls': []},
 "named compose simulation_id='simulation-V'": {'status': 200,
                                                'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": '
                                                                  '0, "dur": 1000, "pid": 1, "tid": 1}]}'},
                                                'order': ['bytes'],
                                                'disposition': 'inline; filename="composite-simulation-V-trace.json"',
                                                'events': '1',
                                                'calls': [['capabilities', 'http://api:8000'],
                                                          ['composite_run_trace',
                                                           'http://api:8000',
                                                           'simulation-V']]},
 "named compose composite_run_id='r1'": {'status': 200,
                                         'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, '
                                                           '"dur": 1000, "pid": 1, "tid": 1}]}'},
                                         'order': ['bytes'],
                                         'disposition': 'inline; filename="composite-r1-trace.json"',
                                         'events': '1',
                                         'calls': [['capabilities', 'http://api:8000'],
                                                   ['composite_run_trace', 'http://api:8000', 'r1']]},
 "named compose compose_id='7'": {'status': 200,
                                  'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": '
                                                    '1000, "pid": 1, "tid": 1}]}'},
                                  'order': ['bytes'],
                                  'disposition': 'inline; filename="composite-corr-1-trace.json"',
                                  'events': '1',
                                  'calls': [['capabilities', 'http://api:8000'],
                                            ['compose_status', 'http://api:8000', 7],
                                            ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "named viva simulation_id='12'": {'status': 200,
                                   'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": '
                                                     '1000, "pid": 1, "tid": 1}]}'},
                                   'order': ['bytes'],
                                   'disposition': 'inline; filename="simulation-12-trace.json"',
                                   'events': '1',
                                   'calls': [['capabilities', 'http://api:8000'],
                                             ['simulation_trace', 'http://api:8000', 12]]},
 "named viva simulation_id='compose-7'": {'status': 200,
                                          'body': {'bytes': '{"traceEvents": [{"ph": "M"}, {"ph": "X", "ts": 1}]}'},
                                          'order': ['bytes'],
                                          'disposition': 'inline; filename="compose-compose-7.json"',
                                          'events': '1',
                                          'calls': [['compose.trace', 'compose-7']]},
 "named viva simulation_id='simulation-N'": {'status': 400,
                                             'body': {'error': 'simulation_id / compose_id must be a positive '
                                                               'integer'},
                                             'order': ['error'],
                                             'disposition': None,
                                             'events': None,
                                             'calls': []},
 "named viva simulation_id='simulation-V'": {'status': 200,
                                             'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, '
                                                               '"dur": 1000, "pid": 1, "tid": 1}]}'},
                                             'order': ['bytes'],
                                             'disposition': 'inline; filename="composite-simulation-V-trace.json"',
                                             'events': '1',
                                             'calls': [['capabilities', 'http://api:8000'],
                                                       ['composite_run_trace', 'http://api:8000', 'simulation-V']]},
 "named viva composite_run_id='r1'": {'status': 200,
                                      'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": '
                                                        '1000, "pid": 1, "tid": 1}]}'},
                                      'order': ['bytes'],
                                      'disposition': 'inline; filename="composite-r1-trace.json"',
                                      'events': '1',
                                      'calls': [['capabilities', 'http://api:8000'],
                                                ['composite_run_trace', 'http://api:8000', 'r1']]},
 "named viva compose_id='7'": {'status': 200,
                               'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": 1000, '
                                                 '"pid": 1, "tid": 1}]}'},
                               'order': ['bytes'],
                               'disposition': 'inline; filename="composite-corr-1-trace.json"',
                               'events': '1',
                               'calls': [['capabilities', 'http://api:8000'],
                                         ['compose_status', 'http://api:8000', 7],
                                         ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 "named legacy simulation_id='12'": {'status': 200,
                                     'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": '
                                                       '1000, "pid": 1, "tid": 1}]}'},
                                     'order': ['bytes'],
                                     'disposition': 'inline; filename="simulation-12-trace.json"',
                                     'events': '1',
                                     'calls': [['capabilities', 'http://api:8000'],
                                               ['simulation_trace', 'http://api:8000', 12]]},
 "named legacy simulation_id='compose-7'": {'status': 200,
                                            'body': {'bytes': '{"traceEvents": [{"ph": "M"}, {"ph": "X", "ts": 1}]}'},
                                            'order': ['bytes'],
                                            'disposition': 'inline; filename="compose-compose-7.json"',
                                            'events': '1',
                                            'calls': [['compose.trace', 'compose-7']]},
 "named legacy simulation_id='simulation-N'": {'status': 400,
                                               'body': {'error': 'simulation_id / compose_id must be a positive '
                                                                 'integer'},
                                               'order': ['error'],
                                               'disposition': None,
                                               'events': None,
                                               'calls': []},
 "named legacy simulation_id='simulation-V'": {'status': 200,
                                               'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": '
                                                                 '0, "dur": 1000, "pid": 1, "tid": 1}]}'},
                                               'order': ['bytes'],
                                               'disposition': 'inline; filename="composite-simulation-V-trace.json"',
                                               'events': '1',
                                               'calls': [['capabilities', 'http://api:8000'],
                                                         ['composite_run_trace', 'http://api:8000', 'simulation-V']]},
 "named legacy composite_run_id='r1'": {'status': 200,
                                        'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, '
                                                          '"dur": 1000, "pid": 1, "tid": 1}]}'},
                                        'order': ['bytes'],
                                        'disposition': 'inline; filename="composite-r1-trace.json"',
                                        'events': '1',
                                        'calls': [['capabilities', 'http://api:8000'],
                                                  ['composite_run_trace', 'http://api:8000', 'r1']]},
 "named legacy compose_id='7'": {'status': 200,
                                 'body': {'bytes': '{"traceEvents": [{"name": "run", "ph": "X", "ts": 0, "dur": '
                                                   '1000, "pid": 1, "tid": 1}]}'},
                                 'order': ['bytes'],
                                 'disposition': 'inline; filename="composite-corr-1-trace.json"',
                                 'events': '1',
                                 'calls': [['capabilities', 'http://api:8000'],
                                           ['compose_status', 'http://api:8000', 7],
                                           ['composite_run_trace', 'http://api:8000', 'corr-1']]},
 'support nothing named': {'status': 200,
                           'body': {'supported': True,
                                    'reason': None,
                                    'capability': 'viva-v1-trace',
                                    'server_version': '0.9.999',
                                    'viewer': {'mode': 'off', 'url': None, 'version': None}},
                           'order': ['supported', 'reason', 'capability', 'server_version', 'viewer'],
                           'disposition': None,
                           'events': None,
                           'calls': [['capabilities', 'http://api:8000']]},
 'support compose named': {'status': 200,
                           'body': {'supported': True,
                                    'reason': None,
                                    'capability': 'get-simulation-trace-chrome',
                                    'viewer': {'mode': 'off', 'url': None, 'version': None}},
                           'order': ['supported', 'reason', 'capability', 'viewer'],
                           'disposition': None,
                           'events': None,
                           'calls': []},
 'support compose named, viva-api unreachable': {'status': 200,
                                                 'body': {'supported': True,
                                                          'reason': None,
                                                          'capability': 'get-simulation-trace-chrome',
                                                          'viewer': {'mode': 'off', 'url': None, 'version': None}},
                                                 'order': ['supported', 'reason', 'capability', 'viewer'],
                                                 'disposition': None,
                                                 'events': None,
                                                 'calls': []},
 'support viva-core named': {'status': 200,
                             'body': {'supported': True,
                                      'reason': None,
                                      'capability': 'viva-v1-trace',
                                      'server_version': '0.9.999',
                                      'viewer': {'mode': 'off', 'url': None, 'version': None}},
                             'order': ['supported', 'reason', 'capability', 'server_version', 'viewer'],
                             'disposition': None,
                             'events': None,
                             'calls': [['capabilities', 'http://api:8000']]},
 'support viva-core named, capability absent': {'status': 200,
                                                'body': {'supported': False,
                                                         'reason': 'capability-absent',
                                                         'capability': 'viva-v1-trace',
                                                         'server_version': '0.9.999',
                                                         'viewer': {'mode': 'off', 'url': None, 'version': None}},
                                                'order': ['supported',
                                                          'reason',
                                                          'capability',
                                                          'server_version',
                                                          'viewer'],
                                                'disposition': None,
                                                'events': None,
                                                'calls': [['capabilities', 'http://api:8000']]},
 'support legacy named': {'status': 200,
                          'body': {'supported': True,
                                   'reason': None,
                                   'capability': 'viva-v1-trace',
                                   'server_version': '0.9.999',
                                   'viewer': {'mode': 'off', 'url': None, 'version': None}},
                          'order': ['supported', 'reason', 'capability', 'server_version', 'viewer'],
                          'disposition': None,
                          'events': None,
                          'calls': [['capabilities', 'http://api:8000']]},
 'support unreachable': {'status': 200,
                         'body': {'supported': False,
                                  'reason': 'unreachable',
                                  'error': 'tunnel down',
                                  'capability': 'viva-v1-trace',
                                  'viewer': {'mode': 'off', 'url': None, 'version': None}},
                         'order': ['supported', 'reason', 'error', 'capability', 'viewer'],
                         'disposition': None,
                         'events': None,
                         'calls': [['capabilities', 'http://api:8000']]},
 'support capability absent': {'status': 200,
                               'body': {'supported': False,
                                        'reason': 'capability-absent',
                                        'capability': 'viva-v1-trace',
                                        'server_version': '0.9.999',
                                        'viewer': {'mode': 'off', 'url': None, 'version': None}},
                               'order': ['supported', 'reason', 'capability', 'server_version', 'viewer'],
                               'disposition': None,
                               'events': None,
                               'calls': [['capabilities', 'http://api:8000']]},
 'support pre-capabilities': {'status': 200,
                              'body': {'supported': False,
                                       'reason': 'capability-absent',
                                       'capability': 'viva-v1-trace',
                                       'server_version': 'pre-capabilities (endpoint absent)',
                                       'viewer': {'mode': 'off', 'url': None, 'version': None}},
                              'order': ['supported', 'reason', 'capability', 'server_version', 'viewer'],
                              'disposition': None,
                              'events': None,
                              'calls': [['capabilities', 'http://api:8000']]}}
