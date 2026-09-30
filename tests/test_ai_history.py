"""Chat history persistence lives in chat.js (browser). The pure logic (open/merge/delete) is
covered in tests/js/test_chat_core.js; this pins the one thing a unit test of that logic cannot:
the storage constants must exist BEFORE the panel first loads its store, or `loadStore()` throws
(temporal dead zone), its catch-all swallows it, and every load silently starts an empty history
(found by loading two real tabs)."""
from pathlib import Path

CHAT_JS = (Path(__file__).parent.parent / "vivarium_workbench" / "static" / "chat.js").read_text()


def test_storage_constants_are_initialised_before_the_first_loadstore_call():
    first_call = CHAT_JS.index("let store = loadStore();")
    for const in ("const LOCAL =", "const durable =", "const ACTIVE_KEY =", "const readDurable =", "const STORE_KEY ="):
        assert CHAT_JS.index(const) < first_call, f"{const} is used by loadStore() but declared after it runs"


def test_history_is_durable_only_on_loopback_hosts():
    assert "['localhost', '127.0.0.1', '[::1]', '::1'].indexOf(location.hostname)" in CHAT_JS
    assert "LOCAL ? localStorage : sessionStorage" in CHAT_JS
