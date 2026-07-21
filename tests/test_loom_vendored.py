# tests/test_loom_vendored.py
import importlib
from pathlib import Path


def test_no_external_bigraph_loom_dep():
    text = Path("pyproject.toml").read_text()
    assert "git+https://github.com/vivarium-collective/bigraph-loom" not in text


def test_vendored_asset_dir_importable():
    mod = importlib.import_module("vivarium_workbench.loom_assets")
    assert callable(mod.asset_dir)
