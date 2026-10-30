"""``vivarium-workbench compose-entry``: the compose-api registry entry, derived from a workspace.

The inputs are real: the pyproject.toml / workspace.yaml of six viva-* workspaces and production's registry as it was
served on 2026-10-30 (``tests/_fixtures/compose_entry/README.md``). Each test builds a git checkout around one of them,
with a real bare repository behind a GitHub-style origin, so what git says about a pin (dirty, pushed, on the default
branch) is git's own answer. What is NOT proven here: that compose-api accepts the entry. The rules checked in
``test_the_entry_has_what_compose_apis_manifest_model_requires`` mirror ``RegistryEntry`` and ``Install`` in
compose-api's ``compose_api/registry/manifest.py``; the entries the rollout proposes are loaded by that model itself
in a compose-api checkout before anything is posted.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from vivarium_workbench import cli
from vivarium_workbench.lib import compose_entry as ce

FIX = Path(__file__).parent / "_fixtures" / "compose_entry"
REGISTRY = FIX / "registry_entries_0.7.76.json"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
       "-c", "core.hooksPath=/dev/null", "-c", "init.defaultBranch=main"]


def _git(ws: Path, *args: str) -> str:
    return subprocess.run([*GIT, "-C", str(ws), *args], check=True, capture_output=True, text=True).stdout.strip()


def _checkout(ws: Path, bare: Path, url: str, *, push: bool = True, branch: str = "main", set_head: bool = True) -> None:
    """Make ``ws`` a git checkout: a GitHub-spelled origin that a real bare repository stands behind, one commit of
    everything in it, pushed unless asked not to."""
    subprocess.run([*GIT, "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run([*GIT, "init", "-q", "-b", branch, str(ws)], check=True)
    _git(ws, "remote", "add", "origin", url)
    _git(ws, "config", f"url.{bare}.insteadOf", url)  # pushes go to the bare repository; the configured URL stays GitHub's
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "the files")
    if push:
        _git(ws, "push", "-q", "origin", branch)
        if set_head:
            _git(ws, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{branch}")


def _workspace(tmp_path: Path, name: str, packages: list[str], *, files: tuple[str, ...] = (), remote_repo: str | None = None,
               **checkout) -> Path:
    """A checkout of a real workspace's files with its packages present and a GitHub-spelled origin that a real bare
    repository stands behind."""
    ws = tmp_path / name
    shutil.copytree(FIX / name, ws)
    for package in packages:
        (ws / package).mkdir()
        (ws / package / "__init__.py").write_text("")
    for rel in files:
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text("")
    _checkout(ws, tmp_path / f"{name}.git", f"https://github.com/vivarium-collective/{remote_repo or 'viva-' + name}.git", **checkout)
    return ws


WORKSPACE_YAML = "name: synth\npackage_path: viva_s\n"
PYPROJECT = """[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "viva-synth"
version = "0"
dependencies = ["process-bigraph"]

[tool.hatch.build.targets.wheel]
packages = ["viva_s"]
"""


def _synthetic(tmp_path: Path, files: dict[str, str] | None = None, *, name: str = "synth", omit: tuple[str, ...] = (),
               **checkout) -> Path:
    """A workspace made of the given files (path -> text), for the shapes none of the real ones has; the defaults are the
    smallest workspace there can be."""
    ws = tmp_path / name
    for rel, text in {"workspace.yaml": WORKSPACE_YAML, "pyproject.toml": PYPROJECT, "viva_s/__init__.py": "",
                      **(files or {})}.items():
        if rel not in omit:
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_text(text)
    _checkout(ws, tmp_path / f"{name}.git", f"https://github.com/vivarium-collective/viva-{name}.git", **checkout)
    return ws


KETCHUP = {"name": "ketchup", "packages": ["viva_ketchup", "pbg_ketchup"]}
ARTISTOO = {"name": "artistoo", "packages": ["pbg_artistoo"],
            "files": ("pbg_artistoo/node_bridge/artistoo_bridge.js", "pbg_artistoo/node_bridge/package.json")}


def _codes(derived: ce.Derived, severity: str | None = None) -> list[str]:
    return [f.code for f in derived.findings if severity is None or f.severity == severity]


# --- the entry -----------------------------------------------------------------------------------------------------


def test_a_clean_pushed_workspace_gets_the_entry_its_own_files_describe(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    head = _git(ws, "rev-parse", "HEAD")
    derived = ce.derive(ws)
    url = "https://github.com/vivarium-collective/viva-ketchup"
    assert derived.entry == {
        "id": "viva-ketchup", "package": "viva-ketchup", "source": url, "commit": head,
        "install": {"git": url, "commit": head}, "module_roots": ["pbg_ketchup", "viva_ketchup"], "level": "listed"}
    assert _codes(derived, "error") == [] and _codes(derived, "warn") == [], "a clean, pure-Python workspace with no dev tooling"
    assert _codes(derived) == ["extras-available"], "its `dev` extra is mentioned, as information only"


def test_the_entry_has_what_compose_apis_manifest_model_requires(tmp_path):
    entry = ce.derive(_workspace(tmp_path, **KETCHUP)).entry
    assert entry is not None
    assert re.fullmatch(r"[0-9a-f]{40}", entry["commit"]), "a git install is pinned by a full commit, never a branch"
    assert entry["install"]["commit"] == entry["commit"] and entry["install"]["git"] == entry["source"]
    assert entry["module_roots"], "a component needs module_roots"
    assert entry["level"] in ("listed", "installable", "tested")


def test_module_roots_are_what_the_build_ships_not_what_the_repository_is_called(tmp_path):
    artistoo = ce.derive(_workspace(tmp_path, **ARTISTOO))
    assert artistoo.entry["module_roots"] == ["pbg_artistoo"]  # the registry says viva_artistoo
    cpm = ce.derive(_workspace(tmp_path, "cpm", ["viva_cpm", "viva_cpm_studies"]))
    assert cpm.entry["module_roots"] == ["viva_cpm", "viva_cpm_studies"]  # viva_cpm is the maturin module-name


def test_a_package_named_app_is_never_a_module_root(tmp_path):
    derived = ce.derive(_workspace(tmp_path, "pennylane-adversarial", ["app", "pbg_pennylane_adversarial"]))
    assert derived.entry["module_roots"] == ["pbg_pennylane_adversarial"]
    assert "generic-module-root" in _codes(derived, "warn")


def test_what_only_a_person_can_decide_is_said_not_guessed(tmp_path):
    artistoo = ce.derive(_workspace(tmp_path, **ARTISTOO))
    assert {"non-python-parts", "dev-tool-dependency"} <= set(_codes(artistoo, "warn"))
    assert "node_bridge" in next(f.message for f in artistoo.findings if f.code == "non-python-parts")
    assert "vivarium-workbench" in next(f.message for f in artistoo.findings if f.code == "dev-tool-dependency")
    assert "compiled-build" in _codes(ce.derive(_workspace(tmp_path, "cpm", ["viva_cpm", "viva_cpm_studies"])), "warn")
    pde = ce.derive(_workspace(tmp_path, "pde-particle", ["viva_pde_particle"]))
    assert "pixi-toml" in _codes(pde, "warn") and "conda" not in pde.entry, "conda: is for a person to fill in"




# --- what compose-api could never run ----------------------------------------------------------------------------


def test_a_dirty_tree_is_refused_because_the_entry_pins_head(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    (ws / "pyproject.toml").write_text((ws / "pyproject.toml").read_text() + "\n# edited\n")
    derived = ce.derive(ws)
    assert derived.entry is None and "dirty-tree" in _codes(derived, "error")


def test_an_untracked_file_does_not_make_the_tree_dirty(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    (ws / "notes.txt").write_text("scratch")
    assert ce.derive(ws).entry is not None


def test_a_head_that_is_on_no_remote_branch_is_refused(tmp_path):
    derived = ce.derive(_workspace(tmp_path, push=False, **KETCHUP))
    assert derived.entry is None and "unpushed-head" in _codes(derived, "error")


def test_a_pin_off_the_default_branch_is_allowed_with_a_warning(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    _git(ws, "checkout", "-q", "-b", "feature")
    (ws / "more.txt").write_text("a commit only the feature branch has")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "feature work")
    _git(ws, "push", "-q", "origin", "feature")
    derived = ce.derive(ws)
    assert derived.entry is not None and "not-default-branch" in _codes(derived, "warn")


def test_a_package_the_build_ships_but_the_checkout_lacks_is_refused(tmp_path):
    derived = ce.derive(_workspace(tmp_path, "ketchup", ["viva_ketchup"]))  # pbg_ketchup is missing
    assert derived.entry is None and "package-missing" in _codes(derived, "error")


def test_an_origin_that_is_not_github_is_refused(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    _git(ws, "remote", "set-url", "origin", "https://gitlab.example.org/group/viva-ketchup.git")
    assert "unsupported-origin" in _codes(ce.derive(ws), "error")


def test_a_directory_with_no_workspace_yaml_is_not_a_workspace(tmp_path):
    assert "not-a-workspace" in _codes(ce.derive(tmp_path), "error")


@pytest.mark.parametrize("remote", [
    "git@github.com:vivarium-collective/viva-ketchup.git",
    "https://github.com/vivarium-collective/viva-ketchup",
    "https://github.com/vivarium-collective/viva-ketchup.git/",
    "https://user:secret@github.com/vivarium-collective/viva-ketchup.git",
    "ssh://git@github.com/vivarium-collective/viva-ketchup.git",
])
def test_every_spelling_of_a_github_remote_names_the_same_repository(remote):
    assert ce.github_repo(remote) == ("vivarium-collective", "viva-ketchup")
    assert ce.canonical_source(remote) == "github.com/vivarium-collective/viva-ketchup"


def test_a_registry_source_that_links_its_commit_names_the_same_repository():
    pinned = "https://github.com/vivarium-collective/viva-basic-processes/tree/9a1a624dc925cf700c3f89a8cc008a8fa4b4e5d4"
    assert ce.canonical_source(pinned) == "github.com/vivarium-collective/viva-basic-processes"


# --- checking an existing entry, against production's registry ----------------------------------------------------


@pytest.fixture(scope="module")
def registry() -> list[dict]:
    return ce.load_registry(str(REGISTRY))


def test_an_entry_whose_roots_share_nothing_with_the_package_is_an_error(tmp_path, registry):
    derived = ce.derive(_workspace(tmp_path, **ARTISTOO), registry=registry)
    wrong = next(f for f in ce.check(derived, registry) if f.code == "module-roots-wrong")
    assert wrong.severity == "error" and "viva_artistoo" in wrong.message and "pbg_artistoo" in wrong.message


def test_a_case_difference_in_a_module_root_is_wrong_too(tmp_path, registry):
    derived = ce.derive(_workspace(tmp_path, "mgen", ["viva_mgen"]), registry=registry)
    assert "module-roots-wrong" in [f.code for f in ce.check(derived, registry)]  # viva_Mgen is not viva_mgen
    assert derived.entry["id"] == "viva-Mgen", "the id the registry already uses, so the entry replaces the catalog's"
    assert "id-from-registry" in _codes(derived)


@pytest.mark.parametrize("workspace, omitted", [
    (KETCHUP, "pbg_ketchup"),
    ({"name": "cpm", "packages": ["viva_cpm", "viva_cpm_studies"]}, "viva_cpm_studies"),
])
def test_an_entry_that_omits_a_package_the_workspace_also_ships_is_a_warning(tmp_path, registry, workspace, omitted):
    derived = ce.derive(_workspace(tmp_path, **workspace), registry=registry)
    found = {f.code: f for f in ce.check(derived, registry)}
    assert found["module-roots-incomplete"].severity == "warn" and omitted in found["module-roots-incomplete"].message
    assert "module-roots-wrong" not in found


def test_a_correct_entry_is_reported_correct_with_where_its_pin_differs(tmp_path, registry):
    derived = ce.derive(_workspace(tmp_path, "pde-particle", ["viva_pde_particle"]), registry=registry)
    codes = {f.code: f for f in ce.check(derived, registry)}
    assert not {"module-roots-wrong", "module-roots-incomplete", "module-roots-extra"} & set(codes)
    assert "tested" in codes["registry-level"].message and "70438826" in codes["commit-differs"].message


def test_a_repository_the_registry_does_not_have_is_new(tmp_path, registry):
    ws = _workspace(tmp_path, remote_repo="viva-brand-new", **KETCHUP)
    assert [f.code for f in ce.check(ce.derive(ws, registry=registry), registry)] == ["no-entry"]


def test_the_registry_is_read_from_a_json_list_or_a_manifest(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({"entries": [{"id": "x", "source": "https://github.com/o/x"}]}))
    assert ce.load_registry(str(manifest))[0]["id"] == "x"
    assert len(ce.load_registry(str(REGISTRY))) == 62


# --- the command ----------------------------------------------------------------------------------------------------


def test_the_command_prints_the_entry_as_a_list_item_to_paste_under_entries(tmp_path, capsys):
    ws = _workspace(tmp_path, **KETCHUP)
    assert cli.main(["compose-entry", "--workspace", str(ws)]) == 0
    out = capsys.readouterr()
    (entry,) = yaml.safe_load("entries:\n" + out.out)["entries"]
    assert entry == ce.derive(ws).entry and out.out.startswith("  - id: viva-ketchup\n")


def test_the_command_prints_no_entry_and_exits_1_when_it_refuses(tmp_path, capsys):
    ws = _workspace(tmp_path, push=False, **KETCHUP)
    assert cli.main(["compose-entry", "--workspace", str(ws)]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "error: unpushed-head:" in out.err


def test_the_command_checks_an_entry_against_a_registry_and_fails_on_wrong_roots(tmp_path, capsys):
    ws = _workspace(tmp_path, **ARTISTOO)
    assert cli.main(["compose-entry", "--workspace", str(ws), "--check", "--registry", str(REGISTRY)]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "error: module-roots-wrong:" in out.err


def test_the_command_json_carries_the_entry_and_every_finding(tmp_path, capsys):
    ws = _workspace(tmp_path, **ARTISTOO)
    assert cli.main(["compose-entry", "--workspace", str(ws), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entry"]["module_roots"] == ["pbg_artistoo"] == payload["module_roots"]
    assert {"non-python-parts", "dev-tool-dependency"} <= {f["code"] for f in payload["findings"]}


def test_the_command_refuses_arguments_it_cannot_use(tmp_path, capsys):
    ws = _workspace(tmp_path, **KETCHUP)
    assert cli.main(["compose-entry", "--workspace", str(ws), "--check"]) == 2
    assert "--check needs --registry" in capsys.readouterr().err
    assert cli.main(["compose-entry", "--workspace", str(tmp_path / "nowhere")]) == 2
    assert cli.main(["compose-entry", "--workspace", str(ws), "--registry", str(tmp_path / "missing.json")]) == 2


# --- what the workspace declares beyond the entry's fields ------------------------------------------------------------


def test_the_extras_a_workspace_declares_are_reported_and_none_is_added(tmp_path):
    derived = ce.derive(_workspace(tmp_path, "pde-particle", ["viva_pde_particle"]))
    said = next(f for f in derived.findings if f.code == "extras-available")
    assert said.severity == "info" and "reference" in said.message
    assert "extras" not in derived.entry["install"], "which extras a run needs is for a person to say"


def test_a_dependency_that_is_itself_a_registry_entry_is_reported_when_a_registry_is_given(tmp_path, registry):
    ws = _workspace(tmp_path, "mgen", ["viva_mgen"])
    found = [f for f in ce.derive(ws, registry=registry).findings if f.code == "registry-dependency"]
    assert [f.severity for f in found] == ["warn"], "process-bigraph is the engine's core entry and is not reported"
    assert "viva-parsimony" in found[0].message and "listed" in found[0].message
    assert [f for f in ce.derive(ws).findings if f.code == "registry-dependency"] == [], "without a registry there is nothing to match"


# --- what the adversarial review found ---------------------------------------------------------------------------------
#
# Most of these need a shape no real workspace has (a repository inside a repository, a shallow clone, a corrupt index), so
# they are built from the smallest workspace there can be (``_synthetic``) or from a real one with its git state changed.

GITHUB_KETCHUP = "https://github.com/vivarium-collective/viva-ketchup.git"


def test_a_package_directory_that_is_not_in_the_commit_is_not_a_module_root(tmp_path):
    ws = _workspace(tmp_path, "ketchup", ["viva_ketchup"])  # pbg_ketchup is not committed ...
    (ws / "pbg_ketchup").mkdir()
    (ws / "pbg_ketchup" / "__init__.py").write_text("")  # ... but is on disk, untracked, so the pinned commit lacks it
    derived = ce.derive(ws)
    assert derived.entry is None and "package-missing" in _codes(derived, "error")


def test_a_tree_whose_status_git_cannot_read_is_not_called_clean(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    (ws / ".git" / "index").write_bytes(b"not an index")
    (ws / "pyproject.toml").write_text((ws / "pyproject.toml").read_text() + "\n# edited\n")
    derived = ce.derive(ws)
    assert derived.entry is None and "git-failed" in _codes(derived, "error")


def test_a_commit_that_is_only_on_another_remote_is_not_pushed(tmp_path):
    ws = _workspace(tmp_path, push=False, **KETCHUP)
    fork = tmp_path / "fork.git"
    subprocess.run([*GIT, "init", "-q", "--bare", str(fork)], check=True)
    _git(ws, "remote", "add", "fork", str(fork))
    _git(ws, "push", "-q", "fork", "HEAD:refs/heads/mine")
    derived = ce.derive(ws)
    assert derived.entry is None and "unpushed-head" in _codes(derived, "error")


def test_a_workspace_inside_a_repository_is_refused_because_the_pin_is_the_whole_repository(tmp_path):
    mono = tmp_path / "mono"
    ws = mono / "sub"
    shutil.copytree(FIX / "ketchup", ws)
    for package in KETCHUP["packages"]:
        (ws / package).mkdir()
        (ws / package / "__init__.py").write_text("")
    _checkout(mono, tmp_path / "mono.git", GITHUB_KETCHUP)
    derived = ce.derive(ws)
    assert derived.entry is None and "not-repo-root" in _codes(derived, "error")


def test_a_shallow_clone_that_cannot_see_how_far_the_remote_moved_says_so(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    bare = tmp_path / "ketchup.git"
    clone = tmp_path / "clone"
    subprocess.run([*GIT, "clone", "-q", "--depth", "1", f"file://{bare}", str(clone)], check=True)
    (ws / "more.txt").write_text("the remote moves on")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "more")
    _git(ws, "push", "-q", "origin", "main")
    _git(clone, "fetch", "-q", "--depth", "1", "origin")  # only the new tip: git cannot connect it to the older HEAD
    _git(clone, "remote", "set-url", "origin", GITHUB_KETCHUP)
    unpushed = next(f for f in ce.derive(clone).findings if f.code == "unpushed-head")
    assert unpushed.severity == "error" and "shallow" in unpushed.message


def test_credentials_in_an_origin_are_never_printed(tmp_path, capsys):
    ws = _workspace(tmp_path, **KETCHUP)
    _git(ws, "remote", "set-url", "origin", "https://user:s3cr3t@gitlab.example.org/g/viva-ketchup.git")
    assert "s3cr3t" not in " ".join(f.message for f in ce.derive(ws).findings)
    assert cli.main(["compose-entry", "--workspace", str(ws), "--json"]) == 1
    assert "s3cr3t" not in capsys.readouterr().out


def test_the_command_does_not_rewrite_the_index(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    later = time.time() + 60
    os.utime(ws / "pyproject.toml", (later, later))  # its stat data no longer matches the index, which git status would refresh
    before = (ws / ".git" / "index").read_bytes()
    ce.derive(ws)
    assert (ws / ".git" / "index").read_bytes() == before, "a command that only reads must not write the repository"


def test_without_git_the_command_says_so(tmp_path, monkeypatch):
    ws = _workspace(tmp_path, **KETCHUP)
    monkeypatch.setattr(ce.shutil, "which", lambda name: None)
    assert "git-missing" in _codes(ce.derive(ws), "error")


def test_a_checkout_with_no_origin_is_refused(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    _git(ws, "remote", "remove", "origin")
    assert "no-origin" in _codes(ce.derive(ws), "error")


def test_an_unknown_default_branch_is_a_warning_and_not_a_guess(tmp_path):
    derived = ce.derive(_workspace(tmp_path, branch="trunk", set_head=False, **KETCHUP))
    assert derived.entry is not None and "default-branch-unknown" in _codes(derived, "warn")


def test_a_detached_head_that_is_pushed_gets_an_entry(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    _git(ws, "checkout", "-q", "--detach")
    assert ce.derive(ws).entry["commit"] == _git(ws, "rev-parse", "HEAD")


# the build's packages

def test_a_dotted_package_pattern_means_its_top_level_package(tmp_path):
    ws = _synthetic(tmp_path, {"pyproject.toml": PYPROJECT.replace('packages = ["viva_s"]', 'packages = ["viva_s", "viva_s.*"]')})
    derived = ce.derive(ws)
    assert derived.entry["module_roots"] == ["viva_s"] and "package-missing" not in _codes(derived)


SETUPTOOLS = (PYPROJECT.replace('[tool.hatch.build.targets.wheel]\npackages = ["viva_s"]',
                                '[tool.setuptools.packages.find]\ninclude = ["INCLUDE"]')
              .replace("hatchling.build", "setuptools.build_meta").replace('requires = ["hatchling"]', 'requires = ["setuptools"]'))


def test_a_star_pattern_expands_to_every_package_it_matches(tmp_path):
    ws = _synthetic(tmp_path, {"pyproject.toml": SETUPTOOLS.replace("INCLUDE", "viva_s*"), "viva_s_extra/__init__.py": ""})
    assert ce.derive(ws).entry["module_roots"] == ["viva_s", "viva_s_extra"]


def test_a_star_pattern_that_matches_nothing_is_a_missing_package(tmp_path):
    ws = _synthetic(tmp_path, {"pyproject.toml": SETUPTOOLS.replace("INCLUDE", "app*")})
    derived = ce.derive(ws)
    assert derived.entry is None and "package-missing" in _codes(derived, "error")


def test_a_maturin_python_source_directory_holds_the_module(tmp_path):
    pyproject = ('[build-system]\nrequires = ["maturin"]\nbuild-backend = "maturin"\n[project]\nname = "viva-synth"\nversion = "0"\n'
                 '[tool.maturin]\nmodule-name = "viva_m.core"\npython-source = "python"\n')
    ws = _synthetic(tmp_path, {"pyproject.toml": pyproject, "workspace.yaml": "name: s\npackage_path: viva_m\n",
                               "python/viva_m/__init__.py": ""}, omit=("viva_s/__init__.py",))
    derived = ce.derive(ws)
    assert derived.entry["module_roots"] == ["viva_m"] and "compiled-build" in _codes(derived, "warn")


def test_a_src_layout_is_read(tmp_path):
    ws = _synthetic(tmp_path, {"pyproject.toml": PYPROJECT.replace('packages = ["viva_s"]', 'packages = ["src/viva_s"]'),
                               "src/viva_s/__init__.py": ""}, omit=("viva_s/__init__.py",))
    assert ce.derive(ws).entry["module_roots"] == ["viva_s"]


def test_a_package_path_that_is_not_a_package_name_is_left_out_with_a_warning(tmp_path):
    derived = ce.derive(_synthetic(tmp_path, {"workspace.yaml": "name: s\npackage_path: ..\n"}))
    assert derived.entry["module_roots"] == ["viva_s"] and "bad-package-name" in _codes(derived, "warn")


def test_a_workspace_without_a_package_path_still_gets_the_packages_its_build_ships(tmp_path):
    assert ce.derive(_synthetic(tmp_path, {"workspace.yaml": "name: s\n"})).entry["module_roots"] == ["viva_s"]


def test_a_pyproject_with_no_project_table_is_refused(tmp_path):
    ws = _synthetic(tmp_path, {"pyproject.toml": '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'})
    derived = ce.derive(ws)
    assert derived.entry is None and "no-package-name" in _codes(derived, "error")


def test_a_workspace_without_a_pyproject_is_refused(tmp_path):
    ws = _workspace(tmp_path, **KETCHUP)
    (ws / "pyproject.toml").unlink()
    assert "no-pyproject" in _codes(ce.derive(ws), "error")


def test_the_core_taken_from_git_is_reported(tmp_path):
    pyproject = PYPROJECT + '\n[tool.uv.sources]\nprocess-bigraph = { git = "https://github.com/vivarium-collective/process-bigraph.git" }\n'
    assert "core-from-git" in _codes(ce.derive(_synthetic(tmp_path, {"pyproject.toml": pyproject})), "warn")


# files that cannot be read

@pytest.mark.parametrize("content", [b": : :\n  - [", b"- just\n- a list\n", b"\xff\xfe\x00 not utf-8"])
def test_a_workspace_yaml_that_cannot_be_read_is_a_finding_and_not_a_traceback(tmp_path, content):
    ws = _workspace(tmp_path, **KETCHUP)
    (ws / "workspace.yaml").write_bytes(content)
    assert "bad-workspace-yaml" in _codes(ce.derive(ws), "error")


@pytest.mark.parametrize("name, text", [("empty.yaml", ""), ("a-list-of-numbers.yaml", "- 1\n- 2\n"), ("broken.yaml", ": : [\n"),
                                        ("no-entries.yaml", "foo: 1\n")])
def test_a_registry_file_that_is_not_a_list_of_entries_is_a_value_error(tmp_path, name, text):
    (tmp_path / name).write_text(text)
    with pytest.raises(ValueError):
        ce.load_registry(str(tmp_path / name))


def test_a_broken_registry_file_is_a_usage_error_for_the_command(tmp_path, capsys):
    ws = _workspace(tmp_path, **KETCHUP)
    (tmp_path / "broken.yaml").write_text(": : [\n")
    assert cli.main(["compose-entry", "--workspace", str(ws), "--registry", str(tmp_path / "broken.yaml")]) == 2
    assert "cannot read the registry" in capsys.readouterr().err


# the registry's own entry

def test_a_curated_registry_entry_is_not_silently_replaced(tmp_path, registry):
    derived = ce.derive(_workspace(tmp_path, "pde-particle", ["viva_pde_particle"]), registry=registry)
    assert derived.entry is None and "curated-entry-exists" in _codes(derived, "error")
    # an entry the catalog sweep generated has nothing curated in it: that is what a generated entry replaces
    assert ce.derive(_workspace(tmp_path / "k", **KETCHUP), registry=registry).entry is not None


def test_two_registry_entries_for_one_repository_are_reported(tmp_path, registry):
    twin = {**next(e for e in registry if e["id"] == "viva-ketchup"), "id": "viva-ketchup-twin"}
    derived = ce.derive(_workspace(tmp_path, **KETCHUP), registry=[*registry, twin])
    assert "duplicate-registry-entries" in _codes(derived, "warn")


def test_an_entry_with_another_package_name_or_roots_the_workspace_lacks_is_reported(tmp_path, registry):
    entry = {**next(e for e in registry if e["id"] == "viva-ketchup"), "package": "ketchup-wrapper",
             "module_roots": ["viva_ketchup", "pbg_ketchup", "viva_gone"]}
    found = {f.code for f in ce.check(ce.derive(_workspace(tmp_path, **KETCHUP), registry=[entry]), [entry])}
    assert {"package-differs", "module-roots-extra"} <= found


# evidence, and what the command prints

def test_a_level_above_listed_without_evidence_is_refused(tmp_path):
    derived = ce.derive(_workspace(tmp_path, **KETCHUP), level="tested")
    assert derived.entry is None and "evidence-needed" in _codes(derived, "error")


def test_evidence_given_is_written_into_the_entry(tmp_path):
    derived = ce.derive(_workspace(tmp_path, **KETCHUP), level="tested", evidence=["Added at tested on 2026-10-30."])
    assert derived.entry["level"] == "tested" and derived.entry["evidence"] == ["Added at tested on 2026-10-30."]


def test_the_command_prints_the_entry_indented_to_paste_under_entries_with_its_evidence(tmp_path, capsys):
    ws = _workspace(tmp_path, **KETCHUP)
    args = ["compose-entry", "--workspace", str(ws), "--level", "tested", "--evidence", "checked on 2026-10-30"]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert out.startswith("  - id: viva-ketchup\n")
    (entry,) = yaml.safe_load("entries:\n" + out)["entries"]
    assert (entry["level"], entry["evidence"]) == ("tested", ["checked on 2026-10-30"])


def test_the_command_refuses_level_tested_without_evidence(tmp_path, capsys):
    ws = _workspace(tmp_path, **KETCHUP)
    assert cli.main(["compose-entry", "--workspace", str(ws), "--level", "tested"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "error: evidence-needed:" in out.err


def test_check_does_not_fail_on_the_state_of_the_checkout(tmp_path, capsys):
    """--check compares the registry's entry with the workspace; a CI checkout is rarely on a branch the remote has."""
    ws = _workspace(tmp_path, "pde-particle", ["viva_pde_particle"], push=False)
    (ws / "pyproject.toml").write_text((ws / "pyproject.toml").read_text() + "\n# edited\n")
    assert cli.main(["compose-entry", "--workspace", str(ws), "--check", "--registry", str(REGISTRY)]) == 0
    err = capsys.readouterr().err
    assert "warn: unpushed-head:" in err and "warn: dirty-tree:" in err and "error:" not in err


def test_an_unknown_level_is_refused(tmp_path):
    derived = ce.derive(_workspace(tmp_path, **KETCHUP), level="curated")
    assert derived.entry is None and "bad-level" in _codes(derived, "error")


@pytest.mark.parametrize("source, expected", [
    ("git@github.com:tree/r.git", "github.com/tree/r"),  # an owner named tree
    ("https://github.com/o/tree/", "github.com/o/tree"),  # a repository named tree
    ("https://github.com/o/tree/tree/9a1a624dc925cf700c3f89a8cc008a8fa4b4e5d4", "github.com/o/tree"),
    ("https://github.com/o/r/tree/feature/x", "github.com/o/r"),  # a branch name with a slash
])
def test_a_name_that_is_tree_is_a_name_and_not_a_link_suffix(source, expected):
    assert ce.canonical_source(source) == expected
