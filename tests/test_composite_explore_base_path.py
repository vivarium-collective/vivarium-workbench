"""Transport regression (§3.3 / §7): composite-explore run/resolve/status calls
must route through the base-path helper (DataSource.apiUrl / _api), NOT bare
root-absolute /api/… strings — otherwise under the co-tenant ALB they misroute to
sms-api → 404 (the "Composites tab → Run → 404" bug).
"""
from __future__ import annotations

import re
from pathlib import Path

import vivarium_workbench

_STATIC = Path(vivarium_workbench.__file__).parent / "static"


def _txt(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def test_data_source_exports_api_url_helper():
    js = _txt("data-source.js")
    assert "apiUrl:" in js and "function apiUrl(" in js


def test_composite_explore_calls_are_base_path_routed():
    # The composite-explore run/resolve/status endpoints, in the files that own
    # that surface. Each must appear ONLY inside an _api(...) / apiUrl(...) wrap —
    # no bare fetch('/api/composite-test-run' …) etc.
    endpoints = [
        "/api/composite-test-run",
        "/api/composite-resolve",
        "/api/composite-runs?",
        "/api/composite-run/",
    ]
    # Only inspect actual call expressions (fetch/_post/_poll), not comment prose.
    call_re = re.compile(r"(?:fetch|_post|_poll)\([^\n]*?(/api/composite[-A-Za-z/?]*)")
    for fname in ("walkthrough.js", "configure-run.js"):
        js = _txt(fname)
        for line in js.splitlines():
            m = call_re.search(line)
            if not m:
                continue
            ep = m.group(1)
            if not any(ep.startswith(e.rstrip("?")) for e in endpoints):
                continue  # e.g. /api/composites (list) — covered by the global shim
            assert "_api(" in line or "apiUrl(" in line, (
                f"{fname}: bare (un-base-path'd) call on {ep!r}: {line.strip()[:80]}"
            )
