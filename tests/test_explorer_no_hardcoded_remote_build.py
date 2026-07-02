from pathlib import Path


def test_walkthrough_has_no_hardcoded_remote_build_message():
    """The old fallback included a misleading 'A remote build cannot build
    generator composites (no local ParCa cache).' message.  This test asserts
    it has been replaced with a server-driven notice."""
    js = Path("vivarium_workbench/static/walkthrough.js").read_text(encoding="utf-8")
    # The key phrase that must be gone — appears on its own source line so
    # a plain substring search is reliable.
    assert "build cannot build generator composites" not in js
