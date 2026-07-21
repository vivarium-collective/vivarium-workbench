# tests/test_loom_vendored.py
import importlib
from pathlib import Path


def test_no_external_bigraph_loom_dep():
    text = Path("pyproject.toml").read_text()
    assert "git+https://github.com/vivarium-collective/bigraph-loom" not in text


def test_vendored_asset_dir_importable():
    mod = importlib.import_module("vivarium_workbench.loom_assets")
    assert callable(mod.asset_dir)


def test_wheel_build_hook_is_wired():
    """`_dist` is gitignored, so without a build hook a wheel from a clean
    clone shipped the loom TS source and NO bundle — a silent 404 Explorer for
    every `pip install` / `git+https` consumer. `artifacts` alone only opts the
    directory back in; something has to build it.

    Asserting on config rather than building a wheel keeps this fast; the
    end-to-end check (clean tree -> wheel contains _dist/index.html) is too
    slow and Node-dependent for the default suite.
    """
    text = Path("pyproject.toml").read_text()
    assert "[tool.hatch.build.targets.wheel.hooks.custom]" in text, \
        "the loom build hook is not registered — wheels will ship without _dist"
    assert 'path = "hatch_build.py"' in text
    assert Path("hatch_build.py").is_file()


def test_build_hook_targets_the_dir_asset_dir_resolves():
    """The hook builds via vite (outDir `_dist`); asset_dir() must point at the
    same place, or the wheel ships a bundle nothing ever serves."""
    mod = importlib.import_module("vivarium_workbench.loom_assets")
    assert Path(mod.asset_dir()).name == "_dist"
    vite = Path("vivarium_workbench/loom/vite.config.ts").read_text()
    assert "_dist" in vite
