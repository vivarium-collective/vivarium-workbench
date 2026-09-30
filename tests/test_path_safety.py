"""``lib/path_safety`` — what it accepts, what it refuses, and that its env-worker twin agrees.

The route-level guarantee (no request can escape the workspace) is ``tests/test_traversal_sweep.py``; this file
pins the helper's contract, the names real workspaces use (so tightening never breaks them), and the
``env_worker`` copy of ``resolve_inside`` (that module runs in the workspace's interpreter and cannot import
workbench code).
"""
from __future__ import annotations

import pytest

from vivarium_workbench import env_worker
from vivarium_workbench.lib.errors import APIError
from vivarium_workbench.lib.path_safety import is_plain_name, plain_name, resolve_inside
from vivarium_workbench.lib.static_serving import is_servable

# Names workspaces actually use: slugs, dotted ids, generation suffixes, federation forms, spaces, unicode.
REAL_NAMES = ["study-1", "my_study", "a.b", "run__1790776435__abc123", "fed::study", "with space", "Ünïcode", "x"]
NOT_NAMES = ["", ".", "..", "a/b", "../x", "a/..", "a\\b", "..\\x", "/abs", "/", "x\x00y", 5, None]


@pytest.mark.parametrize("name", REAL_NAMES)
def test_real_workspace_names_are_plain(name):
    assert is_plain_name(name)
    assert plain_name(name) == name


@pytest.mark.parametrize("name", NOT_NAMES)
def test_paths_are_not_plain_names(name):
    assert not is_plain_name(name)
    with pytest.raises(APIError) as e:
        plain_name(name, "study name")
    assert e.value.status_code == 400 and "study name" in str(e.value)


def test_resolve_inside_accepts_files_under_the_root(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    assert resolve_inside(root, "a/b.yaml") == root / "a" / "b.yaml"
    assert resolve_inside(root, "a/../b.yaml") == root / "b.yaml"          # collapses, still inside
    assert resolve_inside(root, str(root / "a" / "b.yaml")) == root / "a" / "b.yaml"  # absolute but inside


@pytest.mark.parametrize("rel", [
    "../x.yaml", "a/../../x.yaml", "../ws-evil/x.yaml", ".git/config", ".pbg/ai-actions.jsonl",
    "a/.git/hooks/pre-commit", "", "x\x00.yaml",
])
def test_resolve_inside_refuses_escapes_and_control_dirs(tmp_path, rel):
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(APIError) as e:
        resolve_inside(root, rel)
    assert e.value.status_code == 400


def test_resolve_inside_refuses_a_sibling_that_shares_the_root_prefix(tmp_path):
    root = tmp_path / "ws"
    (tmp_path / "ws-evil").mkdir()
    root.mkdir()
    with pytest.raises(APIError):
        resolve_inside(root, str(tmp_path / "ws-evil" / "x.yaml"))


def test_resolve_inside_refuses_an_absolute_path_elsewhere(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(APIError):
        resolve_inside(root, str(tmp_path / "outside.txt"))


def test_resolve_inside_enforces_suffixes_case_insensitively(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    assert resolve_inside(root, "a/B.YAML", suffixes=(".yaml",)).name == "B.YAML"
    for bad in ("a/notes.txt", "a/noext", "a/x.yaml.txt"):
        with pytest.raises(APIError):
            resolve_inside(root, bad, suffixes=(".yaml", ".json"))


def test_resolve_inside_accepts_a_root_reached_through_a_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    # a root given through a symlink still accepts the resolved spelling of a path inside it
    # (macOS /tmp -> /private/tmp is the common case)
    assert resolve_inside(link, str(real / "a.yaml")).name == "a.yaml"
    assert resolve_inside(link, str(link / "a.yaml")).name == "a.yaml"


# --- the env-worker twin ------------------------------------------------------------------------------

TWIN_CASES = [
    ("a/b.yaml", None), ("a/../b.yaml", None), ("../x.yaml", None), ("a/../../x.yaml", None),
    (".git/config", None), (".pbg/ai-actions.jsonl", None), ("a/.git/hooks/x.py", None), ("a/.gitignore", None),
    ("", None), ("x\x00.yaml", None), ("a/b.YAML", (".yaml",)), ("a/b.txt", (".yaml", ".json")),
    ("a/b.py", env_worker._SOURCE_SUFFIXES), ("noext", env_worker._SOURCE_SUFFIXES),
    (".GIT/config", None), (".Pbg/ai-actions.jsonl", None), ("a/.Git/hooks/x", None),
]


def test_the_env_worker_twin_agrees_with_the_library(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    cases = TWIN_CASES + [(str(root / "in.yaml"), None), (str(tmp_path / "out.yaml"), None),
                          (str(tmp_path / "ws-evil" / "x.yaml"), None)]
    for rel, suffixes in cases:
        try:
            lib = str(resolve_inside(root, rel, suffixes=suffixes))
        except APIError:
            lib = None
        twin, _why = env_worker._resolve_inside(str(root), rel, suffixes)
        assert twin == lib, f"{rel!r} suffixes={suffixes}: library={lib!r} twin={twin!r}"


def test_the_env_worker_twin_protects_the_same_directories():
    from vivarium_workbench.lib.path_safety import PROTECTED_DIRS
    assert set(env_worker._PROTECTED_DIR_PARTS) == set(PROTECTED_DIRS)


# --- static serving ------------------------------------------------------------------------------------


@pytest.mark.parametrize("rel", [
    "index.html", "assets/walkthrough.js", "reports/study/a.html", "studies/s/figures/fig.1.png",
    "my.file.js", "a/b-c_d.e.json",
])
def test_the_dashboards_own_paths_are_servable(rel):
    assert is_servable(rel)


@pytest.mark.parametrize("rel", [
    "", ".git/config", ".env", ".pbg/ai-actions.jsonl", ".pbg/runs/x/run.log", "studies/.git/config",
    "a/../b", "..", "../outside.txt", "/etc/passwd", "a\x00b", ".venv/bin/python", "a/.env",
])
def test_dot_segments_and_absolute_paths_are_not_servable(rel):
    assert not is_servable(rel)


@pytest.mark.parametrize("rel", [".GIT/config", ".Pbg/ai-actions.jsonl", "a/.Git/hooks/x"])
def test_control_directories_are_protected_whatever_the_case(tmp_path, rel):
    """macOS and Windows filesystems ignore case: ``.GIT`` is ``.git``."""
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(APIError):
        resolve_inside(root, rel)


@pytest.mark.parametrize("rel", ["run.DB", "a/x.SQLITE", "a/x.sqlite3", "log.JSONL", "server.pid"])
def test_run_data_files_are_not_servable(rel):
    assert not is_servable(rel)


def test_a_symlink_leaving_the_tree_is_not_served(tmp_path):
    from vivarium_workbench.lib.static_serving import resolve_asset

    ws = tmp_path / "ws"
    (ws / "reports").mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("not for serving")
    (ws / "leak.txt").symlink_to(outside)
    (ws / "reports" / "leak2.txt").symlink_to(outside)
    (ws / "real.txt").write_text("ok")
    assert resolve_asset(ws, "leak.txt") is None
    assert resolve_asset(ws, "leak2.txt") is None
    assert resolve_asset(ws, "real.txt") == ws / "real.txt"
    assert resolve_asset(ws, ".git/config") is None


# --- the app-wide identifier guard ---------------------------------------------------------------------


@pytest.mark.parametrize("params", [
    {"study": "../x"}, {"new_name": "a/b"}, {"run_id": "..\\x"}, {"new_name": "/abs"}, {"inv": ".."},
    {"names": ["ok", "../x"]}, [("run_ids", "ok"), ("run_ids", "a/b")], {"job_id": "x\x00y"}, {"parent_studies": ["ok", "../../outside"]}, {"studies": ["/etc"]},
])
def test_identifier_fields_that_are_paths_are_refused(params):
    from vivarium_workbench.lib.path_safety import check_identifiers

    with pytest.raises(APIError) as e:
        check_identifiers(params)
    assert e.value.status_code == 400


@pytest.mark.parametrize("params", [
    {"study": "fed::study"}, {"name": "with space"}, {"study": ""}, {"study": None}, {"study": 5},
    {"names": ["a", "b"]}, {"source_path": "../elsewhere.yaml"},  # not an identifier field: resolved by the route
    [("study", "s1"), ("q", "../free text")],
])
def test_plain_empty_non_string_and_unlisted_values_pass(params):
    from vivarium_workbench.lib.path_safety import check_identifiers

    check_identifiers(params)


def test_every_identifier_field_is_enforced_over_http(tmp_path):
    """The guard is wired app-wide: a non-plain identifier is a 400 on a route that would otherwise accept it."""
    from fastapi.testclient import TestClient

    from vivarium_workbench.api import app as appmod

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: g\n")
    app = appmod.create_app()
    app.dependency_overrides[appmod.get_workspace] = lambda: ws
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/study-results", params={"study": "../x"}).status_code == 400
    assert c.post("/api/study-rename", json={"study": "s", "new_name": "a/b"}).status_code == 400
