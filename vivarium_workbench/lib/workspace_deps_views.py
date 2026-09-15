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
    """Base URL of the viva-api (nee sms-api; the SSM tunnel by default).

    Delegates to the single source of truth :func:`sms_api_client.sms_api_base`;
    kept under this name because several modules import it from here.
    """
    from vivarium_workbench.lib.sms_api_client import sms_api_base

    return sms_api_base()


def build_source_builds() -> dict:
    """Builder for GET /api/source/builds.

    Mirrors ``server.Handler._get_source_builds``.  Env-based (``SMS_API_BASE``);
    no ws_root needed.  Always returns a dict — best-effort (empty builds +
    error reason when sms-api is down).
    """
    from vivarium_workbench.lib import remote_build_source
    from vivarium_workbench.lib.sms_api_client import SmsApiClient

    return remote_build_source.list_build_sources(SmsApiClient(_sms_api_base()))


def remote_health() -> dict:
    """Reachability + config status of the remote sms-api endpoint (``SMS_API_BASE``).

    Powers the Source panel's health indicator and the startup log: tells a fresh
    operator (or Chris) whether the remote endpoint is even configured and whether
    it answers. Best-effort — never raises; returns
    ``{configured, base_url, reachable, version, error}``.
    """
    from vivarium_workbench.lib.sms_api_client import SmsApiClient

    base = _sms_api_base()
    configured = bool(os.environ.get("VIVA_API_BASE") or os.environ.get("SMS_API_BASE"))
    try:
        version = SmsApiClient(base).ping()
        return {"configured": configured, "base_url": base, "reachable": True,
                "version": version, "error": None}
    except Exception as exc:  # noqa: BLE001 — a health probe must never raise
        return {"configured": configured, "base_url": base, "reachable": False,
                "version": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# GET /api/workspaces
# ---------------------------------------------------------------------------

def _git_identity(path: str) -> tuple[str, str, str]:
    """(branch, short_commit, repo) for a git workspace; ('', '', '') when unresolvable.

    ``repo`` is the real remote identity — the origin URL's last path segment,
    parsed the same way ``remote_build_source.list_build_sources`` already
    derives it for Remote-scope builds — NOT ``workspace.yaml``'s ``name``
    field. That field can permanently lag a fork's real repo identity (e.g.
    sms-ecoli's ``workspace.yaml`` still declares ``name: v2ecoli`` — see
    backlog item 54); grouping the Local-scope picker by ``name`` silently
    merged sms-ecoli's branches into v2ecoli's entry. Callers fall back to
    ``name`` when this returns ``""`` (no remote configured, or an unresolvable
    checkout) — same tolerant-degradation shape as ``read_workspace_name``.
    """

    def _run(args: list[str]) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", path, *args], capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    commit = _run(["rev-parse", "--short", "HEAD"])
    origin = _run(["remote", "get-url", "origin"])
    repo = origin.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") if origin else ""
    return branch, commit, repo


def _branch_label(
    name: str,
    branch: str,
    path: str,
    disambiguate: bool = False,
    branch_unique: bool = True,
) -> str:
    """Disambiguate the many worktrees/clones of one repo by branch.

    ``v2ecoli`` → ``v2ecoli:dnaa-biology`` etc. A resolvable branch names
    itself when it's non-default, or — when several catalog checkouts share
    this ``name`` (``disambiguate``) — on the default branch too, so the
    pristine ``v2ecoli:main`` is tellable apart from its sibling worktrees.
    A branch label is only used when the ``(name, branch)`` pairing is unique
    (``branch_unique``); otherwise, and for detached/unresolved checkouts, we
    fall back to the folder leaf so no two rows collide.
    """
    if branch and branch != "HEAD":
        default = branch in ("main", "master")
        if (not default or disambiguate) and branch_unique:
            return f"{name}:{branch}"
    # Detached / unresolved, colliding branch, or a redundant-name default
    # checkout: use the folder leaf when it adds information.
    leaf = Path(path).name
    if leaf and leaf != name:
        return f"{name}:{leaf}"
    return name


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
    via ``viva_superpowers.workspace_catalog.list_workspaces()``; only the
    ``current`` entry needs ``ws_root``.

    Always returns a dict (falls back to current-only on missing/corrupt
    catalog).
    """
    from viva_superpowers import workspace_catalog

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

    # `_git_identity` shells out to git three times per workspace; done serially
    # across the whole pbg ecosystem (~40 repos) that dominated the endpoint
    # (~13s). The calls are independent and I/O-bound, so resolve them in a
    # thread pool up front — cuts the wall time to roughly the slowest one.
    _dir_paths = [e.get("path", "") for e in catalog
                  if e.get("path") and Path(e["path"]).is_dir()]
    _bc: dict = {}
    if _dir_paths:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(16, len(_dir_paths))) as _ex:
            for _p, _res in zip(_dir_paths, _ex.map(_git_identity, _dir_paths)):
                _bc[_p] = _res

    # A name shared by several catalog checkouts needs its default-branch
    # checkout labeled explicitly (``v2ecoli:main``) to tell it apart; a branch
    # label is only unambiguous when no other checkout shares (name, branch).
    from collections import Counter
    _name_counts = Counter(
        (e.get("name") or Path(e.get("path", "")).name) for e in catalog
    )
    _name_branch_counts = Counter(
        ((e.get("name") or Path(e.get("path", "")).name), _bc.get(e.get("path", ""), ("", "", ""))[0])
        for e in catalog
    )

    for entry in catalog:
        path = entry.get("path", "")
        name = entry.get("name") or Path(path).name
        row: dict = {"name": name, "path": path}
        branch, commit, repo = _bc.get(path, ("", "", ""))
        # Real repo identity (item 54): falls back to `name` only when the
        # checkout has no resolvable git remote — never silently groups two
        # different repos together just because their `workspace.yaml`s share
        # a stale/forked `name` field.
        row["repo"] = repo or name
        row["branch"] = branch
        row["commit"] = commit
        _ambig = _name_counts.get(name, 0) > 1
        _branch_unique = _name_branch_counts.get((name, branch), 0) <= 1
        row["label"] = (
            _branch_label(name, branch, path, _ambig, _branch_unique)
            if Path(path).is_dir() else name
        )
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

    Canonical source is ``viva_superpowers.catalog.load_registry(ws_root)``
    (canonical list + optional per-workspace overlay.json). Falls back to a
    legacy per-workspace ``scripts/_catalog/modules.json`` when the installed
    viva_superpowers predates the canonical registry.
    """
    try:
        from viva_superpowers.catalog import load_registry
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
