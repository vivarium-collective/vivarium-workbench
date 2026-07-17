"""Git staging policy — the science / environment boundary for workspace commits.

The workspace is a **fused repo** today (REFACTOR-PLAN §2A.4): the scientific
record and the local compute environment share one git history. This module is
the single, layout-aware source of *what a scoped dashboard commit stages*, split
into two **owned** lists so the boundary is explicit:

- :func:`science_paths` — the scientific record (the ``ScientificContent`` domain:
  studies, investigations, authored config).
- :func:`environment_paths` — the local compute environment / code + deps (the
  deferred ``ComputeEnvironment`` domain).

Both return **workspace-root-relative git pathspecs** resolved through
:class:`WorkspacePaths`, so a ``layout:``-relocated workspace stages correctly —
the hardcoded-literal list this replaces did not (a workspace that relocates
``studies/`` to ``workspace/studies/`` staged *nothing*).

Deliberately absent:
- ``reports/`` — generated, not authored.
- Deployment / integration bindings such as ``ui.ptools_server_url`` — a hosted
  third-party tool URL, not a path; it lives *inside* ``workspace.yaml`` and
  eventually moves to a deployment-config layer (see issue #471). It is neither
  science nor compute-environment.

The union :func:`commit_pathspec` is, by logical content, exactly the legacy
``work_state._STAGE_PATHS`` for a default layout — so routing the scoped committer
through here is behavior-preserving except for the layout-correctness fix.
"""
from __future__ import annotations

from pathlib import Path

from vivarium_workbench.lib.workspace_paths import WorkspacePaths


def _dir(rel: str) -> str:
    """Normalize a directory pathspec to trailing-slash form."""
    return rel if rel.endswith("/") else rel + "/"


def science_paths(wp: WorkspacePaths) -> list[str]:
    """Root-relative pathspecs the **scientific record** owns (layout-driven).

    ``workspace.yaml`` is a three-way straddler (science + compute-env config +
    deployment bindings); kept as science for now — the dashboard authors it, and
    lifting ``ui.*`` out to a deployment layer is deferred (#471). ``.gitignore``
    rides here because content flows (e.g. the reference-fetch cache) write it.
    """
    return [
        _dir(wp.rel("studies")),
        _dir(wp.rel("investigations")),
        "workspace.yaml",
        ".gitignore",
    ]


def environment_paths(wp: WorkspacePaths) -> list[str]:
    """Root-relative pathspecs the **local compute environment** owns.

    Owned by the (deferred) ``ComputeEnvironment`` port; staged transitionally by
    env-modifying flows (install-dep, scaffold, source-build) until that port
    formalizes it. ``models/`` and ``external/`` have no ``layout:`` key and stay
    literal; ``scripts/`` resolves through the layout.
    """
    return [
        "pyproject.toml",
        _dir(wp.rel("scripts")),
        "models/",
        "external/",
        ".gitmodules",
    ]


def commit_pathspec(wp: WorkspacePaths) -> list[str]:
    """science + environment — the full set a fused-repo scoped commit stages.

    Equal (by logical content) to the legacy hardcoded ``_STAGE_PATHS``, but
    layout-resolved and split into two owned lists.
    """
    return science_paths(wp) + environment_paths(wp)


def existing(ws_root: Path | str, paths: list[str]) -> list[str]:
    """Filter *paths* to those that exist on disk (mirrors the legacy guard)."""
    ws_root = Path(ws_root)
    return [p for p in paths if (ws_root / p.rstrip("/")).exists()]
