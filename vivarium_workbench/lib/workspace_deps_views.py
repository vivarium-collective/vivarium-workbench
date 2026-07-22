"""Library builders for workspace-switcher, remote build list, and system-deps check.

These are pure, ws_root-parameterised functions extracted from server.py so the
FastAPI seam (``api/app.py``) can call them without importing the stdlib server
module.

No imports from ``vivarium_workbench.server`` — those live as shim wrappers
inside server.py itself.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# GET /api/source/builds
# ---------------------------------------------------------------------------

def _sms_api_base() -> str:
    """Base URL of the sms-api (the SSM tunnel by default)."""
    return os.environ.get("SMS_API_BASE", "http://localhost:8080")


def build_source_builds() -> dict:
    """Builder for GET /api/source/builds.

    Mirrors ``server.Handler._get_source_builds``.  Env-based (``SMS_API_BASE``);
    no ws_root needed.  Always returns a dict — best-effort (empty builds +
    error reason when sms-api is down).
    """
    from vivarium_workbench.lib import remote_build_source
    from vivarium_workbench.lib.sms_api_client import SmsApiClient

    return remote_build_source.list_build_sources(SmsApiClient(_sms_api_base()))


# ---------------------------------------------------------------------------
# GET /api/workspaces
# ---------------------------------------------------------------------------

def _git_branch_commit(path: str) -> tuple[str, str]:
    """(branch, short_commit) for a git workspace; ('', '') when unresolvable."""

    def _run(args: list[str]) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", path, *args], capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    return _run(["rev-parse", "--abbrev-ref", "HEAD"]), _run(["rev-parse", "--short", "HEAD"])


def _branch_label(name: str, branch: str, path: str) -> str:
    """Disambiguate the many worktrees/clones of one repo by branch.

    ``v2ecoli`` → ``v2ecoli:dnaa-biology`` etc. Falls back to the path
    leaf when git can't resolve a branch; plain name on the default
    branch or when the leaf adds nothing.
    """
    variant = branch if branch and branch not in ("main", "master", "HEAD") else None
    if variant is None:
        leaf = Path(path).name
        if leaf and leaf != name:
            variant = leaf
    return f"{name}:{variant}" if variant else name


def read_workspace_name(root: Path) -> str:
    """Read ``name`` from ``<root>/workspace.yaml``; fall back to dir basename."""
    try:
        import yaml
        data = yaml.safe_load((root / "workspace.yaml").read_text(encoding="utf-8")) or {}
        return data.get("name") or root.name
    except Exception:
        return root.name


def build_workspaces(ws_root: Path) -> dict:
    """Builder for GET /api/workspaces.

    Mirrors ``server.Handler._get_workspaces`` exactly (servers-join + status/
    url/pid logic + sort order).  Reads the GLOBAL ``~/.pbg/workspaces.json``
    via ``pbg_superpowers.workspace_catalog.list_workspaces()``; only the
    ``current`` entry needs ``ws_root``.

    Always returns a dict (falls back to current-only on missing/corrupt
    catalog).
    """
    from pbg_superpowers import workspace_catalog

    current_root = ws_root
    current_resolved = str(current_root.resolve())

    current_name = read_workspace_name(current_root)
    result: dict = {
        "current": {"name": current_name, "path": current_resolved},
        "workspaces": [],
    }

    try:
        catalog = workspace_catalog.list_workspaces()
    except Exception:
        catalog = []

    if not any(e.get("path") == current_resolved for e in catalog):
        catalog = [{
            "name": current_name,
            "path": current_resolved,
            "package": None,
            "added_at": None,
        }] + list(catalog)

    # `_git_branch_commit` shells out to git twice per workspace; done serially
    # across the whole pbg ecosystem (~40 repos) that dominated the endpoint
    # (~13s). The calls are independent and I/O-bound, so resolve them in a
    # thread pool up front — cuts the wall time to roughly the slowest one.
    _dir_paths = [e.get("path", "") for e in catalog
                  if e.get("path") and Path(e["path"]).is_dir()]
    _bc: dict = {}
    if _dir_paths:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(16, len(_dir_paths))) as _ex:
            for _p, _res in zip(_dir_paths, _ex.map(_git_branch_commit, _dir_paths)):
                _bc[_p] = _res

    for entry in catalog:
        path = entry.get("path", "")
        name = entry.get("name") or Path(path).name
        row: dict = {"name": name, "path": path}
        branch, commit = _bc.get(path, ("", ""))
        row["repo"] = name
        row["branch"] = branch
        row["commit"] = commit
        row["label"] = _branch_label(name, branch, path) if Path(path).is_dir() else name
        if not Path(path).is_dir():
            row["status"] = "missing"
        elif path == current_resolved:
            row["status"] = "current"
            catalog_entry = workspace_catalog.find_entry(path)
            if catalog_entry is not None:
                pid_val = int(catalog_entry.get("pid") or 0)
                if pid_val <= 0:
                    alive = False
                else:
                    try:
                        os.kill(pid_val, 0)
                        alive = True
                    except ProcessLookupError:
                        alive = False
                    except PermissionError:
                        alive = True  # PID exists but owned by another user
                    except (OSError, ValueError):
                        alive = False
                if alive:
                    row["url"] = catalog_entry["url"]
                    row["pid"] = catalog_entry["pid"]
        else:
            catalog_entry = workspace_catalog.find_entry(path)
            if catalog_entry is None:
                row["status"] = "stopped"
            else:
                pid_val = int(catalog_entry.get("pid") or 0)
                if pid_val <= 0:
                    alive = False
                else:
                    try:
                        os.kill(pid_val, 0)
                        alive = True
                    except ProcessLookupError:
                        alive = False
                    except PermissionError:
                        alive = True  # PID exists but owned by another user
                    except (OSError, ValueError):
                        alive = False
                if alive:
                    row["status"] = "running"
                    row["url"] = catalog_entry["url"]
                    row["pid"] = catalog_entry["pid"]
                else:
                    row["status"] = "stale"
                    row["pid"] = catalog_entry.get("pid")
        result["workspaces"].append(row)

    order = {"current": 0, "running": 1, "stopped": 2, "stale": 3, "missing": 4}
    result["workspaces"].sort(key=lambda r: (order.get(r["status"], 99), r["name"]))

    return result


# ---------------------------------------------------------------------------
# GET /api/system-deps-check
# ---------------------------------------------------------------------------

def platform_key() -> str:
    """Map sys.platform to the install-key used in catalog system_dependencies.

    Returns one of: 'darwin', 'linux', 'windows', or the raw lowercase
    platform.system() string as a last-resort fallback.
    """
    p = platform.system().lower()
    if p == "darwin":
        return "darwin"
    if p.startswith("linux"):
        return "linux"
    if p == "windows":
        return "windows"
    return p


def check_system_dep(check: dict, venv_py: Path) -> tuple[bool, Optional[str]]:
    """Run a single system-dep check defined in a catalog entry.

    A check is satisfied when its ``import_check`` Python snippet runs
    successfully inside the workspace venv. Empty/missing snippets are
    treated as satisfied.

    Returns ``(satisfied, failure_reason)`` — reason is None on success
    and otherwise the most informative tail line of stderr.
    """
    snippet = check.get("import_check") or ""
    if not snippet:
        return True, None
    if not venv_py.is_file():
        return False, f"workspace venv python not found at {venv_py}"
    try:
        result = subprocess.run(
            [str(venv_py), "-c", snippet],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True, None
        err_lines = [
            ln for ln in (result.stderr or "").strip().splitlines() if ln.strip()
        ]
        return False, (err_lines[-1] if err_lines else f"exit {result.returncode}")
    except subprocess.TimeoutExpired:
        return False, "check timed out"
    except Exception as e:
        return False, str(e)


def module_registry(ws_root: Path) -> list[dict]:
    """The available-modules registry for a workspace.

    Canonical source is ``pbg_superpowers.catalog.load_registry(ws_root)``
    (canonical list + optional per-workspace overlay.json). Falls back to a
    legacy per-workspace ``scripts/_catalog/modules.json`` when the installed
    pbg_superpowers predates the canonical registry.
    """
    try:
        from pbg_superpowers.catalog import load_registry
        return load_registry(ws_root)
    except Exception:
        from vivarium_workbench.lib.workspace_paths import WorkspacePaths
        legacy = WorkspacePaths.load(ws_root).scripts / "_catalog" / "modules.json"
        if legacy.is_file():
            try:
                return json.loads(legacy.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []


def build_system_deps_check(ws_root: Path, name: str) -> tuple[dict, int]:
    """Builder for GET /api/system-deps-check?name=<module>.

    Mirrors ``server.Handler._get_system_deps_check`` exactly.

    Returns ``(body_dict, http_status)``:
      - 400 when ``name`` is empty
      - 404 when the module is not in the registry
      - 200 with full check results
    """
    name = (name or "").strip()
    if not name:
        return {"error": "name required"}, 400

    catalog = module_registry(ws_root)
    entry = next((m for m in catalog if m.get("name") == name), None)
    if entry is None:
        return {"error": f"unknown module: {name}"}, 404

    sys_deps = (entry.get("system_dependencies") or {}).get("checks") or []
    venv_py = ws_root / ".venv" / "bin" / "python3"
    plat = platform_key()

    results = []
    all_ok = True
    for chk in sys_deps:
        ok, reason = check_system_dep(chk, venv_py)
        if not ok:
            all_ok = False
        install_block = chk.get("install") if isinstance(chk.get("install"), dict) else None
        install_spec = install_block.get(plat) if install_block else None
        results.append({
            "name": chk.get("name"),
            "description": chk.get("description", ""),
            "ok": ok,
            "reason": reason,
            "install": install_spec,
            "notes": chk.get("notes"),
        })
    return {
        "name": name,
        "platform": plat,
        "ok": all_ok,
        "checks": results,
    }, 200
