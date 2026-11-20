"""No SMS terms in the browser code outside the quarantined SMS launch UI (docs/backend-adapters.md §2.5, step P3 Z).

The UI of a workbench without the SMS plugin must not speak SMS. Step P3 replaced the SMS nouns in ``static/*.js`` and
``templates/*`` with neutral text (Jim, 2026-11-20: wording B) and gates SMS-only controls by declared feature. This
began as a ``<=`` ratchet (F0) and is a ban since Z: no file may hold any of the terms, except ``static/sms-launch.js``
(the quarantined SMS launch UI, wb#1659), which only the SMS plugin's ``sms-launch`` feature mounts.

Counted case-insensitively, comments included. Exempt: ``static/sms-launch.js`` and the
``<script src=".../sms-launch.js">`` tag that loads it.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "vivarium_workbench"

TERMS = ("sms-api", "GovCloud", "SMS_API_BASE", "smsvpctest", "sms-launch")

#: The only file allowed to name SMS terms: the quarantined SMS launch UI.
EXEMPT_FILES = frozenset({"static/sms-launch.js"})

_SMS_LAUNCH_TAG = re.compile(r'<script[^>]*\bsrc="[^"]*sms-launch\.js[^"]*"[^>]*>\s*</script>')


def _files() -> list[Path]:
    return sorted(ROOT.glob("static/*.js")) + sorted(p for p in ROOT.glob("templates/*") if p.is_file())


def counts(path: Path) -> dict[str, int]:
    text = _SMS_LAUNCH_TAG.sub("", path.read_text(encoding="utf-8"))
    found = {}
    for term in TERMS:
        if term == "sms-launch" and path.name == "sms-launch.js":
            continue
        n = len(re.findall(re.escape(term), text, re.IGNORECASE))
        if n:
            found[term] = n
    return found


def test_no_sms_terms_in_browser_code_outside_sms_launch():
    found = {}
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in EXEMPT_FILES:
            continue
        for term, n in counts(path).items():
            found[f"{rel}: {term}"] = n
    assert not found, (
        "SMS terms in browser code (use neutral words such as 'remote backend', or move SMS-only UI into "
        f"static/sms-launch.js behind the plugin's declared feature; docs/backend-adapters.md §2.5): {found}")


def test_the_exempt_file_exists():
    for rel in EXEMPT_FILES:
        assert (ROOT / rel).is_file(), rel


def test_the_new_features_module_is_neutral():
    assert counts(ROOT / "static" / "backend-features.js") == {}


def test_the_exemptions_are_narrow(tmp_path):
    page = tmp_path / "page.html"
    page.write_text('<script src="/sms-launch.js"></script>\n<!-- sms-launch --> GovCloud', encoding="utf-8")
    assert counts(page) == {"sms-launch": 1, "GovCloud": 1}
    own = tmp_path / "sms-launch.js"
    own.write_text("// sms-launch, via sms-api", encoding="utf-8")
    assert counts(own) == {"sms-api": 1}
