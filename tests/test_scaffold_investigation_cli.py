"""Tests for the ``vwb scaffold-investigation`` verb
(``cli.cmd_scaffold_investigation``).

Phase 2.1k step 0: wraps
``discovery_engine.scaffold.scaffold_investigation_from_wrapper`` so the
``/viva-expert`` skill can stop shelling ``python -m discovery_engine.scaffold
investigation-from-wrapper``. Bootstrap (pre-server) op → a ``vwb`` verb.
"""
import json

import pytest

from vivarium_workbench import cli


def _args(**kw):
    ns = type("NS", (), {})()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _mk_ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("schema_version: 2\nname: ws\n")
    return ws


def test_rejects_non_workspace(tmp_path, capsys):
    plain = tmp_path / "plain"
    plain.mkdir()
    rc = cli.cmd_scaffold_investigation(_args(
        workspace=str(plain), name="X", studies="a", investigation_slug=None, force=False))
    assert rc == 2
    assert "not a workspace" in capsys.readouterr().err


def test_requires_studies(tmp_path, capsys):
    ws = _mk_ws(tmp_path)
    rc = cli.cmd_scaffold_investigation(_args(
        workspace=str(ws), name="X", studies="", investigation_slug=None, force=False))
    assert rc == 2
    assert "--studies" in capsys.readouterr().err


def test_scaffolds_investigation_and_studies(tmp_path, capsys):
    scaffold = pytest.importorskip("discovery_engine.scaffold")
    if not hasattr(scaffold, "scaffold_investigation_from_wrapper"):
        pytest.skip("installed discovery_engine.scaffold predates investigation-from-wrapper")
    ws = _mk_ws(tmp_path)
    rc = cli.cmd_scaffold_investigation(_args(
        workspace=str(ws), name="DnaA Replication",
        studies="pkg.composites.a.a_baseline,pkg.composites.b.b_baseline",
        investigation_slug=None, force=False,
    ))
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    # investigation.yaml + one study per generator written under the workspace.
    inv_slug = result.get("investigation") or result.get("slug") or "dnaa-replication"
    inv_yaml = ws / "investigations" / inv_slug / "investigation.yaml"
    assert inv_yaml.is_file(), f"investigation.yaml not written (result={result})"
    assert len(result.get("studies", [])) == 2
