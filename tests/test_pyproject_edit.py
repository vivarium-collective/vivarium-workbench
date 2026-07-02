"""Unit tests for vivarium_workbench.lib.pyproject_edit."""
import sys
import textwrap
import tempfile
from pathlib import Path

import pytest

from vivarium_workbench.lib.pyproject_edit import add_dependency, remove_dependency, remove_uv_source


MINIMAL_PYPROJECT = textwrap.dedent("""\
    [project]
    name = "my-workspace"
    version = "0.1.0"
    dependencies = [
        "process-bigraph",
    ]
""")


def _write_pyproject(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_text(content)
    return p


def test_add_new_dependency(tmp_path):
    pf = _write_pyproject(tmp_path, MINIMAL_PYPROJECT)
    changed = add_dependency(pf, "v2ecoli")
    assert changed is True
    text = pf.read_text()
    assert '"v2ecoli"' in text


def test_idempotent_second_call(tmp_path):
    pf = _write_pyproject(tmp_path, MINIMAL_PYPROJECT)
    add_dependency(pf, "v2ecoli")
    changed_again = add_dependency(pf, "v2ecoli")
    assert changed_again is False, "second call should return False (already declared)"
    # Verify no duplicate entry.
    text = pf.read_text()
    assert text.count('"v2ecoli"') == 1


def test_idempotent_existing_dep(tmp_path):
    """Dep already in file → returns False without modification."""
    pf = _write_pyproject(tmp_path, MINIMAL_PYPROJECT)
    changed = add_dependency(pf, "process-bigraph")
    assert changed is False


def test_add_dep_with_version_spec(tmp_path):
    pf = _write_pyproject(tmp_path, MINIMAL_PYPROJECT)
    changed = add_dependency(pf, "pbg-tellurium", version_spec=">=0.1.0")
    assert changed is True
    assert '"pbg-tellurium>=0.1.0"' in pf.read_text()


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        add_dependency(tmp_path / "nonexistent.toml", "some-pkg")


def test_bracket_in_existing_dep_not_confused(tmp_path):
    """jsonschema[format-nongpl]>=4.21 must not confuse the array-boundary scanner."""
    content = textwrap.dedent("""\
        [project]
        name = "my-workspace"
        version = "0.1.0"
        dependencies = [
            "jsonschema[format-nongpl]>=4.21",
            "pyyaml>=6.0",
        ]
    """)
    pf = _write_pyproject(tmp_path, content)
    changed = add_dependency(pf, "v2ecoli")
    assert changed is True
    text = pf.read_text()
    # Must not corrupt the jsonschema line
    assert '"jsonschema[format-nongpl]>=4.21"' in text
    assert '"v2ecoli"' in text
    # Verify it's still valid TOML
    if sys.version_info >= (3, 11):
        import tomllib as _tl
    else:
        import tomli as _tl  # type: ignore[no-redef]
    parsed = _tl.loads(text)
    deps = parsed["project"]["dependencies"]
    assert "v2ecoli" in deps
    assert "jsonschema[format-nongpl]>=4.21" in deps


# ---------------------------------------------------------------------------
# Tests for remove_dependency
# ---------------------------------------------------------------------------

def test_remove_existing_dependency(tmp_path):
    """Removing a dep that exists returns True and the dep is gone."""
    pf = _write_pyproject(tmp_path, MINIMAL_PYPROJECT)
    changed = remove_dependency(pf, "process-bigraph")
    assert changed is True
    text = pf.read_text()
    assert "process-bigraph" not in text


def test_remove_dependency_absent_is_noop(tmp_path):
    """Removing a dep that doesn't exist returns False without modifying the file."""
    pf = _write_pyproject(tmp_path, MINIMAL_PYPROJECT)
    original = pf.read_text()
    changed = remove_dependency(pf, "nonexistent-package")
    assert changed is False
    assert pf.read_text() == original


# ---------------------------------------------------------------------------
# Tests for remove_uv_source
# ---------------------------------------------------------------------------

PYPROJECT_WITH_UV_SOURCES = textwrap.dedent("""\
    [project]
    name = "my-workspace"
    version = "0.1.0"
    dependencies = [
        "process-bigraph",
        "pbg-tellurium",
    ]

    [tool.uv.sources]
    pbg-tellurium = { path = "external/pbg-tellurium", editable = true }
""")


def test_remove_existing_uv_source(tmp_path):
    """Removing a uv source that exists returns True and the uv.sources entry is gone."""
    pf = _write_pyproject(tmp_path, PYPROJECT_WITH_UV_SOURCES)
    changed = remove_uv_source(pf, "pbg-tellurium")
    assert changed is True
    text = pf.read_text()
    # The uv.sources entry must be gone but the dep in [project.dependencies] is untouched.
    assert "pbg-tellurium = {" not in text and "pbg-tellurium = { path" not in text
    assert '"pbg-tellurium"' in text  # dep still present (remove_uv_source only touches sources)


def test_remove_uv_source_absent_is_noop(tmp_path):
    """Removing a uv source that doesn't exist returns False without modifying the file."""
    pf = _write_pyproject(tmp_path, PYPROJECT_WITH_UV_SOURCES)
    original = pf.read_text()
    changed = remove_uv_source(pf, "nonexistent-package")
    assert changed is False
    assert pf.read_text() == original
