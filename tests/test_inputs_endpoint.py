"""`_inputs_payload(ws_root)` backs GET /api/inputs: it returns the loaded
investigation's inputs (top), the repo-wide global inputs, and the current
investigation slug (matched to the current git branch)."""
import subprocess

from vivarium_workbench.lib.report_views import build_inputs as _inputs_payload


def _git_ws(tmp):
    (tmp / "workspace.yaml").write_text(
        "name: demo\n"
        "datasets:\n"
        "  - name: shared\n"
        "    path: datasets/shared.csv\n",
        encoding="utf-8",
    )
    # repo-level dataset file
    (tmp / "datasets").mkdir()
    (tmp / "datasets" / "shared.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    # one investigation declaring its own inputs
    # a BibTeX library with one entry the investigation references by bare key
    (tmp / "references").mkdir()
    (tmp / "references" / "papers.bib").write_text(
        "@article{Schmidt2016,\n"
        "  title = {The quantitative and condition-dependent Escherichia coli proteome},\n"
        "  author = {Schmidt, Alexander and others},\n"
        "  journal = {Nature Biotechnology},\n"
        "  year = {2016},\n"
        "  doi = {10.1038/nbt.3418},\n"
        "}\n",
        encoding="utf-8",
    )
    inv = tmp / "investigations" / "dnaa-replication"
    (inv / "studies").mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "name: dnaa-replication\n"
        "title: dnaa-replication\n"
        "studies: []\n"
        "inputs:\n"
        "  datasets:\n"
        "    - name: oric-counts\n"
        "      path: investigations/dnaa-replication/inputs/oric-counts.csv\n"
        "  references:\n"
        "    - Schmidt2016\n"
        "    - GhostKey1999\n"
        "  expert_docs:\n"
        "    - name: oric-notes\n"
        "      path: investigations/dnaa-replication/inputs/expert/oric-notes.md\n",
        encoding="utf-8",
    )
    for c in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "init"], ["branch", "-M", "main"],
              ["checkout", "-qb", "investigation/dnaa-replication"]):
        subprocess.run(["git", *c], cwd=tmp, check=True)
    return tmp


def test_inputs_payload_investigation_global_current(tmp_path):
    ws = _git_ws(tmp_path)
    payload = _inputs_payload(ws)

    assert payload["current"] == "dnaa-replication"

    inv = payload["investigation"]
    inv_ds_names = {d.get("name") for d in inv["datasets"]}
    assert "oric-counts" in inv_ds_names

    glob = payload["global"]
    assert "datasets" in glob and "references" in glob
    glob_ds_names = {d.get("name") for d in glob["datasets"]}
    assert "shared" in glob_ds_names


def test_inputs_payload_enriches_investigation_references(tmp_path):
    ws = _git_ws(tmp_path)
    payload = _inputs_payload(ws)
    refs = payload["investigation"]["references"]
    by_key = {r["key"]: r for r in refs}

    # Matched bare key joined against papers.bib -> rich dict with title + bibtex.
    matched = by_key["Schmidt2016"]
    assert "proteome" in matched["title"]
    assert matched["year"] == "2016"
    assert matched["doi"] == "10.1038/nbt.3418"
    assert matched["bibtex"].startswith("@article{Schmidt2016")
    assert not matched.get("_unmatched")

    # Unmatched key kept as a stub.
    ghost = by_key["GhostKey1999"]
    assert ghost["_unmatched"] is True
    assert ghost["title"] == "GhostKey1999"


def test_inputs_payload_datasets_and_expert_docs_carry_path(tmp_path):
    ws = _git_ws(tmp_path)
    inv = _inputs_payload(ws)["investigation"]
    ds = inv["datasets"][0]
    assert ds["name"] == "oric-counts"
    assert ds["path"] == "investigations/dnaa-replication/inputs/oric-counts.csv"
    ed = inv["expert_docs"][0]
    assert ed["name"] == "oric-notes"
    assert ed["path"].endswith("oric-notes.md")
