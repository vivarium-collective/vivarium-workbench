"""compose-api's run lifecycle: an adapter over :mod:`remote_run_compose` (which step 1a leaves in place).

Every method calls ``remote_run_compose.<name>`` through the module attribute at call time, so a patch of that
module still takes effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from vivarium_workbench.lib import remote_run_compose


class ComposeApiRuns:
    name = "compose-api"

    def owns(self, run_id: object) -> bool:
        return remote_run_compose.is_run_id(run_id)

    def land(self, ws_root: Path, body: dict) -> tuple[dict, int]:
        return remote_run_compose.land(ws_root, body)

    def status(self, run_id: str) -> tuple[dict, int]:
        return remote_run_compose.status(run_id)

    def progress(self, run_id: str) -> tuple[dict, int]:
        return remote_run_compose.progress(run_id)

    def cancel(self, run_id: str) -> tuple[dict, int]:
        return remote_run_compose.cancel(run_id)

    def trace(self, run_id: str) -> "tuple[Union[bytes, dict], int, str]":
        return remote_run_compose.trace(run_id)
