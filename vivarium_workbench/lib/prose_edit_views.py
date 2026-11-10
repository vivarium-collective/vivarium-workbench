"""``POST /api/prose-edit`` — write one whitelisted prose field back into a
study.yaml / investigation.yaml on disk (comment-preserving), so the edit shows
up in ``git diff`` and the user commits it. No auto-commit.

The study-detail page and the SPA investigation view tag editable prose with
``data-edit-field="<yaml path>"`` + ``data-edit-kind`` + the slug; the client
(``static/inline-edit.js``) POSTs ``{kind, slug, field, value}`` here. Only
paths matching the per-kind WHITELIST are writable (no arbitrary YAML surgery).

Paths: dotted keys plus a list-by-id segment, e.g.::

    title
    question
    hypothesis
    findings[F-01].statement
    executive.what_is_this
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import Any

# Whitelisted field paths per kind. A path is allowed if it fullmatches one of
# these patterns. Finalized to the real yaml shapes (see inline-edit recon).
_STUDY_FIELDS = (
    r"title",
    r"question",
    r"hypothesis",
    r"objective",
    r"description",
    r"purpose\.question",
    r"conclusion",
    r"conclusion\.summary",
    r"findings\[[A-Za-z0-9_.-]+\]\.statement",
    r"findings\[[A-Za-z0-9_.-]+\]\.summary",
)
_INVESTIGATION_FIELDS = (
    r"title",
    r"question",
    r"hypothesis",
    r"description",
    r"lead",
    r"executive\.[A-Za-z0-9_]+",
)


def _allowed(kind: str, field: str) -> bool:
    pats = _STUDY_FIELDS if kind == "study" else _INVESTIGATION_FIELDS
    return any(re.fullmatch(p, field) for p in pats)


# ── path parsing + setting ──────────────────────────────────────────────────────

_SEG = re.compile(r"^([A-Za-z0-9_]+)(?:\[([A-Za-z0-9_.-]+)\])?$")


def _parse(path: str) -> "list[tuple[str, str | None]]":
    """Parse ``a.b[ID].c`` → [(a,None),(b,ID),(c,None)]. Raises ValueError."""
    segs: list[tuple[str, str | None]] = []
    for part in path.split("."):
        m = _SEG.match(part)
        if not m:
            raise ValueError(f"bad path segment {part!r}")
        segs.append((m.group(1), m.group(2)))
    return segs


def _descend(node: Any, key: str, idval: "str | None") -> Any:
    """Return the child of *node* for (key, idval), or raise KeyError."""
    if not isinstance(node, dict) or key not in node:
        raise KeyError(key)
    child = node[key]
    if idval is None:
        return child
    if not isinstance(child, list):
        raise KeyError(f"{key} is not a list")
    for item in child:
        if isinstance(item, dict) and str(item.get("id")) == idval:
            return item
    raise KeyError(f"{key}[{idval}] not found")


def _set_path(data: dict, path: str, value: str) -> None:
    """Set the leaf at *path* to *value* (string). Parent must already exist."""
    segs = _parse(path)
    node: Any = data
    for key, idval in segs[:-1]:
        node = _descend(node, key, idval)
    key, idval = segs[-1]
    if idval is not None:
        # setting a leaf that is itself a list item makes no sense here
        raise ValueError("cannot set a list item directly; address its field")
    if not isinstance(node, dict):
        raise KeyError("parent is not a mapping")
    if key not in node:
        raise KeyError(f"{key} not present (refusing to create new keys)")
    node[key] = value


# ── yaml round-trip (comment-preserving) ────────────────────────────────────────


def _yaml():
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    # ruamel's round-trip representer emits None as an empty value, which would
    # rewrite unrelated `key: null` lines to `key:` and add diff noise. Emit
    # None as an explicit `null` so an edit's diff stays minimal.
    y.representer.add_representer(
        type(None),
        lambda rep, data: rep.represent_scalar("tag:yaml.org,2002:null", "null"),
    )
    return y


def _target_path(ws_root: Path, kind: str, slug: str) -> Path:
    from vivarium_workbench.lib.workspace_paths import WorkspacePaths
    from vivarium_workbench.lib.path_safety import plain_name
    slug = plain_name(slug)  # reject traversal / odd names
    paths = WorkspacePaths.load(ws_root)
    if kind == "investigation":
        return paths.dir("investigations") / slug / "investigation.yaml"
    from vivarium_workbench.lib.workspace_paths import study_yaml_path
    return study_yaml_path(ws_root, slug, must_exist=True)


def prose_edit(ws_root: Path, body: dict) -> "tuple[dict, int]":
    """POST /api/prose-edit: write one whitelisted prose field.

    Body ``{kind: study|investigation, slug, field, value}``. Returns
    ``({saved: True, file}, 200)``.

    - 400 missing field / value not a string / kind invalid / field not
      whitelisted / bad path.
    - 404 study/investigation yaml not found, or the field/key doesn't exist.
    - 500 on write failure.
    """
    body = body or {}
    kind = str(body.get("kind") or "").strip()
    slug = str(body.get("slug") or "").strip()
    field = str(body.get("field") or "").strip()
    value = body.get("value")

    if kind not in ("study", "investigation"):
        return {"saved": False, "error": "kind must be study|investigation"}, 400
    if not slug:
        return {"saved": False, "error": "slug required"}, 400
    if not field:
        return {"saved": False, "error": "field required"}, 400
    if not isinstance(value, str):
        return {"saved": False, "error": "value must be a string"}, 400
    if not _allowed(kind, field):
        return {"saved": False, "error": f"field not editable: {field}"}, 400

    try:
        target = _target_path(Path(ws_root), kind, slug)
    except FileNotFoundError:
        return {"saved": False, "error": f"{kind} {slug!r} not found"}, 404
    except Exception as e:  # noqa: BLE001 — plain_name / path errors
        return {"saved": False, "error": str(e)}, 400
    if not target.is_file():
        return {"saved": False, "error": f"{target.name} not found for {slug!r}"}, 404

    try:
        y = _yaml()
        data = y.load(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"saved": False, "error": "yaml root is not a mapping"}, 400
        _set_path(data, field, value)
        buf = StringIO()
        y.dump(data, buf)
    except (KeyError,) as e:
        return {"saved": False, "error": f"no such field: {e}"}, 404
    except ValueError as e:
        return {"saved": False, "error": str(e)}, 400
    except Exception as e:  # noqa: BLE001
        return {"saved": False, "error": f"edit failed: {e}"}, 500

    try:
        from discovery_engine import study_io
        study_io.atomic_write(target, buf.getvalue())
    except Exception:  # noqa: BLE001 — fall back to a plain atomic write
        try:
            from vivarium_workbench.lib.atomic_io import atomic_write_text
            atomic_write_text(target, buf.getvalue())
        except Exception as e:  # noqa: BLE001
            return {"saved": False, "error": f"write failed: {e}"}, 500

    try:
        rel = str(target.relative_to(Path(ws_root)))
    except ValueError:
        rel = target.name
    return {"saved": True, "file": rel}, 200
