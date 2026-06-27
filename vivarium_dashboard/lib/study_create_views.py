"""Pure builder for the ``POST /api/study-create`` route.

Behaviour-preserving port of the stdlib handler
``server.Handler._post_study_create`` — the "scaffold a new
study" flow: it creates ``investigations/<name>/`` (well, the workspace's
``studies/`` dir keyed by the canonical name) with a ``data/.keep`` plus one of
three scaffold shapes depending on the resolved ``source`` composite:

  * ``@composite_generator`` ref  → ``study.yaml`` via
    :func:`lib.scaffold_yaml.v4_study_scaffold` (dotted ref in ``baseline:``,
    no sidecar).
  * YAML source ref               → ``spec.yaml`` (the legacy v2 shape) plus a
    ``composites/<baseline>.yaml`` sidecar copied verbatim from the source.
  * no ``source``                 → a blank ``spec.yaml`` stub.

The single behavioural difference from the live handler is that the git
**commit is DEFERRED**: the legacy server wraps the scaffold in
``_active_branch_action(commit_msg, action)`` (commit-on-active-branch); the
FastAPI path instead runs the ``action`` inline and returns the success body
directly.  All other outcomes are reproduced byte-identically:

  * missing name                 → ``({"error": "name is required"}, 400)``
  * bad name regex               → ``({"error": "name must match [a-zA-Z0-9_-]+"}, 400)``
  * already exists               → ``({"error": "investigation '<name>' already exists"}, 409)``
  * source not resolvable        → ``({"error": "source composite not found: …"}, 404)``
  * action raises                → ``({"error": "action failed: <e>"}, 500)``
                                   (matching ``_active_branch_action``'s no-workstream
                                   fallback shape in ``_commit_or_run``)
  * success                      → ``({"ok": True, "name": <name>}, 200)``

The ``action`` closure is moved verbatim from the handler (``WORKSPACE`` →
``ws_root``, ``workspace_paths()`` → ``WorkspacePaths.load(ws_root)``).  No
``import server`` here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from vivarium_dashboard.lib.workspace_paths import WorkspacePaths


def _ws_add_to_sys_path(ws_root: Path) -> None:
    """Make the workspace's own Python package(s) importable.

    Replicates ``server._ws_add_to_sys_path`` (which uses the ``WORKSPACE``
    global) with the root threaded explicitly: insert ``ws_root`` on
    ``sys.path`` so the workspace package resolves as a top-level package.
    """
    ws = str(ws_root)
    if ws not in sys.path:
        sys.path.insert(0, ws)


def study_create(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """Scaffold a new study directory.

    Pure file-side-effect builder — no HTTP, no git.  ``source`` is an optional
    composite ref (e.g. ``pkg.composites.foo``) that seeds the investigation
    with a baseline composite.  If omitted an empty study is created.  The
    legacy ``composite`` field is accepted but ignored when ``source`` is
    provided.

    Returns ``(response_dict, status_code)``.
    """
    name = (body.get("name") or "").strip()
    source = (body.get("source") or "").strip()
    if not name:
        return {"error": "name is required"}, 400
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        return {"error": "name must match [a-zA-Z0-9_-]+"}, 400

    inv_dir = WorkspacePaths.load(ws_root).studies / name
    if inv_dir.exists() or (WorkspacePaths.load(ws_root).investigations / name).exists():
        return {"error": f"investigation '{name}' already exists"}, 409

    # Resolve source composite if provided. YAML refs land in the
    # legacy v2-shape with a copied sidecar; @composite_generator refs
    # land in the v3 shape (no sidecar — just store the dotted ref in
    # `baseline:`), which sidesteps the "can't serialize live Process
    # instances" problem for v2ecoli-style composites.
    source_path = None
    is_generator = False
    baseline_name = None
    if source:
        _ws_add_to_sys_path(ws_root)
        from vivarium_dashboard.lib.investigation_migrate import (
            _resolve_composite_source_or_generate,
        )
        try:
            source_path, is_generator, baseline_name = (
                _resolve_composite_source_or_generate(source, ws_root)
            )
        except (FileNotFoundError, ValueError) as e:
            return {"error": f"source composite not found: {e}"}, 404

    def action():
        import shutil as _shutil
        inv_dir.mkdir(parents=True, exist_ok=False)
        (inv_dir / "data").mkdir()
        (inv_dir / "data" / ".keep").write_text("")

        if is_generator and baseline_name:
            # v4-shape scaffold: dotted ref lives in `baseline:` (no
            # sidecar — sidesteps the "can't serialize live Process
            # instances" problem for @composite_generator refs). The 14-
            # section narrative spine is emitted as commented placeholders
            # so the user sees the target shape — the same shape the
            # dnaa-replication investigation evolved through use — without
            # having to read docs first.
            from vivarium_dashboard.lib.scaffold_yaml import (
                v4_study_scaffold,
            )
            body_yaml = v4_study_scaffold(
                name,
                composite=source,
                baseline_name=baseline_name,
            )
            (inv_dir / "study.yaml").write_text(body_yaml)
        elif source_path and baseline_name:
            # Legacy v2-shape spec: seed with a baseline composite entry.
            composites_dir = inv_dir / "composites"
            composites_dir.mkdir(parents=True, exist_ok=True)
            sidecar = composites_dir / f"{baseline_name}.yaml"
            _shutil.copy2(source_path, sidecar)
            spec = {
                "name": name,
                "description": "",
                "composites": [
                    {
                        "name": baseline_name,
                        "source": source,
                        "document": f"./composites/{baseline_name}.yaml",
                    }
                ],
                "simulations": [
                    {
                        "name": "baseline",
                        "composite": baseline_name,
                        "kind": "single",
                        "overrides": {},
                        "steps": 10,
                    }
                ],
                "observables": [],
                "visualizations": [],
                "status": "planned",
            }
            (inv_dir / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
        else:
            # Blank study — no composite yet
            stub = (
                f"name: {name}\n"
                f"description: \"\"\n"
                f"\n"
                f"composites: []\n"
                f"\n"
                f"simulations: []\n"
                f"\n"
                f"observables: []\n"
                f"\n"
                f"visualizations: []\n"
                f"\n"
                f"status: planned\n"
            )
            (inv_dir / "spec.yaml").write_text(stub)

    # Deferred commit: the live handler wraps ``action`` in
    # ``_active_branch_action(commit_msg, action)`` (commit-on-active-branch).
    # The FastAPI path runs the action inline; on raise it mirrors the
    # ``_commit_or_run`` no-workstream fallback shape ``{"error": "action
    # failed: <e>"}, 500``.
    try:
        action()
    except Exception as e:
        return {"error": f"action failed: {e}"}, 500
    return {"ok": True, "name": name}, 200
