"""The investigation chrome header (#ws-title) shows the display TITLE, not the slug.

Pins the fix for a deep-link / auto-open race: `_showInvestigationWorkspace` set
`#ws-title` from `(_iset && _iset.title) || name`, reading `window._isetIndex`. On a
`?investigation=<slug>` auto-open the index may not have loaded yet, so `_iset` is
undefined and the header falls back to the slug (e.g. "eukaryogenesis" instead of
"The Endosymbiotic Transition"). `_openInvestigationDetail` always fetches the detail
payload, which carries the authoritative `title`, so it must refresh `#ws-title` from
`d.title || d.name` once that resolves.
"""
import re
from pathlib import Path

WALKTHROUGH = Path(__file__).parent.parent / "vivarium_workbench" / "static" / "walkthrough.js"


def _open_detail_body(js: str) -> str:
    # the .then(function(d){ ... }) success handler inside _openInvestigationDetail
    start = js.index("function _openInvestigationDetail(name)")
    return js[start : start + 6000]


def test_chrome_header_refreshed_from_detail_payload():
    js = WALKTHROUGH.read_text()
    body = _open_detail_body(js)
    # it reads the visible chrome header element
    assert "getElementById('ws-title')" in body, (
        "_openInvestigationDetail must refresh the chrome header #ws-title from the "
        "authoritative detail payload (it was only set from the timing-fragile _isetIndex)"
    )
    # and sets its text from the detail payload's title (falling back to name)
    assert re.search(r"d\.title\s*\|\|\s*d\.name", body), (
        "#ws-title must be set from d.title || d.name so a deep-link/auto-open never "
        "shows the slug once the detail resolves"
    )
