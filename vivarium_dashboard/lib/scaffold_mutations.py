"""Investigation scaffold POST mutation builders.

Pure builders for investigation creation, cloning, and deletion:

    (ws_root: Path, body: dict) -> tuple[dict, int]

File side-effects only — no HTTP, no server imports, no git operations.
The two iset builders are moved verbatim from their ``_for_test`` seams in
``server.py``. The delete builder extracts the rmtree from the server's
``_post_investigation_delete`` action closure.

Batch 21 of the FastAPI strangler-fig migration (POST phase, Phase C).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_ISET_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_name(body: dict[str, Any]) -> str:
    """Extract investigation/study identifier from body (name | study | investigation)."""
    return (
        (body.get("name") or body.get("study") or body.get("investigation") or "")
        .strip()
    )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def investigation_create(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-create — scaffold a new investigation.yaml.

    Moved verbatim from ``server._post_iset_create_for_test``.  Returns the
    new investigation in the same shape as ``GET /api/investigation/<name>``.

    Body:
        name:           required, kebab-case slug (^[a-z0-9][a-z0-9-]*$).
        overview:       optional, becomes the ``description:`` field.
        parent_studies: optional list of study slugs.

    Returns (response_dict, status_code).  File side-effects only — no git.
    """
    name = (body.get("name") or "").strip()
    overview = body.get("overview") or ""
    parent_studies = body.get("parent_studies") or []

    if not name:
        return {"error": "name is required"}, 400
    if not _ISET_SLUG_RE.match(name):
        return {"error": "name must be kebab-case (^[a-z0-9][a-z0-9-]*$)"}, 400

    inv_dir = ws_root / "investigations" / name
    target = inv_dir / "investigation.yaml"
    if target.exists():
        return {"error": f"investigation '{name}' already exists"}, 409

    # Emit a v2-shape investigation.yaml with the narrative spine commented
    # in as TODOs (executive / scientific_argument / biological_story /
    # at_a_glance / glossary / guidelines). The user sees the target shape
    # — the same shape dnaa-replication evolved through use — without having
    # to read docs first. All v2 fields are optional, so the spec validates
    # on day one and the user opts in by uncommenting sections.
    from vivarium_dashboard.lib.scaffold_yaml import v2_investigation_scaffold
    from vivarium_dashboard.lib.atomic_io import atomic_write_text

    body_yaml = v2_investigation_scaffold(
        name,
        title=name,
        overview=overview or None,
        parent_studies=list(parent_studies) if parent_studies else None,
    )

    inv_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, body_yaml)

    # build_iset_detail returns an additive SUPERSET of the legacy seam's
    # minimal `_build_iset_detail_for_test` shape (every legacy key present with
    # an equal value, plus richer fields). Verified additive-only by
    # tests/test_scaffold_mutations_lib.py::TestIsetDetailAdditive.
    from vivarium_dashboard.lib.report_views import build_iset_detail as _bld
    detail = _bld(ws_root, name)
    if detail is None:
        return {"error": "created investigation but failed to load detail"}, 500
    return detail, 200


def iset_clone(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-clone — clone an investigation into a fresh planning state.

    Moved verbatim from ``server._post_iset_clone_for_test``.  Shells out to
    the workspace's ``scripts/clone_investigation.py`` so the dashboard and the
    standalone CLI share a single source of truth.  Returns the new
    investigation in the same shape as ``GET /api/investigation/<target>`` with an extra
    ``clone_summary`` field.

    Body:
        source:         required, slug of the source investigation.
        target:         required, slug of the target investigation.
        source_prefix:  optional, defaults to first dash-segment of source.
        target_prefix:  optional, defaults to first dash-segment of target.

    Returns (response_dict, status_code).  File side-effects only — no git.
    """
    source = (body.get("source") or "").strip()
    target = (body.get("target") or "").strip()
    if not source or not target:
        return {"error": "source and target are required"}, 400
    if not _ISET_SLUG_RE.match(source) or not _ISET_SLUG_RE.match(target):
        return {"error": "source and target must be kebab-case (^[a-z0-9][a-z0-9-]*$)"}, 400
    if source == target:
        return {"error": "source and target must differ"}, 400

    src_dir = ws_root / "investigations" / source
    if not src_dir.is_dir():
        return {"error": f"source investigation '{source}' not found"}, 404
    dst_dir = ws_root / "investigations" / target
    if dst_dir.exists():
        return {"error": f"target investigation '{target}' already exists"}, 409

    script = ws_root / "scripts" / "clone_investigation.py"
    if not script.is_file():
        return {"error": "workspace is missing scripts/clone_investigation.py"}, 501

    argv = [
        sys.executable, str(script),
        "--source", source,
        "--target", target,
        "--source-root", str(ws_root),
        "--target-root", str(ws_root),
        "--json",
    ]
    if body.get("source_prefix"):
        argv += ["--source-prefix", str(body["source_prefix"])]
    if body.get("target_prefix"):
        argv += ["--target-prefix", str(body["target_prefix"])]

    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        return {
            "error": "clone script failed",
            "stderr": (proc.stderr or proc.stdout)[-2000:],
        }, 500
    try:
        summary = json.loads(proc.stdout.strip().split("\n")[-1])
    except (json.JSONDecodeError, IndexError):
        summary = {"stdout_tail": proc.stdout[-500:]}

    # build_iset_detail returns an additive SUPERSET of the legacy seam's
    # minimal shape (see investigation_create) plus the clone_summary field below.
    from vivarium_dashboard.lib.report_views import build_iset_detail as _bld
    detail = _bld(ws_root, target)
    if detail is None:
        return {"error": "cloned investigation but failed to load detail"}, 500
    detail["clone_summary"] = summary
    return detail, 200


def delete_investigation(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/investigation-delete — remove an investigation directory (pure rmtree).

    Extracted from the ``action`` closure in ``server._post_investigation_delete``.
    Returns (response_dict, status_code).  Performs validation + file deletion;
    NO git — the commit stays in the server shim (via ``_active_branch_action``).

    Body:
        name / study / investigation: the investigation slug (any alias accepted).

    Status codes:
        400 — name missing.
        404 — investigation directory not found.
        200 — deletion succeeded.
    """
    import shutil

    name = _extract_name(body)
    if not name:
        return {"error": "name is required"}, 400

    from vivarium_dashboard.lib import study_spec as _study_spec

    inv_dir = _study_spec.study_dir(ws_root, name)
    if not inv_dir.is_dir():
        return {"error": f"investigation '{name}' not found"}, 404

    shutil.rmtree(inv_dir)
    return {"ok": True, "name": name}, 200
