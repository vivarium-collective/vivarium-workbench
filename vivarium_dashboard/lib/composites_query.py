"""Subprocess-isolated composite discovery for GET /api/composites.

Composite generator discovery via ``@composite_generator`` scanning is unreliable
in a long-running process because stale ``sys.modules`` entries hide newly-added
generators.  Running a fresh Python interpreter in a child process avoids that
problem: the child sees the full, current set.

This module is **stdlib-only** (``subprocess``, ``json``, ``sys``).  It must
never import ``vivarium_dashboard.server`` — the FastAPI seam (``api/app.py``)
calls this from a context where importing server would couple the typed app to
the legacy 16k-line module.

``server.py``'s ``_get_composites`` handler re-imports ``composites_via_subprocess``
so the stdlib server still has one implementation (no duplication).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Fence markers — chosen to be unlikely to appear in real Python output.
_START = "@@@C_START@@@"
_END = "@@@C_END@@@"


def composites_via_subprocess(ws_root: Path) -> dict | None:
    """Return composite discovery data by running a fresh Python subprocess.

    The child process imports ``vivarium_dashboard.server``, sets its
    ``WORKSPACE`` global to *ws_root*, calls ``_composites_data(WORKSPACE)``,
    and prints the result as JSON fenced between ``@@@C_START@@@`` /
    ``@@@C_END@@@`` markers.  Fencing lets the parent ignore the noisy import
    warnings that ``@composite_generator`` scanning emits to stdout.

    Parameters
    ----------
    ws_root:
        Workspace root directory (e.g. ``/path/to/my-workspace``).

    Returns
    -------
    dict | None
        The parsed payload dict on success (``{"composites": [...], ...}``),
        or ``None`` on any failure (timeout, non-zero exit, parse error).
    """
    ws_root_str = str(ws_root)
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        f"import vivarium_dashboard.server as _s\n"
        f"_s.WORKSPACE = Path({ws_root_str!r})\n"
        "try:\n"
        "    _result = _s._composites_data(_s.WORKSPACE)\n"
        "except Exception as _e:\n"
        "    _result = {'composites': [], 'error': str(_e)}\n"
        f"print({_START!r} + json.dumps(_result) + {_END!r})\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(ws_root),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None

    stdout = result.stdout or ""
    start_idx = stdout.find(_START)
    end_idx = stdout.find(_END)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return None

    json_text = stdout[start_idx + len(_START) : end_idx]
    try:
        return json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return None
