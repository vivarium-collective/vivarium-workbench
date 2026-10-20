"""Tolerant git text queries (the "Family A" pattern).

Run ``git -C <cwd> <args>`` and return stripped stdout, or a caller-chosen
``default`` sentinel on any failure (non-zero exit, git missing, timeout, OS
error). Never raises — provenance/version lookups must degrade, not block.
"""
from __future__ import annotations

import subprocess
from typing import Any


def git_text(cwd, *args: str, default: Any = "", timeout: float = 5):
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except Exception:  # noqa: BLE001 — git missing / timeout / OS error → sentinel
        return default
    return result.stdout.strip() if result.returncode == 0 else default
