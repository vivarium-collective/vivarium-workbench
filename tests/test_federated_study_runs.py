"""Tests for making a read-only FEDERATED study runnable — the run-execution
half of federation (#1177/#1189 covered browse/detail/report/download).

`study_runs._materialize_federated_study` copies a federated study's `study.yaml`
into the host `studies/<name>/` so the run reads the spec and writes its outputs
into the writable host workspace, leaving the federated source untouched.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib import study_runs


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False) if isinstance(data, dict) else data,
                 encoding="utf-8")


def _host(tmp_path: Path) -> Path:
    ws = tmp_path / "host"
    _write(ws / "workspace.yaml", {"name": "host"})
    return ws


def _link_federated_study(ws: Path, repo: str, slug: str, spec: dict) -> None:
    """Land a read-only federated study under external/<repo>/studies/<slug>/."""
    ext = ws / "external" / repo
    _write(ext / "workspace.yaml", {"name": repo})
    _write(ext / "studies" / slug / "study.yaml", spec)


def test_materializes_federated_spec_into_host(tmp_path):
    ws = _host(tmp_path)
    _link_federated_study(ws, "mymod", "killing-assay",
                          {"name": "killing-assay", "schema_version": 4, "title": "fed"})
    # No host study before.
    assert not (ws / "studies" / "killing-assay" / "study.yaml").exists()

    study_runs._materialize_federated_study(ws, "killing-assay")

    host_spec = ws / "studies" / "killing-assay" / "study.yaml"
    assert host_spec.is_file()
    assert yaml.safe_load(host_spec.read_text())["title"] == "fed"


def test_noop_for_native_study(tmp_path):
    ws = _host(tmp_path)
    native = ws / "studies" / "mine" / "study.yaml"
    _write(native, {"name": "mine", "schema_version": 4, "title": "native"})
    _link_federated_study(ws, "mymod", "mine",
                          {"name": "mine", "title": "SHOULD NOT OVERWRITE"})

    study_runs._materialize_federated_study(ws, "mine")

    # The existing host spec is left untouched (never overwritten by federation).
    assert yaml.safe_load(native.read_text())["title"] == "native"


def test_noop_when_no_federated_match(tmp_path):
    ws = _host(tmp_path)
    study_runs._materialize_federated_study(ws, "ghost")
    assert not (ws / "studies" / "ghost").exists()


def test_run_baseline_404s_without_a_federated_source(tmp_path):
    # No native and no federated study -> the run entry still 404s as before.
    ws = _host(tmp_path)
    resp, code = study_runs.run_study_baseline(ws, {"study": "nope"})
    assert code == 404
