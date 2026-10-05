"""Signal a child's whole process group by its exact pid.

A child started with ``start_new_session=True`` leads its own group (pgid == pid), so signalling that group
reaches the child and anything it spawned, and nothing else. Shared by every place the workbench runs a child
it must be able to stop (the Claude Code provider, the chat's approved commands); never signal by name or pattern.
"""
import contextlib
import os


def signal_group(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)        # start_new_session made pgid == pid: this is our child's group only
