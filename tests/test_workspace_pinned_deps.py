"""Unit tests for remote_run.workspace_pinned_deps (§3.12 version-pinning)."""
from vivarium_workbench.lib.remote_run import workspace_pinned_deps


def test_absent_lockfile_returns_empty(tmp_path):
    assert workspace_pinned_deps(tmp_path) == []


def test_git_and_pypi_pins(tmp_path):
    (tmp_path / "uv.lock").write_text(
        'version = 1\n'
        '[[package]]\n'
        'name = "process-bigraph"\n'
        'version = "1.5.0"\n'
        'source = { git = "https://github.com/vivarium-collective/process-bigraph.git?branch=main#abc123" }\n'
        '[[package]]\n'
        'name = "bigraph-schema"\n'
        'version = "0.15.0"\n'
        '[[package]]\n'
        'name = "some-other-pkg"\n'
        'version = "9.9.9"\n'
    )
    deps = workspace_pinned_deps(tmp_path)
    assert "process-bigraph @ git+https://github.com/vivarium-collective/process-bigraph.git@abc123" in deps
    assert "bigraph-schema==0.15.0" in deps
    assert not any("some-other-pkg" in d for d in deps)


def test_malformed_lockfile_returns_empty(tmp_path):
    (tmp_path / "uv.lock").write_text("this is not valid toml {{{")
    assert workspace_pinned_deps(tmp_path) == []
