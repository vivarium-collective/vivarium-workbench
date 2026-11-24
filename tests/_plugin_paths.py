"""Where the SMS plugin's source lives, for the tests that scan it by path (docs/backend-adapters.md step P4b).

Since P4b the plugin is its own distribution in the uv workspace member ``packages/vivarium-workbench-vivaapi/``. A
test that scans a stale path globs nothing and passes while checking nothing, so every scan goes through
:func:`plugin_files`, which refuses an empty result.

:data:`PLUGIN_DIR` is the installed package's directory when the plugin is installed (``find_spec``, without importing
it), else the member's fixed path: the source is in the checkout even when the core is installed alone.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MEMBER_DIR = REPO / "packages" / "vivarium-workbench-vivaapi"


def _plugin_dir() -> Path:
    try:
        spec = importlib.util.find_spec("vivarium_workbench_sms")
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.origin not in (None, "namespace"):
        return Path(spec.origin).parent
    return MEMBER_DIR / "vivarium_workbench_sms"


PLUGIN_DIR = _plugin_dir()


def plugin_files(pattern: str = "*.py", *, recursive: bool = False) -> list[Path]:
    """The plugin's files matching ``pattern`` (``rglob`` when ``recursive``); never empty."""
    files = sorted(PLUGIN_DIR.rglob(pattern) if recursive else PLUGIN_DIR.glob(pattern))
    assert files, f"no {pattern} under {PLUGIN_DIR}: the plugin's source moved, and this scan would check nothing"
    return files
