# tests/test_loom_vendored.py
import importlib
import sys
from pathlib import Path

import pytest

loom_assets = importlib.import_module("vivarium_workbench.loom_assets")


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


# --- robust bundle resolution (asset_dir fallbacks) --------------------------
#
# `_dist` is a gitignored build artifact: a wheel ships it, but a fresh git
# worktree / editable checkout does not until Node runs. Serving such a tree —
# the ecosystem-wide `PYTHONPATH=<worktree>` preview pattern — left asset_dir()
# pointing at an empty own dir, so every composite graph view broke. asset_dir()
# now falls back to any real built bundle (env override, the installed wheel
# shadowed on sys.path, a sibling worktree) instead of its own empty dir.


def _write_bundle(dist_dir: Path) -> Path:
    """Materialize a minimal built loom bundle (just `index.html`) at `dist_dir`."""
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "index.html").write_text("<!doctype html><title>loom</title>")
    return dist_dir


@pytest.fixture(autouse=True)
def _isolate_resolution(monkeypatch):
    """Keep resolution hermetic: no env override, no sibling-worktree git probe,
    no real site-packages scan, and a fresh fallback cache per test. Individual
    tests opt back into the pieces they exercise."""
    monkeypatch.delenv(loom_assets._ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(loom_assets, "_candidate_roots", lambda: [])
    monkeypatch.setattr(loom_assets, "_sibling_worktree_dist", lambda: None)
    loom_assets._reset_fallback_warning()


def test_asset_dir_prefers_own_when_built(monkeypatch, tmp_path):
    own = _write_bundle(tmp_path / "own" / "loom" / "_dist")
    monkeypatch.setattr(loom_assets, "_own_dist", lambda: own)
    assert loom_assets.asset_dir() == own


def test_asset_dir_falls_back_to_installed_copy_when_own_missing(monkeypatch, tmp_path):
    own = tmp_path / "own" / "loom" / "_dist"  # NOT built
    monkeypatch.setattr(loom_assets, "_own_dist", lambda: own)
    site_root = tmp_path / "site-packages"
    installed = _write_bundle(site_root / "vivarium_workbench" / "loom" / "_dist")
    monkeypatch.setattr(loom_assets, "_candidate_roots", lambda: [str(site_root)])
    assert loom_assets.asset_dir() == installed


def test_asset_dir_env_override_wins_over_own(monkeypatch, tmp_path):
    own = _write_bundle(tmp_path / "own" / "loom" / "_dist")
    monkeypatch.setattr(loom_assets, "_own_dist", lambda: own)
    pinned = _write_bundle(tmp_path / "pinned" / "_dist")
    monkeypatch.setenv(loom_assets._ENV_OVERRIDE, str(pinned))
    assert loom_assets.asset_dir() == pinned


def test_asset_dir_env_override_ignored_when_it_has_no_bundle(monkeypatch, tmp_path):
    own = _write_bundle(tmp_path / "own" / "loom" / "_dist")
    monkeypatch.setattr(loom_assets, "_own_dist", lambda: own)
    empty = tmp_path / "empty" / "_dist"
    empty.mkdir(parents=True)
    monkeypatch.setenv(loom_assets._ENV_OVERRIDE, str(empty))
    assert loom_assets.asset_dir() == own


def test_asset_dir_falls_back_to_sibling_worktree(monkeypatch, tmp_path):
    own = tmp_path / "own" / "loom" / "_dist"  # NOT built, no installed copy
    monkeypatch.setattr(loom_assets, "_own_dist", lambda: own)
    sibling = _write_bundle(tmp_path / "canonical" / "loom" / "_dist")
    monkeypatch.setattr(loom_assets, "_sibling_worktree_dist", lambda: sibling)
    assert loom_assets.asset_dir() == sibling


def test_asset_dir_returns_own_dist_when_nothing_is_built(monkeypatch, tmp_path):
    """No bundle anywhere → still return the canonical own `_dist` path (named
    `_dist`) so the diagnostic page / dep-doctor point at the expected location
    and `loom_bundle_present()` reports False rather than crashing."""
    own = tmp_path / "own" / "loom" / "_dist"  # NOT built
    monkeypatch.setattr(loom_assets, "_own_dist", lambda: own)
    resolved = loom_assets.asset_dir()
    assert resolved == own
    assert resolved.name == "_dist"
    from vivarium_workbench.lib import static_serving
    assert static_serving.loom_bundle_present() is False
