"""Tests for the workspace-default-emitter badge on the Registry tab.

The dashboard reads ``workspace.yaml::runtime.default_emitter`` and tags the
matching emitter entry in /api/registry with ``is_workspace_default: True``
so the UI can render a DEFAULT badge next to it.

These tests cover the pure marking helper directly (fast, no subprocess) and
also exercise the live /api/registry endpoint with a temp workspace where
ParquetEmitter is the registered emitter.
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error

import pytest
import yaml

from vivarium_workbench.lib import registry as reg


# ---------------------------------------------------------------------------
# Pure-function tests for _mark_default_emitter (no subprocess, no server)
# ---------------------------------------------------------------------------

def _emitter_entry(name: str) -> dict:
    return {
        "name": name,
        "address": f"process_bigraph.emitter.{name}",
        "kind": "emitter",
        "schema_preview": "",
        "aliases": [],
        "source": "framework",
    }


def _process_entry(name: str) -> dict:
    return {
        "name": name,
        "address": f"some_pkg.{name}",
        "kind": "process",
        "schema_preview": "",
        "aliases": [],
        "source": "framework",
    }


def test_mark_default_emitter_parquet_match():
    """default_emitter='parquet' tags ParquetEmitter, not SQLite/XArray."""
    data = {
        "processes": [
            _emitter_entry("ParquetEmitter"),
            _emitter_entry("SQLiteEmitter"),
            _emitter_entry("XArrayEmitter"),
        ],
        "types": [],
    }
    reg._mark_default_emitter(data, {"runtime": {"default_emitter": "parquet"}})

    by_name = {p["name"]: p for p in data["processes"]}
    assert by_name["ParquetEmitter"]["is_workspace_default"] is True
    assert by_name["SQLiteEmitter"]["is_workspace_default"] is False
    assert by_name["XArrayEmitter"]["is_workspace_default"] is False
    assert data["default_emitter"] == "parquet"


def test_mark_default_emitter_sqlite_match():
    data = {
        "processes": [
            _emitter_entry("ParquetEmitter"),
            _emitter_entry("SQLiteEmitter"),
        ],
        "types": [],
    }
    reg._mark_default_emitter(data, {"runtime": {"default_emitter": "sqlite"}})
    by_name = {p["name"]: p for p in data["processes"]}
    assert by_name["SQLiteEmitter"]["is_workspace_default"] is True
    assert by_name["ParquetEmitter"]["is_workspace_default"] is False


def test_mark_default_emitter_case_insensitive():
    """Case mismatches between workspace.yaml and class names still resolve."""
    data = {
        "processes": [_emitter_entry("ParquetEmitter")],
        "types": [],
    }
    reg._mark_default_emitter(data, {"runtime": {"default_emitter": "PARQUET"}})
    assert data["processes"][0]["is_workspace_default"] is True


def test_mark_default_emitter_no_runtime_block():
    """Missing runtime block ⇒ no entry marked, default_emitter is None."""
    data = {
        "processes": [_emitter_entry("ParquetEmitter")],
        "types": [],
    }
    reg._mark_default_emitter(data, {"name": "x"})
    assert data["processes"][0]["is_workspace_default"] is False
    assert data["default_emitter"] is None


def test_mark_default_emitter_missing_ws_data():
    data = {"processes": [_emitter_entry("ParquetEmitter")], "types": []}
    reg._mark_default_emitter(data, None)
    assert data["processes"][0]["is_workspace_default"] is False


def test_mark_default_emitter_only_emitter_kind():
    """Non-emitter entries never get the field even if name matches."""
    data = {
        "processes": [
            _emitter_entry("ParquetEmitter"),
            # A process that happens to contain 'parquet' in its name — must NOT
            # be flagged.
            _process_entry("ParquetReaderProcess"),
        ],
        "types": [],
    }
    reg._mark_default_emitter(data, {"runtime": {"default_emitter": "parquet"}})
    by_name = {p["name"]: p for p in data["processes"]}
    assert by_name["ParquetEmitter"]["is_workspace_default"] is True
    # The non-emitter must not have the flag set at all.
    assert "is_workspace_default" not in by_name["ParquetReaderProcess"]


def test_mark_default_emitter_unknown_value():
    """Unrecognized default_emitter value ⇒ no emitter flagged True."""
    data = {
        "processes": [
            _emitter_entry("ParquetEmitter"),
            _emitter_entry("SQLiteEmitter"),
        ],
        "types": [],
    }
    reg._mark_default_emitter(data, {"runtime": {"default_emitter": "rabbit"}})
    for p in data["processes"]:
        assert p["is_workspace_default"] is False


# ---------------------------------------------------------------------------
# End-to-end /api/registry test (mirrors test_registry_source_tagging.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def registry_server_with_parquet_default(tmp_path, dashboard_client):
    """Spin up the live FastAPI app with workspace.yaml setting
    runtime.default_emitter='parquet' and a workspace package that
    registers a fake ParquetEmitter subclass under the link registry.
    """
    ws_root = tmp_path

    pkg_dir = ws_root / "pbg_emitter_default_test"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "emitters.py").write_text(
        "from process_bigraph import Emitter\n\n"
        "class ParquetEmitter(Emitter):\n"
        "    config_schema = {}\n"
        "    def update(self, inputs):\n"
        "        return {}\n\n"
        "class SQLiteEmitter(Emitter):\n"
        "    config_schema = {}\n"
        "    def update(self, inputs):\n"
        "        return {}\n"
    )
    (pkg_dir / "core.py").write_text(
        "from bigraph_schema import allocate_core\n"
        "from pbg_emitter_default_test.emitters import (\n"
        "    ParquetEmitter, SQLiteEmitter,\n"
        ")\n\n"
        "def build_core():\n"
        "    core = allocate_core()\n"
        "    core.register_link('ParquetEmitter', ParquetEmitter)\n"
        "    core.register_link('SQLiteEmitter', SQLiteEmitter)\n"
        "    return core\n"
    )

    (ws_root / "workspace.yaml").write_text(yaml.dump({
        "name": "emitter-default-test",
        "package_path": "pbg_emitter_default_test",
        "runtime": {"default_emitter": "parquet"},
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


def test_registry_endpoint_marks_parquet_emitter_as_default(
    registry_server_with_parquet_default,
):
    """/api/registry tags ParquetEmitter is_workspace_default=True when
    workspace.yaml sets runtime.default_emitter: parquet."""
    code, body = _get(registry_server_with_parquet_default.url + "/api/registry")
    assert code == 200, body
    assert body.get("default_emitter") == "parquet"

    emitters = [p for p in body.get("processes", []) if p.get("kind") == "emitter"]
    assert emitters, f"expected emitter entries; got {body}"

    by_name = {p["name"]: p for p in emitters}
    assert "ParquetEmitter" in by_name, (
        f"ParquetEmitter not found in registry; entries: {list(by_name)}"
    )
    assert by_name["ParquetEmitter"]["is_workspace_default"] is True, (
        f"expected ParquetEmitter is_workspace_default=True; got {by_name['ParquetEmitter']}"
    )
    # Other emitters must explicitly be False (not missing) so the UI is
    # consistent.
    for name, entry in by_name.items():
        if name == "ParquetEmitter":
            continue
        assert entry.get("is_workspace_default") is False, (
            f"expected {name} is_workspace_default=False; got {entry}"
        )
