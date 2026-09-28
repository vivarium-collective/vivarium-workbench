"""W2 (vivarium-workbench#1150): the five simulator ops onto /viva/v1/environments,
by capability, with every caller still seeing the legacy SimulatorVersion shape.

* ``viva-v1-environments-build`` -> upload/register (and the branch-head
  register, one call) via ``POST /viva/v1/environments``
* ``viva-v1-environments`` -> ``simulator_status`` (all variant rows) and
  ``list_simulators`` via ``GET /viva/v1/environments``
* branch lookups stay on ``/core/v1/simulator/versions`` (no capability says the
  server's ``branch`` filter is honoured; an older server ignores it silently)
* capability absent / old server -> the legacy calls, unchanged
"""
from __future__ import annotations

import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from vivarium_workbench.lib import sms_api_client as mod
from vivarium_workbench.lib.sms_api_client import (
    CAPABILITY_VIVA_V1_ENVIRONMENTS as READS,
    CAPABILITY_VIVA_V1_ENVIRONMENTS_BUILD as BUILD,
    SmsApiClient,
    environment_build_status,
    environments_as_simulators,
)

REPO = "https://github.com/CovertLabEcoli/vEcoli-private"


def _env(eid, lid, status="ready", variant="", commit="abc1234", temporary=False, label=None):
    return {"id": str(eid), "legacy_simulator_id": lid, "repo_url": REPO, "commit": commit,
            "variant": variant, "status": status, "created_at": f"2026-09-27T00:00:{eid:02d}",
            "temporary": temporary, "label": label, "key": commit}


class _Resp(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeServer:
    def __init__(self, monkeypatch, caps, *, envs=None, post=None, ignore_filters=False):
        self.caps = caps
        self.envs = envs or []
        self.post = post
        self.ignore_filters = ignore_filters  # viva-api 0.9.165: unknown query params ignored
        self.requests: list[tuple[str, str, dict, object]] = []
        monkeypatch.setattr(mod, "urlopen", self)
        mod._ENV_IDS.clear()

    def __call__(self, req, timeout=None):
        u = urlsplit(req.full_url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path in ("/viva/v1/capabilities", "/core/v1/capabilities"):
            if self.caps == 404:
                raise HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"{}"))
            return _Resp({"version": "t", "capabilities": list(self.caps)})
        body = json.loads(req.data) if req.data else None
        self.requests.append((req.get_method(), u.path, q, body))
        if u.path == "/viva/v1/environments" and req.get_method() == "POST":
            return _Resp(self.post)
        if u.path == "/viva/v1/environments":
            rows = list(self.envs)
            if "legacy_simulator_id" in q and not self.ignore_filters:
                rows = [r for r in rows if r["legacy_simulator_id"] == int(q["legacy_simulator_id"])]
            off, lim = int(q.get("offset", 0)), int(q.get("limit", 100))
            page = rows[off:off + lim]
            return _Resp({"environments": page, "limit": lim, "offset": off, "total": len(rows),
                          "next_offset": off + lim if len(page) == lim else None})
        if u.path.startswith("/viva/v1/environments/"):
            eid = u.path.rsplit("/", 1)[1]
            return _Resp(next(r for r in self.envs if r["id"] == eid))
        if u.path == "/core/v1/simulator/status":
            return _Resp({"status": "completed", "error_message": "legacy says boom"})
        if u.path == "/core/v1/simulator/versions":
            return _Resp({"versions": [{"database_id": 5, "git_branch": "master"}]})
        if u.path == "/core/v1/simulator/upload":
            return _Resp({"database_id": 99, **(body or {})})
        if u.path == "/core/v1/simulator/latest":
            return _Resp({"git_commit_hash": "head999"})
        raise AssertionError(f"unexpected {req.get_method()} {u.path}")

    def paths(self):
        return [(m, p) for m, p, _, _ in self.requests]


# -- the record conversion --------------------------------------------------

def test_conversion_uses_legacy_simulator_id_never_the_environment_id():
    out = environments_as_simulators([
        _env(301, 77, variant="arm64"), _env(302, 77, variant="amd64"), _env(303, None),
    ])
    assert out == [{
        "database_id": 77, "git_repo_url": REPO, "git_commit_hash": "abc1234",
        "created_at": _env(301, 77)["created_at"], "temporary": False, "label": None,
        "environment_ids": ["301", "302"],
    }]
    # _build_id reads database_id first -- the simulator, not environment 301
    from vivarium_workbench.lib.remote_simulations import _build_id
    assert _build_id(out[0]) == 77


@pytest.mark.parametrize("statuses,expected", [
    (["ready", "ready", "ready"], "ready"),
    (["ready", "building", "ready"], "building"),
    (["ready", "pending", "pending"], "building"),
    (["pending", "pending"], "pending"),
    (["ready", "failed", "building"], "failed"),
])
def test_build_status_spans_every_variant(statuses, expected):
    assert environment_build_status([{"status": s} for s in statuses]) == expected


# -- upload / register ------------------------------------------------------

POST_202 = {"selected": False, "commit": "abc1234", "legacy_simulator_id": 88,
            "environments": [_env(1, 88, "pending", "arm64"), _env(2, 88, "pending", "amd64"),
                             _env(3, 88, "pending", "amd64-submit")]}


def test_upload_with_build_capability_posts_environment(monkeypatch):
    server = FakeServer(monkeypatch, [BUILD, READS], post=POST_202)
    out = SmsApiClient("http://h").upload_simulator(
        {"git_repo_url": REPO, "git_branch": "master", "git_commit_hash": "abc1234"}, force=False)
    assert server.paths() == [("POST", "/viva/v1/environments")]
    assert server.requests[0][3] == {"repo_url": REPO, "commit": "abc1234", "branch": "master", "force": False}
    assert out["database_id"] == 88 and out["git_commit_hash"] == "abc1234"
    assert out["environment_ids"] == ["1", "2", "3"]


def test_upload_without_build_capability_is_legacy(monkeypatch):
    server = FakeServer(monkeypatch, [READS])  # reads only: a deployment with no build path
    out = SmsApiClient("http://h").upload_simulator({"git_repo_url": REPO, "git_commit_hash": "c"}, force=True)
    assert server.paths() == [("POST", "/core/v1/simulator/upload")]
    assert server.requests[0][2] == {"force": "true"}
    assert out["database_id"] == 99


def test_upload_answer_naming_no_simulator_falls_back_to_legacy(monkeypatch):
    server = FakeServer(monkeypatch, [BUILD], post={**POST_202, "legacy_simulator_id": None})
    out = SmsApiClient("http://h").register_simulator(REPO, "master", "abc1234")
    assert server.paths() == [("POST", "/viva/v1/environments"), ("POST", "/core/v1/simulator/upload")]
    assert out["database_id"] == 99


def test_branch_head_register_is_one_call(monkeypatch):
    server = FakeServer(monkeypatch, [BUILD], post={**POST_202, "commit": "head123"})
    out = SmsApiClient("http://h").register_branch_head(REPO, "master")
    assert server.paths() == [("POST", "/viva/v1/environments")]
    assert server.requests[0][3] == {"repo_url": REPO, "branch": "master", "force": False}  # no commit
    assert out["git_commit_hash"] == "head123" and out["database_id"] == 88


def test_branch_head_register_legacy_pair(monkeypatch):
    server = FakeServer(monkeypatch, 404)
    out = SmsApiClient("http://h").register_branch_head(REPO, "master")
    assert server.paths() == [("GET", "/core/v1/simulator/latest"), ("POST", "/core/v1/simulator/upload")]
    assert out["git_commit_hash"] == "head999" and out["database_id"] == 99


# -- status -----------------------------------------------------------------

def test_status_polls_the_rows_the_post_returned(monkeypatch):
    rows = [_env(1, 88, "ready", "arm64"), _env(2, 88, "building", "amd64"), _env(3, 88, "ready", "amd64-submit")]
    server = FakeServer(monkeypatch, [BUILD, READS], envs=rows, post=POST_202)
    SmsApiClient("http://h").upload_simulator({"git_repo_url": REPO, "git_commit_hash": "abc1234"})
    st = SmsApiClient("http://h").simulator_status(88)  # a fresh client, as the status view makes
    assert server.paths()[1:] == [("GET", f"/viva/v1/environments/{i}") for i in ("1", "2", "3")]
    assert st["status"] == "building"
    rows[1]["status"] = "ready"
    assert SmsApiClient("http://h").simulator_status(88)["status"] == "ready"


def test_status_lists_by_legacy_id_and_rechecks_client_side(monkeypatch):
    """A server older than viva-api#901 ignores ?legacy_simulator_id= and answers
    the unfiltered list; only rows that really carry the id may count."""
    rows = [_env(9, 12, "failed"), _env(4, 77, "ready", "arm64"), _env(5, 77, "ready", "amd64")]
    server = FakeServer(monkeypatch, [READS], envs=rows, ignore_filters=True)
    st = SmsApiClient("http://h").simulator_status(77)
    assert st["status"] == "ready"
    assert server.requests[0][2]["legacy_simulator_id"] == "77"


def test_failed_status_borrows_the_legacy_error_message(monkeypatch):
    server = FakeServer(monkeypatch, [READS], envs=[_env(4, 77, "failed")])
    st = SmsApiClient("http://h").simulator_status(77)
    assert st["status"] == "failed" and st["error_message"] == "legacy says boom"
    assert server.paths()[-1] == ("GET", "/core/v1/simulator/status")


def test_status_with_no_rows_is_legacy(monkeypatch):
    """A SLURM build has no environment row."""
    server = FakeServer(monkeypatch, [READS], envs=[])
    st = SmsApiClient("http://h").simulator_status(77)
    assert server.paths()[-1] == ("GET", "/core/v1/simulator/status")
    assert st["status"] == "completed"


def test_status_without_capability_is_legacy(monkeypatch):
    server = FakeServer(monkeypatch, 404)
    SmsApiClient("http://h").simulator_status(77)
    assert server.paths() == [("GET", "/core/v1/simulator/status")]


def test_ready_is_terminal_ok_for_every_build_poll():
    from vivarium_workbench.lib import remote_run_jobs, remote_run_views
    assert "ready" in remote_run_jobs._TERMINAL_OK
    assert "ready" in remote_run_views._TERMINAL_OK


# -- listing ----------------------------------------------------------------

def test_list_pages_every_environment_and_converts(monkeypatch):
    rows = [_env(i, 100 + i // 3) for i in range(450)]  # 3 pages at 200
    server = FakeServer(monkeypatch, [READS], envs=rows)
    out = SmsApiClient("http://h").list_simulators()
    assert [q["offset"] for _, _, q, _ in server.requests] == ["0", "200", "400"]
    assert all(q["temporary"] == "any" for _, _, q, _ in server.requests)
    assert len(out["versions"]) == 150
    assert all("git_branch" not in v for v in out["versions"])
    assert SmsApiClient("http://h").simulator_commit(101) == "abc1234"


def test_branch_lookup_stays_on_the_legacy_listing(monkeypatch):
    server = FakeServer(monkeypatch, [READS, BUILD])
    out = SmsApiClient("http://h").list_simulators(branch_lookup=True)
    assert server.paths() == [("GET", "/core/v1/simulator/versions")]
    assert out["versions"][0]["git_branch"] == "master"


def test_list_without_capability_is_legacy(monkeypatch):
    server = FakeServer(monkeypatch, [])
    SmsApiClient("http://h").list_simulators()
    assert server.paths() == [("GET", "/core/v1/simulator/versions")]


class _RecordingClient:
    def __init__(self, versions):
        self.versions = versions
        self.branch_lookups: list[bool] = []

    def list_simulators(self, branch_lookup=False):
        self.branch_lookups.append(branch_lookup)
        return {"versions": self.versions}


def test_branch_callers_ask_for_the_branch_listing():
    from vivarium_workbench.lib import comparison_pinning, remote_build_source, remote_pinned

    v = [{"database_id": 5, "git_repo_url": REPO, "git_branch": "master",
          "git_commit_hash": "abc1234def", "created_at": "x"}]
    c = _RecordingClient(v)
    remote_pinned.resolve_pinned_build(c, REPO, "master")
    comparison_pinning.resolve_environment_build(c, {"repo": REPO, "ref": "master"})
    remote_build_source.list_build_sources(c)
    assert c.branch_lookups == [True, True, True]
    # a SHA ref needs no branch: it may use the environments listing
    comparison_pinning.resolve_environment_build(c, {"repo": REPO, "ref": "abc1234"})
    assert c.branch_lookups[-1] is False
