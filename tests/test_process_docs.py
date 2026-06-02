"""attach_process_docs: resolve per-process docstrings for the inspector."""
from vivarium_dashboard.lib.process_docs import attach_process_docs, _doc_for_address


def test_doc_for_address_resolves_full_dotted_path():
    # json.JSONDecoder has a docstring; a full local:<module>.<Class> resolves.
    assert _doc_for_address("local:json.JSONDecoder")


def test_doc_for_address_skips_bare_and_bad():
    assert _doc_for_address("local:SQLiteEmitter") == ""   # bare registry name
    assert _doc_for_address("") == ""
    assert _doc_for_address("not-an-address") == ""
    assert _doc_for_address("local:nonexistent.module.Cls") == ""


def test_attach_only_processes_get_doc():
    doc = {"state": {
        "p": {"_type": "process", "address": "local:json.JSONDecoder"},
        "s": {"_type": "store", "x": 1},
        "lst": [1, {"_type": "step", "address": "local:json.JSONEncoder"}],
    }}
    attach_process_docs(doc)
    assert doc["state"]["p"].get("doc")          # process gets a doc
    assert "doc" not in doc["state"]["s"]        # store does not
    assert doc["state"]["lst"][1].get("doc")     # step nested in a list does


def test_attach_is_safe_on_arbitrary_json():
    # must never raise, even on weird shapes
    attach_process_docs({"a": [1, 2, {"b": None}], "c": "x"})
    attach_process_docs([])
    attach_process_docs(None)


def test_existing_doc_not_overwritten():
    doc = {"_type": "process", "address": "local:json.JSONDecoder", "doc": "custom"}
    attach_process_docs(doc)
    assert doc["doc"] == "custom"
