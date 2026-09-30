"""W2 (vivarium-workbench#1150): the five simulator ops onto /viva/v1/environments,
by capability, with every caller still seeing the legacy SimulatorVersion shape.

* ``viva-v1-environments-build`` -> upload/register (and the branch-head
  register, one call) via ``POST /viva/v1/environments``
* ``viva-v1-environments`` -> ``simulator_status`` (all variant rows) and
  ``list_simulators`` via ``GET /viva/v1/environments``
* ``viva-v1-environments-filters`` -> branch lookups (``resolve_pinned_build``,
  ``comparison_pinning`` for a branch ref) via ``?repo_url=&branch=``; without it
  they stay on ``/core/v1/simulator/versions`` (an older server ignores the
  filter silently). The build dropdown's branch column stays legacy either way.
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
    CAPABILITY_VIVA_V1_ENVIRONMENTS_FILTERS as FILTERS,
    CAPABILITY_VIVA_V1_ENVIRONMENTS_BUILD as BUILD,
    SmsApiClient,
    environment_build_status,
    environments_as_simulators,
)

REPO = "https://github.com/CovertLabEcoli/vEcoli-private"


def _env(eid, lid, status="ready", variant="", commit="abc1234", temporary=False, label=None,
         branch=None, repo_url=REPO):
    # ``_branch`` is what the SERVER knows (the linked simulator's git_branch);
    # the fake uses it to filter and never returns it (stripped in __call__).
    return {"id": str(eid), "legacy_simulator_id": lid, "repo_url": repo_url, "commit": commit,
            "variant": variant, "status": status, "created_at": f"2026-09-27T00:{eid // 60:02d}:{eid % 60:02d}",
            "temporary": temporary, "label": label, "key": commit, "_branch": branch}


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
            if not self.ignore_filters:
                # viva-api >= 0.9.166 (viva-v1-environments-filters): exact repo_url,
                # branch through the linked simulator (a row with none never
                # matches), temporaries out unless asked for.
                if "branch" in q and "repo_url" not in q:
                    raise HTTPError(req.full_url, 422, "branch needs repo_url", {}, io.BytesIO(b"{}"))
                if "repo_url" in q:
                    rows = [r for r in rows if r["repo_url"] == q["repo_url"]]
                if "branch" in q:
                    rows = [r for r in rows if r["legacy_simulator_id"] is not None
                            and r.get("_branch") == q["branch"]]
                if "status" in q:
                    rows = [r for r in rows if r["status"] == q["status"]]
                temp = q.get("temporary", "any" if "legacy_simulator_id" in q else "false")
                if temp != "any":
                    rows = [r for r in rows if bool(r["temporary"]) == (temp == "true")]
            off, lim = int(q.get("offset", 0)), int(q.get("limit", 100))
            page = [{k: v for k, v in r.items() if k != "_branch"} for r in rows[off:off + lim]]
            return _Resp({"environments": page, "limit": lim, "offset": off, "total": len(rows),
                          "next_offset": off + lim if len(page) == lim else None})
        if u.path.startswith("/viva/v1/environments/"):
            eid = u.path.rsplit("/", 1)[1]
            return _Resp({k: v for k, v in next(r for r in self.envs if r["id"] == eid).items()
                          if k != "_branch"})
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
        self.calls: list[tuple] = []

    def list_simulators(self, branch_lookup=False):
        self.calls.append(("list", branch_lookup))
        return {"versions": self.versions}

    def list_branch_builds(self, repo_url, branch):
        self.calls.append(("branch", repo_url, branch))
        return {"versions": self.versions}


def test_branch_callers_ask_for_the_branch_builds():
    from vivarium_workbench.lib import comparison_pinning, remote_build_source, remote_pinned

    v = [{"database_id": 5, "git_repo_url": REPO, "git_branch": "master",
          "git_commit_hash": "abc1234def", "created_at": "x"}]
    c = _RecordingClient(v)
    remote_pinned.resolve_pinned_build(c, REPO, "master")
    comparison_pinning.resolve_environment_build(c, {"repo": REPO, "ref": "master"})
    assert c.calls == [("branch", REPO, "master"), ("branch", REPO, "master")]
    # the dropdown shows EVERY build's branch: still the legacy branch listing
    remote_build_source.list_build_sources(c)
    assert c.calls[-1] == ("list", True)
    # a SHA ref needs no branch: the whole listing (environments where served)
    comparison_pinning.resolve_environment_build(c, {"repo": REPO, "ref": "abc1234"})
    assert c.calls[-1] == ("list", False)


# -- branch lookups on viva-v1-environments-filters ---------------------------

def _branch_rows():
    return [
        # newest first, as the server lists them
        _env(40, 12, variant="arm64", commit="ccc3333", branch="master"),
        _env(39, 12, variant="amd64", commit="ccc3333", branch="master", status="building"),
        _env(30, 11, commit="bbb2222", branch="master"),
        _env(20, 10, commit="aaa1111", branch="master"),
        _env(35, 13, commit="ddd4444", branch="feature"),        # other branch
        _env(45, 14, commit="eee5555", branch="master", temporary=True, label="smoke"),
        _env(46, None, commit="fff6666", branch="master"),       # no linked simulator
    ]


def test_branch_builds_use_the_pair_filter(monkeypatch):
    server = FakeServer(monkeypatch, [READS, BUILD, FILTERS], envs=_branch_rows())
    out = SmsApiClient("http://h").list_branch_builds(REPO, "master")
    gets = [(p, q) for m, p, q, _ in server.requests if m == "GET"]
    assert all(p == "/viva/v1/environments" for p, _ in gets)
    # the spelling discovery (no filter), then the pair -- no status, no temporary
    assert "branch" not in gets[0][1] and "repo_url" not in gets[0][1]
    pair = gets[-1][1]
    assert pair["repo_url"] == REPO and pair["branch"] == "master"
    assert "status" not in pair and "temporary" not in pair
    assert [v["database_id"] for v in out["versions"]] == [12, 11, 10]
    assert all(v["git_branch"] == "master" and v["git_repo_url"] == REPO for v in out["versions"])
    assert out["versions"][0]["environment_ids"] == ["40", "39"]


def test_branch_builds_exclude_temporaries_by_default(monkeypatch):
    FakeServer(monkeypatch, [READS, FILTERS], envs=_branch_rows())
    ids = [v["database_id"] for v in SmsApiClient("http://h").list_branch_builds(REPO, "master")["versions"]]
    assert 14 not in ids  # the marked-temporary build (D11), though it is the newest


def test_pinned_build_is_the_newest_registered_on_the_branch(monkeypatch):
    """Newest REGISTERED build (12, one variant still building), not the newest
    ready one (11) and not the branch head: today's semantics, unchanged."""
    from vivarium_workbench.lib import remote_pinned
    FakeServer(monkeypatch, [READS, FILTERS], envs=_branch_rows())
    got = remote_pinned.resolve_pinned_build(SmsApiClient("http://h"), REPO, "master")
    assert got == {"simulator_id": 12, "commit": "ccc3333", "branch": "master", "repo_url": REPO}


def test_comparison_branch_ref_uses_the_pair_filter(monkeypatch):
    from vivarium_workbench.lib import comparison_pinning
    server = FakeServer(monkeypatch, [READS, FILTERS], envs=_branch_rows())
    got = comparison_pinning.resolve_environment_build(
        SmsApiClient("http://h"), {"repo": "CovertLabEcoli/vEcoli-private", "ref": "feature"})
    assert got["simulator_id"] == 13 and got["commit"] == "ddd4444"
    assert any(q.get("branch") == "feature" and q.get("repo_url") == REPO
               for _, _, q, _ in server.requests)


@pytest.mark.parametrize("asked", [
    REPO, REPO + ".git", REPO + "/", REPO.lower(), "CovertLabEcoli/vEcoli-private",
    "git@github.com:CovertLabEcoli/vEcoli-private.git",
])
def test_branch_builds_ask_with_the_spelling_the_server_registered(monkeypatch, asked):
    """The server's repo_url match is exact; the workbench's is not. The client
    finds the registered spelling(s) and asks with each."""
    server = FakeServer(monkeypatch, [READS, FILTERS], envs=_branch_rows())
    out = SmsApiClient("http://h").list_branch_builds(asked, "master")
    assert [v["database_id"] for v in out["versions"]] == [12, 11, 10]
    assert {q["repo_url"] for _, _, q, _ in server.requests if "branch" in q} == {REPO}


def test_branch_builds_ask_every_registered_spelling(monkeypatch):
    rows = _branch_rows() + [_env(50, 20, commit="999aaaa", branch="master", repo_url=REPO + ".git")]
    server = FakeServer(monkeypatch, [READS, FILTERS], envs=rows)
    out = SmsApiClient("http://h").list_branch_builds(REPO, "master")
    assert {q["repo_url"] for _, _, q, _ in server.requests if "branch" in q} == {REPO, REPO + ".git"}
    assert sorted(v["database_id"] for v in out["versions"]) == [10, 11, 12, 20]
    from vivarium_workbench.lib import remote_pinned
    # the caller's own rule (.git-insensitive) still picks the newest across both
    assert remote_pinned.resolve_pinned_build(SmsApiClient("http://h"), REPO, "master")["simulator_id"] == 20


def test_branch_builds_for_an_unknown_repo_ask_no_pair(monkeypatch):
    server = FakeServer(monkeypatch, [READS, FILTERS], envs=_branch_rows())
    assert SmsApiClient("http://h").list_branch_builds("https://github.com/x/y", "master") == {"versions": []}
    assert not any("branch" in q for _, _, q, _ in server.requests)


@pytest.mark.parametrize("caps", [[READS, BUILD], [FILTERS], [], 404])
def test_branch_builds_without_the_filters_capability_are_legacy(monkeypatch, caps):
    """No filters capability (an older server silently ignores ?branch=), or no
    reads: the legacy listing, unchanged, for the caller to filter."""
    server = FakeServer(monkeypatch, caps, envs=_branch_rows(), ignore_filters=True)
    out = SmsApiClient("http://h").list_branch_builds(REPO, "master")
    assert server.paths() == [("GET", "/core/v1/simulator/versions")]
    assert out == {"versions": [{"database_id": 5, "git_branch": "master"}]}
