"""Aliased /api/study-* handlers accept study/investigation/name body keys."""
from vivarium_dashboard.server import _study_name_from_body


def test_accepts_name():
    assert _study_name_from_body({"name": "a"}) == "a"


def test_accepts_study():
    assert _study_name_from_body({"study": "b"}) == "b"


def test_accepts_investigation():
    assert _study_name_from_body({"investigation": "c"}) == "c"


def test_strips_whitespace():
    assert _study_name_from_body({"study": "  d  "}) == "d"


def test_empty_when_none_present():
    assert _study_name_from_body({}) == ""
