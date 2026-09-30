"""DNS-rebinding guard: a loopback-bound, un-proxied server only answers to loopback ``Host`` names.

The same-origin CSRF check compares ``Origin`` with ``Host``; in a rebinding attack a page re-points its own DNS
name at 127.0.0.1, so both are the attacker's name and the check passes. The guard looks at ``Host`` alone.
Exercised through the real app (middleware included), not by calling the decision function in isolation.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vivarium_workbench.api import app as appmod
from vivarium_workbench.lib import _root
from vivarium_workbench.lib.csrf import allowed_hosts_via_env, is_host_allowed


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("VIVARIUM_WORKBENCH_ALLOWED_HOSTS", "VIVARIUM_WORKBENCH_TRUST_PROXY",
              "VIVARIUM_WORKBENCH_ALLOWED_ORIGINS"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def make_client(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: hostguard\n")
    saved = _root.get_workspace_root()
    _root.set_workspace_root(ws)

    def make(host: str, *, bind_host="127.0.0.1", base_path=""):
        app = appmod.create_app()
        app.dependency_overrides[appmod.get_workspace] = lambda: ws
        if bind_host is not None:
            app.state.bind_host = bind_host
        app.state.base_path = base_path
        return TestClient(app, base_url=f"http://{host}", raise_server_exceptions=False)

    yield make
    _root._WS_ROOT = saved
    _root._WS_PATHS = None


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "LOCALHOST", "localhost"])
def test_loopback_names_are_served(make_client, host):
    assert make_client(host).get("/health").status_code == 200


def test_the_ipv6_loopback_literal_is_served(make_client):
    # sent as a header: Starlette's TestClient cannot parse a bracketed IPv6 base_url
    assert make_client("localhost").get("/health", headers={"host": "[::1]:8000"}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example", "10.0.0.5:8000",
                                  "localhost.evil.example"])
def test_a_rebound_name_is_refused_on_every_kind_of_route(make_client, host):
    c = make_client(host)
    for method, url in (("GET", "/health"), ("GET", "/api/simulations"), ("POST", "/api/study-rename"),
                        ("GET", "/index.html")):
        r = c.request(method, url, json={} if method == "POST" else None)
        assert r.status_code == 400, f"{method} {url} with Host {host}"
        body = r.json()
        assert body["error"] == "invalid Host header"
        assert "--allowed-host" in body["hint"] and "VIVARIUM_WORKBENCH_ALLOWED_HOSTS" in body["hint"]


def test_a_host_header_with_userinfo_is_refused(make_client):
    r = make_client("localhost:8000").get("/health", headers={"host": "evil.example@localhost:8000"})
    assert r.status_code == 400


def test_a_server_bound_beyond_loopback_is_left_alone(make_client):
    assert make_client("workbench.internal", bind_host="0.0.0.0").get("/health").status_code == 200


def test_an_unknown_bind_address_is_left_alone(make_client):
    assert make_client("testserver", bind_host=None).get("/health").status_code == 200


def test_a_proxied_server_is_left_alone(make_client, monkeypatch):
    monkeypatch.setenv("VIVARIUM_WORKBENCH_TRUST_PROXY", "1")
    assert make_client("workbench.example").get("/health").status_code == 200


def test_a_server_under_a_base_path_is_left_alone(make_client):
    assert make_client("workbench.example", base_path="/workbench").get("/health").status_code == 200


def test_extra_hosts_can_be_allowed_by_env(make_client, monkeypatch):
    assert make_client("tunnel.example").get("/health").status_code == 400
    monkeypatch.setenv("VIVARIUM_WORKBENCH_ALLOWED_HOSTS", " Tunnel.Example , other.example ")
    assert allowed_hosts_via_env({"VIVARIUM_WORKBENCH_ALLOWED_HOSTS": " Tunnel.Example , "}) == ["tunnel.example"]
    assert make_client("tunnel.example:9000").get("/health").status_code == 200
    assert make_client("evil.example").get("/health").status_code == 400


@pytest.mark.parametrize("host,ok", [
    (None, False), ("", False), ("localhost", True), ("[::1]:80", True), ("evil.example", False),
    ("a@localhost", False), ("[bad", False),
])
def test_decision_on_loopback_bind(host, ok):
    assert is_host_allowed(host, bind_host="127.0.0.1") is ok


@pytest.mark.parametrize("bind", ["0.0.0.0", None, "10.1.2.3"])
def test_decision_off_loopback_is_always_true(bind):
    assert is_host_allowed("evil.example", bind_host=bind)
    assert is_host_allowed(None, bind_host=bind)


def test_the_chat_tools_in_process_client_passes_the_guard(make_client):
    """The assistant calls the app through an in-process client. If that client's Host were not loopback, every
    tool call would be refused on a default local server."""
    import asyncio

    from vivarium_workbench.lib import ai_tools

    app = make_client("localhost").app

    async def go():
        async with ai_tools.make_client(app) as client:
            return (await client.get("/health")).status_code

    assert asyncio.run(go()) == 200


@pytest.mark.parametrize("name,ok", [
    ("localhost", True), ("LOCALHOST.", True), ("app.localhost", True), ("127.0.0.1", True), ("127.0.0.2", True),
    ("127.255.255.254", True), ("::1", True), ("::ffff:127.0.0.1", True),
    ("evil.example", False), ("localhost.evil.example", False), ("10.0.0.1", False), ("0.0.0.0", False),
    ("", False), (None, False),
])
def test_what_counts_as_loopback(name, ok):
    from vivarium_workbench.lib.csrf import is_loopback_host

    assert is_loopback_host(name) is ok


@pytest.mark.parametrize("bind", ["127.0.0.2", "::1", "localhost"])
def test_any_loopback_bind_is_guarded(make_client, bind):
    assert make_client("evil.example", bind_host=bind).get("/health").status_code == 400
    assert make_client("localhost", bind_host=bind).get("/health").status_code == 200


def test_a_trailing_dot_host_is_served(make_client):
    assert make_client("localhost").get("/health", headers={"host": "localhost.:8000"}).status_code == 200


def test_the_allowed_host_flag_is_honoured_by_a_real_detached_server(tmp_path):
    """``serve --detach --allowed-host`` boots a real server that answers to that Host name and still refuses
    an unlisted one — the flag survives cmd_serve -> the detached child -> the middleware."""
    import json
    import shutil
    import time

    import httpx

    from pathlib import Path

    from vivarium_workbench import cli

    def _args(**kw):
        ns = type("NS", (), {})()
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    pytest.importorskip("process_bigraph.artifacts")
    fixtures = Path(__file__).parent / "_fixtures"
    src = next((d for d in sorted(fixtures.iterdir()) if (d / "workspace.yaml").exists()), None)
    if src is None:
        pytest.skip("no fixture workspace available")
    ws = tmp_path / "ws_copy"
    shutil.copytree(src, ws)
    cli._clear_server_state(ws)
    try:
        rc = cli.cmd_serve(_args(
            workspace=str(ws), port=0, host="127.0.0.1", base_path="", detach=True, open=False,
            investigation=None, trust_proxy=False, allowed_origin=None, allowed_host=["tunnel.example"],
        ))
        assert rc == 0
        url = cli._read_server_info(ws)["url"]
        codes = {}
        for host in ("localhost", "tunnel.example", "evil.example"):
            for _ in range(100):
                try:
                    codes[host] = httpx.get(url + "/health", headers={"Host": host}, timeout=5).status_code
                    break
                except httpx.HTTPError:
                    time.sleep(0.1)
        assert codes == {"localhost": 200, "tunnel.example": 200, "evil.example": 400}, json.dumps(codes)
    finally:
        cli.cmd_server_stop(_args(workspace=str(ws)))
