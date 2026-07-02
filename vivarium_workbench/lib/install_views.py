"""Pure builders for the two venv / system-install POST routes.

Behaviour-preserving ports of the stdlib handlers
``server.Handler._post_system_deps_install`` and ``_post_import_install``.  Both
return ``(body, status)`` so the FastAPI route wraps every path in
``JSONResponse`` (preserving the non-200 codes verbatim).  No ``import server``
here.

``subprocess`` and ``shutil`` are bound at module level so tests monkeypatch
``subprocess.run`` (with a fake ``CompletedProcess`` / a raised
``TimeoutExpired`` / a generic exception) and ``shutil.which`` — never spawning a
real install.  The lib helpers are referenced through their modules
(``workspace_deps_views.module_registry`` / ``platform_key`` /
``check_system_dep``, ``install_errors.diagnose``,
``workspace_yaml.load_workspace`` / ``save_workspace``,
``registry.clear_registry_cache``) so tests can monkeypatch the canonical source
module.

The single behavioural difference from the live ``import_install`` handler is
that the git **commit is DEFERRED**: the legacy server wraps the workspace.yaml
``installed=True`` mutation in ``_active_branch_action(commit_msg, action)``
(commit-on-active-branch, with a 409-no-changes→200 special case); the FastAPI
path instead runs that mutation inline, invalidates the registry cache, and
returns the success ``{ok, log}`` 200 directly.  Every other outcome (validation
400/404 + the install-failure 500 with diagnosis) is reproduced byte-identically
with ``WORKSPACE`` → ``ws_root``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from vivarium_workbench.lib import install_errors as _install_errors
from vivarium_workbench.lib import registry as _registry
from vivarium_workbench.lib import workspace_deps_views as _workspace_deps
from vivarium_workbench.lib import workspace_yaml as _workspace_yaml


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Make the workspace's own Python package(s) importable.

    Replicates ``server._ws_add_to_sys_path`` (which uses the ``WORKSPACE``
    global) with the root threaded explicitly: insert ``ws_root`` on
    ``sys.path`` so the workspace package resolves as a top-level package.
    """
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def system_deps_install(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Run install commands for the named checks of a catalog module.

    Behaviour-preserving port of ``_post_system_deps_install`` (body
    ``{name, check_names}``).  Returns ``(response_dict, status_code)``:

      * missing name / check_names → ``({"error": "name + check_names required"}, 400)``
      * unknown module             → ``({"error": f"unknown module: {name}"}, 404)``
      * happy path                 → ``({ok, log, recheck}, 200)``

    Install commands run via ``shell=True`` (catalog is workspace-local and
    editable only by trusted users); each command's outcome is appended to
    ``log`` (success / non-zero / timeout / unknown-check / no-install-spec
    branches), then every requested dep is re-checked.
    """
    name = (body.get("name") or "").strip()
    check_names = body.get("check_names") or []
    if not name or not check_names:
        return {"error": "name + check_names required"}, 400

    catalog = _workspace_deps.module_registry(ws_root)
    entry = next((m for m in catalog if m.get("name") == name), None)
    if entry is None:
        return {"error": f"unknown module: {name}"}, 404

    sys_deps = (entry.get("system_dependencies") or {}).get("checks") or []
    plat = _workspace_deps.platform_key()
    by_name = {c.get("name"): c for c in sys_deps if c.get("name")}

    log: list[dict] = []
    overall_ok = True
    for cn in check_names:
        check = by_name.get(cn)
        if check is None:
            log.append({"check_name": cn, "returncode": -1, "error": "unknown check"})
            overall_ok = False
            continue
        install_block = check.get("install") if isinstance(check.get("install"), dict) else None
        install_spec = install_block.get(plat) if install_block else None
        if not install_spec:
            log.append({
                "check_name": cn, "returncode": -1,
                "error": f"no install spec for platform {plat}",
            })
            overall_ok = False
            continue
        commands = install_spec.get("commands") or []
        for cmd in commands:
            # WARNING: shell=True so catalog-supplied commands execute
            # verbatim. Catalog is workspace-local; only trusted users
            # should be allowed to edit it. The UI is expected to have
            # shown each command to the user before this endpoint is
            # called.
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=600,  # brew installs can be slow
                )
            except subprocess.TimeoutExpired:
                log.append({
                    "check_name": cn, "command": cmd,
                    "returncode": -1, "error": "timeout (600s)",
                })
                overall_ok = False
                break
            log.append({
                "check_name": cn,
                "command": cmd,
                "returncode": result.returncode,
                "stdout_tail": (result.stdout or "")[-500:],
                "stderr_tail": (result.stderr or "")[-500:],
            })
            if result.returncode != 0:
                overall_ok = False
                break

    # Re-check each requested dep after install attempts.
    venv_py = ws_root / ".venv" / "bin" / "python3"
    recheck = []
    for cn in check_names:
        check = by_name.get(cn)
        if check is None:
            continue
        ok, reason = _workspace_deps.check_system_dep(check, venv_py)
        recheck.append({"name": cn, "ok": ok, "reason": reason})

    return {
        "ok": overall_ok,
        "log": log,
        "recheck": recheck,
    }, 200


def import_install(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Pip-install an import into the workspace venv.

    Behaviour-preserving port of ``_post_import_install`` (body
    ``{name, target?}``) with the ``_active_branch_action`` commit DEFERRED.
    Returns ``(response_dict, status_code)``:

      * missing name                 → ``({"error": "missing name"}, 400)``
      * import not registered        → ``({"error": f"import '{name}' not registered"}, 404)``
      * no install target            → ``({"error": "no install target …"}, 400)``
      * target path missing          → ``({"error": f"path does not exist: {abs_target}"}, 404)``
      * no pip and no uv             → ``({"error": "<hint>"}, 500)``
      * install timeout              → ``({"error": f"{cmd[0]} install timed out after 120s"}, 500)``
      * install error                → ``({"error": f"install error: {pip_err}"}, 500)``
      * non-zero returncode          → ``({"error": "install failed", "log": …[, "diagnosis"]}, 500)``
      * success                      → run the workspace.yaml mutation inline
                                       (``imports[name].installed=True`` +
                                       ``install_path``), ``clear_registry_cache()``,
                                       then ``({"ok": True, "log": …}, 200)``
    """
    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "missing name"}, 400
    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    imports = ws_data.get("imports", {})
    if name not in imports:
        return {"error": f"import '{name}' not registered"}, 404

    entry = imports[name]
    target = (body.get("target") or "").strip() or entry.get("path") or ""
    if not target:
        return {"error": "no install target — set 'path' in import or pass 'target' in body"}, 400

    # Resolve path relative to workspace (unless it's a URL/VCS spec).
    if not target.startswith(("http://", "https://", "git+")):
        abs_target = (ws_root / target).resolve()
        if not abs_target.exists():
            return {"error": f"path does not exist: {abs_target}"}, 404
        target = str(abs_target)

    # Pick installer: prefer pip in the venv; fall back to system `uv` when
    # the venv has no pip (created via `uv venv`). Both produce the same
    # editable install in the venv's site-packages.
    venv_pip = ws_root / ".venv" / "bin" / "pip"
    venv_py = ws_root / ".venv" / "bin" / "python3"
    if venv_pip.exists():
        cmd = [str(venv_pip), "install", "-e", target]
    else:
        uv_path = shutil.which("uv")
        if uv_path and venv_py.exists():
            cmd = [uv_path, "pip", "install", "--python", str(venv_py), "-e", target]
        else:
            hint = (
                "neither .venv/bin/pip nor `uv` found. "
                "Create a venv with pip (`python -m venv .venv && .venv/bin/pip install --upgrade pip`) "
                "or install uv (`brew install uv`)."
            )
            return {"error": hint}, 500

    # Run install (outside the branch action so errors surface before git work).
    try:
        result = subprocess.run(
            cmd,
            cwd=ws_root, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"{cmd[0]} install timed out after 120s"}, 500
    except Exception as pip_err:
        return {"error": f"install error: {pip_err}"}, 500

    log_excerpt = (result.stdout + "\n" + result.stderr).strip()[-3000:]
    if result.returncode != 0:
        _ws_add_to_sys_path(ws_root)
        diag = _install_errors.diagnose(log_excerpt)
        resp: dict = {
            "error": "install failed",
            "log": log_excerpt[-1000:],
        }
        if diag:
            resp["diagnosis"] = diag.as_dict()
        return resp, 500

    # Mark installed in workspace.yaml. The live handler wraps this in
    # ``_active_branch_action``; here the commit is DEFERRED — run the mutation
    # inline, then invalidate the registry cache and return success directly.
    install_target = target
    _ws_add_to_sys_path(ws_root)
    ws_file = ws_root / "workspace.yaml"
    ws = _workspace_yaml.load_workspace(ws_file)
    ws.setdefault("imports", {}).setdefault(name, {})["installed"] = True
    ws["imports"][name]["install_path"] = install_target
    _workspace_yaml.save_workspace(ws_file, ws)

    # Invalidate registry cache so next /api/registry call sees fresh data.
    _registry.clear_registry_cache()

    return {"ok": True, "log": log_excerpt[-500:]}, 200
