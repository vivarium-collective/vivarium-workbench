"""Pure builder for ``POST /api/catalog-install``.

Behaviour-preserving port of ``server.Handler._post_catalog_install`` (the
biggest handler in the migration).  Installs a catalog module either directly
from PyPI (when the catalog entry carries a ``pypi_name``) or via the legacy
git-submodule + editable-install path, gated on a system-dependency check.
Returns ``(body, status)`` so the FastAPI route wraps every path in
``JSONResponse`` (preserving the non-200 codes verbatim).  No ``import server``.

``subprocess`` and ``shutil`` are bound at module level so tests monkeypatch
``subprocess.run`` (with a fake ``CompletedProcess`` / a raised
``TimeoutExpired``) and ``shutil.which`` — never spawning a real install.  The
lib helpers are referenced through their modules
(``workspace_deps_views.module_registry`` / ``platform_key`` /
``check_system_dep``, ``install_errors.diagnose``, ``workspace_yaml`` load/save,
``pyproject_edit`` add_dependency / add_uv_source, ``registry.clear_registry_cache``)
so tests can monkeypatch the canonical source module.

The single behavioural difference from the live handler is that the git
**commit is DEFERRED**: the legacy server wraps the install ``action()`` in
``_commit_or_run(commit_msg, action)`` (commit-on-active-branch, with a
409-no-changes→200 commit-only special case).  The FastAPI path runs
``action()`` directly — the install subprocess + the ``workspace.yaml`` mutation
both live inside ``action()`` — then invalidates the registry cache and returns
the success ``{ok, module, install_mode, log}`` 200.  A raised ``action()``
maps to the live ``_commit_or_run`` ``{"error": f"action failed: {inner}"}, 500``
(then enriched with ``log``/``install_mode``/``diagnosis`` exactly as the live
handler does when ``log_excerpt`` is truthy).  The commit-only 409-"no changes"
case cannot occur on the no-commit path, so a successful install returns 200.
The system-deps 409 is a *real* pre-install gate and is reproduced verbatim.
Every other outcome (validation 400/404 + the no-installer 500) is reproduced
byte-identically with ``WORKSPACE`` → ``ws_root``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from vivarium_workbench.lib import install_errors as _install_errors
from vivarium_workbench.lib import pyproject_edit as _pyproject_edit
from vivarium_workbench.lib import registry as _registry
from vivarium_workbench.lib import workspace_deps_views as _workspace_deps
from vivarium_workbench.lib import workspace_yaml as _workspace_yaml
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


#: Default per-step timeout (seconds) for catalog-install subprocesses. Generous
#: because a ``git submodule add`` of a large repo, or a ``pip install`` of a heavy
#: scientific stack, over a slow or proxied network routinely exceeds a short cap
#: (the old 120s git / 180s pip caps were the #1 catalog-install failure in the
#: k8s/HeLx deployment). Overridable per-deployment via the env var below.
_INSTALL_TIMEOUT_DEFAULT = 600


def _install_timeout(default: int = _INSTALL_TIMEOUT_DEFAULT) -> int:
    """Subprocess timeout (seconds) for a catalog-install step.

    Overridable with ``VIVARIUM_WORKBENCH_INSTALL_TIMEOUT``; falls back to
    ``default`` when the env var is unset, non-integer, or non-positive.
    """
    raw = os.environ.get("VIVARIUM_WORKBENCH_INSTALL_TIMEOUT")
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return default
        if value > 0:
            return value
    return default


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Make the workspace's own Python package(s) importable.

    Replicates ``server._ws_add_to_sys_path`` (which uses the ``WORKSPACE``
    global) with the root threaded explicitly: insert ``ws_root`` on
    ``sys.path`` so the workspace package resolves as a top-level package.
    """
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def _install_locked_deps_with_uv(
    uv_path: str,
    venv_py: Path,
    project_dir: Path,
    *,
    timeout: int,
    log_holder: list[str],
) -> None:
    """Install a uv-locked module's dependency closure into the workspace venv.

    A module that ships its own ``uv.lock`` is a uv-managed project whose
    dependency closure — including **git/path-only** deps that a plain
    ``pip install -e`` cannot resolve from PyPI (e.g. ``viva-munk``) — is pinned
    in that lock. We export the lock (excluding the project itself) and install
    it *additively* into the workspace venv with ``uv pip install -r``.

    Deliberately NOT ``uv sync``: ``uv sync`` prunes the target venv down to the
    project's lock, which would remove the workbench and framework from the
    workspace venv. ``export`` → ``pip install`` only adds; it never removes.

    Best-effort: any failure (stale/absent lock, uv error, timeout) is logged
    and swallowed so the editable install of the project itself still runs.
    """
    try:
        exported = subprocess.run(
            [uv_path, "export", "--frozen", "--no-hashes", "--no-emit-project",
             "--format", "requirements-txt"],
            cwd=project_dir, capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        if exported.returncode != 0 or not exported.stdout.strip():
            log_holder.append(
                "uv export of locked deps skipped (falling back to pip): "
                + ((exported.stderr or "empty lock").strip()[-300:])
            )
            return
        installed = subprocess.run(
            [uv_path, "pip", "install", "--python", str(venv_py), "-r", "-"],
            input=exported.stdout, cwd=project_dir, capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        tail = (installed.stdout + "\n" + installed.stderr).strip()[-500:]
        log_holder.append(
            ("locked deps installed via uv:\n" if installed.returncode == 0
             else "locked-dep install via uv failed (continuing with pip):\n")
            + tail
        )
    except subprocess.TimeoutExpired:
        log_holder.append(
            f"locked-dep install via uv timed out after {timeout}s (continuing)"
        )
    except Exception as exc:  # never block the editable install
        log_holder.append(f"locked-dep install via uv errored (continuing): {exc}")


def catalog_install(ws_root: Path, body: dict) -> tuple[dict, int]:
    """Install a catalog module into the workspace venv.

    Behaviour-preserving port of ``_post_catalog_install`` (body
    ``{name, skip_system_deps_check?}``) with the ``_commit_or_run`` commit
    DEFERRED.  Returns ``(response_dict, status_code)``:

      * missing name            → ``({"error": "missing name"}, 400)``
      * module not in catalog    → ``({"error": f"module '{name}' not in catalog"}, 404)``
      * unmet system deps        → 409 with ``{error, name, platform, missing, hint}``
                                   (bypassed by ``skip_system_deps_check=true``)
      * no pip and no uv         → ``({"error": "neither pip nor uv available"}, 500)``
      * install failure          → ``({"error": "action failed: …", "log": …,
                                       "install_mode": …[, "diagnosis"]}, 500)``
      * success                  → ``({"ok": True, "module", "install_mode",
                                       "log": …}, 200)``
    """
    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "missing name"}, 400

    # Load catalog entry from the canonical registry (+ workspace overlay).
    modules = _workspace_deps.module_registry(ws_root)
    entry = next((m for m in modules if m["name"] == name), None)
    if not entry:
        return {"error": f"module '{name}' not in catalog"}, 404

    # System-dependency gate: if the catalog declares native checks and any
    # are unsatisfied, refuse the install with a 409 containing structured
    # info. UI then prompts the user to install the system deps (or POST again
    # with skip_system_deps_check=true).
    sys_deps_block = entry.get("system_dependencies") or {}
    sys_deps_checks = sys_deps_block.get("checks") or []
    if sys_deps_checks and not bool(body.get("skip_system_deps_check")):
        venv_py_for_check = ws_root / ".venv" / "bin" / "python3"
        plat = _workspace_deps.platform_key()
        missing = []
        for check in sys_deps_checks:
            ok, reason = _workspace_deps.check_system_dep(check, venv_py_for_check)
            if ok:
                continue
            install_block = check.get("install") if isinstance(check.get("install"), dict) else None
            install_spec = install_block.get(plat) if install_block else None
            missing.append({
                "name": check.get("name"),
                "description": check.get("description", ""),
                "reason": reason,
                "install": install_spec,
                "notes": check.get("notes"),
            })
        if missing:
            return {
                "error": "unmet system dependencies",
                "name": name,
                "platform": plat,
                "missing": missing,
                "hint": "POST again with skip_system_deps_check=true to proceed anyway, or call /api/system-deps-install first.",
            }, 409

    pypi_name = entry.get("pypi_name")  # optional; if set, install from PyPI

    # ``full_repo`` (set by the Marketplace tab) forces the git-submodule
    # full-repo path even when the catalog entry also carries a `pypi_name`,
    # so the module's top-level `studies/`/`investigations/` land on disk
    # under `external/<name>/` and can be federated. Modules with no git
    # `source` still fall back to PyPI (composites-only federation).
    full_repo = bool(body.get("full_repo"))
    use_pypi = bool(pypi_name) and not (full_repo and entry.get("source"))

    target_path = f"external/{name}"
    abs_target = (ws_root / target_path).resolve()

    # Resolve uv / pip command upfront (before the action closure).
    venv_pip = ws_root / ".venv" / "bin" / "pip"
    venv_py = ws_root / ".venv" / "bin" / "python3"
    uv_path = shutil.which("uv")

    if use_pypi:
        # PyPI path: use uv exclusively (faster, no submodule needed).
        if uv_path and venv_py.exists():
            pypi_install_cmd = [uv_path, "pip", "install", "--python", str(venv_py), pypi_name]
        elif venv_pip.exists():
            pypi_install_cmd = [str(venv_pip), "install", pypi_name]
        else:
            return {"error": "neither pip nor uv available"}, 500
    else:
        # Git-submodule fallback: editable local install.
        if venv_pip.exists():
            pip_cmd_base = [str(venv_pip), "install", "-e"]
        elif uv_path and venv_py.exists():
            pip_cmd_base = [uv_path, "pip", "install", "--python", str(venv_py), "-e"]
        else:
            return {"error": "neither pip nor uv available"}, 500

    package_name = entry.get("package", name)
    catalog_entry = entry  # captured for closure
    log_holder: list[str] = []
    install_mode_holder: list[str] = []

    def action():
        if use_pypi:
            # ---- PyPI install path ----
            install_mode_holder.append("pypi")

            timeout = _install_timeout()
            try:
                result = subprocess.run(
                    pypi_install_cmd,
                    cwd=ws_root, capture_output=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"pip install from PyPI timed out after {timeout}s "
                    "(raise VIVARIUM_WORKBENCH_INSTALL_TIMEOUT to allow longer)"
                )

            excerpt = (result.stdout + "\n" + result.stderr).strip()[-2000:]
            log_holder.append(excerpt)
            if result.returncode != 0:
                raise RuntimeError(f"pip install from PyPI failed:\n{excerpt[-500:]}")

            # workspace.yaml
            _ws_add_to_sys_path(ws_root)

            ws_file = ws_root / "workspace.yaml"
            ws = _workspace_yaml.load_workspace(ws_file)
            ws.setdefault("imports", {})[name] = {
                "source": catalog_entry["source"],
                "ref": catalog_entry["ref"],
                "mode": "pypi",
                "pypi_name": pypi_name,
                "description": catalog_entry.get("description", ""),
                "installed": True,
                "package": package_name,
            }
            _workspace_yaml.save_workspace(ws_file, ws)

            # pyproject.toml — only [project.dependencies]; NO uv.sources entry
            # because the package is on PyPI and resolves without local path mapping.
            try:
                _pyproject_edit.add_dependency(ws_root / "pyproject.toml", pypi_name)
            except Exception as e:
                log_dir = WorkspacePaths.load(ws_root).pbg
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / "catalog-install.log").write_text(
                    f"pyproject edit failed for {name}: {e}\n"
                , encoding="utf-8")

        else:
            # ---- Git-submodule fallback path ----
            install_mode_holder.append("git")

            # Step 1: submodule add if the target is missing OR present-but-empty.
            # A leftover empty `external/<name>/` (a failed prior install, or a
            # submodule dir git left behind on a partial checkout) would otherwise
            # skip the add and drop straight into `pip install -e` on an empty
            # directory, failing opaquely ("neither 'setup.py' nor 'pyproject.toml'
            # found"). Treat empty-as-absent: remove the empty shell so
            # `git submodule add` doesn't refuse an existing path.
            target_empty = abs_target.is_dir() and not any(abs_target.iterdir())
            if not abs_target.exists() or target_empty:
                if target_empty:
                    shutil.rmtree(abs_target, ignore_errors=True)
                # Clean up any stale `.git/modules/<path>` left behind by a
                # previous uninstall — git refuses `submodule add` when one
                # exists. The matching working-tree dir was already
                # verified absent (or just removed) above, and the module is
                # not in .gitmodules, so the leftover is safe to remove.
                stale = ws_root / ".git" / "modules" / target_path
                if stale.is_dir():
                    shutil.rmtree(stale, ignore_errors=True)

                timeout = _install_timeout()
                try:
                    r = subprocess.run(
                        ["git", "submodule", "add", "-b", catalog_entry["ref"],
                         catalog_entry["source"], target_path],
                        cwd=ws_root, capture_output=True,
                        encoding="utf-8", errors="replace", timeout=timeout,
                    )
                except subprocess.TimeoutExpired:
                    raise RuntimeError(
                        f"git submodule add timed out after {timeout}s — a large "
                        "repo over a slow/proxied network. Raise "
                        "VIVARIUM_WORKBENCH_INSTALL_TIMEOUT, or bake the module "
                        "into the image at build time."
                    )
                if r.returncode != 0:
                    raise RuntimeError(
                        f"submodule add failed: {(r.stderr or r.stdout)[:300]}"
                    )

            # Step 1b: if the module ships a uv.lock it declares a pinned
            # dependency closure that may include git/path-only deps a plain
            # `pip install -e` can't resolve from PyPI. Install that closure
            # additively into the workspace venv first (best-effort, never
            # prunes). Modules without a uv.lock are unaffected.
            uv_lock = abs_target / "uv.lock"
            if uv_path and venv_py.exists() and uv_lock.is_file():
                _install_locked_deps_with_uv(
                    uv_path, venv_py, abs_target,
                    timeout=_install_timeout(), log_holder=log_holder,
                )

            # Step 2: pip install -e.
            timeout = _install_timeout()
            try:
                result = subprocess.run(
                    pip_cmd_base + [str(abs_target)],
                    cwd=ws_root, capture_output=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"pip install timed out after {timeout}s "
                    "(raise VIVARIUM_WORKBENCH_INSTALL_TIMEOUT to allow longer)"
                )

            excerpt = (result.stdout + "\n" + result.stderr).strip()[-2000:]
            log_holder.append(excerpt)
            if result.returncode != 0:
                raise RuntimeError(f"pip install failed:\n{excerpt[-500:]}")

            # Step 3: workspace.yaml.
            _ws_add_to_sys_path(ws_root)

            ws_file = ws_root / "workspace.yaml"
            ws = _workspace_yaml.load_workspace(ws_file)
            ws.setdefault("imports", {})[name] = {
                "source": catalog_entry["source"],
                "ref": catalog_entry["ref"],
                "mode": "reference",
                "path": f"external/{name}",
                "description": catalog_entry.get("description", ""),
                "installed": True,
                "install_path": str(abs_target),
                "package": package_name,
            }
            _workspace_yaml.save_workspace(ws_file, ws)

            # Step 4: pyproject.toml — both [project.dependencies] and
            # [tool.uv.sources]. The dep line declares the requirement;
            # the uv-source maps it to the local submodule path so uv can
            # resolve a git-only pbg-* package in CI without going to PyPI.
            try:
                _pyproject_edit.add_dependency(ws_root / "pyproject.toml", package_name)
                _pyproject_edit.add_uv_source(
                    ws_root / "pyproject.toml",
                    package_name,
                    path=f"external/{name}",
                    editable=True,
                )
            except Exception as e:
                # Don't fail the whole install if pyproject edit fails — log it.
                log_dir = WorkspacePaths.load(ws_root).pbg
                log_dir.mkdir(parents=True, exist_ok=True)
                (log_dir / "catalog-install.log").write_text(
                    f"pyproject edit failed for {name}: {e}\n"
                , encoding="utf-8")

    # The live handler wraps ``action`` in ``_commit_or_run`` (commit-on-active-
    # branch). Here the commit is DEFERRED — run ``action`` directly. A raised
    # ``action`` maps to the live ``_commit_or_run`` no-commit fallback
    # ``{"error": f"action failed: {inner}"}, 500``; success maps to code 200.
    install_mode = "pypi" if use_pypi else "git"
    try:
        action()
    except Exception as inner:
        log_excerpt = log_holder[0] if log_holder else ""
        install_mode = install_mode_holder[0] if install_mode_holder else install_mode
        _registry.clear_registry_cache()
        resp: dict = {"error": f"action failed: {inner}"}
        # Live enriches the 500 only when log_excerpt is truthy.
        if log_excerpt:
            _ws_add_to_sys_path(ws_root)
            diag = _install_errors.diagnose(log_excerpt)
            resp["log"] = log_excerpt[-1000:]
            resp["install_mode"] = install_mode
            if diag:
                resp["diagnosis"] = diag.as_dict()
        return resp, 500

    log_excerpt = log_holder[0] if log_holder else ""
    install_mode = install_mode_holder[0] if install_mode_holder else install_mode

    # Invalidate registry cache so next /api/registry call sees fresh data.
    _registry.clear_registry_cache()

    return {
        "ok": True,
        "module": name,
        "install_mode": install_mode,
        "log": log_excerpt[-500:],
    }, 200
