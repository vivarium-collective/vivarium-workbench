"""Repair a legacy-corrupted ``workspace.yaml`` so a single bad ``imports``
entry can't permanently block every future catalog install.

Background
----------
``workspace_yaml.load_workspace`` validates the *entire* file against the
workspace's ``.pbg/schemas/workspace.schema.json`` (which requires non-empty
``source``/``ref`` on every ``imports`` entry). Catalog install/uninstall both
call ``load_workspace`` at the top of their mutation, so if any *pre-existing*
entry is malformed the whole install fails with an opaque
``action failed: 'ref' is a required property`` — 500, no traceback — and the
user is stuck until the file is hand-edited.

This state is reached when deployment tooling clears ``source``/``ref`` on a
baked-in package's entry (a since-superseded trick for skipping provisioning —
the supported way is the ``find_spec`` importability skip in env_worker). The
resulting files sit on user NFS and brick installs.

Healing strategy (conservative, no data loss)
--------------------------------------------
Only ever *backfill* fields from the authoritative catalog registry — the same
values a fresh install of that module would write. We never invent placeholder
sources or drop the user's entries. If a malformed entry can't be fully healed
from the catalog, the file is left untouched and normal validation surfaces a
precise error for that entry. Healing is best-effort and idempotent: a file
that already validates is a no-op.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib import workspace_yaml as _workspace_yaml

# Fields the schema requires on every imports entry; the ones we can safely
# restore from the catalog when they're missing or blank.
_HEALABLE_FIELDS = ("source", "ref", "mode")


def _is_blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def heal_workspace_imports(ws_root: Path | str) -> list[str]:
    """Backfill missing/blank ``source``/``ref``/``mode`` on ``imports`` entries
    from the catalog registry, so a legacy-corrupted ``workspace.yaml`` stops
    blocking catalog installs.

    Returns the sorted list of import names that were healed (empty when nothing
    needed repair or the file could not be fully healed). Best-effort: any
    failure returns ``[]`` and leaves the file untouched, so healing can never
    itself break an install.
    """
    ws_root = Path(ws_root)
    ws_file = ws_root / "workspace.yaml"
    try:
        raw = yaml.safe_load(ws_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, dict):
        return []

    # Fast path: a file that already validates needs no repair. A non-validation
    # failure here (e.g. no schema present) means we can't judge validity — bail
    # and leave the file untouched rather than risk a spurious rewrite.
    try:
        _workspace_yaml.validate_workspace(raw)
        return []
    except _workspace_yaml.WorkspaceValidationError:
        pass
    except Exception:
        return []

    imports = raw.get("imports")
    if not isinstance(imports, dict) or not imports:
        return []

    # Authoritative source/ref/mode per module name (catalog + workspace overlay).
    try:
        from vivarium_workbench.lib import workspace_deps_views as _workspace_deps

        catalog = {m["name"]: m for m in _workspace_deps.module_registry(ws_root) if m.get("name")}
    except Exception:
        return []

    healed: list[str] = []
    for name, spec in imports.items():
        if not isinstance(spec, dict):
            continue
        cat = catalog.get(name)
        if not cat:
            continue
        changed = False
        for field in _HEALABLE_FIELDS:
            if _is_blank(spec.get(field)):
                replacement = cat.get(field)
                if not _is_blank(replacement):
                    spec[field] = replacement
                    changed = True
        if changed:
            healed.append(name)

    if not healed:
        return []

    # Only write if the repaired document now fully validates; otherwise leave
    # the original in place so the precise validation error still surfaces.
    try:
        _workspace_yaml.save_workspace(ws_file, raw)
    except _workspace_yaml.WorkspaceValidationError:
        return []
    except Exception:
        return []

    return sorted(healed)
