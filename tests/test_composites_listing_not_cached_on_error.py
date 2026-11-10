"""A composites listing that reports an error must not be remembered as the answer.

`composites_via_subprocess` stores a successful discovery in an in-memory cache and a persistent on-disk cache,
and its comment says "failures are never cached so a transient error re-tries on the next request". But only a
`None` result counted as a failure: the fallback subprocess turns an exception into `{"composites": [], "error": ...}`
and THAT was stored. On the hosted test stack a cold env worker made the first Composites call time out, the empty
error-bearing payload was cached (memory and disk), and the tab stayed empty after the worker was up; selecting the
build again did not clear the disk copy.

Real discovery over a real fixture workspace: the broken one has an unparseable `workspace.yaml`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from vivarium_workbench.lib import catalog_disk_cache as cdc
from vivarium_workbench.lib import composites_query as q

FIXTURE_WS = Path(__file__).parent / "_fixtures" / "ws_increase_demo"


@pytest.fixture()
def ws(tmp_path):
    dest = tmp_path / "ws"
    shutil.copytree(FIXTURE_WS, dest)
    q._COMPOSITES_CACHE.clear()
    yield dest
    q._COMPOSITES_CACHE.clear()


def _remembered(ws: Path) -> tuple[bool, bool]:
    """(in the in-memory cache, in the persistent on-disk cache) for this workspace's composites listing."""
    sig = cdc.catalog_signature(ws)
    assert sig != cdc.DISABLED, "the disk cache is disabled for this workspace: the test could not see it"
    return str(ws) in q._COMPOSITES_CACHE, cdc.load(ws, "composites", sig) is not None


def test_a_healthy_listing_is_remembered_in_both_caches(ws):
    """Control: caching works for a good answer, so the next test can fail."""
    data = q.composites_via_subprocess(ws)
    assert data and data.get("composites") and not data.get("error"), data
    assert _remembered(ws) == (True, True)


def test_a_listing_that_reports_an_error_is_not_remembered(ws):
    (ws / "workspace.yaml").write_text(": : this is not yaml : [\n", encoding="utf-8")
    data = q.composites_via_subprocess(ws)
    assert data is not None and data.get("error"), f"expected the listing to report the failure: {data}"
    assert _remembered(ws) == (False, False)
