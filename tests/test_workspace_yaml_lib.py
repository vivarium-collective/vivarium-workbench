import json
import pytest
from vivarium_workbench.lib import workspace_yaml


@pytest.fixture
def _schema(tmp_path, monkeypatch):
    """Point the wrapper at a temp schema so no live workspace is needed."""
    sp = tmp_path / "workspace.schema.json"
    sp.write_text(json.dumps(
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}))
    monkeypatch.setattr(workspace_yaml, "_schema_path", lambda: sp)
    return sp


def test_roundtrip_through_wrapper(tmp_path, _schema):
    p = tmp_path / "workspace.yaml"
    workspace_yaml.save_workspace(p, {"name": "demo"})
    assert workspace_yaml.load_workspace(p) == {"name": "demo"}


def test_wrapper_rejects_invalid(tmp_path, _schema):
    with pytest.raises(workspace_yaml.WorkspaceValidationError):
        workspace_yaml.validate_workspace({})


def test_wrapper_preserves_unicode(tmp_path, _schema):
    p = tmp_path / "workspace.yaml"
    workspace_yaml.save_workspace(p, {"name": "café-µ"})
    assert "café-µ" in p.read_text(encoding="utf-8")
