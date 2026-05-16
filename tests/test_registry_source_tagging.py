"""Tests for registry source-tagging (Finding #13).

Verifies that /api/registry partitions discovered classes into
'in_workspace', 'framework', and 'environment_only' sources so the
UI can distinguish workspace-declared processes from env-installed ones.
"""
from __future__ import annotations
import json
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# Minimal in-process server fixture (same pattern as test_visualization_endpoints.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def registry_server(tmp_path, monkeypatch):
    """Spin up a Handler-backed server against a minimal temp workspace.

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

    # Add workspace root to sys.path so subprocess can import pbg_registry_test
    monkeypatch.syspath_prepend(str(ws_root))

    import importlib
    import vivarium_dashboard.server as srv
    importlib.reload(srv)
    monkeypatch.setattr(srv, "WORKSPACE", ws_root)
    # Bust any leftover registry cache from other tests
    srv._REGISTRY_CACHE["data"] = None
    srv._REGISTRY_CACHE["ts"] = 0.0

    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    class _Server:
        url = f"http://127.0.0.1:{port}"
        root = ws_root

    yield _Server()
    httpd.shutdown()
    thread.join(timeout=2)


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
        "pbg_superpowers", "vivarium_dashboard",
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


def test_registry_with_declared_import_marks_it_in_workspace(tmp_path, monkeypatch):
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

    monkeypatch.syspath_prepend(str(ws_root))

    import importlib
    import vivarium_dashboard.server as srv
    importlib.reload(srv)
    monkeypatch.setattr(srv, "WORKSPACE", ws_root)
    srv._REGISTRY_CACHE["data"] = None
    srv._REGISTRY_CACHE["ts"] = 0.0

    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        code, body = _get(f"http://127.0.0.1:{port}/api/registry")
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
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
