#!/usr/bin/env python3
"""Check what the two built distributions ship (docs/backend-adapters.md step P4b).

    uv build --package vivarium-workbench-vivaapi -o dist/vivaapi
    uv build --package vivarium-workbench -o dist/core
    python scripts/check_wheels.py dist/core dist/vivaapi

Each directory must hold exactly one wheel and one sdist. Checks:

* the core wheel ships no ``vivarium_workbench_sms/``, declares no ``vivarium_workbench.backends`` entry point,
  requires boto3 under no condition except through the plugin, and provides the ``vivaapi`` extra, which requires
  exactly the plugin at the core's version;
* the plugin wheel ships ``vivarium_workbench_sms/provider.py`` and the ``sms`` entry point, and requires exactly the
  core at its own version and boto3;
* the core sdist carries neither the plugin (``packages/``) nor any build output (``*.whl``, ``*.tar.gz``); the
  plugin sdist carries ``vivarium_workbench_sms/provider.py``.

Standard library only. Exit 0 when everything holds, 1 with one line per problem otherwise. release.yml and the
``wheels`` PR job (standalone.yml) run it.
"""
from __future__ import annotations

import re
import sys
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

GROUP = "vivarium_workbench.backends"
PLUGIN_PKG = "vivarium_workbench_sms"


def _one(directory: Path, pattern: str, problems: list[str]) -> Path | None:
    found = sorted(directory.glob(pattern))
    if len(found) != 1:
        problems.append(f"{directory}: expected exactly one {pattern}, found {[p.name for p in found]}")
        return None
    return found[0]


def _wheel(path: Path) -> tuple[list[str], dict, str]:
    """(entry names, METADATA headers as a Message, entry_points.txt text)."""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        info = next(n for n in names if n.endswith(".dist-info/METADATA")).rsplit("/", 1)[0]
        meta = Parser().parsestr(zf.read(f"{info}/METADATA").decode())
        eps = zf.read(f"{info}/entry_points.txt").decode() if f"{info}/entry_points.txt" in names else ""
    return names, meta, eps


def _requires(meta) -> list[str]:
    return [r.strip() for r in meta.get_all("Requires-Dist") or []]


def _name(req: str) -> str:
    return re.split(r"[\s\[<>=!~;(]", req, maxsplit=1)[0].lower().replace("_", "-")


def _group(eps_text: str, group: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    for line in eps_text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == group and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def check(core_dir: Path, plugin_dir: Path) -> list[str]:
    problems: list[str] = []
    core_whl = _one(core_dir, "*.whl", problems)
    core_sdist = _one(core_dir, "*.tar.gz", problems)
    plugin_whl = _one(plugin_dir, "*.whl", problems)
    plugin_sdist = _one(plugin_dir, "*.tar.gz", problems)
    if problems:
        return problems
    assert core_whl and core_sdist and plugin_whl and plugin_sdist

    names, meta, eps = _wheel(core_whl)
    version = meta["Version"]
    if any(n.startswith(f"{PLUGIN_PKG}/") for n in names):
        problems.append(f"{core_whl.name} ships {PLUGIN_PKG}/")
    if _group(eps, GROUP) or f"[{GROUP}]" in eps:
        problems.append(f"{core_whl.name} declares the {GROUP} entry-point group")
    reqs = _requires(meta)
    if any(_name(r) == "boto3" for r in reqs):
        problems.append(f"{core_whl.name} requires boto3 itself (the plugin declares it): {[r for r in reqs if 'boto3' in r]}")
    if "vivaapi" not in (meta.get_all("Provides-Extra") or []):
        problems.append(f"{core_whl.name} does not provide the vivaapi extra")
    extra = [r for r in reqs if _name(r) == "vivarium-workbench-vivaapi"]
    if len(extra) != 1 or not re.fullmatch(
            rf"vivarium-workbench-vivaapi\s*==\s*{re.escape(version)}\s*;\s*extra\s*==\s*['\"]vivaapi['\"]", extra[0]):
        problems.append(f"{core_whl.name}: the vivaapi extra must require exactly vivarium-workbench-vivaapi=={version}; "
                        f"found {extra}")

    p_names, p_meta, p_eps = _wheel(plugin_whl)
    if p_meta["Version"] != version:
        problems.append(f"{plugin_whl.name} is {p_meta['Version']}, the core wheel is {version}")
    if f"{PLUGIN_PKG}/provider.py" not in p_names:
        problems.append(f"{plugin_whl.name} does not ship {PLUGIN_PKG}/provider.py")
    if _group(p_eps, GROUP) != {"sms": f"{PLUGIN_PKG}.provider:PROVIDER"}:
        problems.append(f"{plugin_whl.name}: {GROUP} entry points are {_group(p_eps, GROUP)}")
    p_reqs = _requires(p_meta)
    core_pin = [r for r in p_reqs if _name(r) == "vivarium-workbench"]
    if [re.sub(r"\s+", "", r) for r in core_pin] != [f"vivarium-workbench=={version}"]:
        problems.append(f"{plugin_whl.name} must require exactly vivarium-workbench=={version}; found {core_pin}")
    if not any(_name(r) == "boto3" and ";" not in r for r in p_reqs):
        problems.append(f"{plugin_whl.name} does not require boto3")

    with tarfile.open(core_sdist) as tf:
        members = [m.split("/", 1)[1] if "/" in m else m for m in tf.getnames()]
    if any(m == "packages" or m.startswith("packages/") for m in members):
        problems.append(f"{core_sdist.name} carries packages/ (the plugin)")
    stray = [m for m in members if m.endswith((".whl", ".tar.gz"))]
    if stray:
        problems.append(f"{core_sdist.name} carries build output: {stray}")
    with tarfile.open(plugin_sdist) as tf:
        p_members = [m.split("/", 1)[1] if "/" in m else m for m in tf.getnames()]
    if f"{PLUGIN_PKG}/provider.py" not in p_members:
        problems.append(f"{plugin_sdist.name} does not carry {PLUGIN_PKG}/provider.py")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} CORE_DIST_DIR PLUGIN_DIST_DIR", file=sys.stderr)
        return 2
    problems = check(Path(argv[1]), Path(argv[2]))
    for p in problems:
        print(f"ERROR: {p}", file=sys.stderr)
    if not problems:
        print(f"wheels: {argv[1]} (core) and {argv[2]} (vivaapi plugin) ship what they should")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
