"""Remote run ids on the core's own routes (docs/backend-adapters.md §2.2, step P3 I1).

A composite-card run dispatched to a remote backend has no local run row; the loom (and viva-superpowers' skills,
which poll ``/api/composite-run/<run_id>/status`` and read ``status``) track it by a synthetic run id: this prefix
followed by the run's id on its backend, an opaque string whose form says nothing about which backend ran it (the
run's record decides, ``backends.for_existing``). The prefix is an external contract; the core spells it only here.
"""

from __future__ import annotations

#: The synthetic run id of a remote composite run is ``REMOTE_RUN_PREFIX + <the run's id on its backend>``.
REMOTE_RUN_PREFIX = "remote-sim-"


def remote_run_id(backend_run_id: object) -> str:
    """The synthetic composite run id for a remote run whose id on its backend is ``backend_run_id``."""
    return f"{REMOTE_RUN_PREFIX}{backend_run_id}"
