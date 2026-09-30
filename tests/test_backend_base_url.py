"""``serve --backend-base-url`` -> VIVARIUM_WORKBENCH_BACKEND_BASE_URL -> ``sms_api_base()``.

Precedence (highest first): the flag / ``VIVARIUM_WORKBENCH_BACKEND_BASE_URL``, then
the pre-existing ``VIVA_API_BASE``, then its alias ``SMS_API_BASE``, then localhost.
The last case boots a REAL detached server (the flag must survive the re-exec that
``--detach`` does) and asks it, over HTTP, which backend it resolved.
"""
import json
import shutil
import time
import urllib.request
from pathlib import Path

import pytest

from vivarium_workbench import cli
from vivarium_workbench.lib import sms_api_client as sac

BACKEND_BASE_ENV_VARS = sac.BACKEND_BASE_ENV_VARS


@pytest.fixture
def clean_env(monkeypatch):
    for v in BACKEND_BASE_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


def test_default_is_localhost(clean_env):
    assert sac.sms_api_base() == "http://localhost:8080"
    assert sac.backend_configured() is False


def test_aliases_still_resolve_in_their_old_order(clean_env):
    clean_env.setenv("SMS_API_BASE", "http://alias")
    assert sac.sms_api_base() == "http://alias"
    clean_env.setenv("VIVA_API_BASE", "http://viva")
    assert sac.sms_api_base() == "http://viva"
    assert sac.backend_configured() is True


def test_new_env_beats_aliases(clean_env):
    clean_env.setenv("VIVA_API_BASE", "http://viva")
    clean_env.setenv("SMS_API_BASE", "http://alias")
    clean_env.setenv("VIVARIUM_WORKBENCH_BACKEND_BASE_URL", "http://new")
    assert sac.sms_api_base() == "http://new"


@pytest.mark.parametrize("url, ok", [
    ("https://sms.cam.uchc.edu", "https://sms.cam.uchc.edu"),
    ("https://sms.cam.uchc.edu/", "https://sms.cam.uchc.edu"),
    ("http://localhost:8080/prefix/", "http://localhost:8080/prefix"),
])
def test_normalize_accepts(url, ok):
    assert sac.normalize_backend_base_url(url) == ok


@pytest.mark.parametrize("url", [
    "sms.cam.uchc.edu", "ftp://x", "https://", "https://user:pw@host",
    "https://host?a=1", "https://host#frag",
])
def test_normalize_rejects(url):
    with pytest.raises(ValueError):
        sac.normalize_backend_base_url(url)


def _ns(**kw):
    ns = type("NS", (), {})()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_bad_flag_is_a_usage_error_not_a_crash(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("schema_version: 2\nname: ws\n")
    # through the real argparse wiring, so this also proves the flag is declared
    rc = cli.main(["serve", "--workspace", str(ws), "--backend-base-url", "https://u:p@h"])
    assert rc == 2
    assert "credentials" in capsys.readouterr().err


def _fixture_ws() -> Path:
    fx = Path(__file__).parent / "_fixtures"
    for d in sorted(fx.iterdir()):
        if (d / "workspace.yaml").exists():
            return d
    pytest.skip("no fixture workspace available")


def test_the_backend_flag_is_honoured_by_a_real_detached_server(tmp_path, monkeypatch):
    """Flag > the env aliases (conftest points VIVA_API_BASE at a closed port), and
    it survives ``--detach``'s re-exec: the child reports the flag's URL."""
    pytest.importorskip("process_bigraph.artifacts")
    ws = tmp_path / "ws_copy"
    shutil.copytree(_fixture_ws(), ws)
    cli._clear_server_state(ws)
    # cwd/HOME/TMPDIR under tmp_path: nothing this boot writes can land in ~.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    flag_url = "http://127.0.0.1:1/flagged"
    try:
        rc = cli.cmd_serve(_ns(
            workspace=str(ws), port=0, host="127.0.0.1", base_path="", detach=True,
            open=False, investigation=None, trust_proxy=False, allowed_origin=None,
            backend_base_url=flag_url))
        assert rc == 0
        url = cli._read_server_info(ws)["url"]
        with urllib.request.urlopen(url + "/api/source/remote-health", timeout=30) as r:
            body = json.loads(r.read())
        assert body["base_url"] == flag_url
        assert body["configured"] is True
    finally:
        cli.cmd_server_stop(_ns(workspace=str(ws)))
        time.sleep(0.2)
