"""SMS terms in the browser code may only shrink (docs/backend-adapters.md §2.5, step P3 F0).

The UI of a workbench without the SMS plugin must not speak SMS. Step P3 replaces the SMS nouns in ``static/*.js`` and
``templates/*`` with neutral text (Jim, 2026-11-20: wording B) and gates SMS-only controls by declared feature; this
ratchet stops new ones arriving meanwhile. Each (file, term) count may only go down (``<=``), so a PR that removes
terms need not edit this file; a file not listed here may have none. Step P3 Z turns it into a ban.

Counted case-insensitively, comments included. Exempt: ``sms-launch`` inside ``sms-launch.js`` itself (the quarantined
SMS launch UI, wb#1659) and the ``<script src=".../sms-launch.js">`` tag that loads it.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "vivarium_workbench"

TERMS = ("sms-api", "GovCloud", "SMS_API_BASE", "smsvpctest", "sms-launch")

#: The counts on the base of step P3 F0 (2026-11-20). Lower a number when you remove terms (optional).
BASELINE: dict[str, dict[str, int]] = {
    "static/branch-source.js": {"sms-api": 15, "SMS_API_BASE": 2},
    "static/configure-run.js": {"sms-api": 1, "sms-launch": 1},
    "static/data-source.js": {"sms-api": 1, "GovCloud": 1},
    "static/progress-track.js": {"sms-api": 1},
    "static/session.js": {"sms-api": 1},
    "static/sms-launch.js": {"sms-api": 1},
    "static/walkthrough.js": {"sms-api": 2, "GovCloud": 2, "sms-launch": 1},
    "static/workspace-picker.js": {"sms-api": 4},
    "templates/index.html.j2": {"sms-api": 2, "GovCloud": 1},
}

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


def test_sms_terms_in_browser_code_only_shrink():
    grown = {}
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        allowed = BASELINE.get(rel, {})
        for term, n in counts(path).items():
            if n > allowed.get(term, 0):
                grown[f"{rel}: {term}"] = f"{n} > {allowed.get(term, 0)}"
    assert not grown, (
        "SMS terms grew in browser code (use neutral words such as 'remote backend', or gate SMS-only UI by a "
        f"declared feature; docs/backend-adapters.md §2.5): {grown}")


def test_the_new_features_module_is_neutral():
    assert counts(ROOT / "static" / "backend-features.js") == {}


def test_the_exemptions_are_narrow(tmp_path):
    page = tmp_path / "page.html"
    page.write_text('<script src="/sms-launch.js"></script>\n<!-- sms-launch --> GovCloud', encoding="utf-8")
    assert counts(page) == {"sms-launch": 1, "GovCloud": 1}
    own = tmp_path / "sms-launch.js"
    own.write_text("// sms-launch, via sms-api", encoding="utf-8")
    assert counts(own) == {"sms-api": 1}
