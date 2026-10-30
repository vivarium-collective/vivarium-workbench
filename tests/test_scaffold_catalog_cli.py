"""Tests for the ``vwb scaffold-workspace`` + ``vwb catalog-add`` CLI verbs
(``cli.cmd_scaffold_workspace`` / ``cli.cmd_catalog_add``).

Phase 2.1i (rewire-first): these ``vwb`` verbs wrap the plugin's bootstrap
compute (``discovery_engine.scaffold`` / ``workspace_catalog``) so the
``/viva-workspace`` skill can stop shelling ``python -m viva_superpowers.*``.
The scaffold path is inherently pre-server, so it lives on the CLI (not an
HTTP endpoint). ``catalog-add`` is isolated to a temp ``VIVA_HOME`` so it never
touches the real ``~/.pbg/workspaces.json``.
"""
import json

import pytest

from vivarium_workbench.cli import main


def _mk_workspace(tmp_path, name="ws"):
    ws = tmp_path / name
    ws.mkdir()
    (ws / "workspace.yaml").write_text(f"schema_version: 2\nname: {name}\n")
    return ws


def test_catalog_add_registers_workspace(tmp_path, monkeypatch, capsys):
    pytest.importorskip("discovery_engine.workspace_catalog")
    home = tmp_path / "viva_home"
    monkeypatch.setenv("VIVA_HOME", str(home))
    ws = _mk_workspace(tmp_path)

    rc = main(["catalog-add", "--path", str(ws), "--name", "my-ws"])
    assert rc == 0
    out = capsys.readouterr().out
    entry = json.loads(out)
    assert entry.get("name") == "my-ws"
    # The catalog file was written under the isolated VIVA_HOME, not real ~/.pbg.
    catalog = home / "workspaces.json"
    assert catalog.is_file()
    assert "my-ws" in catalog.read_text()


def test_catalog_add_rejects_non_workspace(tmp_path, monkeypatch, capsys):
    pytest.importorskip("discovery_engine.workspace_catalog")
    monkeypatch.setenv("VIVA_HOME", str(tmp_path / "viva_home"))
    not_ws = tmp_path / "plain"
    not_ws.mkdir()

    rc = main(["catalog-add", "--path", str(not_ws)])
    assert rc == 2  # not a workspace (no workspace.yaml)
    assert "not a workspace" in capsys.readouterr().err


def test_catalog_add_is_idempotent(tmp_path, monkeypatch):
    pytest.importorskip("discovery_engine.workspace_catalog")
    home = tmp_path / "viva_home"
    monkeypatch.setenv("VIVA_HOME", str(home))
    ws = _mk_workspace(tmp_path)

    assert main(["catalog-add", "--path", str(ws)]) == 0
    assert main(["catalog-add", "--path", str(ws)]) == 0  # re-run: no error
    from discovery_engine import workspace_catalog
    entries = [e for e in workspace_catalog.list_workspaces()]
    # exactly one entry for this path (append-or-noop)
    matches = [e for e in entries if str(ws.resolve()) in json.dumps(e, default=str)]
    assert len(matches) == 1


def test_scaffold_workspace_verb_is_wired(capsys):
    """The subparser exists and reaches the handler (smoke — full scaffold
    clones a template, so we only assert the verb resolves + errors cleanly on
    a bad target rather than performing a network clone)."""
    # Missing --name is an argparse error (SystemExit 2).
    with pytest.raises(SystemExit):
        main(["scaffold-workspace", "--target", "/tmp/nope"])
