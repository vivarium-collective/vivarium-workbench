"""Tests for the canonical workspace-layout resolver."""
import os
import subprocess
import sys
from pathlib import Path

from vivarium_dashboard.lib.workspace_paths import (
    WorkspacePaths, LAYOUT_DEFAULTS, package_slug,
)


def test_load_handles_non_ascii_yaml_under_ascii_locale(tmp_path):
    """workspace.yaml is UTF-8 (em dashes etc. are common in titles); loading
    it must not depend on the process locale.

    Regression for the Simulations DB crash when the dashboard server ran under
    a US-ASCII locale: ``'ascii' codec can't decode byte 0xe2 ...`` raised from
    ``WorkspacePaths.load`` because ``read_text()`` used the locale default
    instead of UTF-8.
    """
    # An em dash -> bytes e2 80 94, undecodable as ascii.
    (tmp_path / "workspace.yaml").write_text(
        'name: demo\ntitle: "Colony — HPC readiness"\n', encoding="utf-8"
    )
    # Run the loader in a child forced into a non-UTF-8 locale, reproducing the
    # server's environment. Pre-fix this raises UnicodeDecodeError; the explicit
    # encoding="utf-8" makes it locale-independent.
    env = {
        **os.environ,
        "LC_ALL": "C", "LANG": "C", "LC_CTYPE": "C",
        "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
    }
    code = (
        "from pathlib import Path;"
        "from vivarium_dashboard.lib.workspace_paths import WorkspacePaths;"
        f"print(WorkspacePaths.load(Path(r'{tmp_path}')).studies)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, (
        f"WorkspacePaths.load crashed under ascii locale:\n{proc.stderr}"
    )
    assert "studies" in proc.stdout


def test_flat_defaults_when_no_layout(tmp_path):
    """No `layout:` block -> classic top-level dirs (existing-workspace behavior)."""
    wp = WorkspacePaths.from_config(tmp_path, {"name": "my-ws"})
    assert wp.studies == tmp_path / "studies"
    assert wp.investigations == tmp_path / "investigations"
    assert wp.pbg == tmp_path / ".pbg"
    assert wp.reports == tmp_path / "reports"
    # subpaths compose off the resolved dir
    assert wp.pbg / "schemas" == tmp_path / ".pbg" / "schemas"
    assert wp.reports / "figures" / "s1" == tmp_path / "reports" / "figures" / "s1"


def test_package_derives_from_name_or_package_path(tmp_path):
    assert WorkspacePaths.from_config(tmp_path, {"name": "v2-ecoli"}).package \
        == tmp_path / "pbg_v2_ecoli"
    assert package_slug("a-b-c") == "pbg_a_b_c"
    # explicit package_path overrides derivation
    assert WorkspacePaths.from_config(tmp_path, {"name": "x", "package_path": "src/pkg"}).package \
        == tmp_path / "src" / "pkg"


def test_layout_overrides_relocate_dirs(tmp_path):
    """A `layout:` map nests dirs; unspecified keys stay flat."""
    cfg = {
        "name": "ws",
        "layout": {
            "studies": "workspace/studies",
            "investigations": "workspace/investigations",
            "pbg": "workspace/.pbg",
        },
    }
    wp = WorkspacePaths.from_config(tmp_path, cfg)
    assert wp.studies == tmp_path / "workspace" / "studies"
    assert wp.investigations == tmp_path / "workspace" / "investigations"
    assert wp.pbg == tmp_path / "workspace" / ".pbg"
    # not overridden -> still flat
    assert wp.references == tmp_path / "references"
    assert wp.scripts == tmp_path / "scripts"


def test_load_reads_workspace_yaml(tmp_path):
    (tmp_path / "workspace.yaml").write_text(
        "name: demo\nlayout:\n  studies: research/studies\n"
    )
    wp = WorkspacePaths.load(tmp_path)
    assert wp.studies == tmp_path / "research" / "studies"
    assert wp.reports == tmp_path / "reports"   # default
    assert wp.package == tmp_path / "pbg_demo"


def test_unknown_and_invalid_overrides_ignored(tmp_path):
    cfg = {"name": "ws", "layout": {"bogus": "x", "studies": "", "reports": 5}}
    wp = WorkspacePaths.from_config(tmp_path, cfg)
    assert wp.studies == tmp_path / "studies"     # empty string ignored
    assert wp.reports == tmp_path / "reports"      # non-string ignored
    assert "bogus" not in LAYOUT_DEFAULTS


def test_dir_by_name_and_rel(tmp_path):
    wp = WorkspacePaths.from_config(tmp_path, {"name": "ws"})
    assert wp.dir("studies") == tmp_path / "studies"
    assert wp.rel("pbg") == ".pbg"
