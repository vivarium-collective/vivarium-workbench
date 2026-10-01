"""Containment for request values that are joined onto workspace paths.

A study name, run id, upload filename or file reference arrives in a request and ends up in
``workspace / "studies" / <value>``. Unchecked, ``..``, a separator or an absolute path lets that join
leave the directory it was meant for: reading, overwriting, deleting or moving files elsewhere on the
machine, or changing the workspace's own control files (``.git``, ``.pbg``).

Two rules, one implementation, so a route inherits the guard instead of re-deriving it:

* :func:`plain_name` — a *name* is a single path component.
* :func:`resolve_inside` — a *file reference* stays under a root, avoids the control directories and
  (optionally) has an expected extension.

Containment is lexical (``normpath``), not ``realpath``: the request value is what is untrusted, and the
normalised path is the one that is then used, so there is no gap between the checked and the accessed
path. A symlink the workspace owner placed inside the tree is followed, as it always was.

``vivarium_workbench/env_worker.py`` runs in the workspace's own interpreter and cannot import this module;
it carries a small twin, pinned to this one by ``tests/test_path_safety.py``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from vivarium_workbench.lib.errors import APIError

# The workspace's own control directories: git metadata (a rewritten ``config`` or hook runs commands on
# the next git call) and the dashboard's state (server info, run database, audit log).
PROTECTED_DIRS: frozenset[str] = frozenset({".git", ".pbg"})


def is_plain_name(name: object) -> bool:
    """True iff ``name`` is a single path component (no separator, ``.``/``..``, NUL or drive/absolute form)."""
    return (isinstance(name, str) and bool(name) and name not in (".", "..") and "\x00" not in name
            and "/" not in name and "\\" not in name and not os.path.isabs(name))


def plain_name(name: object, what: str = "name") -> str:
    """Return ``name`` if it is a single, plain path component; else raise a 400."""
    if not is_plain_name(name):
        raise APIError(400, f"invalid {what}: it must be a plain name, not a path")
    return name  # type: ignore[return-value]  # is_plain_name established str


def resolve_inside(
    root: "Path | str",
    rel: object,
    *,
    what: str = "path",
    suffixes: Optional[Iterable[str]] = None,
    deny_dirs: Iterable[str] = PROTECTED_DIRS,
) -> Path:
    """Resolve ``rel`` (relative to ``root``, or absolute but inside it) to a path under ``root``.

    Raises a 400 when the result would leave ``root``, pass through a denied directory, or (with
    ``suffixes``) not end in one of the given extensions. The returned path is the normalised one — use it,
    not ``rel``, for the access.
    """
    if not isinstance(rel, str) or not rel or "\x00" in rel:
        raise APIError(400, f"invalid {what}")
    denied = frozenset(d.casefold() for d in deny_dirs)  # macOS / Windows filesystems ignore case: .GIT is .git
    bases = dict.fromkeys((os.path.abspath(root), os.path.realpath(root)))
    for base in bases:
        full = os.path.normpath(os.path.join(base, rel))
        if full == base or not full.startswith(base.rstrip(os.sep) + os.sep):
            continue
        parts = Path(full[len(base):].lstrip(os.sep)).parts
        if denied.intersection(p.casefold() for p in parts):
            raise APIError(400, f"invalid {what}: that location is not accessible")
        if suffixes is not None and Path(full).suffix.lower() not in {s.lower() for s in suffixes}:
            raise APIError(400, f"invalid {what}: unsupported file type")
        return Path(full)
    raise APIError(400, f"invalid {what}: it must be inside the workspace")


# Request fields that name ONE directory entry (a study, an investigation, a run, a save-point ...). Routes
# join their values onto workspace directories in dozens of places; refusing a non-plain value here, once, for
# every route, is what keeps a route added tomorrow (or one that forgot to validate) from escaping the tree.
# ``tests/test_traversal_sweep.py`` derives its cases from the same set.
IDENTIFIER_FIELDS: frozenset[str] = frozenset({
    "study", "investigation", "inv", "slug", "new_name", "target_name", "composite_name", "run_id",
    "uid", "item_id", "bib_key", "simulator_id", "class_name", "job_id", "source_prefix",
    "target_prefix", "point_id", "names", "run_ids", "parent_studies", "studies",
})


def check_identifiers(params: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> None:
    """Raise a 400 if any :data:`IDENTIFIER_FIELDS` value in ``params`` is a non-empty, non-plain string.

    ``params`` is a mapping or ``(key, value)`` pairs (repeated query parameters). List values are checked
    element-wise. Empty strings and non-strings pass — routes already report those as "missing" / invalid.
    """
    pairs = params.items() if isinstance(params, Mapping) else params
    for key, value in pairs:
        if key not in IDENTIFIER_FIELDS:
            continue
        for v in (value if isinstance(value, (list, tuple)) else (value,)):
            if isinstance(v, str) and v and not is_plain_name(v):
                raise APIError(400, f"invalid {key}: it must be a plain name, not a path")
