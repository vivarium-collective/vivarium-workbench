"""Route-level goldens for the remote store fetch (docs/backend-adapters.md §2.1, step P2 "Remote tab" R2), providers on.

Recorded on the base before ``simulations_index.resolve_or_fetch_store``'s remote half went behind
``artifacts.fetch_store``: the zip ``/api/simulation-run-download`` serves for an s3-only SMS row (its member names and
bytes, and the one outbound ``download_data`` call), the composite-run artifact's zarr fallback through the same
fetch, the 404 of a viva-core, compose-api or other-deployment row (with nothing downloaded), and an ``SmsApiError``
answered ``(None, None)`` with the temporary directory removed. Every row is the base's answer, byte for byte.
"""
from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from test_backends_existing import ELSEWHERE, HERE, _record, ws  # noqa: F401 -- fixtures, found by name
from vivarium_workbench.lib import simulations_index as si
from vivarium_workbench_sms import sms_api_client
from vivarium_workbench_sms.remote_pinned import remote_deployment_name
from vivarium_workbench_sms.sms_api_client import SmsApiError

#: Goldens recorded with providers on, through the SMS plugin (step P3, K2c): the core-only run ignores this module.
pytestmark = pytest.mark.plugin

DOWNLOAD = "/api/simulation-run-download"

#: The tar's members (arcname -> bytes), as viva-api's ``/data`` endpoint streams them.
TREE = {
    "exp-113/zarr.json": b'{"zarr_format": 3}',
    "exp-113/cell_mass/c/0": b"\x00\x01\x02\x03",
    "exp-113/notes.txt": b"hello\n",
}
#: The zip's members for that tar: ``build_simulation_run_zip`` zips the extracted tree under the run id.
ZIP_NAMES = sorted(["r-s3/exp-113/cell_mass/c/0", "r-s3/exp-113/notes.txt", "r-s3/exp-113/zarr.json"])


def _row(run_id: str, sim_id: object, deployment: "str | None" = None) -> dict:
    """A row shaped like ``remote_simulations._normalize``'s: nothing local, the store on s3."""
    return {
        "run_id": run_id, "spec_id": "", "sim_name": run_id, "label": run_id, "status": "completed",
        "n_steps": None, "progress_step": None, "started_at": 10.0, "completed_at": 10.0,
        "db_path": None, "store_path": f"s3://bucket/vecoli-output/{run_id}", "emitter": "xarray",
        "studies": [], "study_slug": "s", "investigation_slug": None,
        "remote_origin": {"deployment": deployment or remote_deployment_name(), "simulation_id": sim_id,
                          "experiment_id": run_id, "backend": "aws"},
    }


def _tar(tmp_path: Path, tree: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in tree.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    """The class attribute ``SmsApiClient.download_data`` (whichever module builds the client): records each call
    as ``(simulation_id, client base, dest_dir)`` and drops the fabricated tar."""
    calls: list = []
    blob = _tar(tmp_path, TREE)

    def fake(self, simulation_id, dest_dir, timeout=None):
        calls.append((simulation_id, self.base_url, Path(dest_dir)))
        out = Path(dest_dir) / f"sim_{simulation_id}.tar.gz"
        out.write_bytes(blob)
        return out

    monkeypatch.setattr(sms_api_client.SmsApiClient, "download_data", fake)
    return calls


def test_zip_of_an_s3_only_sms_row(ws, downloads, inproc_client, monkeypatch):  # noqa: F811 -- ws is a fixture
    monkeypatch.setattr(si, "list_simulations", lambda workspace: [_row("r-s3", 113)])
    r = inproc_client(ws).get(f"{DOWNLOAD}?run_id=r-s3")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r._body)) as zf:
        assert sorted(zf.namelist()) == ZIP_NAMES
        for name, data in TREE.items():
            assert zf.read(f"r-s3/{name}") == data
    assert [(sid, base) for sid, base, _ in downloads] == [(113, HERE)]
    assert not downloads[0][2].exists()  # the temporary directory is gone once the zip is built


def test_the_composite_run_artifacts_zarr_fallback_fetches_the_store(ws, tmp_path, inproc_client, monkeypatch):  # noqa: F811
    from test_run_artifact_zarr_fallback import make_fake_zarr

    (ws / ".pbg").mkdir(parents=True, exist_ok=True)
    src = tmp_path / "src.zarr"
    make_fake_zarr(src)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(src, arcname="exp-113/store.zarr")
    calls: list = []

    def fake(self, simulation_id, dest_dir, timeout=None):
        calls.append((simulation_id, self.base_url))
        out = Path(dest_dir) / f"sim_{simulation_id}.tar.gz"
        out.write_bytes(buf.getvalue())
        return out

    monkeypatch.setattr(sms_api_client.SmsApiClient, "download_data", fake)
    monkeypatch.setattr(si, "list_simulations", lambda workspace: [_row("r4", 113)])
    r = inproc_client(ws).get("/api/composite-run/r4/artifact/viz")
    assert r.status_code == 200
    assert "cell_mass" in r.text and "Plotly.newPlot" in r.text
    assert calls == [(113, HERE)]


@pytest.mark.parametrize("rid, kind, base, dep", [
    ("7", "viva-core", HERE, "smsvpctest"),
    ("simulation-C", "compose-api", HERE, "smsvpctest"),
    ("55", "sms-legacy", ELSEWHERE, "prod-elsewhere"),
], ids=["viva-core", "compose-api", "other-deployment"])
def test_a_row_of_another_backend_is_a_404_with_nothing_downloaded(ws, downloads, inproc_client, monkeypatch,  # noqa: F811
                                                                    rid, kind, base, dep):
    _record(ws, rid, kind, base, dep=dep)
    monkeypatch.setattr(si, "list_simulations", lambda workspace: [_row("r-x", rid, deployment=dep)])
    r = inproc_client(ws).get(f"{DOWNLOAD}?run_id=r-x")
    assert (r.status_code, r.json()) == (404, {"error": "run data not found: r-x"})
    assert downloads == []


def test_an_sms_api_error_is_none_none_and_the_temporary_directory_is_removed(ws, monkeypatch):  # noqa: F811
    dirs: list = []

    def boom(self, simulation_id, dest_dir, timeout=None):
        dirs.append(Path(dest_dir))
        raise SmsApiError("sms-api unreachable")

    monkeypatch.setattr(sms_api_client.SmsApiClient, "download_data", boom)
    assert si.resolve_or_fetch_store(ws, _row("r-down", 114)) == (None, None)
    assert len(dirs) == 1 and not dirs[0].exists()


def test_a_fetched_store_is_the_extracted_tree_in_a_temporary_directory(ws, downloads):  # noqa: F811
    store, tmp = si.resolve_or_fetch_store(ws, _row("r-s3", "113"))
    try:
        assert store == Path(tmp.name) / "extracted"
        assert sorted(str(p.relative_to(store)) for p in store.rglob("*") if p.is_file()) == sorted(TREE)
        assert (store / "exp-113" / "notes.txt").read_bytes() == b"hello\n"
        assert [(sid, base) for sid, base, _ in downloads] == [(113, HERE)]  # the string id is int()-ed
    finally:
        tmp.cleanup()
