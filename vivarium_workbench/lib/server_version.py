"""Served-version introspection for skill<->server skew detection.

Returns the short git revision of the *served tree* (this package's source
checkout) plus the installed package version. Dependency-light and side-effect
free so it is safe to expose in readonly mode: a failed/absent git only degrades
the ``git_rev`` field to ``"unknown"``, never raises.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

# A per-process boot id, minted once when this module is first imported (i.e.
# once per server process). Unlike ``git_rev`` — which only changes when the
# served code changes — ``boot_id`` changes on *every* restart, including a
# restart of the same code. The SPA uses it to notice that the server it loaded
# against has been replaced and to offer a reload, so a long-lived browser tab
# doesn't silently break after a restart (its click handlers fire requests the
# new process never saw a session for).
_BOOT_ID = uuid.uuid4().hex


def _package_version() -> str:
    """Installed distribution version, else the in-tree ``__version__``, else
    ``"unknown"``. Never raises."""
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("vivarium-workbench")
    except Exception:  # noqa: BLE001 — best-effort
        pass
    try:
        from vivarium_workbench import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"


def _git_rev() -> str:
    """Short git SHA of the served tree (the directory this module lives in),
    or ``"unknown"`` when git is unavailable or the tree is not a repo."""
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:  # noqa: BLE001 — git missing / timeout / OS error
        return "unknown"
    if out.returncode != 0:
        return "unknown"
    rev = (out.stdout or "").strip()
    return rev or "unknown"


def boot_id() -> str:
    """This server process's boot id (stable for the life of the process)."""
    return _BOOT_ID


def server_version() -> dict[str, str]:
    """``{"git_rev": "<short sha>", "version": "<pkg version>", "boot_id": "<hex>"}``.

    ``git_rev``/``version`` degrade to ``"unknown"`` rather than failing;
    ``boot_id`` is a per-process uuid that changes on every restart (see module
    docstring) so a client can detect that the server was replaced.
    """
    return {"git_rev": _git_rev(), "version": _package_version(), "boot_id": _BOOT_ID}
