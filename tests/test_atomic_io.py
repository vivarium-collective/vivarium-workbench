"""Tests for the single-source atomic text write (lib/atomic_io.py)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from vivarium_dashboard.lib.atomic_io import atomic_write_text


def test_writes_utf8_under_ascii_locale(tmp_path):
    """Dashboard YAML carries non-ASCII (em dashes in titles/scaffolds). The
    write must be UTF-8 regardless of the process locale — under LC_ALL=C the
    locale default is ASCII, and a bare ``write_text`` raised UnicodeEncodeError
    (the order-dependent test_api_app.py failures traced here). Mirrors the
    read-side regression in test_workspace_paths.
    """
    target = tmp_path / "doc.yaml"
    code = (
        "from pathlib import Path;"
        "from vivarium_dashboard.lib.atomic_io import atomic_write_text;"
        f"atomic_write_text(Path(r'{target}'), 'title: Colony — HPC readiness\\n')"
    )
    env = {
        **os.environ,
        "LC_ALL": "C", "LANG": "C", "LC_CTYPE": "C",
        "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
    }
    proc = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"atomic_write_text crashed under ASCII locale:\n{proc.stderr}"
    assert target.read_text(encoding="utf-8") == "title: Colony — HPC readiness\n"


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
