"""Shared server-side HTML rendering primitives.

Small building blocks the f-string HTML *report builders* had each
reimplemented — HTML escaping, base64 image embedding, the status "pill".
Only the f-string builders (single_study_report, audit_report,
report_card_section, investigation_report, comparative_viz) use these; the
template/Jinja-driven renderers (report.py, study_page.py, report_views.py)
keep their own autoescaping and are intentionally out of scope.
"""
from __future__ import annotations

import html as _html


def esc(value) -> str:
    """HTML-escape ``value`` (attribute-safe), coercing ``None``/non-str to ``""``.

    The single canonical form of the several private ``_h`` / ``_esc`` / ``_e``
    helpers the report builders each defined — all
    ``html.escape(str(x), quote=True)`` with a ``None`` guard.
    """
    return _html.escape("" if value is None else str(value), quote=True)
