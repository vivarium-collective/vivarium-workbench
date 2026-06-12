"""Tests for the single-source atomic text write (lib/atomic_io.py)."""
from __future__ import annotations

import pytest

from vivarium_dashboard.lib.atomic_io import atomic_write_text


def test_writes_new_file(tmp_path):
    p = tmp_path / "study.yaml"
    atomic_write_text(p, "a: 1\n")
    assert p.read_text() == "a: 1\n"


def test_overwrites_existing_file(tmp_path):
    p = tmp_path / "study.yaml"
    p.write_text("old\n")
    atomic_write_text(p, "new\n")
    assert p.read_text() == "new\n"


def test_no_tmp_left_behind_on_success(tmp_path):
    p = tmp_path / "study.yaml"
    atomic_write_text(p, "x\n")
    assert list(tmp_path.iterdir()) == [p]  # the .tmp sibling was replaced away


def test_failure_cleans_up_tmp_and_preserves_original(tmp_path, monkeypatch):
    p = tmp_path / "study.yaml"
    p.write_text("original\n")

    import os as _os
    def boom(src, dst):
        raise OSError("replace failed")
    monkeypatch.setattr(_os, "replace", boom)

    with pytest.raises(OSError):
        atomic_write_text(p, "doomed\n")

    # the original is intact and no .tmp is left behind
    assert p.read_text() == "original\n"
    assert list(tmp_path.iterdir()) == [p]
