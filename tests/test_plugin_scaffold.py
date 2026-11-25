"""The `scaffold_plugin` helper writes a runnable plugin skeleton that loads
end-to-end through the real plugin contract."""
import importlib
import json
import sys

import pytest

from vivarium_workbench.plugins import PluginRegistry, WorkbenchPlugin
from vivarium_workbench.plugins.scaffold import scaffold_plugin


def test_scaffold_writes_a_loadable_plugin(tmp_path):
    dest = tmp_path / "viva-hello"
    root = scaffold_plugin(dest, "hello")
    assert root == dest

    assert (dest / "pyproject.toml").is_file()
    assert (dest / "viva_hello" / "plugin.py").is_file()
    assert (dest / "viva_hello" / "api.py").is_file()
    assert (dest / "viva_hello" / "static" / "plugin.json").is_file()
    assert (dest / "viva_hello" / "static" / "panel.js").is_file()
    assert (dest / "tests" / "test_hello.py").is_file()
    assert (dest / "README.md").is_file()

    # pyproject declares exactly the entry-point the loader discovers
    pp = (dest / "pyproject.toml").read_text()
    assert '[project.entry-points."vivarium_workbench.plugins"]' in pp
    assert 'hello = "viva_hello.plugin:HelloPlugin"' in pp

    # the UI manifest is valid JSON and its panel entry is a global panel.js defines
    manifest = json.loads((dest / "viva_hello" / "static" / "plugin.json").read_text())
    entry = manifest["panels"][0]["entry"]
    assert ("window." + entry) in (dest / "viva_hello" / "static" / "panel.js").read_text()

    # the generated package imports and registers through the real contract
    sys.path.insert(0, str(dest))
    try:
        mod = importlib.import_module("viva_hello.plugin")
        plugin_cls = mod.HelloPlugin
        assert issubclass(plugin_cls, WorkbenchPlugin)
        plugin = plugin_cls()
        assert plugin.name == "hello"
        reg = PluginRegistry(plugin)
        plugin.register(reg)
        c = reg.contributions()
        assert c.routers, "scaffolded plugin registers a router"
        assert c.static_dir and c.static_dir.is_dir()
        assert c.ui_manifest and c.ui_manifest.is_file()
    finally:
        sys.path.remove(str(dest))
        for m in [m for m in list(sys.modules) if m == "viva_hello" or m.startswith("viva_hello.")]:
            del sys.modules[m]


def test_scaffold_derives_names_for_a_hyphenated_name(tmp_path):
    dest = tmp_path / "viva-knowledge-layer"
    scaffold_plugin(dest, "knowledge-layer")
    pp = (dest / "pyproject.toml").read_text()
    assert 'knowledge-layer = "viva_knowledge_layer.plugin:KnowledgeLayerPlugin"' in pp
    assert (dest / "viva_knowledge_layer" / "plugin.py").is_file()
    plugin_py = (dest / "viva_knowledge_layer" / "plugin.py").read_text()
    assert "class KnowledgeLayerPlugin(WorkbenchPlugin)" in plugin_py
    assert 'name = "knowledge-layer"' in plugin_py


@pytest.mark.parametrize("bad", ["Bad Name", "-leading", "has/slash", "UPPER", ""])
def test_scaffold_rejects_a_bad_name(tmp_path, bad):
    with pytest.raises(ValueError):
        scaffold_plugin(tmp_path / "x", bad)


def test_scaffold_refuses_a_nonempty_dest(tmp_path):
    dest = tmp_path / "viva-hello"
    dest.mkdir()
    (dest / "something").write_text("x")
    with pytest.raises(FileExistsError):
        scaffold_plugin(dest, "hello")
