"""`_inputs_payload(ws_root)` backs GET /api/inputs: it returns the loaded
investigation's inputs (top), the repo-wide global inputs, and the current
investigation slug (matched to the current git branch)."""
import subprocess

from vivarium_dashboard.server import _inputs_payload


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
    inv = tmp / "investigations" / "dnaa-replication"
    (inv / "studies").mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "name: dnaa-replication\n"
        "title: dnaa-replication\n"
        "studies: []\n"
        "inputs:\n"
        "  datasets:\n"
        "    - name: oric-counts\n"
        "      path: investigations/dnaa-replication/inputs/oric-counts.csv\n",
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
