"""Regression: the investigation ↓ figures button must appear in read-only snapshots.

The button is injected async into #ws-actions-figures after fetching the
investigation summaries and checking n_figures > 0. In a static bundle the baked
file is /api/investigation-summaries.json, so the fetch MUST go through
`window.DataSource.loadIsetList()`, which maps to that `.json` file in snapshot
mode.

A prior fix wrapped the fetch in `_api()`, on the assumption that `_api()` adapts
the URL for snapshots. It does not: `_api()` → `DataSource.apiUrl()` only PREFIXES
the base path — it never appends `.json`. So `_api('/api/investigation-summaries')`
resolves to `<base>/api/investigation-summaries` (no extension), which 404s in a
published bundle, the `.then` never runs, and the ↓ figures button stays greyed
even though figures.zip is baked. Only `DataSource.loadIsetList()` (or a raw fetch
of the literal `.json`) works offline.
"""
from pathlib import Path

import vivarium_workbench


def _js() -> str:
    return (Path(vivarium_workbench.__file__).parent / "static" / "walkthrough.js").read_text(
        encoding="utf-8"
    )


def test_investigation_actions_summaries_fetch_uses_snapshot_loader():
    """The ↓ figures button gates on n_figures from the summaries; that load must
    go through DataSource.loadIsetList() so it resolves the baked .json offline."""
    js = _js()
    # The button block lives just after the #ws-actions-figures span.
    anchor = js.index("ws-actions-figures")
    block = js[anchor:anchor + 1500]
    assert "DataSource.loadIsetList()" in block, (
        "the ↓ figures button must load summaries via DataSource.loadIsetList() "
        "(snapshot-aware), not a base-path-only _api() fetch that 404s offline"
    )


def test_no_api_wrapped_summaries_fetch_remains():
    """`_api('/api/investigation-summaries')` is the snapshot bug: `_api()` only
    prefixes the base path, so the URL has no `.json` and 404s in a bundle. Summary
    loads must use DataSource.loadIsetList() instead."""
    js = _js()
    assert "_api('/api/investigation-summaries')" not in js, (
        "found _api('/api/investigation-summaries') — this 404s in a snapshot "
        "(no .json). Use window.DataSource.loadIsetList() instead."
    )


def test_no_unguarded_raw_summaries_fetch_remains():
    """A bare fetch('/api/investigation-summaries') is only allowed as the else-branch
    of a `window.DataSource ? DataSource.loadIsetList() : ...` guard (DataSource is
    always present in a published bundle, so that fallback never runs). Any raw fetch
    NOT preceded by such a guard is the snapshot bug."""
    js = _js()
    lines = js.splitlines()
    for idx, line in enumerate(lines):
        if "fetch('/api/investigation-summaries'" in line:
            # a nearby line must show the DataSource loadIsetList fallback guard
            context = "\n".join(lines[max(0, idx - 3):idx + 1])
            assert "DataSource" in context, (
                f"unguarded raw summaries fetch at line {idx + 1}: {line.strip()}"
            )
