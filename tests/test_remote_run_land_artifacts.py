"""Study-less land-on-demand for a Runs-table remote row.

`land_remote_simulation_artifacts` downloads a remote run's result tar and folds
analyses.json + copies ptools/*.tsv into .pbg/runs/<run_id>/ — no study needed.
The sms-api client and the tar are fabricated; no network.
"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from vivarium_workbench.lib import remote_run_landing as rrl
from vivarium_workbench.lib import remote_run_views as rrv


def _make_results_tar(dst: Path) -> Path:
    """A results tar carrying one PTools TSV and one analysis.json."""
    stage = dst / "stage"
    (stage / "run" / "ptools").mkdir(parents=True)
    (stage / "run" / "ptools" / "genes.tsv").write_text("id\tvalue\nb0001\t1.0\n", encoding="utf-8")
    (stage / "run" / "analyses" / "mymod").mkdir(parents=True)
    (stage / "run" / "analyses" / "mymod" / "analysis.json").write_text(
        json.dumps({"name": "mymod", "outputs": []}), encoding="utf-8")
    tar_path = dst / "sim_501.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(stage, arcname=".")
    return tar_path


class _FakeClient:
    """Stands in for SmsApiClient: download_data drops the fabricated tar."""
    def __init__(self, tar_src: Path):
        self._tar_src = tar_src

    def download_data(self, simulation_id, dest_dir, timeout=None):
        import shutil
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"sim_{simulation_id}.tar.gz"
        shutil.copy2(self._tar_src, out)
        return out


@pytest.fixture
def _ws(tmp_path):
    # Minimal workspace so WorkspacePaths.load resolves .pbg under it.
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    return tmp_path


def test_lands_ptools_and_analyses_into_run_dir(_ws, tmp_path):
    tar = _make_results_tar(tmp_path / "src")
    result = rrl.land_remote_simulation_artifacts(_ws, 501, "exp-a", client=_FakeClient(tar))
    run_dir = _ws / ".pbg" / "runs" / "exp-a"
    assert (run_dir / "ptools" / "genes.tsv").is_file()
    assert result["run_id"] == "exp-a"
    assert result["ptools"] == 1
    # `analyses` reflects whether fold_analyses wrote analyses.json (its own
    # schema decides that); here we assert MY wiring surfaces it as a bool that
    # matches the file's presence, without coupling to fold's internals.
    assert isinstance(result["analyses"], bool)
    assert result["analyses"] == (run_dir / "analyses.json").is_file()


def test_no_ptools_in_tar_lands_zero(_ws, tmp_path):
    stage = tmp_path / "empty"
    stage.mkdir()
    (stage / "note.txt").write_text("nothing to land", encoding="utf-8")
    tar = tmp_path / "sim_9.tar.gz"
    with tarfile.open(tar, "w:gz") as t:
        t.add(stage, arcname=".")
    result = rrl.land_remote_simulation_artifacts(_ws, 9, "exp-empty", client=_FakeClient(tar))
    assert result == {"run_id": "exp-empty", "ptools": 0, "analyses": False}


# ---- the HTTP-shaped view ------------------------------------------------

def test_view_requires_simulation_id_and_run_id(_ws, monkeypatch):
    monkeypatch.setattr(rrv, "_run_auth_ok", lambda: True)
    body, status = rrv.remote_run_land_artifacts(_ws, {"run_id": "exp-a"})
    assert status == 400
    body, status = rrv.remote_run_land_artifacts(_ws, {"simulation_id": 501})
    assert status == 400


def test_view_returns_landing_result(_ws, tmp_path, monkeypatch):
    monkeypatch.setattr(rrv, "_run_auth_ok", lambda: True)
    monkeypatch.setattr(rrv, "SmsApiClient", lambda *a, **k: object())
    monkeypatch.setattr(
        "vivarium_workbench.lib.remote_run_landing.land_remote_simulation_artifacts",
        lambda ws, sid, rid, client: {"run_id": rid, "ptools": 3, "analyses": True},
    )
    body, status = rrv.remote_run_land_artifacts(_ws, {"simulation_id": 501, "run_id": "exp-a"})
    assert status == 200
    assert body == {"run_id": "exp-a", "ptools": 3, "analyses": True}


def test_view_shapes_sms_api_error(_ws, monkeypatch):
    from vivarium_workbench.lib.sms_api_client import SmsApiError
    monkeypatch.setattr(rrv, "_run_auth_ok", lambda: True)
    monkeypatch.setattr(rrv, "SmsApiClient", lambda *a, **k: object())

    def _boom(ws, sid, rid, client):
        raise SmsApiError("GET /data -> 502: 502 Bad Gateway", status=502)
    monkeypatch.setattr(
        "vivarium_workbench.lib.remote_run_landing.land_remote_simulation_artifacts", _boom)
    body, status = rrv.remote_run_land_artifacts(_ws, {"simulation_id": 1, "run_id": "x"})
    assert status == 502
    assert "sms-api unavailable" in body["error"]
