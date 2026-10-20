"""Feedback → action: the closed half of the reflexive loop (SP3b).

The existing :mod:`feedback_import` / :mod:`feedback_tracking` modules import
and display expert feedback but dead-end at a free-text status string —
nothing turns a feedback item into a tracked, applied change. This module adds
the missing half:

- :func:`feedback_item_id` — a stable id for one annotation entry (section +
  ts + author), so an action can be keyed back to the exact feedback it
  addresses.
- :func:`study_feedback_actions` — a PURE aggregator (mirrors
  :func:`feedback_tracking.study_feedback_tracked`) that reads the feedback
  files, matches ``study-<slug>`` annotations, and joins each with a NEW
  ``actions:`` block (parallel to ``responses:``) keyed by ``item_id``,
  deriving status ``open`` / ``applied`` / ``dismissed``.
- :func:`record_feedback_action` — write an ``actions[item_id]`` entry (the
  responder skill's deterministic write-helper; the *judgment* of which kind /
  proposed_text lives in the skill, not here).
- :func:`apply_feedback_action` — dispatch by ``kind`` and effect the change:
  - ``next_action`` → write ``findings[<target_finding>].next_action`` (THE
    SP3a JOIN — once written, SP3a's seed-from-finding can originate a child
    study, closing the loop).
  - ``study-seed`` → :func:`seed_from_followup.resolve_seed_source` +
    :func:`seed_from_followup.write_child_study`.
  - ``finding`` → draft a new finding stub in the target study.
  - ``design-edit`` → record a tracked note (NO silent design mutation).
  Then flip the action ``status: applied`` (by/at). IDEMPOTENT — a re-apply is
  a no-op (``already_applied``).

All writes go through ruamel round-trips (comment-preserving) + the atomic
:mod:`study_io` writer. Nothing here makes a judgment call — that is the
``/viva-study feedback-respond`` skill's job (AI-free split).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Iterator

import yaml
from ruamel.yaml import YAML

from viva_superpowers import study_io
from viva_superpowers.feedback_import import _feedback_files
from viva_superpowers.workspace_paths import WorkspacePaths
from vivarium_workbench.lib.workspace_paths import study_yaml_path

# Action status sets ──────────────────────────────────────────────────────────

_APPLIED_STATUSES = {"applied", "done", "resolved"}
_DISMISSED_STATUSES = {"dismissed", "wontfix", "rejected"}

_VALID_KINDS = ("next_action", "finding", "design-edit", "study-seed")


# ── stable item id ────────────────────────────────────────────────────────────


def feedback_item_id(section: str, ts: str, author: str) -> str:
    """A stable, content-derived id for one annotation entry.

    Derived from ``section`` + ``ts`` + ``author`` (the natural key of an
    annotation entry) so an ``actions[item_id]`` block can point back at the
    exact feedback it addresses. Deterministic across processes (sha1 hex,
    truncated) — no randomness, no clock.
    """
    h = hashlib.sha1(
        "\x00".join((section or "", ts or "", author or "")).encode("utf-8")
    )
    return "fb-" + h.hexdigest()[:16]


def _derive_action_status(action: dict | None) -> str:
    """Map an ``actions[item_id]`` entry to ``open`` / ``applied`` / ``dismissed``."""
    if not action or not isinstance(action, dict):
        return "open"
    raw = str(action.get("status") or "").lower().strip()
    if raw in _APPLIED_STATUSES:
        return "applied"
    if raw in _DISMISSED_STATUSES:
        return "dismissed"
    return "open"


# ── pure aggregator ───────────────────────────────────────────────────────────


def study_feedback_actions(
    workspace: Path | str,
    study_slug: str,
) -> dict[str, Any]:
    """Aggregate all feedback for *study_slug*, joined with its tracked actions.

    Mirrors :func:`feedback_tracking.study_feedback_tracked`: scans every
    ``investigations/<inv>/`` for feedback files (via ``_feedback_files``),
    matches sections by the ``study-<slug>`` prefix, and for each annotation
    entry joins the NEW ``actions:`` block (keyed by ``feedback_item_id``).

    Returns ``{"items": [...], "summary": {open, applied, dismissed, total}}``.
    Each item::

        {
            "item_id":   str,
            "section":   str,
            "ts":        str,
            "author":    str,
            "text":      str,
            "report_id": str | None,
            "action":    dict | None,   # the actions[item_id] entry, if any
            "status":    "open" | "applied" | "dismissed",
        }

    PURE read — no writes. Items are newest-first; malformed files are skipped.
    """
    prefix = "study-" + study_slug

    inv_root = WorkspacePaths.load(workspace).investigations
    if not inv_root.is_dir():
        return _empty_result()

    items: list[dict] = []

    for inv_dir in sorted(inv_root.iterdir()):
        if not inv_dir.is_dir():
            continue
        for path in _feedback_files(inv_dir):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError):
                continue
            if not isinstance(data, dict):
                continue

            meta = data.get("meta") or {}
            report_id = meta.get("report_id") if isinstance(meta, dict) else None

            annotations = data.get("annotations") or {}
            if not isinstance(annotations, dict):
                continue

            actions = data.get("actions") or {}
            if not isinstance(actions, dict):
                actions = {}

            for section_id, entries in annotations.items():
                if section_id != prefix and not section_id.startswith(prefix + "-"):
                    continue
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    ts = entry.get("ts") or ""
                    author = entry.get("author") or ""
                    item_id = feedback_item_id(section_id, ts, author)
                    action = actions.get(item_id)
                    if not isinstance(action, dict):
                        action = None
                    items.append({
                        "item_id": item_id,
                        "section": section_id,
                        "ts": ts,
                        "author": author,
                        "text": entry.get("text") or "",
                        "report_id": report_id,
                        "action": action,
                        "status": _derive_action_status(action),
                    })

    items.sort(key=lambda i: i.get("ts") or "", reverse=True)
    return {"items": items, "summary": _summarize(items)}


def _empty_result() -> dict:
    return {
        "items": [],
        "summary": {"open": 0, "applied": 0, "dismissed": 0, "total": 0},
    }


def _summarize(items: list[dict]) -> dict:
    open_c = sum(1 for i in items if i["status"] == "open")
    applied_c = sum(1 for i in items if i["status"] == "applied")
    dismissed_c = sum(1 for i in items if i["status"] == "dismissed")
    return {
        "open": open_c,
        "applied": applied_c,
        "dismissed": dismissed_c,
        "total": len(items),
    }


# ── ruamel helpers (comment-preserving) ───────────────────────────────────────


def _ruamel() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    return y


def _load_rt(path: Path):
    y = _ruamel()
    data = y.load(path.read_text(encoding="utf-8"))
    return y, (data if data is not None else {})


def _dump_rt(y: YAML, path: Path, data) -> None:
    buf = StringIO()
    y.dump(data, buf)
    study_io.atomic_write(path, buf.getvalue())


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── locate the feedback file carrying a given item_id ─────────────────────────


def _iter_feedback_files(workspace: Path | str) -> Iterator[Path]:
    inv_root = WorkspacePaths.load(workspace).investigations
    if not inv_root.is_dir():
        return
    for inv_dir in sorted(inv_root.iterdir()):
        if inv_dir.is_dir():
            yield from _feedback_files(inv_dir)


def _find_item_file(workspace: Path | str, item_id: str) -> tuple[Path, dict, str] | None:
    """Find the feedback file whose annotations contain *item_id*.

    Returns ``(path, parsed_data, section_id)`` or ``None``. Match is by
    recomputing :func:`feedback_item_id` over each annotation entry — so the
    file is found even before any ``actions`` block exists for the item.
    """
    for path in _iter_feedback_files(workspace):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        annotations = data.get("annotations") or {}
        if not isinstance(annotations, dict):
            continue
        for section_id, entries in annotations.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                iid = feedback_item_id(section_id, entry.get("ts") or "",
                                       entry.get("author") or "")
                if iid == item_id:
                    return path, data, section_id
    return None


# ── record an action (the responder skill's write-helper) ─────────────────────


def record_feedback_action(
    workspace: Path | str,
    item_id: str,
    *,
    kind: str,
    target_study: str,
    proposed_text: str,
    target_finding: str | None = None,
    by: str | None = None,
    **extra: Any,
) -> dict:
    """Write an ``actions[item_id]`` entry to the feedback yaml (open status).

    Deterministic — the *judgment* (which ``kind``, what ``proposed_text``)
    is the responder skill's; this just persists it as a tracked artifact.
    Comment-preserving (ruamel round-trip). Returns ``{recorded, path, kind}``.
    """
    if kind not in _VALID_KINDS:
        return {"recorded": False, "error": f"unknown kind {kind!r} (valid: {_VALID_KINDS})"}
    found = _find_item_file(workspace, item_id)
    if found is None:
        return {"recorded": False, "error": f"no annotation matches item_id {item_id!r}"}
    path, _, _ = found

    y, data = _load_rt(path)
    actions = data.get("actions")
    if actions is None:
        actions = {}
        data["actions"] = actions

    action: dict = {
        "kind": kind,
        "target_study": target_study,
        "proposed_text": proposed_text,
        "status": "open",
    }
    if target_finding:
        action["target_finding"] = target_finding
    if by:
        action["proposed_by"] = by
    for k, v in extra.items():
        if v is not None:
            action[k] = v
    actions[item_id] = action

    _dump_rt(y, path, data)
    return {"recorded": True, "path": str(path), "kind": kind}


# ── apply ─────────────────────────────────────────────────────────────────────


def _write_finding_next_action(study_yaml: Path, finding_id: str, text: str) -> None:
    """Set ``findings[<finding_id>].next_action = text`` (ruamel, atomic).

    This is the SP3a join: once a finding carries a next_action, SP3a's
    seed-from-finding can originate a child study from it.
    """
    y, spec = _load_rt(study_yaml)
    findings = spec.get("findings")
    if not isinstance(findings, list):
        raise KeyError(f"{study_yaml}: no findings list")
    for f in findings:
        if isinstance(f, dict) and f.get("id") == finding_id:
            f["next_action"] = text
            _dump_rt(y, study_yaml, spec)
            return
    raise KeyError(f"finding {finding_id!r} not in {study_yaml}")


def _draft_finding_stub(study_yaml: Path, text: str, item_id: str) -> str:
    """Append a draft finding stub; return its id."""
    y, spec = _load_rt(study_yaml)
    findings = spec.get("findings")
    if findings is None:
        findings = []
        spec["findings"] = findings
    existing = {f.get("id") for f in findings if isinstance(f, dict)}
    n = len(findings) + 1
    while f"F-{n:02d}" in existing:
        n += 1
    fid = f"F-{n:02d}"
    findings.append({
        "id": fid,
        "statement": text,
        "status": "draft",
        "from_feedback": item_id,
    })
    _dump_rt(y, study_yaml, spec)
    return fid


def _flip_action_applied(path: Path, item_id: str, *, by: str, at: str,
                         note: str | None = None) -> None:
    """Mark ``actions[item_id].status = applied`` (by/at) — comment-preserving."""
    y, data = _load_rt(path)
    actions = data.get("actions") or {}
    action = actions.get(item_id)
    if not isinstance(action, dict):
        raise KeyError(f"action {item_id!r} not in {path}")
    action["status"] = "applied"
    action["by"] = by
    action["at"] = at
    if note:
        action["applied_note"] = note
    _dump_rt(y, path, data)


def apply_feedback_action(
    workspace: Path | str,
    item_id: str,
    *,
    by: str = "claude",
) -> dict:
    """Apply the tracked action for *item_id* and flip it to ``applied``.

    Dispatch by ``action.kind``:

    - ``next_action`` → write ``findings[<target_finding>].next_action`` (the
      SP3a join).
    - ``study-seed`` → seed a child study via SP3a's ``resolve_seed_source`` +
      ``write_child_study``.
    - ``finding`` → draft a new finding stub in the target study.
    - ``design-edit`` → record a tracked note (no silent design mutation).

    IDEMPOTENT — re-applying an already-applied action returns
    ``{"already_applied": True}`` without re-writing. Best-effort with clear
    errors (returns ``{"error": ...}`` rather than raising for the common
    not-found cases).
    """
    found = _find_item_file(workspace, item_id)
    if found is None:
        return {"applied": False, "error": f"no feedback item matches item_id {item_id!r}"}
    path, data, _section = found

    actions = data.get("actions") or {}
    action = actions.get(item_id)
    if not isinstance(action, dict):
        return {"applied": False, "error": f"no action recorded for item_id {item_id!r}"}

    if _derive_action_status(action) == "applied":
        return {"already_applied": True, "item_id": item_id,
                "kind": action.get("kind")}

    kind = action.get("kind")
    target_study = action.get("target_study")
    proposed_text = action.get("proposed_text") or ""
    result: dict = {"item_id": item_id, "kind": kind}
    applied_note: str | None = None

    try:
        if kind == "next_action":
            if not target_study:
                return {"applied": False, "error": "next_action action has no target_study"}
            target_finding = action.get("target_finding")
            if not target_finding:
                return {"applied": False, "error": "next_action action has no target_finding"}
            _write_finding_next_action(
                study_yaml_path(workspace, target_study), target_finding, proposed_text)
            result["target_finding"] = target_finding
            result["wrote"] = "findings.next_action"

        elif kind == "finding":
            if not target_study:
                return {"applied": False, "error": "finding action has no target_study"}
            fid = _draft_finding_stub(
                study_yaml_path(workspace, target_study), proposed_text, item_id)
            result["drafted_finding"] = fid

        elif kind == "design-edit":
            # NO silent design mutation — record the proposal as a tracked note.
            applied_note = proposed_text
            result["recorded_note"] = True

        elif kind == "study-seed":
            from viva_superpowers import seed_from_followup
            if not target_study:
                return {"applied": False, "error": "study-seed action has no target_study"}
            parent_spec = study_io.load_yaml(study_yaml_path(workspace, target_study))
            seed_source = seed_from_followup.resolve_seed_source(
                parent_spec,
                finding_id=action.get("target_finding"),
                proposal_id=action.get("proposal_id"),
            )
            seeded = seed_from_followup.write_child_study(
                workspace, target_study, seed_source,
                new_slug=action.get("new_slug"),
            )
            result["seeded_study"] = seeded.get("new_slug")
            result["child_path"] = seeded.get("child_path")

        else:
            return {"applied": False, "error": f"unknown action kind {kind!r}"}
    except (FileNotFoundError, KeyError, ValueError, FileExistsError) as e:
        return {"applied": False, "error": str(e), "kind": kind}

    _flip_action_applied(path, item_id, by=by, at=_now_iso(), note=applied_note)
    result["applied"] = True
    return result
