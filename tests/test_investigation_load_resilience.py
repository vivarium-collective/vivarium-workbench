"""A failed investigation-detail load must not strand the previous graph.

Two robustness fixes in the frontend:

1. ``DataSource._get`` retries **network-level** fetch rejections (WebKit
   "TypeError: Load failed", a reset connection, a CDN hiccup, a local server
   briefly down mid-restart), not only 429/503 HTTP responses. A single transient
   blip previously surfaced as a hard failure.

2. ``_openInvestigationDetail``'s ``.catch`` clears the DAG (nodes + edges) and
   offers a Retry, instead of only updating the description — so a failed load
   can't leave the *previously-opened* investigation's graph on screen (which
   read as the wrong investigation's graph).

Static assertions over the source (no browser).
"""
from pathlib import Path

STATIC = Path(__file__).parent.parent / "vivarium_workbench" / "static"
DS = (STATIC / "data-source.js").read_text()
WT = (STATIC / "walkthrough.js").read_text()


def test_get_retries_network_level_fetch_rejections():
    # the fetch is wrapped so a rejected promise (not just a non-ok response) retries
    assert "catch (netErr)" in DS
    assert "Network-level failure" in DS
    # it retries (continue) up to the cap, then rethrows
    assert "await _backoff(attempt, 0); continue;" in DS
    assert "throw netErr;" in DS
    # the 429/503 path still retries too (shared backoff helper)
    assert "r.status === 429 || r.status === 503" in DS
    assert "function (attempt, retryAfter)" in DS


def test_failed_investigation_load_clears_the_stale_graph():
    # the catch clears the DAG so the previous investigation's graph can't linger
    assert "investigation-dag-nodes" in WT and "investigation-dag-edges" in WT
    # specifically inside the detail-load catch, and it offers a retry
    catch = WT.split(".catch(function(err) {", 1)
    assert len(catch) == 2, "the _openInvestigationDetail catch block was not found"
    body = catch[1].split("window._openInvestigationDetail", 1)[0]
    assert "investigation-dag-nodes" in body, "catch does not clear the graph nodes"
    assert "investigation-dag-edges" in body, "catch does not clear the graph edges"
    assert "_openInvestigationDetail(name)" in body, "catch does not wire a Retry"
    assert "Failed to load this investigation" in body
