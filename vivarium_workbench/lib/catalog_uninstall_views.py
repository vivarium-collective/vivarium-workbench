"""Pure builders for ``POST /api/catalog-uninstall`` (the last catalog committer).

Behaviour-preserving port of two ``server.Handler`` members that reverse
catalog-install:

  * ``uninstall_unmanaged_or_404(ws_root, name)`` — the extracted
    ``_uninstall_unmanaged_or_404`` instance method (the "unmanaged" case: a
    catalog module present in the venv but NOT declared in
    ``workspace.yaml.imports``).  ``server.py`` keeps a 1-line instance shim
    (``return self._json(*uninstall_unmanaged_or_404(WORKSPACE, name))``) so the
    call-site stays byte-identical.
  * ``catalog_uninstall(ws_root, body)`` — the main handler
    ``_post_catalog_uninstall``, which delegates the not-in-imports case to the
    lib ``uninstall_unmanaged_or_404`` (NOT the server shim) and otherwise runs
    the pypi / git-submodule uninstall ``action()``.

Both return ``(body, status)`` so the FastAPI route wraps every path in
``JSONResponse`` (preserving the non-200 codes verbatim).  No ``import server``.

``subprocess`` and ``shutil`` are bound at module level so tests monkeypatch
``subprocess.run`` (never spawning a real pip/git) and ``shutil.which``.  The lib
helpers are referenced through their modules (``catalog._detect_workspace_venv_distributions``,
``workspace_deps_views.module_registry``, ``workspace_yaml`` load/save,
``pyproject_edit`` remove_dependency / remove_uv_source,
``registry.clear_registry_cache``) so tests can monkeypatch the canonical source.

The single behavioural difference from the live handler is that the git
**commit is DEFERRED**: the legacy ``_post_catalog_uninstall`` wraps the
uninstall ``action()`` in ``_commit_or_run(commit_msg, action)`` (commit-on-
active-branch, with a 409-"no changes"→200 commit-only special case).  The
FastAPI path runs ``action()`` directly — the uninstall subprocess(es) + the
``workspace.yaml`` mutation all live inside ``action()`` — then invalidates the
registry cache and returns the success ``{ok, module, install_mode, log}`` 200.
A raised ``action()`` maps to the live ``_commit_or_run``
``{"error": f"action failed: {inner}"}, 500``.  The commit-only 409-"no changes"
case cannot occur on the no-commit path, so a successful uninstall returns 200.
Every other outcome is reproduced byte-identically with ``WORKSPACE`` → ``ws_root``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from vivarium_workbench.lib import catalog as _catalog
from vivarium_workbench.lib import pyproject_edit as _pyproject_edit
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


def uninstall_unmanaged_or_404(ws_root: Path, name: str) -> tuple[dict, int]:
    """Handle uninstall for catalog modules NOT in workspace.yaml.imports.

    Three cases:
    1. Not in venv either → genuinely already uninstalled (200).
    2. In venv but other installed packages require it (transitive with
       real parents) → 409, tell the user to uninstall the parent(s) first.
    3. In venv with no parent (unmanaged orphan) → pip uninstall +
       best-effort remove of untracked external/<name>/ checkout. No
       pyproject/workspace.yaml edits to make (nothing claims it).

    Skips the _commit_or_run wrapper because case 3 has no tracked-file
    changes to commit (external/<name>/ is removed only if untracked).
    """
    venv_dists = _catalog._detect_workspace_venv_distributions(ws_root)

    # Resolve catalog metadata so we know the actual python pkg / pypi name.
    catalog_pkg = name.replace("-", "_")
    catalog_pypi = name
    try:
        for cat_m in _workspace_deps.module_registry(ws_root):
            if cat_m.get("name") == name:
                catalog_pkg = cat_m.get("package") or catalog_pkg
                catalog_pypi = cat_m.get("pypi_name") or name
                break
    except Exception:
        pass

    variants = {name.lower(), catalog_pkg.lower(), catalog_pypi.lower()}
    dist_info = None
    matched_dist = None
    for v in variants:
        if v in venv_dists:
            dist_info = venv_dists[v]
            matched_dist = v
            break

    if dist_info is None:
        return {"ok": True, "already_uninstalled": True}, 200

    parents = dist_info.get("requires_by") or []
    if parents:
        return {
            "error": f"{name} is required by {', '.join(parents)} — uninstall the parent(s) first",
            "transitive_via": parents,
            "module": name,
        }, 409

    # Orphaned venv install — safe to remove directly.
    venv_py = ws_root / ".venv" / "bin" / "python3"
    uv_path = shutil.which("uv")
    # ``matched_dist`` is set together with ``dist_info`` in the loop above, so
    # it is non-None past the ``dist_info is None`` early-return (assertion is
    # for the type-checker; the runtime branch matches the legacy handler).
    assert matched_dist is not None
    target = catalog_pypi or matched_dist
    if uv_path and venv_py.exists():
        uninstall_cmd = [uv_path, "pip", "uninstall", "--python", str(venv_py), target]
    else:
        venv_pip = ws_root / ".venv" / "bin" / "pip"
        if venv_pip.exists():
            uninstall_cmd = [str(venv_pip), "uninstall", "-y", target]
        else:
            return {"error": "no venv pip/uv available to uninstall"}, 500

    log: list[str] = []
    try:
        r = subprocess.run(
            uninstall_cmd, cwd=ws_root, capture_output=True, text=True, timeout=60,
        )
        log.append((r.stdout + "\n" + r.stderr).strip()[-2000:])
    except Exception as e:
        log.append(f"pip uninstall failed: {e}")

    # Remove untracked external/<name>/ checkout if present and NOT a
    # registered submodule (deinit/git-rm flow is reserved for the
    # imports-declared path).
    ext_path = ws_root / "external" / name
    if ext_path.exists():
        gm = ws_root / ".gitmodules"
        is_submodule = False
        if gm.exists():
            try:
                is_submodule = f"external/{name}" in gm.read_text(encoding="utf-8")
            except Exception:
                pass
        if is_submodule:
            log.append(f"external/{name} is a tracked submodule; left in place")
        else:
            try:
                shutil.rmtree(ext_path)
                log.append(f"removed external/{name}/")
            except Exception as e:
                log.append(f"rm external/{name} failed: {e}")

    _registry.clear_registry_cache()

    return {
        "ok": True,
        "module": name,
        "install_mode": "unmanaged",
        "log": "\n".join(log)[-500:],
    }, 200


def catalog_uninstall(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Remove a catalog module from this workspace.

    Behaviour-preserving port of ``_post_catalog_uninstall`` (body ``{name}``)
    with the ``_commit_or_run`` commit DEFERRED.  Reverses catalog-install:

    - PyPI mode: uv pip uninstall <pypi_name>, remove from [project.dependencies].
    - Git mode: git submodule deinit + git rm external/<name>, remove dep +
      [tool.uv.sources] entry from pyproject.toml.
    - Both: remove workspace.yaml imports.<name>.

    Returns ``(response_dict, status_code)``:

      * missing name        → ``({"error": "missing name"}, 400)``
      * not in imports       → delegates to ``uninstall_unmanaged_or_404``
      * uninstall failure    → ``({"error": "action failed: …"}, 500)``
      * success              → ``({"ok": True, "module", "install_mode", "log"}, 200)``
    """
    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "missing name"}, 400

    # Read workspace.yaml to check if it's installed.
    ws_file = ws_root / "workspace.yaml"
    _ws_add_to_sys_path(ws_root)

    ws = _workspace_yaml.load_workspace(ws_file)
    imports = ws.get("imports") or {}
    if name not in imports:
        # Either truly not installed, OR an "unmanaged" venv install
        # (editable/hand-installed without a workspace.yaml.imports
        # declaration — common when a previous workspace flow added the
        # package as an editable submodule and the user later wants it
        # gone). Detect the latter and run a minimal uninstall:
        # pip uninstall + best-effort remove of untracked external/<name>/.
        return uninstall_unmanaged_or_404(ws_root, name)

    entry = imports[name]
    mode = entry.get("mode", "reference")  # "pypi" or "reference"
    pypi_name = entry.get("pypi_name")
    package_name = entry.get("package", name)

    venv_py = ws_root / ".venv" / "bin" / "python3"
    uv_path = shutil.which("uv")

    # Build uninstall command (best-effort; don't fail if pip uninstall errors).
    if uv_path and venv_py.exists():
        uninstall_cmd_base = [uv_path, "pip", "uninstall", "--python", str(venv_py)]
    else:
        venv_pip = ws_root / ".venv" / "bin" / "pip"
        if venv_pip.exists():
            uninstall_cmd_base = [str(venv_pip), "uninstall", "-y"]
        else:
            uninstall_cmd_base = None

    log_holder: list[str] = []
    uninstall_mode_holder: list[str] = []

    def action():
        remove_dependency = _pyproject_edit.remove_dependency
        remove_uv_source = _pyproject_edit.remove_uv_source

        if mode == "pypi":
            uninstall_mode_holder.append("pypi")
            pkg_to_uninstall = pypi_name or package_name

            # Remove from pyproject.toml [project.dependencies].
            try:
                remove_dependency(ws_root / "pyproject.toml", pkg_to_uninstall)
            except Exception as e:
                log_holder.append(f"pyproject dep remove failed: {e}")

            # Pip uninstall — best effort.
            if uninstall_cmd_base:
                try:
                    result = subprocess.run(
                        uninstall_cmd_base + [pkg_to_uninstall],
                        cwd=ws_root, capture_output=True, text=True, timeout=60,
                    )
                    excerpt = (result.stdout + "\n" + result.stderr).strip()[-2000:]
                    log_holder.append(excerpt)
                except Exception as e:
                    log_holder.append(f"pip uninstall failed (best-effort): {e}")

        else:
            # Reference / git-submodule mode.
            uninstall_mode_holder.append("reference")

            # Remove dep + uv source from pyproject.toml.
            try:
                remove_dependency(ws_root / "pyproject.toml", package_name)
                remove_uv_source(ws_root / "pyproject.toml", package_name)
            except Exception as e:
                log_holder.append(f"pyproject edit failed: {e}")

            # Remove git submodule.
            target_path = f"external/{name}"
            abs_target = (ws_root / target_path).resolve()
            if abs_target.exists() or (ws_root / ".gitmodules").exists():
                try:
                    subprocess.run(
                        ["git", "submodule", "deinit", "-f", target_path],
                        cwd=ws_root, capture_output=True, text=True, timeout=30,
                    )
                except Exception as e:
                    log_holder.append(f"submodule deinit failed (best-effort): {e}")

                try:
                    r = subprocess.run(
                        ["git", "rm", "-f", target_path],
                        cwd=ws_root, capture_output=True, text=True, timeout=30,
                    )
                    log_holder.append((r.stdout + "\n" + r.stderr).strip()[-500:])
                except Exception as e:
                    log_holder.append(f"git rm failed (best-effort): {e}")

            # Pip uninstall — best effort.
            if uninstall_cmd_base:
                try:
                    result = subprocess.run(
                        uninstall_cmd_base + [package_name],
                        cwd=ws_root, capture_output=True, text=True, timeout=60,
                    )
                    excerpt = (result.stdout + "\n" + result.stderr).strip()[-2000:]
                    log_holder.append(excerpt)
                except Exception as e:
                    log_holder.append(f"pip uninstall failed (best-effort): {e}")

        # Remove workspace.yaml imports entry.
        ws2 = _workspace_yaml.load_workspace(ws_file)
        ws2.get("imports", {}).pop(name, None)
        _workspace_yaml.save_workspace(ws_file, ws2)

    # The live handler wraps ``action`` in ``_commit_or_run`` (commit-on-active-
    # branch). Here the commit is DEFERRED — run ``action`` directly. A raised
    # ``action`` maps to the live ``_commit_or_run`` no-commit fallback
    # ``{"error": f"action failed: {inner}"}, 500``; success maps to code 200.
    try:
        action()
    except Exception as inner:
        _registry.clear_registry_cache()
        return {"error": f"action failed: {inner}"}, 500

    log_excerpt = "\n".join(log_holder)[-500:]
    uninstall_mode = uninstall_mode_holder[0] if uninstall_mode_holder else mode

    # Invalidate registry cache.
    _registry.clear_registry_cache()

    return {
        "ok": True,
        "module": name,
        "install_mode": uninstall_mode,
        "log": log_excerpt,
    }, 200
