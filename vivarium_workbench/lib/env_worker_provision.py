"""Derive env-worker install specs from a workspace's declared imports.

The env-worker cannot read the workspace PVC, so the workbench (which can) reads
``workspace.yaml`` ``imports:`` and pushes the install specs to the worker via the
``install_modules`` RPC. This module is the *source* side of that: it projects each
installed import into the small spec shape the worker's ``_provision_modules``
consumes (``{name, mode, pypi_name, source, ref, package}``). The worker decides
installability (pypi_name / git source) — this side does not duplicate that logic.

Best-effort: a missing or malformed ``workspace.yaml`` yields ``[]``, never raises.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Fields the worker's _pip_target_for_spec looks at, plus name/package for report.
_SPEC_FIELDS = ("mode", "pypi_name", "source", "ref", "package")


def install_specs_from_workspace(ws_root: "str | Path") -> list[dict[str, Any]]:
    """``[{name, mode, pypi_name, source, ref, package}]`` for installed imports.

    Skips entries explicitly marked ``installed: false`` and any that carry no
    installable form (no ``pypi_name`` and no git ``source``). Never raises.
    """
    ws_root = Path(ws_root)
    try:
        data = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    imports = data.get("imports") or {}
    if not isinstance(imports, dict):
        return []

    out: list[dict[str, Any]] = []
    for name, entry in imports.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("installed") is False:
            continue
        if not (entry.get("pypi_name") or entry.get("source")):
            continue  # nothing the worker could install
        spec: dict[str, Any] = {"name": str(name)}
        for f in _SPEC_FIELDS:
            if entry.get(f) is not None:
                spec[f] = entry[f]
        out.append(spec)
    return out
