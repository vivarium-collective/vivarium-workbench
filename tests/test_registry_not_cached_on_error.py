"""A registry build that fails must not be remembered for the cache TTL.

`build_registry` turns any exception into `{"error": ..., "processes": [], "types": []}` and stored it in the
in-memory cache unconditionally, whose TTL is an hour: after one cold env-worker failure the Registry tab stayed
empty for an hour (until a workspace switch or an explicit clear). Same family as the composites listing.

Real build over a real fixture workspace; the broken one has an unparseable `workspace.yaml`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from vivarium_workbench.lib import registry as reg

FIXTURE_WS = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


@pytest.fixture()
def ws(tmp_path):
    dest = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, dest)
    reg.clear_cache()
    yield dest
    reg.clear_cache()


def test_a_healthy_registry_is_remembered(ws):
    """Control: caching works for a good build, so the next test can fail."""
    data = reg.build_registry(ws)
    assert data.get("processes") and not data.get("error"), data.get("error")
    assert reg._REGISTRY_CACHE


def test_a_registry_build_that_fails_is_not_remembered(ws):
    (ws / "workspace.yaml").write_text(": : this is not yaml : [\n", encoding="utf-8")
    data = reg.build_registry(ws)
    assert data.get("error"), f"expected the build to report the failure: {list(data)}"
    assert not reg._REGISTRY_CACHE
