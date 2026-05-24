"""UI-driven composite authoring — name validation, serialization, draft I/O,
and subprocess-based resolveability validation.

Mirrors the style of :mod:`vivarium_dashboard.lib.pyproject_edit`: pure
functions that are HTTP-free and unit-testable. The HTTP layer in
``server.py`` calls these and wraps results in JSON responses.

The on-disk composite shape is the same one consumed by
:mod:`vivarium_dashboard.lib.composite_lookup` and the fixture
``tests/_fixtures/ws_increase_demo/pbg_ws_increase_demo/composites/increase-demo.composite.yaml``:

.. code-block:: yaml

    name: my-thing
    description: "..."
    requires:
      processes: [Foo, Bar]
    parameters:
      rate:
        type: float
        default: 2.0
    state:
      foo:
        _type: process
        address: "local:Foo"
        ...

This module provides:

- :func:`validate_name` — slug-rule check, matching the existing study/composite
  rule in CLAUDE.md.
- :func:`serialize_composite` — render a draft dict to YAML with key ordering
  matching the fixture.
- :func:`write_composite` — write the YAML at the canonical path
  ``<workspace>/<pkg>/composites/<name>.composite.yaml``.
- :func:`write_draft` / :func:`read_draft` / :func:`list_drafts` /
  :func:`delete_draft` / :func:`promote_draft` — manage in-progress drafts
  under ``.pbg/composite-drafts/``.
- :func:`validate_composite` — shell out to ``_composite_validate`` (in the
  workspace venv) and return a structured report.
- :func:`gc_drafts` — best-effort sweep of drafts older than ``max_age_days``.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# Mirrors the study/composite slug rule documented in CLAUDE.md "Quirks":
# Study slug: ``^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$`` — underscores allowed.
_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")

# Document section order in the YAML output. Matches the fixture's key
# ordering and what users expect when reading a generated file.
_TOP_LEVEL_ORDER = ("name", "description", "tags", "requires",
                    "parameters", "state")


class CompositeAuthorError(ValueError):
    """User-facing error from this module. HTTP layer maps to 4xx."""


@dataclass
class ValidationReport:
    """Result of :func:`validate_composite`.

    ``ok`` is True iff the subprocess imported the workspace's core, loaded the
    composite document, and reported no unresolved processes / types.
    """

    ok: bool
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    stderr: str = ""


# ---------------------------------------------------------------------------
# Name + slug
# ---------------------------------------------------------------------------

def validate_name(name: str) -> None:
    """Raise :exc:`CompositeAuthorError` if ``name`` isn't a valid slug."""
    if not isinstance(name, str):
        raise CompositeAuthorError("name must be a string")
    if not name:
        raise CompositeAuthorError("name is required")
    if len(name) > 64:
        raise CompositeAuthorError("name must be 64 chars or fewer")
    if not _NAME_RE.match(name):
        raise CompositeAuthorError(
            "name must match ^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$ "
            "(lowercase, digits, dashes, underscores; no leading/trailing "
            "dash or underscore)"
        )


def composite_path(workspace: Path, pkg: str, name: str) -> Path:
    """Canonical on-disk path: ``<workspace>/<pkg>/composites/<name>.composite.yaml``."""
    return workspace / pkg / "composites" / f"{name}.composite.yaml"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _ordered_doc(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with keys reordered to match the fixture convention.

    Keys not in :data:`_TOP_LEVEL_ORDER` follow in their original order, so
    callers can carry extras (e.g. ``tags``, ``visualizations``) without this
    module dropping them.
    """
    out: dict[str, Any] = {}
    for key in _TOP_LEVEL_ORDER:
        if key in doc:
            out[key] = doc[key]
    for key, value in doc.items():
        if key not in out:
            out[key] = value
    return out


def serialize_composite(draft: dict[str, Any]) -> str:
    """Render a draft dict to YAML in fixture-style key ordering.

    The draft must contain ``name`` and ``state`` (the minimum the discovery
    layer accepts; see :func:`composite_lookup._spec_record`). Other keys
    (``description``, ``requires``, ``parameters``, ``tags``) are optional
    and pass through.
    """
    if not isinstance(draft, dict):
        raise CompositeAuthorError("draft must be a dict")
    if "name" not in draft:
        raise CompositeAuthorError("draft missing 'name'")
    if "state" not in draft:
        raise CompositeAuthorError("draft missing 'state'")
    return yaml.safe_dump(_ordered_doc(draft), sort_keys=False,
                          default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# On-disk write — published composites
# ---------------------------------------------------------------------------

def write_composite(workspace: Path, pkg: str, name: str, yaml_text: str,
                    *, overwrite: bool = False) -> Path:
    """Write a published composite to ``<pkg>/composites/<name>.composite.yaml``.

    Refuses to overwrite an existing file unless ``overwrite=True``. The
    target directory is created if missing — workspaces scaffolded from
    ``pbg-template`` already ship ``<pkg>/composites/``, but a freshly-init'd
    workspace might not.
    """
    target = composite_path(workspace, pkg, name)
    if target.exists() and not overwrite:
        raise CompositeAuthorError(
            f"composite '{name}' already exists at "
            f"{target.relative_to(workspace)}; pass overwrite=true to replace"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml_text)
    return target


# ---------------------------------------------------------------------------
# Drafts — ephemeral, git-ignored
# ---------------------------------------------------------------------------

def _drafts_dir(workspace: Path) -> Path:
    return workspace / ".pbg" / "composite-drafts"


def write_draft(workspace: Path, yaml_text: str,
                *, draft_id: str | None = None) -> tuple[str, Path]:
    """Write a draft under ``.pbg/composite-drafts/<draft_id>.composite.yaml``.

    Returns ``(draft_id, path)``. If ``draft_id`` is provided, the same file
    is overwritten (the autosave loop reuses the same id throughout a session).
    Otherwise a fresh UUID-based id is allocated.
    """
    drafts = _drafts_dir(workspace)
    drafts.mkdir(parents=True, exist_ok=True)
    if draft_id is None:
        draft_id = uuid.uuid4().hex[:12]
    elif not re.match(r"^[a-zA-Z0-9_-]{1,64}$", draft_id):
        raise CompositeAuthorError("invalid draft_id")
    path = drafts / f"{draft_id}.composite.yaml"
    path.write_text(yaml_text)
    return draft_id, path


def read_draft(workspace: Path, draft_id: str) -> tuple[str, dict]:
    """Read a draft. Returns ``(yaml_text, parsed_dict)``.

    Raises :exc:`CompositeAuthorError` with a 404-style message if missing.
    """
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", draft_id):
        raise CompositeAuthorError("invalid draft_id")
    path = _drafts_dir(workspace) / f"{draft_id}.composite.yaml"
    if not path.is_file():
        raise CompositeAuthorError(f"draft '{draft_id}' not found")
    text = path.read_text()
    try:
        parsed = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise CompositeAuthorError(f"draft is not valid YAML: {e}") from e
    return text, parsed


def list_drafts(workspace: Path) -> list[dict]:
    """List drafts as ``[{draft_id, name?, mtime, size}]``, newest first."""
    drafts = _drafts_dir(workspace)
    if not drafts.is_dir():
        return []
    out: list[dict] = []
    for p in drafts.glob("*.composite.yaml"):
        draft_id = p.name[: -len(".composite.yaml")]
        name = None
        try:
            parsed = yaml.safe_load(p.read_text()) or {}
            if isinstance(parsed, dict):
                name = parsed.get("name")
        except Exception:
            pass
        stat = p.stat()
        out.append({
            "draft_id": draft_id,
            "name": name,
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        })
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def delete_draft(workspace: Path, draft_id: str) -> bool:
    """Delete a draft. Returns True if removed, False if it was already gone."""
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", draft_id):
        raise CompositeAuthorError("invalid draft_id")
    path = _drafts_dir(workspace) / f"{draft_id}.composite.yaml"
    if not path.exists():
        return False
    path.unlink()
    return True


def promote_draft(workspace: Path, pkg: str, draft_id: str,
                  *, name: str | None = None,
                  overwrite: bool = False) -> Path:
    """Move a draft to ``<pkg>/composites/<name>.composite.yaml``.

    If ``name`` is omitted, the draft's own ``name`` field is used. The draft
    file is deleted after a successful copy.
    """
    yaml_text, parsed = read_draft(workspace, draft_id)
    if name is None:
        name = parsed.get("name") if isinstance(parsed, dict) else None
    if not isinstance(name, str):
        raise CompositeAuthorError("draft has no 'name' field; pass name explicitly")
    validate_name(name)
    target = write_composite(workspace, pkg, name, yaml_text, overwrite=overwrite)
    # Best-effort cleanup: drafts are git-ignored, but leaving stale files
    # clutters the directory listing. Don't fail the promotion if delete fails.
    try:
        delete_draft(workspace, draft_id)
    except Exception:
        pass
    return target


def gc_drafts(workspace: Path, *, max_age_days: float = 7.0) -> int:
    """Delete drafts older than ``max_age_days``. Returns the count removed."""
    import warnings
    drafts = _drafts_dir(workspace)
    if not drafts.is_dir():
        return 0
    cutoff = time.time() - (max_age_days * 86400.0)
    removed = 0
    for p in drafts.glob("*.composite.yaml"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError as e:
            warnings.warn(f"gc_drafts: could not remove {p.name}: {e}",
                          stacklevel=2)
    return removed


# ---------------------------------------------------------------------------
# Validation — subprocess into the workspace venv
# ---------------------------------------------------------------------------

def _workspace_python(workspace: Path) -> str:
    """Pick the workspace's venv python, falling back to the current interpreter.

    Mirrors the resolution in ``server._get_registry_data``.
    """
    venv_py = workspace / ".venv" / "bin" / "python3"
    return str(venv_py) if venv_py.exists() else sys.executable


def validate_composite(workspace: Path, path: Path,
                       *, timeout_s: float = 20.0) -> ValidationReport:
    """Run ``python -m vivarium_dashboard._composite_validate <path>`` in the
    workspace venv and parse the JSON report from stdout.
    """
    py = _workspace_python(workspace)
    cmd = [py, "-m", "vivarium_dashboard._composite_validate", str(path)]
    try:
        result = subprocess.run(
            cmd, cwd=workspace, capture_output=True, text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return ValidationReport(
            ok=False,
            errors=[{"kind": "timeout",
                     "message": f"validation subprocess timed out after {timeout_s}s"}],
            stderr="",
        )

    stderr = (result.stderr or "")[-2000:]
    # The validator prints exactly one JSON line on stdout; pick the last
    # parseable line so warnings before it don't break us.
    payload: dict | None = None
    for line in reversed((result.stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue

    if payload is None:
        return ValidationReport(
            ok=False,
            errors=[{"kind": "subprocess",
                     "message": f"validator returned no JSON (exit {result.returncode})"}],
            stderr=stderr,
        )

    return ValidationReport(
        ok=bool(payload.get("ok")),
        errors=list(payload.get("errors") or []),
        warnings=list(payload.get("warnings") or []),
        stderr=stderr,
    )


# ---------------------------------------------------------------------------
# Soft (no-subprocess) structural checks
# ---------------------------------------------------------------------------

def soft_check(draft: dict[str, Any]) -> list[dict]:
    """Pure-Python sanity checks the client could also run.

    Returns a list of ``{kind, path, message}`` dicts. Used by the route
    handler as a cheap pre-filter before spawning the validator subprocess.
    """
    issues: list[dict] = []

    if not isinstance(draft, dict):
        return [{"kind": "shape", "path": "", "message": "draft is not a dict"}]

    name = draft.get("name")
    if not name:
        issues.append({"kind": "missing", "path": "name",
                       "message": "name is required"})
    state = draft.get("state")
    if not isinstance(state, dict):
        issues.append({"kind": "missing", "path": "state",
                       "message": "state must be a dict"})
        return issues

    for key in state.keys():
        if not isinstance(key, str) or not key:
            issues.append({"kind": "shape", "path": f"state.{key!r}",
                           "message": "state keys must be non-empty strings"})

    for key, value in state.items():
        if not isinstance(value, dict):
            continue
        ntype = value.get("_type")
        if ntype in ("process", "step"):
            if not value.get("address"):
                issues.append({"kind": "missing",
                               "path": f"state.{key}.address",
                               "message": "process/step node requires an address"})

    return issues
