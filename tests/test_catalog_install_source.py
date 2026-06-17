"""Tests for the tri-source install-detection in /api/catalog.

Three install-source layers, in priority order:
  1. `imports` — workspace.yaml.imports has an explicit entry.
  2. `pyproject` — pyproject.toml [project.dependencies] declares it
     (but workspace.yaml.imports does NOT — typical for v2ecoli, which
     pre-dates the imports flow and lists pbg-copasi / viva-munk in
     pyproject directly).
  3. `venv` — installed in the workspace venv via another package's
     transitive dep; not in either declared layer.

Each detected install also gets `installed_via: [<parent_pkgs>]` for the
venv-transitive case so the UI can render "via X, Y" and skip the
Uninstall button (the user has to remove the parent to drop the dep).

This test file exercises the pure-function helpers without spinning up a
venv (we monkeypatch the bulk-venv-probe).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers under test
# ---------------------------------------------------------------------------


@pytest.fixture
def _ws_with_catalog(tmp_path):
    """Workspace fixture with a 3-entry catalog + a pyproject.toml that
    declares one of the catalog modules + a workspace.yaml with one
    explicit import. Mirrors v2ecoli's actual layout (pbg-copasi in
    pyproject; workspace.yaml.imports has no entry for it)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(yaml.safe_dump({
        "schema_version": 2,
        "name": "test-ws",
        "package_path": "pbg_testws",
        "imports": {
            "pbg-readdy": {  # the only EXPLICIT install
                "source": "https://github.com/...readdy.git",
                "ref": "main",
            }
        },
    }))
    # pyproject declares pbg-copasi + viva-munk but workspace.yaml.imports
    # has neither.
    (ws / "pyproject.toml").write_text(
        '[project]\n'
        'name = "test-ws"\n'
        'dependencies = [\n'
        '    "pbg-copasi",\n'
        '    "viva-munk>=0.0.1",\n'
        '    "pbg-superpowers",\n'
        ']\n'
    )
    (ws / "pbg_testws").mkdir()
    (ws / "scripts" / "_catalog").mkdir(parents=True)
    (ws / "scripts" / "_catalog" / "modules.json").write_text(json.dumps([
        {"name": "pbg-readdy",  "package": "pbg_readdy",  "description": "r"},
        {"name": "pbg-copasi",  "package": "pbg_copasi",  "description": "c"},
        {"name": "spatio-flux", "package": "spatio_flux", "description": "s"},
    ]))
    return ws


def test_read_workspace_pyproject_deps(_ws_with_catalog):
    """The pyproject parser extracts bare package names, lowercased,
    stripping version markers + extras."""
    from vivarium_dashboard.server import _read_workspace_pyproject_deps
    deps = _read_workspace_pyproject_deps(_ws_with_catalog)
    assert "pbg-copasi" in deps
    assert "viva-munk" in deps
    assert "pbg-superpowers" in deps
    # not declared
    assert "pbg-readdy" not in deps
    assert "spatio-flux" not in deps


def test_read_workspace_pyproject_deps_missing_file(tmp_path):
    """No pyproject → empty set (degrades gracefully)."""
    from vivarium_dashboard.server import _read_workspace_pyproject_deps
    assert _read_workspace_pyproject_deps(tmp_path) == set()


def test_read_workspace_pyproject_deps_malformed(tmp_path):
    """Malformed pyproject → empty set (degrades gracefully)."""
    from vivarium_dashboard.server import _read_workspace_pyproject_deps
    (tmp_path / "pyproject.toml").write_text("this is { not valid toml")
    assert _read_workspace_pyproject_deps(tmp_path) == set()


def test_detect_venv_distributions_missing_venv(tmp_path):
    """No .venv → empty dict (degrades gracefully)."""
    from vivarium_dashboard.server import _detect_workspace_venv_distributions
    assert _detect_workspace_venv_distributions(tmp_path) == {}


# ---------------------------------------------------------------------------
# End-to-end test of _get_catalog via monkeypatched helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def _patched_server(monkeypatch, _ws_with_catalog):
    """Point the server module at the test workspace + stub the venv probe
    to return a fixed dict (so we can test the three-layer detection
    without spinning up a real venv)."""
    import vivarium_dashboard.server as srv
    monkeypatch.setattr(srv, "WORKSPACE", _ws_with_catalog)

    # Venv-probe stub: spatio_flux is installed as a transitive dep of
    # viva-munk; pbg-copasi is also installed (matches pyproject); pbg-readdy
    # is installed (matches workspace.yaml.imports).
    fake_dists = {
        "pbg-copasi":      {"version": "0.1", "requires": [],                "requires_by": []},
        "viva-munk":       {"version": "0.1", "requires": ["spatio-flux"],   "requires_by": []},
        "spatio-flux":     {"version": "0.1", "requires": [],                "requires_by": ["viva-munk"]},
        "pbg-superpowers": {"version": "0.1", "requires": [],                "requires_by": []},
        "pbg-readdy":      {"version": "0.1", "requires": [],                "requires_by": []},
    }
    monkeypatch.setattr(srv, "_detect_workspace_venv_distributions",
                        lambda _ws: fake_dists)
    # Stub the sync-check so it never shells out to a real venv.
    monkeypatch.setattr(srv, "_check_installed_module_sync",
                        lambda pkg, path: None)
    # The catalog is now sourced via _module_registry (canonical pbg-superpowers
    # registry + workspace overlay) rather than a per-workspace modules.json.
    # Feed this test's controlled 3-entry catalog through that seam so the
    # install-source annotation logic is exercised against a known set.
    fixture_modules = json.loads(
        (_ws_with_catalog / "scripts" / "_catalog" / "modules.json").read_text())
    monkeypatch.setattr(srv.Handler, "_module_registry",
                        lambda self: [dict(m) for m in fixture_modules])
    return srv


def _run_get_catalog(srv) -> list[dict]:
    """Invoke the bound _get_catalog handler via a stub `self` object
    and return the parsed modules list. The handler's only side-effect
    we care about is the JSON body it would have written."""
    captured = {}
    class _Stub:
        # Delegate the workspace-self-module helper to the real Handler
        # implementation (it only reads WORKSPACE + shells out for the
        # git branch, both of which work fine from the stub context).
        _workspace_self_module = srv.Handler._workspace_self_module
        # _module_registry is monkeypatched on Handler by _patched_server.
        _module_registry = srv.Handler._module_registry
        def _json(self, payload, code):
            captured["payload"] = payload
            captured["code"] = code
    srv.Handler._get_catalog(_Stub())
    assert captured["code"] == 200
    return captured["payload"]["modules"]


def test_three_layer_detection_marks_each_source(_patched_server):
    modules = _run_get_catalog(_patched_server)
    by_name = {m["name"]: m for m in modules}

    # pbg-readdy: declared in workspace.yaml.imports → install_source: imports
    rd = by_name["pbg-readdy"]
    assert rd["installed"] is True
    assert rd["install_source"] == "imports"
    # imports-source pulls in the source/ref metadata from workspace.yaml
    assert rd.get("source") == "https://github.com/...readdy.git"

    # pbg-copasi: declared in pyproject.toml (NOT in workspace.yaml.imports)
    #             → install_source: pyproject
    cp = by_name["pbg-copasi"]
    assert cp["installed"] is True
    assert cp["install_source"] == "pyproject"

    # spatio-flux: NOT in pyproject, NOT in workspace.yaml.imports, but
    # present in venv as a transitive dep of viva-munk → install_source: venv
    sf = by_name["spatio-flux"]
    assert sf["installed"] is True
    assert sf["install_source"] == "venv"
    assert sf.get("installed_via") == ["viva-munk"]


def test_workspace_self_entry_prepended(_patched_server):
    modules = _run_get_catalog(_patched_server)
    assert modules[0]["kind"] == "workspace"
    assert modules[0]["installed"] is True
    assert modules[0]["package"] == "pbg_testws"


def test_uninstalled_module_has_no_install_source(_patched_server, monkeypatch):
    """A module that's in the catalog but in NONE of the three install
    layers should be `installed: False` and have no install_source field
    (the UI then renders an Install button)."""
    import vivarium_dashboard.server as srv
    # Drop spatio-flux from the venv-probe stub so it's no longer detected.
    monkeypatch.setattr(srv, "_detect_workspace_venv_distributions",
                        lambda _ws: {
                            "pbg-readdy": {"version": "0.1", "requires": [], "requires_by": []},
                            "pbg-copasi": {"version": "0.1", "requires": [], "requires_by": []},
                        })
    modules = _run_get_catalog(_patched_server)
    sf = next(m for m in modules if m["name"] == "spatio-flux")
    assert sf["installed"] is False
    assert "install_source" not in sf
    assert "installed_via" not in sf


def test_module_registry_is_canonical_plus_overlay(tmp_path, monkeypatch):
    """The registry is now the canonical pbg-superpowers list (single source
    of truth) merged with the workspace's optional overlay.json — NOT a
    vendored per-workspace modules.json."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    (ws / "scripts" / "_catalog").mkdir(parents=True)
    monkeypatch.setattr(srv, "WORKSPACE", ws)

    # No per-workspace catalog file at all → still populated from canonical.
    reg = srv.Handler._module_registry(object())
    names = {m["name"] for m in reg}
    assert "v2ecoli" in names                 # a known canonical module
    assert "pbg-physicell" not in names       # removed from the ecosystem
    assert len(names) > 10

    # A local-only module added via overlay.json appears on top of canonical.
    (ws / "scripts" / "_catalog" / "overlay.json").write_text(
        json.dumps([{"name": "pbg-localonly", "description": "local"}]))
    names2 = {m["name"] for m in srv.Handler._module_registry(object())}
    assert "pbg-localonly" in names2
    assert names2 >= names


def test_uncurated_declared_import_surfaces_in_catalog(tmp_path, monkeypatch):
    """A workspace.yaml import that the curated catalog doesn't know about
    must still appear in /api/catalog as an installed module (install_source
    'imports') — otherwise imported pbg-* repos silently vanish from the
    Modules grid."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "name": "t", "package_path": "pkg_t",
        "imports": {
            "pbg_ketchup": {  # NOT in the curated catalog below
                "source": "https://github.com/x/pbg-ketchup",
                "ref": "main", "description": "KETCHUP estimators",
            },
        },
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    # Curated catalog deliberately does NOT include pbg_ketchup.
    monkeypatch.setattr(srv, "_read_workspace_pyproject_deps", lambda _w: set())
    monkeypatch.setattr(srv, "_detect_workspace_venv_distributions", lambda _w: {})
    monkeypatch.setattr(srv, "_check_installed_module_sync", lambda pkg, path: None)
    import vivarium_dashboard.server  # ensure module ref
    monkeypatch.setattr("pbg_superpowers.catalog.load_registry",
                        lambda _w: [{"name": "pbg-copasi", "package": "pbg_copasi",
                                     "description": "c"}])

    data = srv._catalog_data(ws)
    by_name = {m["name"]: m for m in data["modules"]}
    assert "pbg_ketchup" in by_name, list(by_name)
    ket = by_name["pbg_ketchup"]
    assert ket["installed"] is True
    assert ket["install_source"] == "imports"
    assert ket.get("source") == "https://github.com/x/pbg-ketchup"
