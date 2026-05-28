"""Read, write, validate workspace.yaml."""
from __future__ import annotations
import json
from pathlib import Path
import yaml
from jsonschema import Draft7Validator, FormatChecker, ValidationError


class WorkspaceValidationError(Exception):
    """Raised when workspace.yaml does not conform to the schema."""


def _schema_path() -> Path:
    from ._root import workspace_root
    return workspace_root() / ".pbg" / "schemas" / "workspace.schema.json"


def _validator() -> Draft7Validator:
    return Draft7Validator(
        json.loads(_schema_path().read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    )


def validate_workspace(data: dict) -> None:
    try:
        _validator().validate(data)
    except ValidationError as e:
        raise WorkspaceValidationError(str(e)) from e


def load_workspace(path: Path | str) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    validate_workspace(data)
    return data


def save_workspace(path: Path | str, data: dict) -> None:
    validate_workspace(data)
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
