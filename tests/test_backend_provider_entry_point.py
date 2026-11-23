"""Providers are found through the ``vivarium_workbench.backends`` entry point (docs/backend-adapters.md step P4a).

The registry (``lib/backends/provider._load``) is driven here through its ``_entry_points`` seam with real
``importlib.metadata.EntryPoint`` objects naming throwaway modules, so these tests need no plugin (they pass in the
core-only job) unless marked ``plugin``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

import vivarium_workbench.lib.backends as backends
from vivarium_workbench.lib.backends import provider as provider_mod

ROOT = Path(__file__).resolve().parent.parent
GROUP = provider_mod.ENTRY_POINT_GROUP


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(provider_mod.PROVIDERS_ENV, raising=False)
    backends.reset_registry()
    yield
    backends.reset_registry()


@pytest.fixture
def mods(tmp_path, monkeypatch):
    """Write throwaway modules (name -> source) importable for this test only."""
    monkeypatch.syspath_prepend(str(tmp_path))
    written: list[str] = []

    def write(**sources: str) -> None:
        for name, src in sources.items():
            (tmp_path / f"{name}.py").write_text(textwrap.dedent(src))
            written.append(name)

    yield write
    for name in written:
        sys.modules.pop(name, None)


def _use(monkeypatch, *pairs: tuple[str, str], plugin_installed: bool = False) -> None:
    eps = sorted((importlib.metadata.EntryPoint(name, value, GROUP) for name, value in pairs),
                 key=lambda ep: (ep.name, ep.value))
    monkeypatch.setattr(provider_mod, "_entry_points", lambda: eps)
    monkeypatch.setattr(provider_mod, "_plugin_installed", lambda: plugin_installed)


_GOOD = """
    class P:
        name = "{name}"
        api_version = {version}

        def backends(self):
            return ()

    PROVIDER = P()
"""


# --- the declaration ------------------------------------------------------------------------------------------------

def test_pyproject_declares_the_group_and_the_installed_metadata_matches():
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["entry-points"][GROUP]
    assert declared == {"sms": "vivarium_workbench_sms.provider:PROVIDER"}
    assert all(v.startswith(provider_mod._PLUGIN + ".") for v in declared.values())
    installed = {ep.name: ep.value for ep in importlib.metadata.entry_points(group=GROUP)}
    assert installed == declared, ("the installed metadata does not match pyproject.toml: a stale dev install; "
                                   "re-run `uv sync`")


# --- absent and broken ----------------------------------------------------------------------------------------------

def test_an_entry_point_whose_module_is_not_installed_is_absent(monkeypatch):
    _use(monkeypatch, ("ghost", "no_such_provider_pkg.provider:PROVIDER"))
    assert provider_mod.providers() == ()


def test_a_missing_dependency_inside_a_provider_is_broken(monkeypatch, mods):
    mods(broken_dep_provider="import no_such_dependency_xyz\nPROVIDER = None\n")
    _use(monkeypatch, ("broken", "broken_dep_provider:PROVIDER"))
    with pytest.raises(ImportError, match="no_such_dependency_xyz"):
        provider_mod.providers()


def test_a_missing_provider_object_is_broken(monkeypatch, mods):
    mods(empty_provider="X = 1\n")
    _use(monkeypatch, ("empty", "empty_provider:PROVIDER"))
    with pytest.raises(ImportError, match="PROVIDER"):
        provider_mod.providers()


def test_a_missing_submodule_of_a_present_package_is_broken(monkeypatch, tmp_path):
    """The package is installed but its provider module is missing: broken, never mistaken for an absent plugin."""
    pkg = tmp_path / "half_installed_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    _use(monkeypatch, ("half", "half_installed_pkg.provider:PROVIDER"))
    with pytest.raises(ModuleNotFoundError, match="half_installed_pkg.provider"):
        provider_mod.providers()
    sys.modules.pop("half_installed_pkg", None)


def test_a_namespace_leftover_of_the_package_is_absent(monkeypatch, tmp_path):
    """Only a ``__pycache__`` directory is left where the package was (a namespace package): absent."""
    (tmp_path / "leftover_pkg" / "__pycache__").mkdir(parents=True)
    monkeypatch.syspath_prepend(str(tmp_path))
    _use(monkeypatch, ("left", "leftover_pkg.provider:PROVIDER"))
    assert provider_mod.providers() == ()
    sys.modules.pop("leftover_pkg", None)


def test_a_provider_that_loads_is_active(monkeypatch, mods):
    mods(good_provider=_GOOD.format(name="good", version=provider_mod.PROVIDER_API_VERSION))
    _use(monkeypatch, ("good", "good_provider:PROVIDER"))
    (p,) = provider_mod.providers()
    assert p is sys.modules["good_provider"].PROVIDER


# --- the interface version and the name -----------------------------------------------------------------------------

@pytest.mark.parametrize("version", [provider_mod.PROVIDER_API_VERSION + 1, None])
def test_a_provider_for_another_interface_version_is_refused(monkeypatch, mods, version):
    src = _GOOD.format(name="old", version=version)
    if version is None:
        src = src.replace("api_version = None\n", "")
    mods(old_provider=src)
    _use(monkeypatch, ("old", "old_provider:PROVIDER"))
    with pytest.raises(provider_mod.ProviderVersionError, match=f"offers version {provider_mod.PROVIDER_API_VERSION}"):
        provider_mod.providers()
    assert issubclass(provider_mod.ProviderVersionError, ImportError)


def test_the_server_reports_a_version_mismatch(monkeypatch, mods, capsys, tmp_path):
    from vivarium_workbench.lib import startup

    mods(old_provider2=_GOOD.format(name="old", version=provider_mod.PROVIDER_API_VERSION + 1))
    _use(monkeypatch, ("old", "old_provider2:PROVIDER"))
    assert startup.serve_fastapi(tmp_path, port=0) == 2
    assert "the backend plugin failed to load: ProviderVersionError" in capsys.readouterr().err


def test_a_provider_named_otherwise_than_its_entry_point_is_refused(monkeypatch, mods):
    mods(misnamed_provider=_GOOD.format(name="other", version=provider_mod.PROVIDER_API_VERSION))
    _use(monkeypatch, ("mine", "misnamed_provider:PROVIDER"))
    with pytest.raises(ImportError, match="named 'other'"):
        provider_mod.providers()


# --- duplicates -----------------------------------------------------------------------------------------------------

def test_one_object_under_two_entry_points_is_kept_once(monkeypatch, mods):
    mods(twice_provider=_GOOD.format(name="twice", version=provider_mod.PROVIDER_API_VERSION))
    _use(monkeypatch, ("twice", "twice_provider:PROVIDER"), ("twice", "twice_provider:PROVIDER"))
    assert len(provider_mod.providers()) == 1


def test_one_name_from_two_values_is_refused_before_loading(monkeypatch):
    # neither module exists: the refusal must come before any load (which would skip both as absent)
    _use(monkeypatch, ("sms", "old_dist_ghost.provider:PROVIDER"), ("sms", "new_dist_ghost.provider:PROVIDER"))
    with pytest.raises(ImportError, match="same name"):
        provider_mod.providers()


class _Backend:
    def __init__(self, name):
        self.name = name
        self.unrouted = None


class _Prov:
    api_version = provider_mod.PROVIDER_API_VERSION

    def __init__(self, name, *kinds):
        self.name = name
        self._b = tuple(_Backend(k) for k in kinds)

    def backends(self):
        return self._b

    def check_config(self, named):
        return None


def test_two_backends_of_one_kind_are_refused(monkeypatch):
    monkeypatch.setattr(provider_mod, "providers", lambda: (_Prov("a", "k"), _Prov("b", "k")))
    with pytest.raises(provider_mod.ProviderConflictError, match="more than one backend is named 'k'"):
        backends.by_kind("k")


def test_a_provider_backend_named_compose_api_is_refused(monkeypatch):
    monkeypatch.setattr(provider_mod, "providers", lambda: (_Prov("a", backends.COMPOSE.name),))
    with pytest.raises(provider_mod.ProviderConflictError, match="core's own"):
        backends.unrouted()


def test_conflicting_providers_refuse_at_start_up(monkeypatch, capsys, tmp_path):
    """The registry is built by ``check_config`` at every process entry, so a conflict refuses at start-up (exit 2,
    the broken-plugin report), not as a 500 on the first request."""
    from vivarium_workbench.lib import startup
    from vivarium_workbench.lib.backends import config as backend_cfg

    assert issubclass(provider_mod.ProviderConflictError, ImportError)
    monkeypatch.setattr(provider_mod, "providers", lambda: (_Prov("a", "k"), _Prov("b", "k")))
    with pytest.raises(provider_mod.ProviderConflictError):
        backend_cfg.check_config()
    backends.reset_registry()
    assert startup.serve_fastapi(tmp_path, port=0) == 2
    assert "the backend plugin failed to load: ProviderConflictError" in capsys.readouterr().err


# --- a stale install ------------------------------------------------------------------------------------------------

def test_the_plugin_package_with_no_entry_point_is_a_stale_install(monkeypatch):
    _use(monkeypatch, plugin_installed=True)
    with pytest.raises(ImportError, match="stale install"):
        provider_mod.providers()


def test_no_entry_point_and_no_plugin_is_no_provider(monkeypatch):
    _use(monkeypatch, plugin_installed=False)
    assert provider_mod.providers() == ()


def _hides_the_plugin(finder) -> bool:
    """A finder that makes the plugin unimportable (conftest's core-only finder, a sitecustomize one)."""
    try:
        finder.find_spec(provider_mod._PLUGIN, None)
    except ImportError:
        return True
    except Exception:  # noqa: BLE001 -- not ours to judge
        return False
    return False


def test_a_leftover_pycache_directory_is_not_the_plugin(tmp_path, monkeypatch):
    """The core-only job (and a hand-deleted plugin) can leave ``vivarium_workbench_sms/__pycache__`` behind: a
    namespace package, which must not count as a stale install."""
    for name in [n for n in sys.modules if n == provider_mod._PLUGIN or n.startswith(provider_mod._PLUGIN + ".")]:
        monkeypatch.delitem(sys.modules, name)
    (tmp_path / provider_mod._PLUGIN / "__pycache__").mkdir(parents=True)
    monkeypatch.setattr(sys, "meta_path", [f for f in sys.meta_path if not _hides_the_plugin(f)])
    monkeypatch.setattr(sys, "path", [str(tmp_path)] + [p for p in sys.path if Path(p or ".").resolve() != ROOT])
    importlib.invalidate_caches()
    spec = importlib.util.find_spec(provider_mod._PLUGIN)
    if spec is not None and spec.origin not in (None, "namespace"):
        pytest.skip(f"the real plugin still resolves outside the repo root ({spec.origin})")
    assert provider_mod._plugin_installed() is False


def test_a_real_package_counts_as_the_plugin(tmp_path, monkeypatch):
    for name in [n for n in sys.modules if n == provider_mod._PLUGIN or n.startswith(provider_mod._PLUGIN + ".")]:
        monkeypatch.delitem(sys.modules, name)
    pkg = tmp_path / provider_mod._PLUGIN
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    monkeypatch.setattr(sys, "meta_path", [f for f in sys.meta_path if not _hides_the_plugin(f)])
    monkeypatch.setattr(sys, "path", [str(tmp_path)] + sys.path)
    importlib.invalidate_caches()
    assert provider_mod._plugin_installed() is True


# --- the real thing -------------------------------------------------------------------------------------------------

@pytest.mark.plugin
def test_the_real_entry_point_loads_the_sms_provider():
    from vivarium_workbench_sms.provider import PROVIDER

    assert provider_mod.providers() == (PROVIDER,)
    assert PROVIDER.api_version == provider_mod.PROVIDER_API_VERSION
