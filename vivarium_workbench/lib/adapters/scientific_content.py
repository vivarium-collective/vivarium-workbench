"""Local-git adapter for the :class:`ScientificContent` port.

Wraps today's ``lib.git_status`` read functions **verbatim** — behavior-preserving.
This module (together with the composition-root factory below) is the only place
that knows the record is a local git working tree; the domain sees the
``ScientificContent`` Protocol, so a cloud (S3/CodeCommit) adapter later swaps in
here without the domain changing.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from vivarium_workbench.lib import git_status
from vivarium_workbench.lib.ports.scientific_content import ScientificContent


@dataclass(frozen=True)
class LocalGitScientificContent:
    """``ScientificContent`` backed by a local git working tree at ``ws_root``."""

    ws_root: Path

    def status(self) -> dict:
        return git_status.build_git_status(self.ws_root)

    def work_status(self) -> dict:
        return git_status.build_work_status(self.ws_root)

    def dirty_status(self) -> dict:
        return git_status.build_dirty_status(self.ws_root)

    def head_version(self) -> str:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.ws_root, capture_output=True, text=True,
        )
        return r.stdout.strip() if r.returncode == 0 else ""


def for_workspace(ws_root: Path | str) -> ScientificContent:
    """Composition-root factory: the ``ScientificContent`` for a workspace.

    The single place that binds the port to the local git adapter today. A cloud
    adapter is introduced by changing only this function.
    """
    return LocalGitScientificContent(Path(ws_root))
