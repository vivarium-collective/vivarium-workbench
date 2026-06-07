"""Close/archive control — `_set_investigation_status` writes the `status`
field into investigations/<slug>/investigation.yaml."""
import yaml

from vivarium_dashboard.server import _set_investigation_status


def _ws(tmp):
    (tmp / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    inv = tmp / "investigations" / "dnaa-replication"
    (inv / "studies").mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "name: dnaa-replication\ntitle: DnaA\nstatus: in-progress\nstudies: []\n",
        encoding="utf-8")
    return tmp


def test_set_status_archives(tmp_path):
    ws = _ws(tmp_path)
    result = _set_investigation_status(ws, "dnaa-replication", "archived")
    assert result.get("ok") is True
    assert result.get("status") == "archived"
    spec = yaml.safe_load(
        (ws / "investigations" / "dnaa-replication" / "investigation.yaml").read_text())
    assert spec["status"] == "archived"


def test_invalid_status_400(tmp_path):
    ws = _ws(tmp_path)
    result = _set_investigation_status(ws, "dnaa-replication", "bogus")
    assert result.get("_code") == 400
    assert "error" in result


def test_missing_investigation_404(tmp_path):
    ws = _ws(tmp_path)
    result = _set_investigation_status(ws, "does-not-exist", "archived")
    assert result.get("_code") == 404
