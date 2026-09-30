"""lib/yaml_io: libyaml loader parity + the per-request parse memo."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib import yaml_io

REPO = Path(__file__).resolve().parents[1]


def _tracked_yaml_files() -> list[Path]:
    """Every YAML file tracked in the repo (the fixture study/investigation/
    workspace files plus templates); falls back to a glob outside a git checkout."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.yaml", "*.yml"], cwd=REPO,
            capture_output=True, text=True, check=True).stdout.split()
        files = [REPO / f for f in out]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in REPO.rglob("*.y*ml") if ".venv" not in p.parts]
    return [f for f in files if f.is_file()]


# Constructs where a scanner/constructor difference would show: anchors +
# merge keys, YAML 1.1 bools/octals/sexagesimals, timestamps, special floats,
# block scalars, flow collections, unicode, explicit tags, nulls.
_TRICKY = """
base: &b {seed: 1, dt: 0.5, on: yes, off: No, tilde: ~}
derived:
  <<: *b
  seed: 2
octal: 0o17
legacy_octal: 017
sexagesimal: 1:30
hex: 0x1F
floats: [.inf, -.Inf, 1e3, 6.02e+23, 0.1]
when: 2026-09-29
stamp: 2026-09-29T12:34:56.5Z
folded: >
  one
  two
literal: |
  keep
    indent
quoted: "caf\\u00e9 \\t tab"
plain_unicode: élan — ok
explicit: !!str 123
empty_map: {}
empty_list: []
nested: [{a: [1, {b: null}]}, 'single ''quoted''']
"""


def _norm(obj: object) -> str:
    # repr, so NaN compares equal to NaN and types (int vs str) are distinguished.
    return repr(obj)


@pytest.mark.skipif(not getattr(yaml, "__with_libyaml__", False),
                    reason="PyYAML built without libyaml")
def test_c_and_pure_safe_loaders_agree_on_repo_yaml():
    files = _tracked_yaml_files()
    assert files, "expected tracked YAML fixtures"
    for f in files:
        text = f.read_text(encoding="utf-8")
        try:
            pure = _norm(yaml.load(text, Loader=yaml.SafeLoader))
        except yaml.YAMLError as e:
            pure = f"ERR {type(e).__name__}"
        try:
            fast = _norm(yaml.load(text, Loader=yaml.CSafeLoader))
        except yaml.YAMLError as e:
            fast = f"ERR {type(e).__name__}"
        assert pure == fast, f
    assert _norm(yaml.load(_TRICKY, Loader=yaml.SafeLoader)) == \
        _norm(yaml.load(_TRICKY, Loader=yaml.CSafeLoader))


def test_safe_loader_is_libyaml_when_available():
    expected = yaml.CSafeLoader if getattr(yaml, "__with_libyaml__", False) else yaml.SafeLoader
    assert yaml_io.SafeLoader is expected


def test_loader_stays_safe():
    with pytest.raises(yaml.YAMLError):
        yaml_io.load_yaml_text("!!python/object/apply:os.system ['true']")


def test_load_yaml_matches_safe_load(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text(_TRICKY, encoding="utf-8")
    assert _norm(yaml_io.load_yaml(p)) == _norm(yaml.safe_load(_TRICKY))
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    assert yaml_io.load_yaml(empty) is None


def test_errors_propagate_and_are_not_memoized(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("a: [unclosed", encoding="utf-8")
    with yaml_io.parse_scope():
        with pytest.raises(yaml.YAMLError):
            yaml_io.load_yaml(p)
        p.write_text("a: [1]\n", encoding="utf-8")
        assert yaml_io.load_yaml(p) == {"a": [1]}
    with pytest.raises(OSError):
        yaml_io.load_yaml(tmp_path / "missing.yaml")


def test_no_memo_outside_a_scope(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("a: 1\n", encoding="utf-8")
    before = yaml_io._parse_count
    yaml_io.load_yaml(p)
    yaml_io.load_yaml(p)
    assert yaml_io._parse_count - before == 2
    assert not yaml_io.in_parse_scope()


def test_scope_parses_once_and_is_reentrant(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("a: 1\n", encoding="utf-8")
    before = yaml_io._parse_count
    with yaml_io.parse_scope():
        first = yaml_io.load_yaml(p)
        with yaml_io.parse_scope():  # nested: shares the outer memo
            assert yaml_io.load_yaml(p) is first
        assert yaml_io.load_yaml(str(p)) is first  # str / Path keys agree
        assert yaml_io.in_parse_scope()
    assert yaml_io._parse_count - before == 1
    assert not yaml_io.in_parse_scope()
    # The memo died with the scope.
    with yaml_io.parse_scope():
        yaml_io.load_yaml(p)
    assert yaml_io._parse_count - before == 2


def test_scope_sees_a_file_rewritten_mid_request(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("a: 1\n", encoding="utf-8")
    with yaml_io.parse_scope():
        assert yaml_io.load_yaml(p) == {"a": 1}
        p.write_text("a: 22\n", encoding="utf-8")  # size changes
        assert yaml_io.load_yaml(p) == {"a": 22}
        p.write_text("a: 33\n", encoding="utf-8")  # same size: bump mtime explicitly
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        assert yaml_io.load_yaml(p) == {"a": 33}
