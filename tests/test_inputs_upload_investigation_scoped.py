"""Investigation-scoped uploads: the dataset / reference / expert-doc POST
endpoints accept an optional ``investigation`` slug and, when present, write the
file under investigations/<slug>/inputs/... and append the entry to that
investigation.yaml's ``inputs:`` block (instead of the global pool).

The HTTP handler normally routes uploads through ``_active_branch_action`` (git
commit on the active workstream branch). These tests patch that seam to run the
action directly so we can exercise the write logic without a git workstream.
"""
import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def workspace_server(tmp_path, dashboard_client):
    ws_root = tmp_path
    (ws_root / "workspace.yaml").write_text(
        yaml.dump({"name": "testws", "datasets": [], "expert_docs": []},
                  sort_keys=False),
        encoding="utf-8",
    )
    inv = ws_root / "investigations" / "dnaa-replication"
    (inv / "studies").mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "name: dnaa-replication\ntitle: dnaa-replication\nstudies: []\n",
        encoding="utf-8",
    )

    # The FastAPI routes call the lib builder directly (no _active_branch_action,
    # no git commit), so the file writes happen eagerly and return 200 without a
    # git workstream — exactly what the old fixture's monkeypatch simulated.
    client = dashboard_client(ws_root)

    class _WS:
        url = client.base_url
        root = ws_root

    yield _WS()


def _post(url, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _inv_yaml(ws):
    return yaml.safe_load(
        (ws.root / "investigations" / "dnaa-replication" / "investigation.yaml")
        .read_text(encoding="utf-8")
    )


def test_dataset_upload_investigation_scoped(workspace_server):
    ws = workspace_server
    b64 = base64.b64encode(b"col_a,col_b\n1,2\n").decode()
    status, resp = _post(ws.url + "/api/dataset", {
        "name": "oric-counts",
        "filename": "oric.csv",
        "file_b64": b64,
        "investigation": "dnaa-replication",
    })
    assert status == 200, resp

    # File landed under the investigation's inputs dir.
    expected = (ws.root / "investigations" / "dnaa-replication" / "inputs"
                / "datasets" / "oric-counts" / "oric.csv")
    assert expected.is_file()

    # Entry appears in the investigation's inputs.datasets (NOT the global pool).
    spec = _inv_yaml(ws)
    ds = spec["inputs"]["datasets"]
    assert any(d.get("name") == "oric-counts" for d in ds)
    rel = "investigations/dnaa-replication/inputs/datasets/oric-counts/oric.csv"
    assert any(d.get("path") == rel for d in ds)

    # Global workspace.yaml datasets stayed empty.
    glob = yaml.safe_load((ws.root / "workspace.yaml").read_text(encoding="utf-8"))
    assert not (glob.get("datasets") or [])


def test_expert_doc_upload_investigation_scoped(workspace_server):
    ws = workspace_server
    b64 = base64.b64encode(b"# oriC notes\n").decode()
    status, resp = _post(ws.url + "/api/expert-doc", {
        "name": "oric-notes",
        "filename": "notes.md",
        "file_b64": b64,
        "investigation": "dnaa-replication",
    })
    assert status == 200, resp
    expected = (ws.root / "investigations" / "dnaa-replication" / "inputs"
                / "expert" / "oric-notes.md")
    assert expected.is_file()
    spec = _inv_yaml(ws)
    assert any(d.get("name") == "oric-notes" for d in spec["inputs"]["expert_docs"])


def test_reference_bibtex_investigation_scoped(workspace_server):
    ws = workspace_server
    status, resp = _post(ws.url + "/api/reference-bibtex", {
        "bibtex_text": "@article{Foo2020, title = {A foo}, year = {2020}}",
        "investigation": "dnaa-replication",
    })
    assert status == 200, resp
    # Bare key appended to the investigation's references.
    spec = _inv_yaml(ws)
    assert "Foo2020" in spec["inputs"]["references"]
    # And the entry exists in the global papers.bib library.
    bib = (ws.root / "references" / "papers.bib").read_text(encoding="utf-8")
    assert "Foo2020" in bib
