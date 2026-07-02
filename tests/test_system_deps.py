"""Tests for /api/system-deps-check and /api/catalog-install's system-deps gate.

The gate flow:
  1. GET /api/system-deps-check?name=<module> — returns structured info
     about which native deps are satisfied in the workspace venv.
  2. POST /api/catalog-install — refuses with 409 if any deps are unmet,
     and returns the same structured info. Caller passes
     ``skip_system_deps_check=true`` to bypass.

Tests use a synthetic catalog entry with a deliberately-failing
``import_check`` so the asserts are deterministic regardless of what
native libs happen to be present on the host.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixture — spins up the live FastAPI app against a temp workspace whose
# catalog contains one synthetic module with a deliberately-failing
# system-deps check.
# ---------------------------------------------------------------------------

@pytest.fixture
def sys_deps_server(tmp_path, dashboard_client):
    ws_root = tmp_path

    # Minimal workspace.yaml.
    (ws_root / "workspace.yaml").write_text(yaml.dump({
        "name": "testws",
        "package_path": "pbg_testws",
        "imports": {},
    }, sort_keys=False))

    # Catalog with one synthetic entry. The import_check raises
    # ModuleNotFoundError on every conceivable Python install (the name is
    # 64 random-looking characters), so the check will always fail.
    catalog_dir = ws_root / "scripts" / "_catalog"
    catalog_dir.mkdir(parents=True)
    catalog = [{
        "name": "pbg-syntheticfail",
        "description": "Synthetic catalog entry whose import_check always fails.",
        "source": "https://example.invalid/pbg-syntheticfail.git",
        "ref": "main",
        "package": "pbg_syntheticfail",
        "homepage": "https://example.invalid",
        "tags": ["test"],
        "system_dependencies": {
            "checks": [
                {
                    "name": "synthetic-missing-lib",
                    "description": "Always-missing library used by tests.",
                    "import_check": "import __pbg_definitely_not_a_real_module_xyzzy__",
                    "install": {
                        "darwin": {
                            "manager": "brew",
                            "commands": ["brew install pbg-definitely-not-a-real-package"],
                            "notes": "Not a real package; the test never executes this.",
                        },
                        "linux": {
                            "manager": "apt",
                            "commands": ["sudo apt install -y pbg-definitely-not-a-real-package"],
                        },
                    },
                },
            ],
        },
    }]
    # Workspace-local module via the per-workspace overlay (the registry is
    # now canonical pbg-superpowers list + this overlay; the synthetic module
    # isn't in the canonical list, so it must live in the overlay).
    (catalog_dir / "overlay.json").write_text(json.dumps(catalog))

    # Ensure the venv-python path used by the helper resolves to *some*
    # python: point .venv/bin/python3 at the host interpreter so the
    # subprocess call actually runs (and reports ModuleNotFoundError).
    venv_bin = ws_root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python3").symlink_to(Path(sys.executable))

    client = dashboard_client(ws_root)

    class _WS:
        url = client.base_url
        root = ws_root

    yield _WS()


# ---------------------------------------------------------------------------
# Helpers (mirrors test_visualization_endpoints.py)
# ---------------------------------------------------------------------------

def _get(url):
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


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

def test_get_system_deps_check_returns_missing_for_synthetic_entry(sys_deps_server):
    """GET /api/system-deps-check surfaces a failing check with structured info."""
    code, body = _get(
        sys_deps_server.url + "/api/system-deps-check?name=pbg-syntheticfail"
    )
    assert code == 200, body
    assert body["name"] == "pbg-syntheticfail"
    assert body["ok"] is False
    assert isinstance(body["checks"], list) and len(body["checks"]) == 1
    check = body["checks"][0]
    assert check["name"] == "synthetic-missing-lib"
    assert check["ok"] is False
    # The reason should be the dlopen/import error tail line.
    assert check["reason"] is not None
    assert "__pbg_definitely_not_a_real_module_xyzzy__" in check["reason"] or \
        "ModuleNotFoundError" in check["reason"] or \
        "No module" in check["reason"]
    # Platform-keyed install spec should be exposed (darwin or linux based
    # on the host running the tests).
    assert body["platform"] in {"darwin", "linux", "windows"}
    if body["platform"] in {"darwin", "linux"}:
        assert check["install"] is not None
        assert "commands" in check["install"]


def test_get_system_deps_check_unknown_module_returns_404(sys_deps_server):
    code, body = _get(
        sys_deps_server.url + "/api/system-deps-check?name=does-not-exist"
    )
    assert code == 404
    assert "unknown module" in body.get("error", "")


def test_get_system_deps_check_requires_name(sys_deps_server):
    code, body = _get(sys_deps_server.url + "/api/system-deps-check")
    assert code == 400
    assert "name required" in body.get("error", "")


def test_catalog_install_returns_409_when_system_deps_unmet(sys_deps_server):
    """POST /api/catalog-install refuses with 409 when native deps fail.

    Structured payload contains ``missing`` so the UI can render a modal.
    """
    code, body = _post(
        sys_deps_server.url + "/api/catalog-install",
        {"name": "pbg-syntheticfail"},
    )
    assert code == 409, body
    assert body.get("error") == "unmet system dependencies"
    assert body.get("name") == "pbg-syntheticfail"
    assert isinstance(body.get("missing"), list) and len(body["missing"]) == 1
    miss = body["missing"][0]
    assert miss["name"] == "synthetic-missing-lib"
    assert miss["reason"] is not None
    # The hint must mention the bypass flag so the UI / callers know how
    # to proceed despite the failure.
    assert "skip_system_deps_check" in body.get("hint", "")


def test_catalog_install_proceeds_when_skip_system_deps_check_true(
    sys_deps_server, monkeypatch
):
    """POST with skip_system_deps_check=true bypasses the gate.

    We do NOT actually pip-install — _active_branch_action will fail (no
    git workstream) but it does so AFTER the system-deps gate. The point
    is to assert the request gets past the 409 path.
    """
    code, body = _post(
        sys_deps_server.url + "/api/catalog-install",
        {"name": "pbg-syntheticfail", "skip_system_deps_check": True},
    )
    # We are not on a stage/* branch and the source URL is invalid, so
    # the install will fail downstream — but it must NOT be the 409
    # system-deps gate. Acceptable downstream codes:
    #   500 (subprocess / submodule add failure)
    #   409 (no active workstream — message differs from "unmet system dependencies")
    assert code != 409 or body.get("error") != "unmet system dependencies", (
        f"Expected gate bypassed, but got 409 unmet-system-deps: {body}"
    )
