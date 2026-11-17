"""Comment-preserving YAML round-trip helper.

Authoring actions in the dashboard edit a single field of a hand-authored
``study.yaml`` / ``workspace.yaml`` and write the file back. Doing that with
``yaml.safe_dump`` discards every comment in the file — so after two GUI edits
the scaffold's guide comments were gone (issue #1618, bug 4 / suggested
direction #7). ruamel's round-trip loader keeps comments, quoting and key order
across an edit; this module centralizes that load+dump so every writer gets the
same behavior, with a plain-``PyYAML`` fallback for the minimal test venv that
has no ruamel.

Usage::

    data, dump = load_yaml_preserving(path.read_text())
    data["field"] = new_value
    path.write_text(dump(data), encoding="utf-8")

``data`` is a mutable mapping (a ruamel ``CommentedMap`` when available, else a
plain ``dict``); assigning plain ``dict`` / ``list`` values into it is fine and
round-trips correctly.
"""

from __future__ import annotations

from typing import Any, Callable

import yaml


def load_yaml_preserving(text: str) -> "tuple[Any, Callable[[Any], str]]":
    """Return ``(data, dump)`` for *text*, preserving comments when possible.

    ``dump(data)`` serializes back to a string. Prefers ruamel's round-trip
    engine (keeps comments / quotes / order); falls back to
    ``yaml.safe_dump(sort_keys=False, allow_unicode=True)`` when ruamel is
    unavailable or the text can't be round-trip-loaded.
    """
    try:
        from io import StringIO

        from ruamel.yaml import YAML

        ry = YAML()
        ry.preserve_quotes = True
        ry.width = 4096
        data = ry.load(text)
        if data is None:
            data = {}

        def _dump(d: Any) -> str:
            buf = StringIO()
            ry.dump(d, buf)
            return buf.getvalue()

        return data, _dump
    except Exception:
        data = yaml.safe_load(text) or {}

        def _dump_fb(d: Any) -> str:
            return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)

        return data, _dump_fb
