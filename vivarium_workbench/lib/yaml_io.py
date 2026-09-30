"""Shared YAML loading for hot read paths: libyaml when available, and an
opt-in per-request parse memo.

Why this exists: ``GET /api/simulations`` read every ``study.yaml`` several
times per run — ``study_owner`` re-parsed the run's study.yaml and then every
``investigation.yaml``, and ``_read_study_yaml_runs`` / ``_build_run_to_studies_map``
each re-parsed every study.yaml — so one request made ~500-2000 pure-Python
``yaml.safe_load`` calls on large (50-60 KB) study files. Measured on the dev
workbench (sms-ecoli, 93 runs, 113 study.yaml): 27 s of a 28 s ``list_simulations``
was YAML parsing.

Two independent fixes live here:

* :data:`SafeLoader` is ``yaml.CSafeLoader`` when PyYAML was built against
  libyaml (``yaml.__with_libyaml__``), else the pure-Python ``yaml.SafeLoader``.
  Both are SAFE loaders with the same (Python-side) constructor and resolver,
  so they produce the same objects for the same document; the C one is ~10x
  faster. ``tests/test_yaml_io.py`` checks the two agree on every fixture
  study/investigation file in the repo.

* :func:`parse_scope` opens a memo for the duration of one request (one
  ``build_simulations_data`` call, including its nested calls on the same
  thread). Inside the scope :func:`load_yaml` parses each file at most once
  per ``(resolved path, st_mtime_ns, st_size)``; outside any scope it parses
  every time, exactly as before. The memo is deliberately REQUEST-scoped, not
  process-wide: a process-wide cache keyed only on mtime can serve stale data
  when a file is rewritten within the filesystem's mtime granularity at the same
  size, and would hold every parsed study in memory forever. Scoped to one
  request, the worst case is what the request would have seen anyway, and the
  stat key still picks up a file rewritten mid-request (e.g. by a concurrent
  run recorder). Cross-request reuse is the job of the result cache in
  ``simulations_index.build_simulations_data_cached``.

Objects returned from inside a scope are SHARED between callers in that
request — treat them as read-only (every caller on the ``/api/simulations``
path only reads). Code that edits a YAML document and writes it back must not
use this module's memo; call ``yaml.safe_load`` (or :func:`load_yaml_text`) on
the file text directly.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
from pathlib import Path
from typing import Any, Iterator, Union

import yaml

#: The fastest available SAFE loader (libyaml-backed when PyYAML has it).
SafeLoader: type = (
    yaml.CSafeLoader if getattr(yaml, "__with_libyaml__", False) else yaml.SafeLoader
)

#: Test hook: number of real parses performed by :func:`load_yaml` /
#: :func:`load_yaml_text` (memo hits are not counted).
_parse_count = 0

_MemoKey = tuple[str, int, int]
_scope: contextvars.ContextVar[dict[_MemoKey, Any] | None] = contextvars.ContextVar(
    "vivarium_workbench_yaml_parse_scope", default=None)


def load_yaml_text(text: str, loader: type | None = None) -> Any:
    """``yaml.safe_load`` semantics on ``text``, using :data:`SafeLoader`."""
    global _parse_count
    _parse_count += 1
    return yaml.load(text, Loader=loader or SafeLoader)  # noqa: S506 — safe loaders only


@contextlib.contextmanager
def parse_scope() -> Iterator[None]:
    """Memoize :func:`load_yaml` for the enclosed call tree. Re-entrant: a
    nested scope reuses the outer memo, and only the outermost one clears it."""
    if _scope.get() is not None:
        yield
        return
    token = _scope.set({})
    try:
        yield
    finally:
        _scope.reset(token)


def in_parse_scope() -> bool:
    """True inside :func:`parse_scope`."""
    return _scope.get() is not None


def load_yaml(path: Union[str, "os.PathLike[str]"]) -> Any:
    """Parse the YAML file at ``path`` (UTF-8) with safe-load semantics.

    Raises exactly what ``yaml.safe_load(Path(path).read_text("utf-8"))`` would
    (``OSError`` for an unreadable file, ``yaml.YAMLError`` for a malformed one),
    so it drops into existing ``try/except`` blocks unchanged. A failed parse is
    never memoized. Returns the document as-is (``None`` for an empty file) —
    callers keep their own ``or {}``.
    """
    p = Path(path)
    memo = _scope.get()
    if memo is None:
        return load_yaml_text(p.read_text(encoding="utf-8"))
    st = p.stat()
    key: _MemoKey = (str(p.resolve()), st.st_mtime_ns, st.st_size)
    try:
        return memo[key]
    except KeyError:
        pass
    data = load_yaml_text(p.read_text(encoding="utf-8"))
    memo[key] = data
    return data
