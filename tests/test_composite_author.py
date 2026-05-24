"""Unit tests for ``vivarium_dashboard.lib.composite_author``.

These exercise the pure-function helpers (no HTTP, no subprocess unless
explicitly testing the subprocess path).
"""
from __future__ import annotations
import shutil
from pathlib import Path

import pytest
import yaml

from vivarium_dashboard.lib import composite_author as ca


_FIXTURES = Path(__file__).parent / "_fixtures"
_INCREASE = _FIXTURES / "ws_increase_demo"


# ---------------------------------------------------------------------------
# validate_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "my-composite",
    "abc",
    "a1",
    "snake_case_thing",
    "kebab-case-thing",
    "name_with-mixed",
    "0starts-with-digit",
    "a" * 64,
])
def test_validate_name_accepts_valid(name):
    ca.validate_name(name)


@pytest.mark.parametrize("name", [
    "",
    "-leading-dash",
    "trailing-dash-",
    "_leading-underscore",
    "trailing-underscore_",
    "Has-Capitals",
    "has space",
    "has.dot",
    "a" * 65,
])
def test_validate_name_rejects_invalid(name):
    with pytest.raises(ca.CompositeAuthorError):
        ca.validate_name(name)


# ---------------------------------------------------------------------------
# serialize_composite — round trip against the fixture composite
# ---------------------------------------------------------------------------

def test_serialize_composite_round_trip():
    """Loading the fixture, re-serializing, and reloading yields an
    equivalent dict (ignoring key order)."""
    src = _INCREASE / "pbg_ws_increase_demo" / "composites" / "increase-demo.composite.yaml"
    original = yaml.safe_load(src.read_text())

    rendered = ca.serialize_composite(original)
    reparsed = yaml.safe_load(rendered)

    assert reparsed == original


def test_serialize_orders_keys():
    draft = {
        "state": {"x": None},
        "name": "z",
        "parameters": {},
        "description": "d",
        "requires": {"processes": []},
    }
    out = ca.serialize_composite(draft)
    keys_in_order = [line.split(":", 1)[0] for line in out.splitlines() if line and not line.startswith(" ")]
    # name comes before state.
    assert keys_in_order.index("name") < keys_in_order.index("state")
    assert keys_in_order.index("description") < keys_in_order.index("state")


def test_serialize_rejects_missing_required_keys():
    with pytest.raises(ca.CompositeAuthorError):
        ca.serialize_composite({"name": "x"})
    with pytest.raises(ca.CompositeAuthorError):
        ca.serialize_composite({"state": {}})
    with pytest.raises(ca.CompositeAuthorError):
        ca.serialize_composite("not-a-dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# write_composite / drafts
# ---------------------------------------------------------------------------

def test_write_composite_creates_file_and_refuses_overwrite(tmp_path):
    pkg = "pbg_demo"
    (tmp_path / pkg).mkdir()
    name = "thing"
    text = "name: thing\nstate: {}\n"
    out = ca.write_composite(tmp_path, pkg, name, text)
    assert out == tmp_path / pkg / "composites" / "thing.composite.yaml"
    assert out.read_text() == text

    # Refuses to overwrite by default.
    with pytest.raises(ca.CompositeAuthorError):
        ca.write_composite(tmp_path, pkg, name, "name: thing\nstate: {a: 1}\n")

    # Overwrite=True lets you replace.
    ca.write_composite(tmp_path, pkg, name, "name: thing\nstate: {a: 1}\n", overwrite=True)
    assert "a: 1" in out.read_text()


def test_drafts_roundtrip(tmp_path):
    text = "name: foo\nstate: {}\n"
    draft_id, path = ca.write_draft(tmp_path, text)
    assert path.is_file()
    assert path.parent == tmp_path / ".pbg" / "composite-drafts"

    yaml_text, parsed = ca.read_draft(tmp_path, draft_id)
    assert yaml_text == text
    assert parsed == {"name": "foo", "state": {}}

    rows = ca.list_drafts(tmp_path)
    assert any(r["draft_id"] == draft_id for r in rows)

    assert ca.delete_draft(tmp_path, draft_id) is True
    assert ca.delete_draft(tmp_path, draft_id) is False


def test_draft_id_reuse(tmp_path):
    """Passing draft_id reuses the same slot (autosave behaviour)."""
    id1, p1 = ca.write_draft(tmp_path, "name: a\nstate: {}\n")
    id2, p2 = ca.write_draft(tmp_path, "name: b\nstate: {}\n", draft_id=id1)
    assert id1 == id2
    assert p1 == p2
    assert p2.read_text() == "name: b\nstate: {}\n"


def test_promote_draft(tmp_path):
    pkg = "pbg_demo"
    (tmp_path / pkg).mkdir()
    draft_id, _ = ca.write_draft(tmp_path, "name: thing\nstate: {x: 1}\n")
    target = ca.promote_draft(tmp_path, pkg, draft_id)
    assert target.name == "thing.composite.yaml"
    assert target.read_text() == "name: thing\nstate: {x: 1}\n"
    # Draft was removed after successful promote.
    with pytest.raises(ca.CompositeAuthorError):
        ca.read_draft(tmp_path, draft_id)


def test_promote_draft_explicit_name_override(tmp_path):
    pkg = "pbg_demo"
    (tmp_path / pkg).mkdir()
    draft_id, _ = ca.write_draft(tmp_path, "name: ignored\nstate: {}\n")
    target = ca.promote_draft(tmp_path, pkg, draft_id, name="explicit-name")
    assert target.name == "explicit-name.composite.yaml"


def test_gc_drafts_sweeps_old_files(tmp_path):
    import time as _time
    drafts = tmp_path / ".pbg" / "composite-drafts"
    drafts.mkdir(parents=True)
    old = drafts / "old.composite.yaml"
    old.write_text("name: x\nstate: {}\n")
    # Stamp the file two weeks in the past.
    past = _time.time() - 14 * 86400
    import os as _os
    _os.utime(old, (past, past))
    fresh = drafts / "fresh.composite.yaml"
    fresh.write_text("name: y\nstate: {}\n")

    removed = ca.gc_drafts(tmp_path, max_age_days=7)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_invalid_draft_id_rejected(tmp_path):
    with pytest.raises(ca.CompositeAuthorError):
        ca.write_draft(tmp_path, "x", draft_id="../etc/passwd")
    with pytest.raises(ca.CompositeAuthorError):
        ca.read_draft(tmp_path, "../etc/passwd")
    with pytest.raises(ca.CompositeAuthorError):
        ca.delete_draft(tmp_path, "x/y")


# ---------------------------------------------------------------------------
# soft_check
# ---------------------------------------------------------------------------

def test_soft_check_clean_draft():
    draft = {
        "name": "ok",
        "state": {
            "p": {"_type": "process", "address": "Foo",
                  "inputs": {}, "outputs": {}, "interval": 1.0},
            "s": 1.0,
        },
    }
    assert ca.soft_check(draft) == []


def test_soft_check_flags_missing_address():
    draft = {
        "name": "x",
        "state": {"p": {"_type": "process", "address": "", "inputs": {}, "outputs": {}}},
    }
    issues = ca.soft_check(draft)
    assert any(i["kind"] == "missing" and "address" in i["path"] for i in issues)


def test_soft_check_flags_missing_name():
    issues = ca.soft_check({"name": "", "state": {}})
    assert any(i["path"] == "name" for i in issues)


# ---------------------------------------------------------------------------
# validate_composite — subprocess against the increase-demo fixture
# ---------------------------------------------------------------------------

def _copy_fixture(tmp_path: Path) -> Path:
    dest = tmp_path / "ws"
    shutil.copytree(_INCREASE, dest)
    return dest


def test_validate_composite_passes_on_fixture(tmp_path):
    ws = _copy_fixture(tmp_path)
    target = ws / "pbg_ws_increase_demo" / "composites" / "increase-demo.composite.yaml"
    report = ca.validate_composite(ws, target, timeout_s=60.0)
    # The validation should succeed; if the subprocess can't import
    # process_bigraph or the workspace package, surface stderr in the failure
    # so CI logs explain why.
    assert report.ok, (
        f"validate_composite did not return ok=True on the fixture composite.\n"
        f"errors: {report.errors}\nstderr: {report.stderr}"
    )


def test_validate_composite_flags_unknown_process(tmp_path):
    ws = _copy_fixture(tmp_path)
    bad = ws / "pbg_ws_increase_demo" / "composites" / "bogus.composite.yaml"
    bad.write_text(
        "name: bogus\n"
        "state:\n"
        "  proc:\n"
        "    _type: process\n"
        "    address: local:DefinitelyNotARealProcessClassXYZ\n"
        "    inputs: {}\n"
        "    outputs: {}\n"
        "    interval: 1.0\n"
    )
    report = ca.validate_composite(ws, bad, timeout_s=60.0)
    assert not report.ok
    assert report.errors, "expected at least one error for the unresolved address"
