"""Tests for registry source-tagging (Finding #13).

Verifies that /api/registry partitions discovered classes into
'in_workspace', 'framework', and 'environment_only' sources so the
UI can distinguish workspace-declared processes from env-installed ones.
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error

import pytest
import yaml


# ---------------------------------------------------------------------------
# Live FastAPI fixture spun up via the shared dashboard_client factory.
# ---------------------------------------------------------------------------

@pytest.fixture
def registry_server(tmp_path, dashboard_client):
    """Spin up the live FastAPI app against a minimal temp workspace.

    The workspace package (pbg_registry_test) registers exactly ONE custom
    process (FakeProcess) so we have a known in_workspace entry to assert on.
    """
    ws_root = tmp_path

    # Workspace package skeleton
    pkg_dir = ws_root / "pbg_registry_test"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")

    # A minimal Process subclass defined in the workspace package
    (pkg_dir / "processes.py").write_text(
        "from process_bigraph import Step\n\n"
        "class FakeProcess(Step):\n"
        "    config_schema = {}\n"
        "    def inputs(self): return {}\n"
        "    def outputs(self): return {}\n"
        "    def update(self, inputs): return {}\n"
    )
    (pkg_dir / "core.py").write_text(
        "from bigraph_schema import allocate_core\n"
        "from pbg_registry_test.processes import FakeProcess\n\n"
        "def build_core():\n"
        "    core = allocate_core()\n"
        "    core.register_link('FakeProcess', FakeProcess)\n"
        "    return core\n"
    )

    # workspace.yaml — no imports declared
    (ws_root / "workspace.yaml").write_text(yaml.dump({
        "name": "registry-test",
        "package_path": "pbg_registry_test",
        "visualizations": [],
        "observables": [],
        "simulations": [],
    }, sort_keys=False))

    client = dashboard_client(ws_root)

    class _Server:
        url = client.base_url
        root = ws_root

    yield _Server()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_registry_all_entries_have_source_field(registry_server):
    """Every process entry returned by /api/registry must carry a 'source' field."""
    code, body = _get(registry_server.url + "/api/registry")
    assert code == 200, body
    processes = body.get("processes", [])
    assert processes, f"expected at least one process entry; got {body}"
    for p in processes:
        assert "source" in p, (
            f"process entry missing 'source' field: {p}"
        )
        assert p["source"] in ("in_workspace", "framework", "environment_only"), (
            f"unexpected source value {p['source']!r} in entry {p['name']!r}"
        )


def test_registry_workspace_package_in_workspace(registry_server):
    """FakeProcess (defined in pbg_registry_test) must have source='in_workspace'."""
    code, body = _get(registry_server.url + "/api/registry")
    assert code == 200, body
    processes = {p["name"]: p for p in body.get("processes", [])}

    # FakeProcess is registered by name under build_core()
    assert "FakeProcess" in processes, (
        f"FakeProcess not found in registry; entries: {list(processes)}"
    )
    assert processes["FakeProcess"]["source"] == "in_workspace", (
        f"FakeProcess source={processes['FakeProcess']['source']!r}, expected 'in_workspace'"
    )


def test_registry_environment_packages_not_in_workspace(registry_server):
    """Packages installed in the environment but not declared in workspace.yaml
    must have source='environment_only', not 'in_workspace'."""
    code, body = _get(registry_server.url + "/api/registry")
    assert code == 200, body
    processes = body.get("processes", [])

    # Any entry whose address starts with a package that is NOT pbg_registry_test,
    # and is not a framework package, should be environment_only.
    framework_pkgs = {
        "process_bigraph", "bigraph_schema", "bigraph_viz",
        "pbg_superpowers", "vivarium_workbench",
    }
    for p in processes:
        top_pkg = p.get("address", "").split(".")[0]
        if top_pkg in ("pbg_registry_test",):
            assert p["source"] == "in_workspace", (
                f"{p['name']!r}: expected in_workspace, got {p['source']!r}"
            )
        elif top_pkg in framework_pkgs:
            assert p["source"] == "framework", (
                f"{p['name']!r}: expected framework, got {p['source']!r}"
            )
        else:
            # Any other installed package must be environment_only
            assert p["source"] == "environment_only", (
                f"{p['name']!r} (from {top_pkg!r}): expected environment_only, "
                f"got {p['source']!r}"
            )


def test_registry_includes_workspace_pkgs_field(registry_server):
    """Response includes 'workspace_pkgs' list containing the workspace's own package."""
    code, body = _get(registry_server.url + "/api/registry")
    assert code == 200, body
    ws_pkgs = body.get("workspace_pkgs", [])
    assert "pbg_registry_test" in ws_pkgs, (
        f"workspace_pkgs={ws_pkgs!r} should contain 'pbg_registry_test'"
    )


def test_registry_with_declared_import_marks_it_in_workspace(tmp_path, dashboard_client):
    """When workspace.yaml.imports declares a package, its classes get source='in_workspace'."""
    ws_root = tmp_path

    # Workspace package
    pkg_dir = ws_root / "pbg_ws_with_import"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "core.py").write_text(
        "from bigraph_schema import allocate_core\n\n"
        "def build_core():\n"
        "    return allocate_core()\n"
    )

    # Declare spatio_flux (if installed) as an explicit import.
    # This test passes even if spatio_flux is NOT installed — it just verifies
    # that the workspace_pkgs set includes the declared package.
    (ws_root / "workspace.yaml").write_text(yaml.dump({
        "name": "ws-with-import",
        "package_path": "pbg_ws_with_import",
        "visualizations": [],
        "observables": [],
        "simulations": [],
        "imports": {
            "spatio-flux": {
                "package": "spatio_flux",
                "source": "https://github.com/vivarium-collective/spatio-flux",
            }
        },
    }, sort_keys=False))

    client = dashboard_client(ws_root)

    code, body = _get(client.base_url + "/api/registry")
    assert code == 200, body

    # workspace_pkgs must include both the own package and the declared import
    ws_pkgs = body.get("workspace_pkgs", [])
    assert "pbg_ws_with_import" in ws_pkgs, f"ws_pkgs={ws_pkgs!r}"
    assert "spatio_flux" in ws_pkgs, (
        f"declared import 'spatio_flux' missing from workspace_pkgs={ws_pkgs!r}"
    )

    # If spatio_flux is actually installed, its entries must be in_workspace
    for p in body.get("processes", []):
        top = p.get("address", "").split(".")[0]
        if top == "spatio_flux":
            assert p["source"] == "in_workspace", (
                f"spatio_flux entry {p['name']!r} should be in_workspace; got {p['source']!r}"
            )


def test_registry_imports_as_list_of_dicts(tmp_path, dashboard_client):
    """v2ecoli + newer pbg-template workspaces ship workspace.yaml.imports
    as a list of dicts (each with ``name`` + optional ``package``), not
    as the older dict-keyed-by-catalog-name shape. The registry endpoint
    must accept both — older shapes broke with ``'list' object has no
    attribute 'items'``."""
    ws_root = tmp_path

    pkg_dir = ws_root / "pbg_ws_list_imports"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "core.py").write_text(
        "from bigraph_schema import allocate_core\n\n"
        "def build_core():\n"
        "    return allocate_core()\n"
    )

    # List-of-dicts shape (mirrors v2ecoli workspace.yaml).
    (ws_root / "workspace.yaml").write_text(yaml.dump({
        "name": "ws-list-imports",
        "package_path": "pbg_ws_list_imports",
        "visualizations": [],
        "observables": [],
        "simulations": [],
        "imports": [
            {
                "name": "spatio_flux",
                "source": "https://github.com/vivarium-collective/spatio-flux",
                "description": "test",
            },
            # Entry with explicit `package` override.
            {
                "name": "some-catalog-name",
                "package": "spatio_flux",
            },
            # String entry (defensive — bare names).
            "bigraph_schema",
        ],
    }, sort_keys=False))

    client = dashboard_client(ws_root)

    code, body = _get(client.base_url + "/api/registry")
    assert code == 200, body
    # No ``error`` field — the list shape parsed cleanly.
    assert body.get("error") is None, f"unexpected error: {body.get('error')!r}"
    ws_pkgs = body.get("workspace_pkgs", [])
    # All three import-shape variants should resolve to package names.
    assert "pbg_ws_list_imports" in ws_pkgs, f"ws_pkgs={ws_pkgs!r}"
    assert "spatio_flux" in ws_pkgs, f"name-only entry missing: {ws_pkgs!r}"
    assert "bigraph_schema" in ws_pkgs, f"string entry missing: {ws_pkgs!r}"


def test_registry_imports_missing_or_none(tmp_path, dashboard_client):
    """Workspace without an ``imports`` field (or ``imports: null``) — the
    registry shows the workspace's own package + framework classes
    without crashing."""
    ws_root = tmp_path
    pkg_dir = ws_root / "pbg_ws_no_imports"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "core.py").write_text(
        "from bigraph_schema import allocate_core\n\n"
        "def build_core():\n"
        "    return allocate_core()\n"
    )
    (ws_root / "workspace.yaml").write_text(yaml.dump({
        "name": "ws-no-imports",
        "package_path": "pbg_ws_no_imports",
        "visualizations": [],
        "observables": [],
        "simulations": [],
        # No imports field at all.
    }, sort_keys=False))

    client = dashboard_client(ws_root)

    code, body = _get(client.base_url + "/api/registry")
    assert code == 200, body
    assert body.get("error") is None
    ws_pkgs = body.get("workspace_pkgs", [])
    assert ws_pkgs == ["pbg_ws_no_imports"]


# ---------------------------------------------------------------------------
# Catalog filter: registry.include must not hide INSTALLED modules
# ---------------------------------------------------------------------------

def test_filter_catalog_keeps_installed_modules_outside_include():
    """`dashboard.registry.include` limits which *available* modules surface, but
    INSTALLED modules (the workspace's active deps) must always be kept — else a
    narrow include like [v2ecoli] hides pbg-emitters/viva-munk/etc."""
    from vivarium_workbench.lib import catalog

    modules = [
        {"name": "v2ecoli", "installed": True},
        {"name": "pbg-emitters", "package": "pbg_emitters", "installed": True},
        {"name": "viva-munk", "package": "viva_munk", "installed": True},
        {"name": "pbg-cellpack", "package": "pbg_cellpack", "installed": False},
    ]
    ws_data = {"dashboard": {"registry": {"include": ["v2ecoli"]}}}
    kept = {m["name"] for m in catalog._filter_catalog_modules(modules, ws_data)}

    assert kept == {"v2ecoli", "pbg-emitters", "viva-munk"}, (
        "installed modules must survive the include filter; available "
        "(not-installed) modules outside the include must be dropped"
    )


def test_filter_catalog_noop_without_include():
    """No include configured -> full catalog unchanged."""
    from vivarium_workbench.lib import catalog
    modules = [{"name": "a", "installed": False}, {"name": "b", "installed": True}]
    assert catalog._filter_catalog_modules(modules, {}) == modules


def test_registry_filter_always_keeps_emitters():
    """`dashboard.registry.include` scopes processes to the repo, but emitters
    (the workspace's I/O backends, in framework/env packages outside the
    include) must always survive — else the Registry's Emitters section is
    empty under a repo-scoped include like [v2ecoli]."""
    from vivarium_workbench.lib import registry

    data = {"processes": [
        {"name": "EcoliWCM", "address": "v2ecoli.bridge.EcoliWCM", "kind": "process"},
        {"name": "Foreign", "address": "viva_munk.x.Foreign", "kind": "process"},
        {"name": "XArrayEmitter", "address": "pbg_emitters.x.XArrayEmitter", "kind": "emitter"},
        {"name": "ConsoleEmitter", "address": "process_bigraph.emitter.ConsoleEmitter", "kind": "emitter"},
    ]}
    registry._apply_registry_include_filter(data, {"dashboard": {"registry": {"include": ["v2ecoli"]}}})
    kept = {p["name"] for p in data["processes"]}
    assert "EcoliWCM" in kept            # own package
    assert "XArrayEmitter" in kept       # emitter (env pkg) survives
    assert "ConsoleEmitter" in kept      # emitter (framework pkg) survives
    assert "Foreign" not in kept         # foreign non-emitter still filtered
