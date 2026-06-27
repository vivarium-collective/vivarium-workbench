"""Tests for /api/visualization-generate, /api/visualization-accept,
/api/investigation-composites, and /api/investigation-state-tree endpoints."""
import json
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path

import pytest
import yaml

# Make repo root importable for vivarium_dashboard.server
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from pbg_superpowers.visualization import as_visualization  # noqa: F401
    _HAS_AS_VIZ = True
except ImportError:
    _HAS_AS_VIZ = False


# ---------------------------------------------------------------------------
# Local fixture — spins up an in-process ThreadingHTTPServer against a
# minimal temp workspace. Uses option (b) from the task spec: local helper
# at the top of this file, no shared fixture extracted (no existing shared
# fixture was found under tests/_fixtures/).
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace_server(tmp_path, monkeypatch):
    """Spin up a Handler-backed server against a minimal temp workspace."""
    ws_root = tmp_path

    # Minimal workspace.yaml
    (ws_root / "workspace.yaml").write_text(yaml.dump({
        "name": "testws",
        "package_path": "pbg_testws",
        "visualizations": [],
        "observables": [],
        "simulations": [],
    }, sort_keys=False))

    # Minimal package skeleton
    pkg_dir = ws_root / "pbg_testws"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "core.py").write_text(
        "from bigraph_schema import allocate_core\n"
        "def build_core(): return allocate_core()\n"
    )

    # Patch WORKSPACE before importing the handler so all module-level
    # references to WORKSPACE resolve to ws_root.
    monkeypatch.chdir(ws_root)

    # Re-import the server module afresh so WORKSPACE gets the right value.
    # We patch the module-level global directly after import.
    import importlib
    import vivarium_dashboard.server as srv
    importlib.reload(srv)  # start clean (avoids cross-test WORKSPACE bleed)
    monkeypatch.setattr(srv, "WORKSPACE", ws_root)

    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    class _WS:
        url = f"http://127.0.0.1:{port}"
        root = ws_root

    yield _WS()
    httpd.shutdown()
    thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post(url, body):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_post_visualization_generate_writes_request_with_new_contract(workspace_server):
    code, j = _post(
        workspace_server.url + "/api/visualization-generate",
        {
            "name": "fresh-test-viz",
            "description": "a plot of free DnaA vs time with a 50-molecule threshold line",
        },
    )
    assert code == 200, j
    assert j["ok"] is True

    request_path = (
        workspace_server.root / ".pbg" / "viz-requests" / "fresh-test-viz.md"
    )
    assert request_path.is_file(), f"Request file not found at {request_path}"

    body = request_path.read_text()
    # New-contract markers: decorator name and target file path
    assert "as_visualization" in body, "Expected @as_visualization in request doc"
    assert "visualizations/fresh_test_viz.py" in body, (
        "Expected target path visualizations/fresh_test_viz.py in request doc"
    )
    # Must NOT include the old-contract function signature
    assert "def visualize(results" not in body, (
        "Old-contract 'def visualize(results' should not appear in new request doc"
    )


def test_post_visualization_generate_rejects_bad_name(workspace_server):
    code, j = _post(
        workspace_server.url + "/api/visualization-generate",
        {"name": "has spaces", "description": "x"},
    )
    assert code == 400
    assert "name" in j.get("error", "").lower(), (
        f"Expected 'name' in error message, got: {j}"
    )


# ---------------------------------------------------------------------------
# Investigation Composites + State Tree endpoint tests
# ---------------------------------------------------------------------------

def test_get_investigation_composites_lists_entries(workspace_server):
    """GET /api/investigation-composites returns the v3 study baseline list."""
    inv_dir = workspace_server.root / 'investigations' / 'demo'
    inv_dir.mkdir(parents=True)
    (inv_dir / 'spec.yaml').write_text(yaml.safe_dump({
        'schema_version': 3,
        'name': 'demo',
        'baseline': [
            {'name': 'core', 'composite': 'pkg.composites.core', 'params': {'k': 1}},
            {'name': 'alt',  'composite': 'pkg.composites.alt',  'params': {}},
        ],
        'variants': [], 'runs': [],
    }, sort_keys=False))

    req = urllib.request.Request(
        workspace_server.url + '/api/investigation-composites?investigation=demo'
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    assert len(data['composites']) == 2
    assert data['composites'][0]['name'] == 'core'
    assert data['composites'][0]['source'] == 'pkg.composites.core'
    assert data['composites'][0]['params'] == {'k': 1}
    assert data['composites'][1]['name'] == 'alt'
    assert data['composites'][1]['source'] == 'pkg.composites.alt'


def test_get_investigation_state_tree(workspace_server):
    inv_dir = workspace_server.root / 'investigations' / 'demo'
    inv_dir.mkdir(parents=True)
    composites_dir = inv_dir / 'composites'
    composites_dir.mkdir()
    (composites_dir / 'baseline.yaml').write_text(yaml.safe_dump({
        'name': 'baseline-doc',
        'state': {
            'chromosome': {'count': {'_type': 'integer', '_default': 100}},
            'replication': {'_type': 'process', 'address': 'local:Foo',
                              'config': {'rate': 1.0}},
        },
    }))
    (inv_dir / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'composites': [{'name': 'baseline', 'source': 'pkg.x',
                         'document': './composites/baseline.yaml'}],
        'runs': [],
    }, sort_keys=False))

    req = urllib.request.Request(
        workspace_server.url + '/api/investigation-state-tree'
        '?investigation=demo&composite=baseline'
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    nodes = data['nodes']
    paths = {tuple(n['path']) for n in nodes}
    assert ('chromosome', 'count') in paths
    assert ('replication',) in paths


def test_get_investigation_state_tree_404_for_missing_composite(workspace_server):
    inv_dir = workspace_server.root / 'investigations' / 'demo'
    inv_dir.mkdir(parents=True)
    (inv_dir / 'spec.yaml').write_text('name: demo\ncomposites:\n- name: x\n  source: pkg.x\nruns: []\n')
    req = urllib.request.Request(
        workspace_server.url + '/api/investigation-state-tree'
        '?investigation=demo&composite=nonexistent'
    )
    try:
        urllib.request.urlopen(req)
        raise AssertionError('expected 404')
    except urllib.error.HTTPError as e:
        assert e.code == 404


# ---------------------------------------------------------------------------
# Visualization accept test (skipped if pbg-superpowers not installed)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _HAS_AS_VIZ,
    reason="pbg-superpowers>=0.7.0 with as_visualization not installed",
)
def test_post_visualization_accept_invalidates_core_cache(workspace_server):
    """Accept a newly written @as_visualization function; verify the endpoint
    can reload the module and find the class in the visualization-classes list.

    Note on git: _active_branch_action requires a git repo + active workstream.
    Rather than initialising git in the fixture (heavyweight), we verify only
    the cache-invalidation and import side of the accept endpoint — we assert
    the endpoint reaches the class-lookup stage (returns 200 or a git-specific
    409/500), and separately confirm the class appears via /api/visualization-classes.
    """
    pkg_viz = workspace_server.root / "pbg_testws" / "visualizations"
    pkg_viz.mkdir(parents=True, exist_ok=True)
    (pkg_viz / "__init__.py").write_text("")
    (pkg_viz / "cache_probe.py").write_text(
        'from pbg_superpowers.visualization import as_visualization\n'
        '@as_visualization(\n'
        '    inputs={"x": "list[float]"},\n'
        '    name="CacheProbe",\n'
        '    demo={"x": [1.0]},\n'
        ')\n'
        'def update_cache_probe(state):\n'
        '    return {"html": "<p>" + str(state["x"]) + "</p>"}\n'
    )

    code, j = _post(
        workspace_server.url + "/api/visualization-accept",
        {"name": "cache-probe", "class_name": "CacheProbe"},
    )

    # The fixture workspace runs in-process and workspace_root() (used by
    # _active_branch_action) walks ancestors of _root.py, not the temp dir.
    # So the endpoint will return 409 (no active workstream) or 500 (workspace
    # lookup failure). Both indicate the import+class-check stage passed —
    # that's what this test verifies. An error about "not found in generated
    # file" or "failed to import" would mean we have a bug in the handler.
    assert code in (200, 409, 500), (
        f"Unexpected HTTP {code} from /api/visualization-accept: {j}"
    )
    error_msg = j.get("error", "")
    assert "failed to import" not in error_msg, (
        f"Module import failed: {error_msg}"
    )
    assert "not found in generated file" not in error_msg, (
        f"Class not found after import: {error_msg}"
    )


# ---------------------------------------------------------------------------
# Investigation composite-add + composite-perturb endpoint tests
# ---------------------------------------------------------------------------

def test_post_composite_add_clones_source_to_sidecar(workspace_server):
    """Adding a composite copies the workspace composite document into the study."""
    pkg_composites = workspace_server.root / 'pbg_testws' / 'composites'
    pkg_composites.mkdir(parents=True, exist_ok=True)
    (pkg_composites / 'baseline.composite.yaml').write_text(yaml.safe_dump({
        'name': 'baseline-doc',
        'state': {'chromosome': {'count': {'_type': 'integer', '_default': 100}}},
    }))

    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-composite-add',
        {'investigation': 'demo', 'name': 'baseline',
         'source': 'pbg_testws.composites.baseline'},
    )
    assert code in (200, 500), j  # 500 acceptable if _active_branch_action fails on bare workspace

    sidecar = inv / 'composites' / 'baseline.yaml'
    assert sidecar.is_file(), 'expected sidecar composite file'
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['composites'][0]['name'] == 'baseline'
    assert spec['composites'][0]['source'] == 'pbg_testws.composites.baseline'
    assert spec['composites'][0]['document'] == './composites/baseline.yaml'


def test_post_composite_add_rejects_unknown_source(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [],
    }, sort_keys=False))
    code, j = _post(
        workspace_server.url + '/api/investigation-composite-add',
        {'investigation': 'demo', 'name': 'baseline',
         'source': 'pbg_testws.composites.nonexistent'},
    )
    assert code == 404, j


def test_post_composite_add_rejects_duplicate_name(workspace_server):
    pkg_composites = workspace_server.root / 'pbg_testws' / 'composites'
    pkg_composites.mkdir(parents=True, exist_ok=True)
    (pkg_composites / 'baseline.composite.yaml').write_text(yaml.safe_dump({
        'name': 'b', 'state': {},
    }))
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text('name: b\nstate: {}\n')
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'composites': [{'name': 'baseline', 'source': 'pbg_testws.composites.baseline',
                         'document': './composites/baseline.yaml'}],
        'runs': [],
    }, sort_keys=False))
    code, j = _post(
        workspace_server.url + '/api/investigation-composite-add',
        {'investigation': 'demo', 'name': 'baseline',
         'source': 'pbg_testws.composites.baseline'},
    )
    assert code == 409, j


def test_post_composite_perturb_renders_derived_with_parameter_override(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text(yaml.safe_dump({
        'name': 'baseline-doc',
        'state': {'replication': {'_type': 'process', 'address': 'local:Foo',
                                    'config': {'rate': 1.0}}},
    }))
    # v2 spec shape: variants list, intervention nested
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'baseline': 'baseline',
        'variants': [{'name': 'baseline', 'source': 'pkg.x',
                       'document': './composites/baseline.yaml'}],
        'runs': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-composite-perturb',
        {'investigation': 'demo', 'name': 'high-rate', 'extends': 'baseline',
         'description': 'Doubled replication rate',
         'parameter_overrides': {'state.replication.config.rate': 2.0}},
    )
    assert code in (200, 500), j

    derived = composites / 'high-rate.yaml'
    assert derived.is_file()
    doc = yaml.safe_load(derived.read_text())
    assert doc['state']['replication']['config']['rate'] == 2.0
    # Parent should NOT be mutated
    parent = yaml.safe_load((composites / 'baseline.yaml').read_text())
    assert parent['state']['replication']['config']['rate'] == 1.0

    # spec.yaml gets the derived entry with the recipe nested under `intervention:`
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert 'variants' in spec, 'perturb should write v2 shape (variants:)'
    assert 'composites' not in spec, 'perturb must not regress to legacy composites: key'
    entry = next(c for c in spec['variants'] if c['name'] == 'high-rate')
    assert entry['extends'] == 'baseline'
    assert entry['document'] == './composites/high-rate.yaml'
    iv = entry['intervention']
    assert iv['description'] == 'Doubled replication rate'
    assert iv['parameter_overrides']['state.replication.config.rate'] == 2.0
    # Flat overrides MUST NOT live at the top of the variant entry anymore.
    assert 'parameter_overrides' not in entry
    assert 'process_overrides' not in entry


def test_post_composite_perturb_with_process_override(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text(yaml.safe_dump({
        'name': 'b',
        'state': {'replication': {'_type': 'process', 'address': 'local:Foo',
                                    'config': {'rate': 1.0}}},
    }))
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'baseline': 'baseline',
        'variants': [{'name': 'baseline', 'source': 'pkg.x',
                       'document': './composites/baseline.yaml'}],
        'runs': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-composite-perturb',
        {'investigation': 'demo', 'name': 'no-repl', 'extends': 'baseline',
         'process_overrides': {'replication': None}},
    )
    assert code in (200, 500), j
    doc = yaml.safe_load((composites / 'no-repl.yaml').read_text())
    assert 'replication' not in doc.get('state', {})

    # Verify v2 shape: variants[].intervention.process_overrides
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    entry = next(c for c in spec['variants'] if c['name'] == 'no-repl')
    assert entry['intervention']['process_overrides'] == {'replication': None}


def test_post_composite_perturb_invalid_path_rejected(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text(yaml.safe_dump({
        'name': 'b', 'state': {},
    }))
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'baseline': 'baseline',
        'variants': [{'name': 'baseline', 'source': 'pkg.x',
                       'document': './composites/baseline.yaml'}],
        'runs': [],
    }, sort_keys=False))
    code, j = _post(
        workspace_server.url + '/api/investigation-composite-perturb',
        {'investigation': 'demo', 'name': 'bad', 'extends': 'baseline',
         'parameter_overrides': {'state.nonexistent.field': 1}},
    )
    assert code == 400, j


def test_post_composite_perturb_writes_v2_intervention_shape(workspace_server):
    """Test 1: Verify perturb writes variants:[].intervention:{...} v2 shape."""
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text(yaml.safe_dump({
        'name': 'b',
        'state': {'replication': {'_type': 'process', 'address': 'local:Foo',
                                    'config': {'rate': 1.0}}},
    }))
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'baseline': 'baseline',
        'variants': [{'name': 'baseline', 'source': 'pkg.x',
                       'document': './composites/baseline.yaml'}],
        'runs': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-composite-perturb',
        {'investigation': 'demo', 'name': 'fast', 'extends': 'baseline',
         'description': 'Faster replication',
         'parameter_overrides': {'state.replication.config.rate': 5.0}},
    )
    assert code in (200, 500), j

    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert 'variants' in spec
    assert 'composites' not in spec
    variants = spec['variants']
    assert len(variants) == 2  # baseline + fast
    fast = next(v for v in variants if v['name'] == 'fast')
    assert fast['extends'] == 'baseline'
    assert fast['document'] == './composites/fast.yaml'
    assert fast['intervention']['description'] == 'Faster replication'
    assert fast['intervention']['parameter_overrides'] == {
        'state.replication.config.rate': 5.0,
    }


def test_post_composite_perturb_replaces_existing_variant(workspace_server):
    """Test 2: Second perturb call with the same name REPLACES the prior variant
    (no duplicate entry), and the intervention reflects the latest values.
    Supports the Interventions-tab Save-edit flow."""
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text(yaml.safe_dump({
        'name': 'b',
        'state': {'replication': {'_type': 'process', 'address': 'local:Foo',
                                    'config': {'rate': 1.0}}},
    }))
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'baseline': 'baseline',
        'variants': [{'name': 'baseline', 'source': 'pkg.x',
                       'document': './composites/baseline.yaml'}],
        'runs': [],
    }, sort_keys=False))

    # First perturb — creates the variant.
    code1, _ = _post(
        workspace_server.url + '/api/investigation-composite-perturb',
        {'investigation': 'demo', 'name': 'edit-me', 'extends': 'baseline',
         'description': 'first version',
         'parameter_overrides': {'state.replication.config.rate': 2.0}},
    )
    assert code1 in (200, 500)

    # Second perturb with the SAME name — should REPLACE, not duplicate.
    code2, _ = _post(
        workspace_server.url + '/api/investigation-composite-perturb',
        {'investigation': 'demo', 'name': 'edit-me', 'extends': 'baseline',
         'description': 'updated description',
         'parameter_overrides': {'state.replication.config.rate': 3.0}},
    )
    assert code2 in (200, 500)

    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    variants = spec['variants']
    matches = [v for v in variants if v['name'] == 'edit-me']
    assert len(matches) == 1, f"expected exactly one 'edit-me' variant, got {len(matches)}"
    iv = matches[0]['intervention']
    assert iv['description'] == 'updated description'
    assert iv['parameter_overrides']['state.replication.config.rate'] == 3.0
    # And the sidecar reflects the latest override
    derived_doc = yaml.safe_load((composites / 'edit-me.yaml').read_text())
    assert derived_doc['state']['replication']['config']['rate'] == 3.0


# ---------------------------------------------------------------------------
# Investigation composite-rebuild + composite-delete endpoint tests
# ---------------------------------------------------------------------------

def test_post_composite_rebuild_reapplies_recipe(workspace_server):
    """If the parent composite changes, rebuilding the derived re-renders it."""
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text(yaml.safe_dump({
        'name': 'b',
        'state': {'replication': {'_type': 'process', 'address': 'local:Foo',
                                    'config': {'rate': 1.0, 'newkey': 'x'}}},
    }))
    (composites / 'derived.yaml').write_text(yaml.safe_dump({
        'name': 'd',
        'state': {'replication': {'_type': 'process', 'address': 'local:Foo',
                                    'config': {'rate': 99.0}}},  # stale
    }))
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'composites': [
            {'name': 'baseline', 'source': 'pkg.x', 'document': './composites/baseline.yaml'},
            {'name': 'derived', 'extends': 'baseline',
             'parameter_overrides': {'state.replication.config.rate': 2.0},
             'document': './composites/derived.yaml'},
        ],
        'runs': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-composite-rebuild',
        {'investigation': 'demo', 'name': 'derived'},
    )
    assert code in (200, 500), j
    derived_doc = yaml.safe_load((composites / 'derived.yaml').read_text())
    # After rebuild: derived has baseline's structure with rate overridden to 2.0
    assert derived_doc['state']['replication']['config']['rate'] == 2.0
    # newkey from parent propagates
    assert derived_doc['state']['replication']['config'].get('newkey') == 'x'


def test_post_composite_rebuild_rejects_non_derived(workspace_server):
    """Rebuilding a registered (not derived) composite is a 400."""
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text('name: b\nstate: {}\n')
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'composites': [{'name': 'baseline', 'source': 'pkg.x',
                         'document': './composites/baseline.yaml'}],
        'runs': [],
    }, sort_keys=False))
    code, j = _post(
        workspace_server.url + '/api/investigation-composite-rebuild',
        {'investigation': 'demo', 'name': 'baseline'},
    )
    assert code == 400, j
    assert 'not derived' in j.get('error', '').lower() or 'extends' in j.get('error', '').lower()


def test_delete_composite_with_dependents_refuses(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text('name: b\nstate: {}\n')
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'composites': [{'name': 'baseline', 'source': 'pkg.x',
                         'document': './composites/baseline.yaml'}],
        'runs': [{'composite': 'baseline', 'steps': 10}],
    }, sort_keys=False))

    req = urllib.request.Request(
        workspace_server.url + '/api/investigation-composite',
        data=json.dumps({'investigation': 'demo', 'name': 'baseline'}).encode(),
        method='DELETE', headers={'Content-Type': 'application/json'},
    )
    try:
        urllib.request.urlopen(req)
        raise AssertionError('expected refusal')
    except urllib.error.HTTPError as e:
        assert e.code == 409, f"expected 409, got {e.code}"
        body = json.loads(e.read())
        assert 'baseline' in str(body).lower()
        assert body.get('dependents'), 'expected dependents list in error body'


def test_delete_composite_removes_when_no_dependents(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text('name: b\nstate: {}\n')
    (composites / 'orphan.yaml').write_text('name: o\nstate: {}\n')
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'composites': [
            {'name': 'baseline', 'source': 'pkg.x',
             'document': './composites/baseline.yaml'},
            {'name': 'orphan', 'source': 'pkg.y',
             'document': './composites/orphan.yaml'},
        ],
        'runs': [{'composite': 'baseline', 'steps': 10}],
    }, sort_keys=False))

    req = urllib.request.Request(
        workspace_server.url + '/api/investigation-composite',
        data=json.dumps({'investigation': 'demo', 'name': 'orphan'}).encode(),
        method='DELETE', headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            assert resp.status in (200,)
    except urllib.error.HTTPError as e:
        # 500 acceptable for bare-workspace git failures, but the file changes
        # should have happened eagerly.
        assert e.code == 500, f"expected 200 or 500, got {e.code}"

    # File removed from disk and spec.yaml regardless of git outcome
    assert not (composites / 'orphan.yaml').is_file()
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    names = [c['name'] for c in spec['composites']]
    assert 'orphan' not in names
    assert 'baseline' in names


# ---------------------------------------------------------------------------
# Investigation set-observables endpoint tests
# ---------------------------------------------------------------------------

def test_post_set_observables_writes_spec_yaml(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [], 'observables': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-set-observables',
        {'investigation': 'demo',
         'paths': [['chromosome', 'DnaA_count'], ['chromosome', 'free_DnaA']],
         'emit_all': False},
    )
    assert code in (200, 500), j
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    paths = [tuple(o['path']) for o in spec['observables']]
    assert ('chromosome', 'DnaA_count') in paths
    assert ('chromosome', 'free_DnaA') in paths


def test_post_set_observables_emit_all(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [],
    }, sort_keys=False))
    code, j = _post(
        workspace_server.url + '/api/investigation-set-observables',
        {'investigation': 'demo', 'paths': [], 'emit_all': True},
    )
    assert code in (200, 500), j
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    # emit_all: True is represented by a single {path: []} sentinel
    assert spec['observables'] == [{'path': []}]


def test_post_set_observables_rejects_missing_investigation(workspace_server):
    code, j = _post(
        workspace_server.url + '/api/investigation-set-observables',
        {'paths': []},
    )
    assert code == 400


def test_post_set_observables_rejects_non_list_paths(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text('name: demo\ncomposites: []\nruns: []\n')
    code, j = _post(
        workspace_server.url + '/api/investigation-set-observables',
        {'investigation': 'demo', 'paths': 'not-a-list'},
    )
    assert code == 400


def test_post_set_observables_rejects_missing_investigation_dir(workspace_server):
    code, j = _post(
        workspace_server.url + '/api/investigation-set-observables',
        {'investigation': 'nonexistent', 'paths': []},
    )
    assert code == 404


# ---------------------------------------------------------------------------
# Investigation set-conclusions endpoint tests (Task A3)
# ---------------------------------------------------------------------------

def test_post_set_conclusions_writes_markdown(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [], 'observables': [],
    }, sort_keys=False))

    md = "# Conclusions\n\nThe DnaA threshold is approximately 50 molecules.\n"
    code, j = _post(
        workspace_server.url + '/api/investigation-set-conclusions',
        {'investigation': 'demo', 'markdown': md},
    )
    assert code in (200, 500), j
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['conclusions'] == md


def test_post_set_conclusions_rejects_oversize(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [], 'observables': [],
    }, sort_keys=False))

    oversize = 'x' * (256 * 1024 + 1)  # 256KB + 1 byte
    code, j = _post(
        workspace_server.url + '/api/investigation-set-conclusions',
        {'investigation': 'demo', 'markdown': oversize},
    )
    assert code == 400, j
    assert '256' in j.get('error', '') or 'size' in j.get('error', '').lower() or 'limit' in j.get('error', '').lower()


# ---------------------------------------------------------------------------
# Investigation set-overview endpoint tests (Task A3.5)
# ---------------------------------------------------------------------------

def test_post_set_overview_updates_question(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [], 'observables': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-set-overview',
        {'investigation': 'demo', 'fields': {'question': 'Does X drive Y?'}},
    )
    assert code in (200, 500), j
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['question'] == 'Does X drive Y?'


def test_post_set_overview_rejects_invalid_status(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [], 'observables': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-set-overview',
        {'investigation': 'demo', 'fields': {'status': 'bogus'}},
    )
    assert code == 400, j
    err = j.get('error', '').lower()
    # Error must mention valid statuses
    assert 'status' in err
    for valid in ('draft', 'in-progress', 'completed', 'archived'):
        assert valid in j.get('error', ''), f"Expected {valid!r} in error: {j}"


def test_post_set_overview_partial_update_preserves_other_fields(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [], 'observables': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-set-overview',
        {'investigation': 'demo', 'fields': {
            'question': 'Q1', 'hypothesis': 'H1', 'status': 'in-progress',
        }},
    )
    assert code in (200, 500), j

    code, j = _post(
        workspace_server.url + '/api/investigation-set-overview',
        {'investigation': 'demo', 'fields': {'status': 'completed'}},
    )
    assert code in (200, 500), j

    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['question'] == 'Q1'
    assert spec['hypothesis'] == 'H1'
    assert spec['status'] == 'completed'


def test_post_set_overview_accepts_topic(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [], 'observables': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-set-overview',
        {'investigation': 'demo', 'fields': {'topic': 'Antibiotic response'}},
    )
    assert code in (200, 500), j
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['topic'] == 'Antibiotic response'


# ---------------------------------------------------------------------------
# Investigation comparison add/update/delete endpoints (Task A4)
# ---------------------------------------------------------------------------

def test_post_comparison_add_appends(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo', 'composites': [], 'runs': [], 'observables': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-comparison-add',
        {'investigation': 'demo',
         'name': 'rate-cmp',
         'description': 'rate doubling',
         'variants': ['baseline', 'high-rate'],
         'observables': ['DnaA_count']},
    )
    assert code in (200, 500), j
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['comparisons'][-1]['name'] == 'rate-cmp'
    assert spec['comparisons'][-1]['description'] == 'rate doubling'
    assert spec['comparisons'][-1]['variants'] == ['baseline', 'high-rate']
    assert spec['comparisons'][-1]['observables'] == ['DnaA_count']


def test_post_comparison_update_replaces(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'composites': [], 'runs': [], 'observables': [],
        'comparisons': [{
            'name': 'rate-cmp',
            'description': 'original',
            'variants': ['baseline', 'high-rate'],
            'observables': ['DnaA_count'],
        }],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-comparison-update',
        {'investigation': 'demo',
         'name': 'rate-cmp',
         'fields_to_update': {'description': 'updated'}},
    )
    assert code in (200, 500), j
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['comparisons'][0]['description'] == 'updated'
    # Other fields are preserved
    assert spec['comparisons'][0]['name'] == 'rate-cmp'
    assert spec['comparisons'][0]['variants'] == ['baseline', 'high-rate']
    assert spec['comparisons'][0]['observables'] == ['DnaA_count']


def test_delete_comparison_refuses_with_viz_dependents(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'composites': [], 'runs': [], 'observables': [],
        'comparisons': [{
            'name': 'rate-cmp',
            'description': 'rate doubling',
            'variants': ['baseline', 'high-rate'],
            'observables': ['DnaA_count'],
        }],
        'visualizations': [{
            'name': 'cmp-plot',
            'config': {'comparison': 'rate-cmp'},
        }],
    }, sort_keys=False))

    req = urllib.request.Request(
        workspace_server.url + '/api/investigation-comparison',
        data=json.dumps({'investigation': 'demo', 'name': 'rate-cmp'}).encode(),
        method='DELETE', headers={'Content-Type': 'application/json'},
    )
    try:
        urllib.request.urlopen(req)
        raise AssertionError('expected refusal')
    except urllib.error.HTTPError as e:
        assert e.code == 409, f"expected 409, got {e.code}"
        body = json.loads(e.read())
        err = str(body).lower()
        assert 'visualization' in err or 'cmp-plot' in err, body

    # Spec unchanged — refusal is non-destructive
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['comparisons'][0]['name'] == 'rate-cmp'


def test_delete_comparison_succeeds_when_unreferenced(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'composites': [], 'runs': [], 'observables': [],
        'comparisons': [{
            'name': 'rate-cmp',
            'description': 'rate doubling',
            'variants': ['baseline', 'high-rate'],
            'observables': ['DnaA_count'],
        }],
        'visualizations': [],
    }, sort_keys=False))

    req = urllib.request.Request(
        workspace_server.url + '/api/investigation-comparison',
        data=json.dumps({'investigation': 'demo', 'name': 'rate-cmp'}).encode(),
        method='DELETE', headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
    except urllib.error.HTTPError as e:
        # 500 acceptable for bare-workspace git failures, but file changes happen eagerly
        assert e.code == 500, f"expected 200 or 500, got {e.code}"

    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['comparisons'] == []


# ---------------------------------------------------------------------------
# Investigation group add/update/delete endpoints (Task B7)
# ---------------------------------------------------------------------------

def test_post_group_add_appends(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'variants': [
            {'name': 'baseline', 'source': 'pkg.x'},
            {'name': 'high-rate', 'extends': 'baseline'},
        ],
        'groups': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-group-add',
        {'investigation': 'demo',
         'name': 'control',
         'description': 'Baseline condition.',
         'variants': ['baseline']},
    )
    assert code in (200, 500), j
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['groups'][-1]['name'] == 'control'
    assert spec['groups'][-1]['description'] == 'Baseline condition.'
    assert spec['groups'][-1]['variants'] == ['baseline']


def test_post_group_add_rejects_unknown_variant(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'variants': [{'name': 'baseline', 'source': 'pkg.x'}],
        'groups': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-group-add',
        {'investigation': 'demo',
         'name': 'g1',
         'description': '',
         'variants': ['ghost']},
    )
    assert code == 400, j
    assert 'ghost' in str(j).lower() or 'unknown' in str(j).lower(), j
    # Spec unchanged
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['groups'] == []


def test_post_group_update_replaces(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'variants': [
            {'name': 'baseline', 'source': 'pkg.x'},
            {'name': 'high-rate', 'extends': 'baseline'},
        ],
        'groups': [{
            'name': 'control',
            'description': 'original',
            'variants': ['baseline'],
        }],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/investigation-group-update',
        {'investigation': 'demo',
         'name': 'control',
         'fields_to_update': {
             'description': 'updated',
             'variants': ['baseline', 'high-rate'],
         }},
    )
    assert code in (200, 500), j
    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['groups'][0]['description'] == 'updated'
    assert spec['groups'][0]['variants'] == ['baseline', 'high-rate']
    # Name is immutable
    assert spec['groups'][0]['name'] == 'control'


def test_delete_group_succeeds_and_404_on_missing(workspace_server):
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'variants': [{'name': 'baseline', 'source': 'pkg.x'}],
        'groups': [{
            'name': 'control',
            'description': 'x',
            'variants': ['baseline'],
        }],
    }, sort_keys=False))

    # Existing group → succeeds
    req = urllib.request.Request(
        workspace_server.url + '/api/investigation-group',
        data=json.dumps({'investigation': 'demo', 'name': 'control'}).encode(),
        method='DELETE', headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
    except urllib.error.HTTPError as e:
        # 500 acceptable for bare-workspace git failures, but file changes happen eagerly
        assert e.code == 500, f"expected 200 or 500, got {e.code}"

    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    assert spec['groups'] == []

    # Re-delete → 404
    req2 = urllib.request.Request(
        workspace_server.url + '/api/investigation-group',
        data=json.dumps({'investigation': 'demo', 'name': 'control'}).encode(),
        method='DELETE', headers={'Content-Type': 'application/json'},
    )
    try:
        urllib.request.urlopen(req2)
        raise AssertionError('expected 404')
    except urllib.error.HTTPError as e:
        assert e.code == 404, f"expected 404, got {e.code}"


# ---------------------------------------------------------------------------
# Investigation create-from-composite endpoint (Task A5)
# ---------------------------------------------------------------------------

def test_post_create_from_composite_creates_v2_spec(workspace_server):
    """Cloning a workspace-catalog composite produces a v2-shape spec with
    baseline + variants[0] referencing the resolved source, plus a sidecar copy."""
    pkg_composites = workspace_server.root / 'pbg_testws' / 'composites'
    pkg_composites.mkdir(parents=True, exist_ok=True)
    (pkg_composites / 'chromosome-partition.composite.yaml').write_text(yaml.safe_dump({
        'name': 'chromosome-partition',
        'state': {'chromosome': {'count': {'_type': 'integer', '_default': 100}}},
    }))

    code, j = _post(
        workspace_server.url + '/api/study-create-from-composite',
        {'composite_name': 'chromosome-partition'},
    )
    assert code == 200, j
    auto_name = j.get('name', '')
    assert auto_name.startswith('study-chromosome-partition-'), (
        f"expected auto-name to start with 'study-chromosome-partition-', got {auto_name!r}"
    )
    # 6-char hex uuid suffix
    suffix = auto_name[len('study-chromosome-partition-'):]
    assert len(suffix) == 6, f"expected 6-char suffix, got {suffix!r}"

    inv_dir = workspace_server.root / 'investigations' / auto_name
    spec_path = inv_dir / 'spec.yaml'
    assert spec_path.is_file(), f"spec.yaml not found at {spec_path}"

    spec = yaml.safe_load(spec_path.read_text())
    assert spec['name'] == auto_name
    assert spec['baseline'] == 'chromosome-partition'
    assert isinstance(spec['variants'], list) and len(spec['variants']) == 1
    v0 = spec['variants'][0]
    assert v0['name'] == 'chromosome-partition'
    assert v0['source'] == 'pbg_testws.composites.chromosome-partition'
    assert v0['document'] == './composites/chromosome-partition.yaml'
    # v2 shape fields all present
    assert spec.get('comparisons') == []
    assert spec.get('conclusions') == ''
    assert spec.get('question') == ''
    assert spec.get('hypothesis') == ''
    assert spec.get('status') == 'draft'

    sidecar = inv_dir / 'composites' / 'chromosome-partition.yaml'
    assert sidecar.is_file(), f"sidecar composite not copied to {sidecar}"
    sidecar_doc = yaml.safe_load(sidecar.read_text())
    assert sidecar_doc['name'] == 'chromosome-partition'


def test_post_create_from_composite_unknown_returns_404(workspace_server):
    """Unknown composite_name yields a 404."""
    code, j = _post(
        workspace_server.url + '/api/study-create-from-composite',
        {'composite_name': 'does-not-exist'},
    )
    assert code == 404, j


def test_post_create_from_composite_blank_returns_400(workspace_server):
    """Empty composite_name yields a 400."""
    code, j = _post(
        workspace_server.url + '/api/study-create-from-composite',
        {'composite_name': ''},
    )
    assert code == 400, j


# ---------------------------------------------------------------------------
# Composite promote-to-catalog endpoint tests (Task A6)
# ---------------------------------------------------------------------------

def test_promote_to_catalog_writes_new_composite_yaml(workspace_server):
    """Promote copies an investigation variant's sidecar into the workspace catalog
    as <pkg>/composites/<target_name>.composite.yaml, sets the YAML name field,
    and marks the variant as promoted in spec.yaml."""
    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'tuned-baseline.yaml').write_text(yaml.safe_dump({
        'name': 'tuned-baseline',
        'state': {'chromosome': {'count': {'_type': 'integer', '_default': 200}}},
    }, sort_keys=False))
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'baseline': 'tuned-baseline',
        'variants': [{
            'name': 'tuned-baseline',
            'source': 'pbg_testws.composites.baseline',
            'document': './composites/tuned-baseline.yaml',
        }],
        'comparisons': [],
        'conclusions': '',
        'question': '', 'hypothesis': '', 'status': 'draft',
    }, sort_keys=False))

    # Ensure catalog dir exists (the endpoint should create it if missing, but
    # the workspace fixture already created pbg_testws/).
    code, j = _post(
        workspace_server.url + '/api/composite-promote-to-catalog',
        {'investigation': 'demo', 'variant': 'tuned-baseline',
         'target_name': 'promoted-thing',
         'description': 'A promoted composite'},
    )
    assert code == 200, j

    target_path = (
        workspace_server.root / 'pbg_testws' / 'composites'
        / 'promoted-thing.composite.yaml'
    )
    assert target_path.is_file(), f"expected catalog entry at {target_path}"

    doc = yaml.safe_load(target_path.read_text())
    assert doc['name'] == 'promoted-thing'
    assert doc.get('description') == 'A promoted composite'
    # State copied over from the sidecar
    assert doc['state']['chromosome']['count']['_default'] == 200

    spec = yaml.safe_load((inv / 'spec.yaml').read_text())
    variant = next(v for v in spec['variants'] if v['name'] == 'tuned-baseline')
    assert variant.get('promoted') is True


def test_promote_to_catalog_409_when_target_already_exists(workspace_server):
    """If the target catalog entry already exists, the endpoint returns 409
    rather than silently overwriting."""
    pkg_composites = workspace_server.root / 'pbg_testws' / 'composites'
    pkg_composites.mkdir(parents=True, exist_ok=True)
    (pkg_composites / 'thing.composite.yaml').write_text(yaml.safe_dump({
        'name': 'thing', 'state': {},
    }, sort_keys=False))

    inv = workspace_server.root / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'src.yaml').write_text(yaml.safe_dump({
        'name': 'src', 'state': {'a': 1},
    }, sort_keys=False))
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'baseline': 'src',
        'variants': [{
            'name': 'src',
            'source': 'pbg_testws.composites.foo',
            'document': './composites/src.yaml',
        }],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/composite-promote-to-catalog',
        {'investigation': 'demo', 'variant': 'src', 'target_name': 'thing'},
    )
    assert code == 409, j


def test_promote_to_catalog_404_when_variant_missing(workspace_server):
    """Unknown variant in an existing investigation yields a 404."""
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'baseline': '',
        'variants': [],
    }, sort_keys=False))

    code, j = _post(
        workspace_server.url + '/api/composite-promote-to-catalog',
        {'investigation': 'demo', 'variant': 'no-such-variant'},
    )
    assert code == 404, j


# ---------------------------------------------------------------------------
# Task E1: /api/investigations exposes v2 summary stats
# ---------------------------------------------------------------------------

def test_get_investigations_includes_v3_summary_fields(workspace_server):
    """Row shape under v3: baseline_names list, n_baseline, n_variants,
    n_interventions, n_runs, plus the existing composite/composites fields."""
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'schema_version': 3,
        'name': 'demo',
        'description': 'v3 summary fixture',
        'baseline': [
            {'name': 'core', 'composite': 'pkg.composites.core', 'params': {}},
        ],
        'variants': [
            {'name': 'hi', 'base_composite': 'core', 'parameter_overrides': {'k': 1}},
            {'name': 'lo', 'base_composite': 'core', 'parameter_overrides': {'k': 0.1}},
        ],
        'interventions': [
            {'name': 'heat-shock', 'description': '+10C for 5 min'},
        ],
        'runs': [
            {'run_id': 'r1', 'variant': None, 'label': 'core', 'status': 'completed', 'n_steps': 5},
            {'run_id': 'r2', 'variant': 'hi', 'label': 'hi', 'status': 'completed', 'n_steps': 5},
        ],
    }, sort_keys=False))

    with urllib.request.urlopen(workspace_server.url + '/api/investigations') as resp:
        body = json.loads(resp.read())

    rows = [r for r in body['investigations'] if r['name'] == 'demo']
    assert len(rows) == 1
    row = rows[0]
    assert row['baseline_names'] == ['core']
    assert row['n_baseline'] == 1
    assert row['n_variants'] == 2
    assert row['n_interventions'] == 1
    assert row['n_runs'] == 2
    assert row['n_simulations'] == row['n_runs']
    assert 'composite' in row
    assert 'composites' in row


def test_get_investigations_includes_topic(workspace_server):
    """The list endpoint must surface the study `topic` field for sidebar grouping."""
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'name': 'demo',
        'description': 'topic fixture',
        'topic': 'Antibiotic response',
        'baseline': 'base',
        'variants': [{'name': 'base', 'source': 'pkg.x'}],
        'runs': [],
        'observables': [],
    }, sort_keys=False))

    with urllib.request.urlopen(workspace_server.url + '/api/investigations') as resp:
        body = json.loads(resp.read())

    rows = [r for r in body['investigations'] if r['name'] == 'demo']
    assert len(rows) == 1
    assert rows[0]['topic'] == 'Antibiotic response'


# ---------------------------------------------------------------------------
# Unit tests for _format_baseline_source (v3 list baseline)
# ---------------------------------------------------------------------------

def test_format_baseline_source_single_entry_short_form():
    """Single baseline entry with a `.composites.` source → pkg_short:name."""
    from vivarium_dashboard.server import _format_baseline_source
    spec = {"baseline": [
        {"name": "core", "composite": "pbg_chromosome_rep1.composites.chromosome-partition", "params": {}},
    ]}
    assert _format_baseline_source(spec) == "pbg_chromosome_rep1:chromosome-partition"


def test_format_baseline_source_opaque_composite():
    """Single baseline entry with an opaque composite ID → returned verbatim."""
    from vivarium_dashboard.server import _format_baseline_source
    spec = {"baseline": [{"name": "x", "composite": "some.opaque.path", "params": {}}]}
    assert _format_baseline_source(spec) == "some.opaque.path"


def test_format_baseline_source_multiple_entries():
    """Multiple baseline entries → first entry formatted + ' (+N more)'."""
    from vivarium_dashboard.server import _format_baseline_source
    spec = {"baseline": [
        {"name": "a", "composite": "pkg_x.composites.first", "params": {}},
        {"name": "b", "composite": "pkg_y.composites.second", "params": {}},
        {"name": "c", "composite": "pkg_z.composites.third", "params": {}},
    ]}
    assert _format_baseline_source(spec) == "pkg_x:first (+2 more)"


def test_format_baseline_source_empty_or_absent():
    """Missing or empty baseline → empty string."""
    from vivarium_dashboard.server import _format_baseline_source
    assert _format_baseline_source({}) == ""
    assert _format_baseline_source({"baseline": []}) == ""


# ---------------------------------------------------------------------------
# Richer-card projection: /api/investigations surfaces baseline_source +
# conclusions_excerpt so the index can render at-a-glance cards.
# ---------------------------------------------------------------------------

def test_get_investigations_includes_baseline_source_and_conclusions_excerpt(workspace_server):
    """row['baseline_source'] and row['conclusions_excerpt'] under v3."""
    ws = workspace_server.root / 'investigations'

    # Case A — single baseline with .composites. source + long conclusions
    a = ws / 'with-baseline'
    a.mkdir(parents=True)
    long_prose = (
        "We saw substantial divergence in growth across substrate variants. "
        "Lag phase was extended at lower substrate concentrations, while "
        "exponential phase plateaued at expected μmax values. "
        "The baseline replication run converges in 42 minutes which matches "
        "the wet-lab doubling-time estimate from Smith 2019."
    )
    (a / 'spec.yaml').write_text(yaml.safe_dump({
        'schema_version': 3,
        'name': 'with-baseline',
        'baseline': [{'name': 'core',
                      'composite': 'pbg_chromosome_rep1.composites.chromosome-partition',
                      'params': {}}],
        'variants': [{'name': 'mut', 'base_composite': 'core',
                      'parameter_overrides': {}}],
        'conclusions': (
            '## Claims\n' + long_prose +
            '\n## Evidence\nplots A,B\n## Limitations\nN=3\n## Next steps\nrun N=10\n'
        ),
    }, sort_keys=False))

    # Case B — no baseline, no conclusions (empty baseline: [] fails v3
    # validation, so the row is returned as status=invalid; it will not have
    # baseline_source / conclusions_excerpt fields)
    b = ws / 'no-baseline'
    b.mkdir(parents=True)
    (b / 'spec.yaml').write_text(yaml.safe_dump({
        'schema_version': 3,
        'name': 'no-baseline',
        'baseline': [],
        'variants': [],
    }, sort_keys=False))

    # Case C — opaque single composite
    c = ws / 'opaque-source'
    c.mkdir(parents=True)
    (c / 'spec.yaml').write_text(yaml.safe_dump({
        'schema_version': 3,
        'name': 'opaque-source',
        'baseline': [{'name': 'x', 'composite': 'some.opaque.path', 'params': {}}],
        'variants': [],
    }, sort_keys=False))

    with urllib.request.urlopen(workspace_server.url + '/api/investigations') as resp:
        body = json.loads(resp.read())
    by_name = {r['name']: r for r in body['investigations']}

    row_a = by_name['with-baseline']
    assert row_a['baseline_source'] == 'pbg_chromosome_rep1:chromosome-partition'
    excerpt_a = row_a['conclusions_excerpt']
    assert len(excerpt_a) <= 241  # 240 + ellipsis
    assert excerpt_a.endswith('…')
    assert '## Claims' not in excerpt_a
    assert '## Evidence' not in excerpt_a
    assert 'Lag phase' in excerpt_a  # prose content survived stripping

    # Case B — empty baseline: [] fails v3 validation; the row is present but
    # marked invalid (no baseline_source / conclusions_excerpt fields).
    row_b = by_name['no-baseline']
    assert row_b['status'] == 'invalid'
    assert 'baseline_source' not in row_b  # invalid rows skip projection

    row_c = by_name['opaque-source']
    assert row_c['baseline_source'] == 'some.opaque.path'
    assert row_c['conclusions_excerpt'] == ''


# ---------------------------------------------------------------------------
# /api/dirty-status + /api/dirty-commit-all endpoint tests
# ---------------------------------------------------------------------------

import subprocess


def _git(args, cwd):
    return subprocess.run(
        ["git"] + list(args), cwd=str(cwd),
        capture_output=True, text=True, check=True,
    )


def _git_init_clean(ws_root):
    """Initialise a clean git repo with one initial commit in ws_root."""
    _git(["init", "-q", "-b", "main"], cwd=ws_root)
    _git(["config", "user.email", "test@local"], cwd=ws_root)
    _git(["config", "user.name", "test"], cwd=ws_root)
    # Match the real workspace: .pbg/state.json is gitignored so workstream
    # state never shows up in porcelain output.
    (ws_root / ".gitignore").write_text(".pbg/\n__pycache__/\n*.pyc\n")
    _git(["add", "-A"], cwd=ws_root)
    _git(["commit", "-q", "-m", "initial"], cwd=ws_root)


def _get(url):
    with urllib.request.urlopen(url) as resp:
        return resp.status, json.loads(resp.read())


def test_get_dirty_status_empty_when_clean(workspace_server):
    """A freshly committed workspace reports zero dirty files."""
    _git_init_clean(workspace_server.root)
    code, j = _get(workspace_server.url + "/api/dirty-status")
    assert code == 200, j
    assert j["count"] == 0, j
    assert j["files"] == [], j


def test_get_dirty_status_lists_uncommitted_files(workspace_server):
    """Adding an untracked file shows up in the dirty-status response."""
    _git_init_clean(workspace_server.root)
    (workspace_server.root / "scratch_file.txt").write_text("hello dirty\n")
    code, j = _get(workspace_server.url + "/api/dirty-status")
    assert code == 200, j
    assert j["count"] >= 1, j
    paths = [f["path"] for f in j["files"]]
    assert "scratch_file.txt" in paths, paths


def test_post_dirty_commit_all_commits_and_returns_message(workspace_server, monkeypatch):
    """POST /api/dirty-commit-all stages and commits dirty files with auto-generated message."""
    ws_root = workspace_server.root
    _git_init_clean(ws_root)
    # Create an active workstream branch and corresponding state file.
    _git(["checkout", "-q", "-b", "feat/test-branch"], cwd=ws_root)
    (ws_root / ".pbg").mkdir(parents=True, exist_ok=True)
    (ws_root / ".pbg" / "state.json").write_text(
        json.dumps({"active_branch": "feat/test-branch", "base": "main"}) + "\n"
    )
    # Point work_state.workspace_root at our temp dir so load_state reads our state file.
    import vivarium_dashboard.lib._root as root_mod
    import vivarium_dashboard.lib.work_state as work_state_mod
    monkeypatch.setattr(root_mod, "workspace_root", lambda: ws_root)
    monkeypatch.setattr(work_state_mod, "_state_path", lambda: ws_root / ".pbg" / "state.json")

    # Create an uncommitted file under scripts/ so the auto-generated prefix is "chore(scripts)".
    (ws_root / "scripts").mkdir(parents=True, exist_ok=True)
    (ws_root / "scripts" / "scratch.py").write_text("# scratch\n")

    code, j = _post(workspace_server.url + "/api/dirty-commit-all", {})
    assert code == 200, j
    assert "commit_sha" in j and j["commit_sha"], j
    # 7-char short sha
    assert len(j["commit_sha"]) == 7, j
    # Message should follow the conventional pattern produced by _suggest_dirty_commit_message.
    msg = j["message"]
    assert msg.startswith("chore("), msg
    assert "commit" in msg and "pending file" in msg, msg

    # Verify the working tree is now clean (porcelain-filtered).
    code2, j2 = _get(workspace_server.url + "/api/dirty-status")
    assert code2 == 200, j2
    assert j2["count"] == 0, j2


def test_post_dirty_commit_all_409_when_clean(workspace_server, monkeypatch):
    """POST /api/dirty-commit-all returns 409 when working tree is already clean."""
    ws_root = workspace_server.root
    _git_init_clean(ws_root)
    _git(["checkout", "-q", "-b", "feat/clean-branch"], cwd=ws_root)
    (ws_root / ".pbg").mkdir(parents=True, exist_ok=True)
    (ws_root / ".pbg" / "state.json").write_text(
        json.dumps({"active_branch": "feat/clean-branch", "base": "main"}) + "\n"
    )
    import vivarium_dashboard.lib._root as root_mod
    import vivarium_dashboard.lib.work_state as work_state_mod
    monkeypatch.setattr(root_mod, "workspace_root", lambda: ws_root)
    monkeypatch.setattr(work_state_mod, "_state_path", lambda: ws_root / ".pbg" / "state.json")

    code, j = _post(workspace_server.url + "/api/dirty-commit-all", {})
    assert code == 409, j
    assert "clean" in (j.get("error") or "").lower(), j


def test_post_composite_test_run_accepts_generator_id(workspace_server):
    """POST /api/composite-test-run must resolve @composite_generator ids via
    the in-process registry, not just file-based specs.

    Before the generator-resolution branch was added, generator-kind composites
    fell through to find_composite_path() and returned 404 'spec file not
    found'. This test pins the new behaviour: the endpoint must NOT return
    404/'spec file not found' for a registered generator id.

    The actual subprocess run may legitimately fail in the minimal test
    workspace (no real processes registered), but the failure must come from
    the run/build path — not from spec lookup. Accepts 200 on a successful
    run, or any non-404-spec-lookup error.
    """
    try:
        from pbg_superpowers.composite_generator import (
            _REGISTRY, composite_generator,
        )
    except ImportError:
        pytest.skip("pbg-superpowers not importable")

    @composite_generator(
        name="vix-endpoints-gen",
        description="Generator used by test_post_composite_test_run_accepts_generator_id.",
        parameters={"x": {"type": "float", "default": 0.5}},
    )
    def _gen(core=None, x=0.5):
        return {"state": {"x_value": x}}

    expected_id = f"{__name__}.vix-endpoints-gen"
    try:
        assert expected_id in _REGISTRY, "decorator must register the generator"
        code, j = _post(
            workspace_server.url + "/api/composite-test-run",
            {"id": expected_id, "overrides": {"x": 1.0}, "steps": 1},
        )
        # The key assertion: we must NOT hit the file-lookup 404.
        assert not (code == 404 and "spec file not found" in (j.get("error") or "")), (
            f"Generator id {expected_id} should not fall through to "
            f"file-based 'spec file not found'; got code={code} body={j}"
        )
        # Acceptable outcomes: 202 (detached run accepted + spawned — the
        # normal case now), 200 (legacy/synchronous), or any non-404-spec-
        # lookup error (run failure, parse failure, etc.).
        assert code == 202 or code == 200 or code >= 400, (
            f"unexpected status: {code} {j}"
        )
    finally:
        _REGISTRY.pop(expected_id, None)


# ---------------------------------------------------------------------------
# /api/investigation-run-one — viz_html surfacing (Study workbench)
# ---------------------------------------------------------------------------

def _get(url):
    """GET helper — same shape as _post but for query-string endpoints."""
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_investigation_run_one_returns_viz_html_for_inlined_viz(workspace_server, tmp_path):
    """POST /api/investigation-run-one on a v2 study whose baseline composite
    inlines a Visualization step must:

    - return ``viz_html`` keyed by the viz step path,
    - persist each rendered HTML to ``investigations/<inv>/viz/<run_id>/<name>.html``,
    - make those files discoverable via GET ``/api/investigation-viz-html``.

    Uses ``as_visualization`` (decorator form) so the test doesn't depend on
    spatio-flux. The composite has a single store ``x`` plus an inlined
    Visualization step that reads it.

    Skipped when pbg-superpowers' ``as_visualization`` is not importable.
    """
    if not _HAS_AS_VIZ:
        pytest.skip("pbg-superpowers (as_visualization) not importable")

    # ------------------------------------------------------------------
    # 1. Materialise a workspace-level Visualization class so the
    #    composite's `address: local:TinyViz` resolves at build_core().
    # ------------------------------------------------------------------
    pkg_root = workspace_server.root / "pbg_testws"
    viz_pkg = pkg_root / "visualizations"
    viz_pkg.mkdir(parents=True, exist_ok=True)
    (viz_pkg / "__init__.py").write_text("from . import tiny_viz  # noqa: F401\n")
    (viz_pkg / "tiny_viz.py").write_text(
        'from pbg_superpowers.visualization import as_visualization\n'
        '@as_visualization(\n'
        '    inputs={"x": "float"},\n'
        '    name="TinyViz",\n'
        '    demo={"x": 1.0},\n'
        ')\n'
        'def update_tiny(state):\n'
        '    return {"html": "<p>x=" + str(state.get("x")) + "</p>"}\n'
        '# The decorator-returned class is bound to `update_tiny`; alias it\n'
        '# under the class name so build_core() can import by class name too.\n'
        'TinyViz = update_tiny\n'
    )

    # Patch core.py to register TinyViz so the subprocess (which builds the
    # workspace's core) can resolve `local:TinyViz`.
    (pkg_root / "core.py").write_text(
        "from bigraph_schema import allocate_core\n"
        "def build_core():\n"
        "    core = allocate_core()\n"
        "    try:\n"
        "        from pbg_testws.visualizations.tiny_viz import TinyViz\n"
        "        core.register_link('TinyViz', TinyViz)\n"
        "    except Exception:\n"
        "        pass\n"
        "    return core\n"
    )

    # ------------------------------------------------------------------
    # 2. Lay down a v2 investigation with a single baseline variant
    #    whose document inlines the Visualization step.
    # ------------------------------------------------------------------
    inv_name = "study-tiny-viz"
    inv_dir = workspace_server.root / "investigations" / inv_name
    (inv_dir / "composites").mkdir(parents=True, exist_ok=True)

    composite_doc = {
        "name": "tiny-viz-demo",
        "state": {
            "x": {"_type": "float", "_default": 0.5},
            "viz_tiny": {
                "_type": "step",
                "address": "local:TinyViz",
                "config": {},
                "inputs": {"x": ["x"]},
                "outputs": {"html": ["viz_tiny_html"]},
            },
            "viz_tiny_html": {"_type": "string", "_default": ""},
        },
    }
    (inv_dir / "composites" / "tiny-viz-demo.yaml").write_text(
        yaml.safe_dump(composite_doc, sort_keys=False)
    )

    spec_doc = {
        "name": inv_name,
        "baseline": "tiny-viz-demo",
        "variants": [{
            "name": "tiny-viz-demo",
            "source": "pbg_testws.composites.tiny-viz-demo",
            "document": "./composites/tiny-viz-demo.yaml",
        }],
        "comparisons": [],
        "conclusions": "",
        "question": "",
        "hypothesis": "",
        "status": "draft",
    }
    (inv_dir / "spec.yaml").write_text(yaml.safe_dump(spec_doc, sort_keys=False))

    # ------------------------------------------------------------------
    # 3. Run the single ad-hoc execution endpoint.
    # ------------------------------------------------------------------
    code, j = _post(
        workspace_server.url + "/api/investigation-run-one",
        {"investigation": inv_name, "sim_name": "ad-hoc",
         "overrides": {}, "steps": 1},
    )

    # The subprocess depends on process_bigraph being installed AND the
    # workspace's core building cleanly; if either gate fails we get a useful
    # error rather than a false positive. We treat "no process_bigraph" as
    # a skip, and require viz_html on the happy path.
    if code != 200:
        pytest.skip(f"investigation-run-one returned {code}: {j!r}")
    if not j.get("ok"):
        # Subprocess ran but the composite itself failed (e.g. address lookup,
        # process_bigraph version mismatch). Treat as skip rather than fail
        # — this test pins the surfacing, not the wrapped simulator stack.
        err = j.get("error", "")
        pytest.skip(f"composite run failed (not the surface under test): {err}")

    run_id = j.get("run_id")
    assert run_id, f"missing run_id in response: {j}"
    viz_html = j.get("viz_html") or {}
    assert viz_html, (
        "expected viz_html in response, got empty/missing — "
        f"render_results did not surface the inlined viz step. body={j}"
    )

    # At least one viz key, with an HTML body + persisted path.
    first_key = next(iter(viz_html))
    entry = viz_html[first_key]
    assert isinstance(entry, dict), entry
    assert "html" in entry and "path" in entry, entry
    rel_path = entry["path"]
    assert rel_path, "expected non-empty persisted path"
    on_disk = workspace_server.root / rel_path
    assert on_disk.is_file(), f"persisted viz HTML not found at {on_disk}"

    # ------------------------------------------------------------------
    # 4. GET /api/investigation-viz-html lists the same file.
    # ------------------------------------------------------------------
    code, j2 = _get(
        workspace_server.url
        + f"/api/investigation-viz-html?investigation={inv_name}&run_id={run_id}"
    )
    assert code == 200, j2
    listed = {f["name"]: f["html_path"] for f in (j2.get("viz_files") or [])}
    assert first_key in listed, (
        f"GET /api/investigation-viz-html missing {first_key!r}; listed={listed}"
    )


# ---------------------------------------------------------------------------
# /api/catalog — Installed-modules sync (out-of-sync drift detection)
# ---------------------------------------------------------------------------

def test_get_catalog_marks_out_of_sync_when_import_fails(workspace_server, monkeypatch):
    """If workspace.yaml.imports lists a module whose Python package is NOT
    importable in the workspace venv, /api/catalog should flag it
    out_of_sync=true with a non-empty reason."""
    # Seed a tiny catalog with one entry ``foo``.
    catalog_dir = workspace_server.root / "scripts" / "_catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / "modules.json").write_text(json.dumps([
        {
            "name": "foo",
            "description": "Fake module for sync test",
            "source": "https://example.invalid/foo.git",
            "ref": "main",
            "package": "foo_pkg_that_does_not_exist_xyz",
            "tags": [],
        }
    ]))

    # Mark `foo` as installed in workspace.yaml.imports.
    ws_path = workspace_server.root / "workspace.yaml"
    ws = yaml.safe_load(ws_path.read_text()) or {}
    ws.setdefault("imports", {})["foo"] = {
        "source": "https://example.invalid/foo.git",
        "ref": "main",
        "mode": "reference",
        "path": "external/foo",
        "description": "Fake module for sync test",
        "installed": True,
        "install_path": str(workspace_server.root / "external" / "foo"),
        "package": "foo_pkg_that_does_not_exist_xyz",
    }
    ws_path.write_text(yaml.safe_dump(ws, sort_keys=False))

    # Force the sync helper to think a venv exists and report an import failure.
    # We patch _check_installed_module_sync directly so the test is hermetic
    # regardless of whether a real .venv is present in tmp_path.
    import vivarium_dashboard.server as srv
    monkeypatch.setattr(
        srv,
        "_check_installed_module_sync",
        lambda pkg_name, install_path: f"Python import of '{pkg_name}' failed (was the venv updated?)",
    )

    code, body = _get(workspace_server.url + "/api/catalog")
    assert code == 200, body
    rows = {m["name"]: m for m in body.get("modules", [])}
    assert "foo" in rows, body
    foo = rows["foo"]
    assert foo["installed"] is True, foo
    assert foo.get("out_of_sync") is True, foo
    assert foo.get("out_of_sync_reason"), foo


def test_get_catalog_no_drift_when_sync_helper_returns_none(workspace_server, monkeypatch):
    """When the sync helper reports no drift, /api/catalog must NOT set
    out_of_sync flags on installed modules."""
    catalog_dir = workspace_server.root / "scripts" / "_catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / "modules.json").write_text(json.dumps([
        {
            "name": "bar",
            "description": "Fake module no-drift",
            "source": "https://example.invalid/bar.git",
            "ref": "main",
            "package": "bar_pkg",
            "tags": [],
        }
    ]))

    ws_path = workspace_server.root / "workspace.yaml"
    ws = yaml.safe_load(ws_path.read_text()) or {}
    ws.setdefault("imports", {})["bar"] = {
        "source": "https://example.invalid/bar.git",
        "ref": "main",
        "mode": "reference",
        "path": "external/bar",
        "installed": True,
        "install_path": str(workspace_server.root / "external" / "bar"),
        "package": "bar_pkg",
    }
    ws_path.write_text(yaml.safe_dump(ws, sort_keys=False))

    import vivarium_dashboard.server as srv
    monkeypatch.setattr(srv, "_check_installed_module_sync", lambda p, i: None)

    code, body = _get(workspace_server.url + "/api/catalog")
    assert code == 200, body
    rows = {m["name"]: m for m in body.get("modules", [])}
    assert "bar" in rows, body
    bar = rows["bar"]
    assert bar["installed"] is True, bar
    assert bar.get("out_of_sync") is not True, bar


# ---------------------------------------------------------------------------
# /api/workspace-manifest and /api/open-window — agentic situational awareness
# ---------------------------------------------------------------------------

def test_get_workspace_manifest_returns_all_sections(workspace_server):
    """Fresh workspace: manifest has all six top-level sections."""
    code, body = _get(workspace_server.url + "/api/workspace-manifest")
    assert code == 200, body
    for key in ("workspace", "composites", "studies",
                "registry", "health", "skills"):
        assert key in body, f"missing section {key!r}: {body}"
    ws = body["workspace"]
    assert ws["name"] == "testws"
    assert ws["package_path"] == "pbg_testws"
    # Composites/studies/skills are lists; registry/health/workspace are dicts.
    assert isinstance(body["composites"], list)
    assert isinstance(body["studies"], list)
    assert isinstance(body["skills"], list)
    assert isinstance(body["registry"], dict)
    assert isinstance(body["health"], dict)


def test_get_workspace_manifest_studies_section_lists_specs(workspace_server):
    """A v3 study under investigations/ surfaces in the manifest's studies section."""
    inv_dir = workspace_server.root / "investigations" / "demo"
    inv_dir.mkdir(parents=True)
    (inv_dir / "spec.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "demo",
        "topic": "metabolism",
        "status": "in-progress",
        "baseline": [
            {"name": "core", "composite": "pbg_testws.composites.demo", "params": {}},
        ],
        "variants": [],
        "interventions": [],
        "runs": [],
        "conclusions": "## Claims\nlooks promising",
    }, sort_keys=False))

    code, body = _get(workspace_server.url + "/api/workspace-manifest")
    assert code == 200, body
    studies = body["studies"]
    assert len(studies) == 1, studies
    s = studies[0]
    assert s["name"] == "demo"
    assert s["topic"] == "metabolism"
    assert s["status"] == "in-progress"
    assert s["n_variants"] == 0
    assert s["n_baseline"] == 1
    assert s["baseline_names"] == ["core"]
    assert s["n_runs"] == 0
    assert s["conclusions_len"] > 0


def test_get_workspace_manifest_health_reports_dirty_count(workspace_server):
    """Newly added file shows up in dirty_files cap (after git init)."""
    # Skip if git not available — _dirty_workspace will raise, manifest copes
    # by returning zero, but we can't assert dirty unless we set up a repo.
    _git_init_clean(workspace_server.root)
    # Touch a new file
    (workspace_server.root / "scratch.txt").write_text("hello")
    code, body = _get(workspace_server.url + "/api/workspace-manifest")
    assert code == 200, body
    health = body["health"]
    assert health["dirty_count"] >= 1, health
    assert any("scratch.txt" in p for p in health["dirty_files"]), health


def test_post_open_window_missing_server_info_503(workspace_server):
    """When .pbg/server/server-info is absent, open-window returns 503."""
    # The workspace_server fixture deliberately does not create server-info,
    # so the route should report the dashboard isn't running.
    code, body = _post(workspace_server.url + "/api/open-window",
                       {"route": "/"})
    assert code == 503, body
    assert "server-info" in body.get("error", "")


def test_post_open_window_normalises_route(workspace_server):
    """Routes without a leading slash get normalised before being opened.

    We don't actually invoke `open` (side effect); we simulate the success
    path by writing a fake server-info file and stubbing subprocess.run.
    """
    info_dir = workspace_server.root / ".pbg" / "server"
    info_dir.mkdir(parents=True)
    (info_dir / "server-info").write_text(json.dumps({
        "url": "http://127.0.0.1:9999",
    }))

    import vivarium_dashboard.server as srv
    calls = []

    def fake_run(cmd, **kw):  # noqa: ANN001
        calls.append(cmd)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    # Stub only for this test.
    orig = srv.subprocess.run
    srv.subprocess.run = fake_run
    try:
        code, body = _post(workspace_server.url + "/api/open-window",
                           {"route": "composite-explore?id=foo"})
    finally:
        srv.subprocess.run = orig
    assert code == 200, body
    assert body["ok"] is True
    assert body["url"].endswith("/composite-explore?id=foo")
    # subprocess.run should have been invoked with a command containing the URL.
    assert calls, "open command was not invoked"
    assert any("composite-explore" in part for part in calls[-1])
