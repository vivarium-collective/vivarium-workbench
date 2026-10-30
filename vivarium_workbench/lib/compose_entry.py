"""``vivarium-workbench compose-entry``: the compose-api registry entry for this workspace, read from the workspace.

compose-api runs only the repositories its registry lists (``compose_api/registry/manifest.yaml``, changed by pull
request). Typing an entry by hand is where it goes wrong: of the 33 viva-* workspaces that had a catalog entry on
2026-10-30, 18 listed module roots that share nothing with the package the workspace ships, so the service would have
refused the workspace's own classes. This derives the entry from the workspace's files and from the commit its git state
pins, refuses what compose-api could never run (a dirty or unpushed HEAD), and says what only a person can decide. With
a registry (``--registry``) it also checks an existing entry against the workspace.

Pure functions over a workspace directory and an optional list of registry entries. It only reads: git runs with
``GIT_OPTIONAL_LOCKS=0`` so that even ``git status`` leaves the repository as it was. The only network use is
:func:`load_registry` given a URL.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import tomllib
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: Development tooling, not what a run needs: a composed environment installs every runtime dependency.
DEV_TOOLS = ("vivarium-workbench", "vivarium-dashboard", "viva-superpowers", "pbg-superpowers")
#: Top-level names that must never become a module root: they would claim every import of that name.
GENERIC_ROOTS = frozenset({"app", "src", "tests", "test", "utils", "common", "core", "lib", "main", "scripts", "docs"})
#: Directories in a package that hold something PyPI cannot supply.
NON_PYTHON_DIRS = frozenset({"node_bridge", "cxx", "cpp", "src_cpp", "java", "jar", "native"})
#: compose-api composes every environment on a pinned release of these.
CORE_PACKAGES = ("process-bigraph", "bigraph-schema")
LEVELS = ("listed", "installable", "tested")
_FULL_COMMIT = re.compile(r"[0-9a-fA-F]{40}")

_REMOTE = re.compile(
    r"^(?:git@github\.com:|(?:https?|ssh|git)://(?:[^@/]+@)?github\.com/)(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$",
    re.IGNORECASE)
_PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_*?]*")  # a top-level package name, or a glob over them
#: How the catalog sweep words the evidence of the entries it generates (``compose_api/registry/catalog.py``).
_CATALOG_EVIDENCE = "Discovered in viva-catalog"


@dataclass(frozen=True)
class Finding:
    """``error``: no entry can be derived, or the registry disagrees with the workspace; ``warn``: a person decides;
    ``info``: how the entry was filled in."""

    severity: str
    code: str
    message: str


@dataclass
class Derived:
    entry: "dict[str, Any] | None"  # None when an error finding makes an entry impossible
    roots: list[str]                # the module roots the workspace ships (generic names left out)
    findings: list[Finding]
    repo: "tuple[str, str] | None"  # (owner, repo) of the GitHub origin
    head: "str | None"
    package: "str | None"           # the distribution name, ``[project].name``


# -- git ------------------------------------------------------------------------------------------------------------


def _git(ws: Path, *args: str) -> "str | None":
    """git's stdout, stripped; None when git fails. ``GIT_OPTIONAL_LOCKS=0`` stops a read from refreshing the index."""
    try:
        out = subprocess.run(["git", "-C", str(ws), *args], capture_output=True, encoding="utf-8", errors="replace",
                             timeout=30, check=True, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def _git_ok(ws: Path, *args: str) -> bool:
    try:
        return subprocess.run(["git", "-C", str(ws), *args], capture_output=True, timeout=30,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _redacted(text: str) -> str:
    """``text`` without the credentials of any URL in it."""
    return re.sub(r"(?<=//)[^/@\s]+@", "", text)


def github_repo(remote: str) -> "tuple[str, str] | None":
    """``(owner, repo)`` for any spelling of a GitHub remote (https, ssh, with credentials, ``.git``)."""
    m = _REMOTE.match(remote.strip())
    return (m["owner"], m["repo"]) if m else None


def canonical_source(url: "str | None") -> str:
    """``github.com/owner/repo`` (lower case) for a remote or a registry entry's ``source``, which links the commit
    (``…/repo/tree/<commit>``) when the entry is pinned by one. An owner or a repository named ``tree`` is a name."""
    text = re.sub(r"^git@([^:]+):", r"\1/", str(url or "").strip())
    text = re.sub(r"^[a-z+]+://", "", text)
    text = re.sub(r"^[^@/]+@", "", text)
    text = re.sub(r"^([^/]+/[^/]+/[^/]+)/tree/.*$", r"\1", text)
    return text.removesuffix("/").removesuffix(".git").lower()


def _default_branch(ws: Path) -> "str | None":
    head = _git(ws, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head:
        return head
    return next((b for b in ("origin/main", "origin/master")
                 if _git_ok(ws, "rev-parse", "--verify", "--quiet", f"refs/remotes/{b}")), None)


def _tree(ws: Path, path: str) -> "list[tuple[str, str]] | None":
    """``(type, name)`` of each entry of the directory ``path`` in the commit HEAD (``""`` is its root); None when the
    commit has no such directory. What is on disk, untracked or ignored, is not what the entry pins."""
    out = _git(ws, "ls-tree", "-z", f"HEAD:{path}" if path else "HEAD")
    if out is None:
        return None
    entries = []
    for record in out.split("\0"):
        meta, _, name = record.partition("\t")
        if name:
            entries.append((meta.split()[1], name))
    return entries


# -- the workspace's own files ----------------------------------------------------------------------------------------


def shipped_packages(pyproject: dict, package_path: str) -> list[str]:
    """Top-level package names (or ``*`` patterns over them) the build ships: ``workspace.yaml``'s ``package_path`` plus
    what the build backend lists (hatch ``packages``, setuptools ``find``, a maturin ``module-name`` or ``python-packages``).
    A dotted name is cut to its top level, since a module root claims everything beneath it."""
    tool = pyproject.get("tool", {})
    raw = [package_path]
    wheel = tool.get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {}).get("packages")
    if isinstance(wheel, list):
        raw += [str(p).removeprefix("src/") for p in wheel]
    find = tool.get("setuptools", {}).get("packages", {})
    if isinstance(find, dict):
        raw += [str(i) for i in find.get("find", {}).get("include", [])]
    elif isinstance(find, list):
        raw += [str(i) for i in find]
    maturin = tool.get("maturin", {})
    if maturin.get("module-name"):
        raw.append(str(maturin["module-name"]))
    raw += [str(p) for p in maturin.get("python-packages", [])]
    return sorted({n for n in (r.split("/")[0].split(".")[0] for r in raw if r) if n})


def _source_dirs(pyproject: dict) -> list[str]:
    """Where a package directory may sit in the commit: the root, ``src/``, and a maturin ``python-source``."""
    source = str(pyproject.get("tool", {}).get("maturin", {}).get("python-source") or "")
    return ["", "src", *([source.strip("/")] if source.strip("./") else [])]


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    return _normalized(re.split(r"[\[<>=!~ ;@(]", requirement.strip(), maxsplit=1)[0])


def _is_catalog_entry(entry: dict) -> bool:
    """An entry the catalog sweep generated (level listed, in no bundle, evidence only its discovery): nothing curated
    is lost when a generated entry takes its id."""
    return (entry.get("level") == "listed" and not entry.get("bundles")
            and all(str(e).startswith(_CATALOG_EVIDENCE) for e in entry.get("evidence") or []))


# -- deriving the entry -----------------------------------------------------------------------------------------------


def derive(ws: Path, *, level: str = "listed", registry: "list[dict] | None" = None, evidence: Sequence[str] = (),
           check_only: bool = False) -> Derived:
    """The registry entry for the workspace at ``ws``, and what stands in its way or needs a person. With ``check_only``
    the state of the checkout (dirty, unpushed) is a warning, since only the comparison with the registry is wanted."""
    findings: list[Finding] = []

    def say(severity: str, code: str, message: str) -> None:
        findings.append(Finding(severity, code, message))

    def refused() -> Derived:
        return Derived(None, [], findings, None, None, None)

    pin = "warn" if check_only else "error"
    if level not in LEVELS:
        say("error", "bad-level", f"level must be one of {', '.join(LEVELS)}, not {level!r}")
    if shutil.which("git") is None:
        say("error", "git-missing", "git is not installed or not on PATH")
        return refused()
    try:
        workspace = yaml.safe_load((ws / "workspace.yaml").read_text(encoding="utf-8"))
    except OSError:
        say("error", "not-a-workspace", f"no workspace.yaml in {ws}")
        return refused()
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        say("error", "bad-workspace-yaml", f"workspace.yaml cannot be read: {e}")
        return refused()
    if not isinstance(workspace, dict):
        say("error", "bad-workspace-yaml", "workspace.yaml is not a mapping")
        return refused()
    try:
        pyproject = tomllib.loads((ws / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        say("error", "no-pyproject", f"pyproject.toml is missing or unreadable: {e}")
        return refused()
    project = pyproject.get("project", {})
    package = project.get("name")
    if not package:
        say("error", "no-package-name", "pyproject.toml has no [project].name")

    # -- git: the pin is a commit compose-api can fetch, and a reviewer can read
    top = _git(ws, "rev-parse", "--show-toplevel")
    remote = _git(ws, "config", "--get", "remote.origin.url")
    repo = github_repo(remote) if remote else None
    head = _git(ws, "rev-parse", "HEAD")
    if top is None:
        say("error", "not-a-git-repo", "the workspace is not a git checkout")
    elif Path(top).resolve() != ws.resolve():
        say("error", "not-repo-root", f"the workspace is inside the git repository at {top}; the entry pins that whole "
                                      "repository, which compose-api installs from its root, so run this at its root")
    if package and repo and _normalized(package) != _normalized(repo[1]):
        say("warn" if level == "listed" else "error", "distribution-name-differs",
            f"[project].name {package!r} differs from the repository name {repo[1]!r}: compose-api installs a git source under the "
            "repository's name, and uv fails the lock when the package's metadata names another ('Package metadata name ... does "
            "not match given name ...'). Rename the distribution to match, or pin a commit where they agree")
    if not remote:
        say("error", "no-origin", "the workspace has no git remote named origin to pin")
    elif repo is None:
        say("error", "unsupported-origin", f"origin {_redacted(remote)!r} is not a GitHub repository; compose-api installs "
                                           "from a public git URL, so add the entry by hand")
    if not head:
        say("error", "no-commit", "the workspace has no commit to pin")
    elif remote and top is not None:
        status = _git(ws, "status", "--porcelain", "--untracked-files=no")
        if status is None:
            say("error", "git-failed", "git status failed (a damaged index or repository?), so the tree cannot be called clean")
        elif status:
            say(pin, "dirty-tree", "tracked files have uncommitted changes: commit them, because the entry pins HEAD")
        on_origin = [b for b in (_git(ws, "branch", "-r", "--contains", head, "--list", "origin/*") or "").splitlines()
                     if "->" not in b]
        if not on_origin:
            shallow = _git(ws, "rev-parse", "--is-shallow-repository") == "true"
            say(pin, "unpushed-head", f"HEAD {head[:8]} is on no origin branch git can see: push it (git fetch first if it "
                                      "is already pushed), because compose-api fetches the pin from the remote"
                                      + (" This is a shallow clone, which cannot see how far the remote has moved: "
                                         "git fetch --unshallow" if shallow else ""))
        else:
            default = _default_branch(ws)
            if default is None:
                say("warn", "default-branch-unknown", "origin's default branch is not known here (git fetch origin); "
                                                       "pin a commit on it, because that is what a reviewer reads")
            elif not _git_ok(ws, "merge-base", "--is-ancestor", head, default):
                say("warn", "not-default-branch", f"HEAD {head[:8]} is not on {default}: pin a commit that is, or the "
                                                  "pin may disappear with its branch")

    # -- module roots: what the commit's build ships, not what the repository is called nor what is on disk
    package_path = str(workspace.get("package_path") or "")
    if package_path and not _PACKAGE.fullmatch(package_path):
        say("warn", "bad-package-name", f"workspace.yaml's package_path {package_path!r} is not a package name and is ignored")
        package_path = ""
    bases = _source_dirs(pyproject)
    top_level = sorted({n for b in bases for t, n in (_tree(ws, b) or []) if t == "tree"})
    roots: list[str] = []
    for pattern in shipped_packages(pyproject, package_path):
        if not _PACKAGE.fullmatch(pattern):
            say("warn", "bad-package-name", f"the build lists {pattern!r}, which is not a package name, and it is ignored")
            continue
        names = fnmatch.filter(top_level, pattern) if re.search(r"[*?]", pattern) else [pattern]
        if not names:
            say("error", "package-missing", f"the build ships {pattern!r} but no such package directory is in the commit")
        for name in names:
            contents = next((c for b in bases if (c := _tree(ws, f"{b}/{name}" if b else name)) is not None), None)
            if contents is None:
                say("error", "package-missing", f"the build ships package {name!r} but no such directory is in the commit")
            elif name in GENERIC_ROOTS:
                say("warn", "generic-module-root", f"package {name!r} is left out of module_roots: a generic name would "
                                                   "claim every import of it")
            else:
                roots.append(name)
                assets = sorted(n for t, n in contents if t == "tree" and n in NON_PYTHON_DIRS)
                if assets:
                    say("warn", "non-python-parts", f"{name} contains {', '.join(assets)}, which PyPI cannot supply: add "
                                                    "what it runs (for example nodejs) under conda:")
    roots = sorted(set(roots))
    if not roots and not any(f.severity == "error" for f in findings):
        say("error", "no-module-roots", "no package that holds the workspace's classes was found")

    # -- what a composed environment would install
    declared = {_requirement_name(r) for r in project.get("dependencies", [])}
    leaked = [d for d in DEV_TOOLS if d in declared]
    if leaked:
        say("warn", "dev-tool-dependency", f"{', '.join(leaked)} is a runtime dependency, and a composed environment installs "
                                           "it with everything it needs, including version bounds the composition's pinned core "
                                           "may not meet (the lock then fails): move it to an optional extra")
    extras = sorted(project.get("optional-dependencies", {}))
    if extras:
        say("info", "extras-available", f"pyproject.toml declares extras ({', '.join(extras)}); none is added to install.extras, "
                                        "which lists only the ones a run needs")
    by_package = {_normalized(e.get("package", "")): e for e in registry or []}
    others = declared - set(DEV_TOOLS) - set(CORE_PACKAGES) - {_normalized(package or "")}
    for name in sorted(others & set(by_package)):
        registered = by_package[name]
        say("warn" if registered.get("level") == "listed" else "info", "registry-dependency",
            f"depends on {name}, registry entry {registered.get('id')!r} at level {registered.get('level')}: if it is not on "
            "PyPI, it must be a manifest.yaml entry first and be listed under requires:")
    sources = pyproject.get("tool", {}).get("uv", {}).get("sources", {})
    for core in CORE_PACKAGES:
        if "git" in sources.get(core, {}):
            say("warn", "core-from-git", f"{core} comes from git in [tool.uv.sources]; compose-api builds on its pinned "
                                         "release, so check the workspace runs on that")
    for name, source in sorted(sources.items()):
        if _normalized(name) not in declared or _normalized(name) in CORE_PACKAGES:
            # a source for what only an extra installs is not followed (viva-simbio declares vivarium-workbench only in its
            # `dashboard` extra, and its real lock holds neither it nor its source); the core has its own finding above
            continue
        floating = [s for s in (source if isinstance(source, list) else [source])
                    if isinstance(s, dict) and s.get("git") and not _FULL_COMMIT.fullmatch(str(s.get("rev", "")))]
        if floating:
            ref = next((f" ({k} {floating[0][k]})" for k in ("rev", "branch", "tag") if floating[0].get(k)), "")
            say("warn", "git-source-without-commit", f"[tool.uv.sources] sends {name} to {floating[0]['git']}{ref} without a full "
                                                     "commit: pixi follows it for a package installed from git, so the lock takes "
                                                     "whatever that ref holds when it runs, with its own sources, and the entry's "
                                                     f"pin does not cover it; if {name} is on PyPI, delete the source")
    backend = pyproject.get("build-system", {}).get("build-backend", "")
    if "maturin" in backend or "setuptools_rust" in backend:
        say("warn", "compiled-build", f"built with {backend}: the extension is compiled when the environment is built, so "
                                      "check it builds in compose-api's builder")
    if (ws / "pixi.toml").is_file():
        say("warn", "pixi-toml", "a pixi.toml is present: its conda packages are not copied into conda:; list what the run "
                                 "needs, as viva-pde-particle's entry does")
    if level != "listed" and not evidence and not check_only:
        say("error", "evidence-needed", f"level {level} needs evidence (--evidence TEXT): what was run, where, with which "
                                        "versions, on which date; compose-api's tests refuse an entry above listed without it")

    # -- the registry's own entry for this repository
    url = f"https://github.com/{repo[0]}/{repo[1]}" if repo else ""
    matches = [e for e in registry or [] if repo and canonical_source(e.get("source")) == canonical_source(url)]
    match = matches[0] if matches else None
    if len(matches) > 1:
        say("warn", "duplicate-registry-entries", f"the registry has {len(matches)} entries for this repository "
                                                  f"({', '.join(str(e.get('id')) for e in matches)}); the first is used")
    if match and not check_only and not _is_catalog_entry(match):
        say("error", "curated-entry-exists", f"the registry entry {match.get('id')!r} is curated (level {match.get('level')}; it "
                                             "may carry conda, requires, bundles or evidence): a generated entry would replace "
                                             "it or duplicate its id, so edit that entry instead (--check shows what differs)")

    if any(f.severity == "error" for f in findings) or repo is None or head is None:
        return Derived(None, roots, findings, repo, head, package)
    entry_id = repo[1]
    if match and match.get("id") and match["id"] != entry_id:
        entry_id = match["id"]
        say("info", "id-from-registry", f"the registry already calls this repository {entry_id!r}; the entry keeps that id so "
                                        "it replaces the catalog's rather than duplicating it")
    entry: dict[str, Any] = {"id": entry_id, "package": package, "source": url, "commit": head,
                             "install": {"git": url, "commit": head}, "module_roots": roots, "level": level}
    if evidence:
        entry["evidence"] = list(evidence)
    return Derived(entry, roots, findings, repo, head, package)


# -- checking an existing entry -----------------------------------------------------------------------------------------


def load_registry(source: str) -> list[dict]:
    """Registry entries from a compose-api base URL (``GET /registry/entries``), a JSON list of them, or a
    ``manifest.yaml`` / ``catalog.yaml`` file. Raises ValueError (or OSError) when it cannot."""
    if re.match(r"^https?://", source):
        with urllib.request.urlopen(source.rstrip("/") + "/registry/entries", timeout=30) as r:  # noqa: S310
            data = json.load(r)
    else:
        path = Path(source)
        text = path.read_text(encoding="utf-8")
        try:
            data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ValueError(f"{source} is not valid YAML: {e}") from e
    if isinstance(data, dict):
        data = data.get("entries")
    if not isinstance(data, list) or not all(isinstance(e, dict) for e in data):
        raise ValueError(f"{source} does not hold a list of registry entries")
    return data


def check(derived: Derived, registry: list[dict]) -> list[Finding]:
    """How the registry's entry for this repository differs from the workspace."""
    if derived.repo is None:
        return []
    url = f"https://github.com/{derived.repo[0]}/{derived.repo[1]}"
    entry = next((e for e in registry if canonical_source(e.get("source")) == canonical_source(url)), None)
    if entry is None:
        return [Finding("warn", "no-entry", f"the registry has no entry whose source is {url}: this entry would be new")]
    found: list[Finding] = []
    registry_roots, mine = set(entry.get("module_roots") or []), set(derived.roots)
    if mine and not mine & registry_roots:
        found.append(Finding("error", "module-roots-wrong", f"the registry entry {entry.get('id')!r} lists {sorted(registry_roots)}, "
                                                            f"which share nothing with what the workspace ships: {sorted(mine)}"))
    elif mine - registry_roots:
        found.append(Finding("warn", "module-roots-incomplete", f"the registry entry {entry.get('id')!r} omits {sorted(mine - registry_roots)} "
                                                                "that the workspace also ships (documents naming them are refused)"))
    if registry_roots - mine and mine & registry_roots:
        found.append(Finding("warn", "module-roots-extra", f"the registry entry lists {sorted(registry_roots - mine)}, "
                                                           "which the workspace does not ship"))
    if derived.package and entry.get("package") != derived.package:
        found.append(Finding("warn", "package-differs", f"the registry entry's package is {entry.get('package')!r}; "
                                                        f"pyproject.toml's is {derived.package!r}"))
    if entry.get("commit") and derived.head and entry["commit"] != derived.head:
        found.append(Finding("info", "commit-differs", f"the registry pins {str(entry['commit'])[:8]}; the workspace is at {derived.head[:8]}"))
    if entry.get("level") != "listed" and any(f.code == "distribution-name-differs" for f in derived.findings):
        found.append(Finding("error", "registered-entry-cannot-lock", f"the registry entry {entry.get('id')!r} is at level "
                                                                      f"{entry.get('level')}, but the workspace's distribution name "
                                                                      "differs from its repository's, so its first lock fails"))
    found.append(Finding("info", "registry-level", f"the registry entry {entry.get('id')!r} is at level {entry.get('level')}"))
    return found
