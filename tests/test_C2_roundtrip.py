"""C2: Roundtrip test for export_composite_pbg against the fixture workspace."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

FIXTURE_WS = Path(__file__).parent / "_fixtures" / "ws_increase_demo"
# Fully-qualified composite id expected by find_composite_path: <pkg>.composites.<stem>
COMPOSITE_ID = "pbg_ws_increase_demo.composites.increase-demo"


@pytest.fixture(autouse=True)
def _ws_on_path():
    """Make the fixture workspace package importable for the duration of the test."""
    ws = str(FIXTURE_WS)
    inserted = ws not in sys.path
    if inserted:
        sys.path.insert(0, ws)
    yield
    if inserted:
        try:
            sys.path.remove(ws)
        except ValueError:
            pass


def test_export_composite_pbg_creates_file(tmp_path):
    """export_composite_pbg writes a .pbg JSON file."""
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    out = tmp_path / "increase-demo.pbg"
    result = export_composite_pbg(FIXTURE_WS, COMPOSITE_ID, out)
    assert result == out
    assert out.is_file()


def test_exported_json_has_state_and_schema(tmp_path):
    """The exported .pbg must have top-level 'state' and 'schema' keys."""
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    out = tmp_path / "increase-demo.pbg"
    export_composite_pbg(FIXTURE_WS, COMPOSITE_ID, out)
    doc = json.loads(out.read_text())
    assert "state" in doc
    assert "schema" in doc


def test_all_local_addresses_are_full_path(tmp_path):
    """Every local: address in the exported document must be in local:!module.qualname form."""
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    out = tmp_path / "increase-demo.pbg"
    export_composite_pbg(FIXTURE_WS, COMPOSITE_ID, out)
    doc = json.loads(out.read_text())

    short_addresses = _collect_short_local_addresses(doc)
    assert short_addresses == [], (
        f"Found non-full-path local: addresses: {short_addresses}"
    )


def test_addresses_use_full_module_path(tmp_path):
    """Exported addresses should contain the workspace module path."""
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    out = tmp_path / "increase-demo.pbg"
    export_composite_pbg(FIXTURE_WS, COMPOSITE_ID, out)
    doc = json.loads(out.read_text())

    all_addresses = _collect_all_addresses(doc)
    # At least some addresses should reference pbg_ws_increase_demo package
    full_path_addrs = [a for a in all_addresses if a.startswith("local:!")]
    assert len(full_path_addrs) > 0, "Expected at least one full-path address"
    # Verify the workspace processes are properly encoded
    ws_addrs = [a for a in full_path_addrs if "pbg_ws_increase_demo" in a]
    assert len(ws_addrs) > 0, (
        f"Expected addresses containing 'pbg_ws_increase_demo', got: {full_path_addrs}"
    )


def test_exported_document_is_valid_json(tmp_path):
    """The exported file must be valid, parseable JSON."""
    from vivarium_workbench.lib.pbg_export import export_composite_pbg

    out = tmp_path / "exported.pbg"
    export_composite_pbg(FIXTURE_WS, COMPOSITE_ID, out)
    # Should not raise
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_short_local_addresses(node: object) -> list[str]:
    """Collect any local:<Name> (non-full-path) addresses in the document tree."""
    found: list[str] = []
    _scan(node, found)
    return found


def _scan(node: object, found: list[str]) -> None:
    if not isinstance(node, dict):
        return
    if "address" in node:
        addr = node["address"]
        if isinstance(addr, str) and addr.startswith("local:") and not addr.startswith("local:!"):
            found.append(addr)
    for v in node.values():
        if isinstance(v, dict):
            _scan(v, found)
        elif isinstance(v, list):
            for item in v:
                _scan(item, found)


def _collect_all_addresses(node: object) -> list[str]:
    """Collect all addresses (any protocol) in the document tree."""
    found: list[str] = []
    _scan_all(node, found)
    return found


def _scan_all(node: object, found: list[str]) -> None:
    if not isinstance(node, dict):
        return
    if "address" in node and isinstance(node["address"], str):
        found.append(node["address"])
    for v in node.values():
        if isinstance(v, dict):
            _scan_all(v, found)
        elif isinstance(v, list):
            for item in v:
                _scan_all(item, found)
