"""The ``ScientificContent`` port — the versioned science system-of-record.

Domain code depends on this Protocol, never on a concrete adapter (which lives in
``lib.adapters`` and is chosen once at the composition root).

**Scope of this iteration (PR-3):** the **read / versioning-status surface** the
app already uses. The write/commit core is deliberately absent — the FastAPI app
*defers* commits (mutations write uncommitted; versioning is a user-initiated
commit-all / push), so ``snapshot``/write verbs await a commit-model decision
(REFACTOR-PLAN §5A: deferred-commit-all vs. scoped-per-mutation). Adding them here
before that decision would bake the wrong shape in.

Version identifiers are **opaque** (a git SHA underneath today, never surfaced as
"SHA"/"push") so a future S3/CodeCommit adapter swaps in without the domain seeing
git-isms (§2A.3).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ScientificContent(Protocol):
    """Read / versioning-status surface of the science record."""

    def status(self) -> dict:
        """Overall version/sync status — branch, remote push state, ahead/behind,
        PR linkage, dirty-file count, host availability."""
        ...

    def work_status(self) -> dict:
        """Active-workstream status (``{active: false}`` when none is running,
        else the full branch/ahead-behind/staleness/push payload)."""
        ...

    def dirty_status(self) -> dict:
        """Filtered list of uncommitted files in the record (excludes generated
        paths — reports/, out/, .pbg/)."""
        ...

    def head_version(self) -> str:
        """Opaque id of the current record version (empty string if none)."""
        ...
