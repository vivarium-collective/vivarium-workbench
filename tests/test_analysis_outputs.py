"""Unit tests for lib.analysis_outputs — the Data-tab download backend."""

import io
import zipfile

import pytest

from vivarium_workbench.lib import analysis_outputs as ao
from vivarium_workbench.lib.download_views import DownloadError


def _ws(tmp_path):
    """A minimal workspace with one study holding mixed result files."""
    d = tmp_path / "studies" / "demo"
    (d / "ptools").mkdir(parents=True)
    (d / "ptools" / "rna.tsv").write_text("gene\tcount\nA\t1\n", encoding="utf-8")
    (d / "ptools" / "proteins.tsv").write_text("p\tn\nX\t2\n", encoding="utf-8")
    (d / "analyses").mkdir()
    (d / "analyses" / "growth.csv").write_text("t,mass\n0,1\n", encoding="utf-8")
    # Non-result files that must NOT be listed.
    (d / "study.yaml").write_text("name: demo\n", encoding="utf-8")
    (d / "runs.001.zarr").mkdir()
    (d / "runs.001.zarr" / "chunk.csv").write_text("x\n", encoding="utf-8")
    return tmp_path


def test_lists_csv_and_tsv_grouped_by_dir(tmp_path):
    ws = _ws(tmp_path)
    out = ao.list_analysis_outputs(ws, "demo")
    names = sorted(f["relpath"] for f in out["files"])
    assert names == ["analyses/growth.csv", "ptools/proteins.tsv", "ptools/rna.tsv"]
    # zarr-internal csv and study.yaml are excluded.
    assert all("zarr" not in f["relpath"] for f in out["files"])
    dirs = {f["dir"] for f in out["files"]}
    assert dirs == {"ptools", "analyses"}
    assert out["total_bytes"] > 0
    # download_url round-trips the study + relpath.
    rna = next(f for f in out["files"] if f["name"] == "rna.tsv")
    assert "study=demo" in rna["download_url"] and "ptools" in rna["download_url"]


def test_missing_study_is_404(tmp_path):
    with pytest.raises(DownloadError) as exc:
        ao.list_analysis_outputs(tmp_path, "nope")
    assert exc.value.status == 404


def test_resolve_serves_file_bytes(tmp_path):
    ws = _ws(tmp_path)
    data, mime, filename = ao.resolve_analysis_output(ws, "demo", "ptools/rna.tsv")
    assert b"gene\tcount" in data
    assert mime.startswith("text/tab-separated-values")
    assert filename == "rna.tsv"


def test_resolve_rejects_traversal(tmp_path):
    ws = _ws(tmp_path)
    (tmp_path / "secret.csv").write_text("nope\n", encoding="utf-8")
    with pytest.raises(DownloadError) as exc:
        ao.resolve_analysis_output(ws, "demo", "../../secret.csv")
    assert exc.value.status == 400


def test_resolve_rejects_non_result_extension(tmp_path):
    ws = _ws(tmp_path)
    with pytest.raises(DownloadError) as exc:
        ao.resolve_analysis_output(ws, "demo", "study.yaml")
    assert exc.value.status == 400


def test_zip_bundles_all_result_files(tmp_path):
    ws = _ws(tmp_path)
    data, filename = ao.build_analysis_outputs_zip(ws, "demo")
    assert filename == "demo-analyses.zip"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = sorted(zf.namelist())
    assert members == ["analyses/growth.csv", "ptools/proteins.tsv", "ptools/rna.tsv"]
