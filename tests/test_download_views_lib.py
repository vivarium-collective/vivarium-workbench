"""Parity and unit tests for vivarium_dashboard.lib.download_views.

The download builders return raw bytes (or a path) plus serving metadata
(content-type, inline-vs-attachment, filename). These tests verify the happy
paths and the 400/404/500/None (→204) error contracts, and that the legacy
stdlib server.py handler shims still resolve through the same lib functions.
"""

from __future__ import annotations

import json
import zipfile
import io
from pathlib import Path

import pytest
import yaml

from vivarium_dashboard.lib import download_views as dv


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_workspace(tmp_path: Path) -> Path:
    """Minimal workspace: a study dir + a per-investigation report + a guidance
    HTML + a workspace.yaml with a data-source provider."""
    (tmp_path / "studies" / "s1").mkdir(parents=True)
    (tmp_path / "studies" / "s1" / "study.yaml").write_text(
        yaml.dump({"name": "s1"}), encoding="utf-8"
    )
    (tmp_path / "studies" / "s1" / "data.txt").write_text("hello", encoding="utf-8")

    # per-investigation report (investigations/<slug>/reports/index.html)
    rep = tmp_path / "investigations" / "inv-a" / "reports"
    rep.mkdir(parents=True)
    (rep / "index.html").write_text("<html>report</html>", encoding="utf-8")

    # guidance: .pbg/server/content/*.html
    content = tmp_path / ".pbg" / "server" / "content"
    content.mkdir(parents=True)
    (content / "guidance.html").write_text("<html>guidance</html>", encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# build_study_export
# ---------------------------------------------------------------------------

class TestBuildStudyExport:
    def test_happy_path(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        data, mime, filename = dv.build_study_export(ws, "s1")
        assert mime == "application/zip"
        assert filename == "s1.zip"
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
        assert any(n.endswith("data.txt") for n in names)
        assert all(n.startswith("s1/") for n in names)

    def test_missing_study_400(self, tmp_path: Path) -> None:
        with pytest.raises(dv.DownloadError) as exc:
            dv.build_study_export(tmp_path, "")
        assert exc.value.status == 400
        assert exc.value.body == {"error": "missing study"}

    def test_not_found_404(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        with pytest.raises(dv.DownloadError) as exc:
            dv.build_study_export(ws, "nope")
        assert exc.value.status == 404
        assert exc.value.body == {"error": "study not found"}


# ---------------------------------------------------------------------------
# resolve_data_source_file
# ---------------------------------------------------------------------------

def _ws_with_data_source(tmp_path: Path, fname: str, content: str = "a\tb\n") -> Path:
    """Workspace whose data-source provider returns one entry for *fname*."""
    src = tmp_path / "bundle" / fname
    src.parent.mkdir(parents=True)
    src.write_text(content, encoding="utf-8")
    # Unique provider module name per workspace so sys.modules caching across
    # tests can't alias one provider onto another's path.
    mod = "prov_" + Path(fname).stem
    (tmp_path / f"{mod}.py").write_text(
        "def sources():\n"
        f"    return [{{'key': 'k1', 'path': {str(src)!r}}}]\n",
        encoding="utf-8",
    )
    (tmp_path / "workspace.yaml").write_text(
        yaml.dump({
            "name": "ws",
            "dashboard": {"data_sources": {"provider": f"{mod}:sources", "label": "L"}},
        }),
        encoding="utf-8",
    )
    return tmp_path


class TestResolveDataSourceFile:
    def test_inline_text(self, tmp_path: Path, monkeypatch) -> None:
        ws = _ws_with_data_source(tmp_path, "table.tsv")
        monkeypatch.syspath_prepend(str(ws))
        data, mime, inline, filename = dv.resolve_data_source_file(ws, "k1")
        assert data == b"a\tb\n"
        assert mime == "text/tab-separated-values; charset=utf-8"
        assert inline is True
        assert filename == "table.tsv"

    def test_binary_attachment(self, tmp_path: Path, monkeypatch) -> None:
        ws = _ws_with_data_source(tmp_path, "blob.bin", content="x")
        monkeypatch.syspath_prepend(str(ws))
        data, mime, inline, filename = dv.resolve_data_source_file(ws, "k1")
        assert mime == "application/octet-stream"
        assert inline is False
        assert filename == "blob.bin"

    def test_missing_key_400(self, tmp_path: Path) -> None:
        with pytest.raises(dv.DownloadError) as exc:
            dv.resolve_data_source_file(tmp_path, None)
        assert exc.value.status == 400
        assert exc.value.body == {"error": "missing ?key="}

    def test_unknown_key_404(self, tmp_path: Path, monkeypatch) -> None:
        ws = _ws_with_data_source(tmp_path, "table.tsv")
        monkeypatch.syspath_prepend(str(ws))
        with pytest.raises(dv.DownloadError) as exc:
            dv.resolve_data_source_file(ws, "ghost")
        assert exc.value.status == 404
        assert "ghost" in exc.value.body["error"]


# ---------------------------------------------------------------------------
# resolve_iset_report / resolve_guidance
# ---------------------------------------------------------------------------

class TestResolveIsetReport:
    def test_present(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        p = dv.resolve_iset_report(ws, "inv-a")
        assert p is not None
        assert p.name == "index.html"
        assert p.read_text() == "<html>report</html>"

    def test_absent_returns_none(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        assert dv.resolve_iset_report(ws, "no-such") is None


class TestResolveGuidance:
    def test_present(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        p = dv.resolve_guidance(ws)
        assert p is not None
        assert p.name == "guidance.html"

    def test_absent_returns_none(self, tmp_path: Path) -> None:
        # No .pbg/server/content dir at all.
        assert dv.resolve_guidance(tmp_path) is None

    def test_empty_dir_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / ".pbg" / "server" / "content").mkdir(parents=True)
        assert dv.resolve_guidance(tmp_path) is None

    def test_latest_by_mtime(self, tmp_path: Path) -> None:
        content = tmp_path / ".pbg" / "server" / "content"
        content.mkdir(parents=True)
        old = content / "old.html"
        new = content / "new.html"
        old.write_text("old")
        new.write_text("new")
        import os
        os.utime(old, (1, 1))
        os.utime(new, (10**9, 10**9))
        p = dv.resolve_guidance(tmp_path)
        assert p is not None and p.name == "new.html"


# ---------------------------------------------------------------------------
# build_investigation_notebook
# ---------------------------------------------------------------------------

class TestBuildInvestigationNotebook:
    def test_missing_slug_400(self, tmp_path: Path) -> None:
        with pytest.raises(dv.DownloadError) as exc:
            dv.build_investigation_notebook(tmp_path, "", "ipynb")
        assert exc.value.status == 400
        assert exc.value.body == {"error": "investigation slug required"}

    def test_unknown_investigation_404(self, tmp_path: Path) -> None:
        (tmp_path / "workspace.yaml").write_text(
            yaml.dump({"name": "ws"}), encoding="utf-8"
        )
        with pytest.raises(dv.DownloadError) as exc:
            dv.build_investigation_notebook(tmp_path, "ghost", "ipynb")
        # FileNotFoundError → 404; any other export error → 500.
        assert exc.value.status in (404, 500)


# ---------------------------------------------------------------------------
# DownloadError
# ---------------------------------------------------------------------------

class TestDownloadError:
    def test_body_and_status(self) -> None:
        err = dv.DownloadError({"error": "oops"}, 404)
        assert err.status == 404
        assert err.body == {"error": "oops"}
        assert str(err) == "oops"


# ---------------------------------------------------------------------------
# TestServerShimParity: legacy handler shims resolve through the lib
# ---------------------------------------------------------------------------

class TestServerShimParity:
    def test_study_export_zip_shim(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        from vivarium_dashboard.server import _study_export_zip
        assert _study_export_zip(ws, "s1") == dv.study_export_zip(ws, "s1")

    def test_iset_report_file_shim(self, tmp_path: Path) -> None:
        ws = _make_workspace(tmp_path)
        from vivarium_dashboard.server import _iset_report_file
        assert _iset_report_file(ws, "inv-a") == dv.resolve_iset_report(ws, "inv-a")
        assert _iset_report_file(ws, "ghost") is None

    def test_data_source_mime_single_sourced(self) -> None:
        import vivarium_dashboard.server as server
        assert server._DATA_SOURCE_MIME is dv._DATA_SOURCE_MIME
