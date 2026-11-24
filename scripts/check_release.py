#!/usr/bin/env python3
"""The lockstep versions check (docs/backend-adapters.md step P4b).

The core (``vivarium-workbench``) and the backend plugin (``vivarium-workbench-vivaapi``, the uv workspace member
``packages/vivarium-workbench-vivaapi``) are released together with identical versions and ``==`` pins both ways.
uv does not check those pins between workspace members (it records them as editable sources), so this does. It is
the one checker behind both ``tests/test_lockstep_versions.py`` and ``release.yml``'s guard step:

    python scripts/check_release.py            # the four places and uv.lock agree
    python scripts/check_release.py 0.6.0      # ... and they all say 0.6.0 (release.yml passes the tag)

Exit 0 when they agree, 1 with one line per problem otherwise. Standard library only (``tomllib``: Python 3.11+).
``scripts/set_version.sh`` moves all of them at once.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE = "vivarium-workbench"
PLUGIN = "vivarium-workbench-vivaapi"
MEMBER_DIR = Path("packages") / PLUGIN


def _load(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _lock_versions(lock: dict) -> dict[str, str | None]:
    """The version uv.lock records for each of the two editable workspace packages (None when missing)."""
    found: dict[str, str | None] = {CORE: None, PLUGIN: None}
    for pkg in lock.get("package", []):
        if pkg.get("name") in found and "editable" in pkg.get("source", {}):
            found[pkg["name"]] = pkg.get("version")
    return found


def problems(repo: Path = REPO, expected: str | None = None) -> list[str]:
    """Every way the versions and pins in ``repo`` disagree (with each other, and with ``expected`` when given)."""
    root = _load(repo / "pyproject.toml")["project"]
    member = _load(repo / MEMBER_DIR / "pyproject.toml")["project"]
    version = root["version"]
    out: list[str] = []
    if expected is not None and version != expected:
        out.append(f"pyproject.toml version is {version}, expected {expected}")
    if member["version"] != version:
        out.append(f"{MEMBER_DIR}/pyproject.toml version is {member['version']}, the root's is {version}")
    core_pins = [r for r in member.get("dependencies", []) if re.match(rf"{re.escape(CORE)}\s*([\[<>=!~;]|$)", r)]
    if core_pins != [f"{CORE}=={version}"]:
        out.append(f"{MEMBER_DIR}/pyproject.toml must require exactly {CORE}=={version}; it requires {core_pins}")
    extra = root.get("optional-dependencies", {}).get("vivaapi")
    if extra != [f"{PLUGIN}=={version}"]:
        out.append(f"pyproject.toml's vivaapi extra must be [{PLUGIN}=={version}]; it is {extra}")
    lock_path = repo / "uv.lock"
    if lock_path.is_file():
        for name, locked in _lock_versions(_load(lock_path)).items():
            if locked != version:
                out.append(f"uv.lock records {name} {locked} (editable), not {version}: re-run `uv lock`")
    else:
        out.append("uv.lock is missing")
    return out


def main(argv: list[str]) -> int:
    expected = argv[1] if len(argv) > 1 else None
    found = problems(REPO, expected)
    for p in found:
        print(f"ERROR: {p}", file=sys.stderr)
    if not found:
        version = _load(REPO / "pyproject.toml")["project"]["version"]
        print(f"lockstep: {CORE} and {PLUGIN} are both {version}, pinned == both ways, uv.lock agrees")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
