"""State the workbench keeps OUTSIDE any workspace: which workspaces the user has trusted to run commands, and a
protected, append-only copy of the command audit trail (docs/plan-autonomous-bash-commands.md, R7 and R8).

It lives in the user's config folder, keyed by the workspace's real path, because a workspace is the one place a
cloned repository (or an approved command) can write: a repo must not be able to mark itself trusted, and a command
must not be able to erase the record of what it ran.

``VIVARIUM_WORKBENCH_CONFIG_DIR`` overrides the folder (tests); otherwise ``$XDG_CONFIG_HOME/vivarium-workbench``,
else ``~/.config/vivarium-workbench``.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vivarium_workbench.lib.atomic_io import atomic_write_text

_LOCK = threading.Lock()


def config_dir() -> Path:
    override = os.environ.get("VIVARIUM_WORKBENCH_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "vivarium-workbench"


def workspace_id(ws_root: Path | str) -> str:
    """A stable key for a workspace: the hash of its real path (so a symlink or a ``..`` cannot alias another)."""
    return hashlib.sha256(os.path.realpath(ws_root).encode("utf-8")).hexdigest()[:32]


def _private_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


# --- workspace trust ------------------------------------------------------------------------------------------


def _trust_file() -> Path:
    return config_dir() / "trusted-workspaces.json"


def _read_trust() -> dict[str, Any]:
    try:
        data = json.loads(_trust_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("trusted", {}) if isinstance(data, dict) and isinstance(data.get("trusted"), dict) else {}


def is_trusted(ws_root: Path | str) -> bool:
    """True only when the user has explicitly trusted exactly this real path. Any read problem means "not trusted"."""
    entry = _read_trust().get(workspace_id(ws_root))
    return isinstance(entry, dict) and entry.get("path") == os.path.realpath(ws_root)


def grant_trust(ws_root: Path | str) -> None:
    """Record the user's explicit decision. Called only from the approval of the trust card, never from a tool."""
    with _LOCK:
        trusted = _read_trust()
        trusted[workspace_id(ws_root)] = {"path": os.path.realpath(ws_root),
                                          "at": datetime.now(timezone.utc).isoformat()}
        _private_dir(config_dir())
        atomic_write_text(_trust_file(), json.dumps({"trusted": trusted}, indent=2))
        os.chmod(_trust_file(), 0o600)


def revoke_trust(ws_root: Path | str) -> bool:
    with _LOCK:
        trusted = _read_trust()
        if trusted.pop(workspace_id(ws_root), None) is None:
            return False
        atomic_write_text(_trust_file(), json.dumps({"trusted": trusted}, indent=2))
        return True


# --- the protected command log -------------------------------------------------------------------------------


def command_log_path(ws_root: Path | str) -> Path:
    return config_dir() / "command-log" / f"{workspace_id(ws_root)}.jsonl"


def append_command_log(ws_root: Path | str, record: dict[str, Any]) -> None:
    """Append one fsync'd JSON line to the per-workspace log in the config folder. Raises ``OSError`` when it cannot be
    written, so the caller can refuse to run an unrecorded command."""
    path = command_log_path(ws_root)
    _private_dir(path.parent.parent)
    _private_dir(path.parent)
    line = json.dumps({"workspace": os.path.realpath(ws_root), **record}, separators=(",", ":")) + "\n"
    with _LOCK:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
