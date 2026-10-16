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
        "    path: datasets/shared.csv\n"
        "expert_docs:\n"
        "  - name: Campaign Dossier\n"
        "    path: references/expert/campaign.pdf\n"
        "    description: Source dossier the whole workspace draws on.\n",
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
    # A study that cites the shared reference (inline + nested `cites`) — powers
    # the Resources page "Cited by" chips.
    study = tmp / "studies" / "oric-counting"
    study.mkdir(parents=True)
    (study / "study.yaml").write_text(
        "schema_version: 4\n"
        "name: oric-counting\n"
        "title: oriC counting\n"
        "investigation: dnaa-replication\n"
        "cites: [Schmidt2016]\n"
        "findings:\n"
        "  - id: f1\n"
        "    expected:\n"
        "      cites: [Schmidt2016]\n",
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


def test_inputs_payload_global_expert_docs(tmp_path):
    """workspace.yaml top-level `expert_docs` surface in the global block so the
    Resources page can render a repo-wide "Expert docs" section (not scoped to a
    single investigation). Each entry keeps its name, path and description."""
    ws = _git_ws(tmp_path)
    glob = _inputs_payload(ws)["global"]

    assert "expert_docs" in glob, "global block must carry expert_docs"
    docs = {d.get("name"): d for d in glob["expert_docs"]}
    assert "Campaign Dossier" in docs
    doc = docs["Campaign Dossier"]
    assert doc["path"] == "references/expert/campaign.pdf"
    assert doc.get("description")  # dict extras pass through for display


def test_inputs_payload_global_expert_docs_empty_when_absent(tmp_path):
    """A workspace with no top-level expert_docs gets an empty list, never a
    KeyError — so the frontend's length check hides the section cleanly."""
    (tmp_path / "workspace.yaml").write_text("name: bare\n", encoding="utf-8")
    for c in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"], ["add", "-A"],
              ["commit", "-qm", "init"], ["branch", "-M", "main"]):
        subprocess.run(["git", *c], cwd=tmp_path, check=True)
    glob = _inputs_payload(tmp_path)["global"]
    assert glob.get("expert_docs") == []


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


def test_inputs_payload_references_carry_cited_by(tmp_path):
    """Each global reference is annotated with the studies that cite it (by bib
    key, collected from every study.yaml's `cites:` — inline or nested), so the
    Resources page can render clickable "Cited by" study chips."""
    ws = _git_ws(tmp_path)
    glob = _inputs_payload(ws)["global"]
    by_key = {r.get("key"): r for r in glob["references"] if isinstance(r, dict)}

    schmidt = by_key["Schmidt2016"]
    cited = schmidt.get("cited_by") or []
    slugs = {c["slug"] for c in cited}
    assert "oric-counting" in slugs, "citing study linked to the reference"
    # De-duplicated even though the study cites the key twice (root + nested).
    assert len([c for c in cited if c["slug"] == "oric-counting"]) == 1
    entry = next(c for c in cited if c["slug"] == "oric-counting")
    assert entry["title"] == "oriC counting"


def test_inputs_payload_folds_investigation_resources_into_global(tmp_path):
    """Repo-wide merge / back-compat: an investigation's declared datasets +
    expert_docs (scoped under investigations/<slug>/investigation.yaml) are
    folded into the global block so the unified Resources page shows them all
    together — not only the workspace.yaml-level ones."""
    ws = _git_ws(tmp_path)
    glob = _inputs_payload(ws)["global"]

    ds_names = {d.get("name") for d in glob["datasets"]}
    assert "shared" in ds_names, "workspace-level dataset present"
    assert "oric-counts" in ds_names, "investigation-scoped dataset folded in"

    ed_names = {d.get("name") for d in glob["expert_docs"]}
    assert "Campaign Dossier" in ed_names, "workspace-level expert doc present"
    assert "oric-notes" in ed_names, "investigation-scoped expert doc folded in"


def test_inputs_payload_datasets_and_expert_docs_carry_path(tmp_path):
    ws = _git_ws(tmp_path)
    inv = _inputs_payload(ws)["investigation"]
    ds = inv["datasets"][0]
    assert ds["name"] == "oric-counts"
    assert ds["path"] == "investigations/dnaa-replication/inputs/oric-counts.csv"
    ed = inv["expert_docs"][0]
    assert ed["name"] == "oric-notes"
    assert ed["path"].endswith("oric-notes.md")
