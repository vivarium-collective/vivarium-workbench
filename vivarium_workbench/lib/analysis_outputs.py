"""Discovery + download of a study's tabular Analysis result files.

The study-detail **Data** tab lets a biologist pull the raw outputs an
Analysis Step writes — in v2ecoli these are ``.csv`` / ``.tsv`` files (e.g. the
PTools exports under ``**/ptools/*.tsv``, plus any CSVs an Analysis flush
drops in the study dir). This module is the pure, ``ws_root``-parameterised
backend behind the three routes:

    GET /api/study-analysis-outputs?study=<slug>   → list_analysis_outputs
    GET /api/study-analysis-file?study=<slug>&path= → resolve_analysis_output
    GET /api/study-analysis-zip?study=<slug>        → build_analysis_outputs_zip

Path safety: the per-file route never trusts the client ``path`` blindly — it
re-resolves it under the study dir and rejects anything that escapes the dir or
isn't an allowed tabular extension. This mirrors the ``DownloadError`` contract
in :mod:`lib.download_views`, which we reuse so the error shape lives once.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from vivarium_workbench.lib.download_views import DownloadError
from vivarium_workbench.lib.workspace_paths import WorkspacePaths

# Tabular result extensions surfaced in the Data tab. Served inline-friendly
# (text) but offered as a download by the route. Keep in sync with the
# front-end's expectations.
_RESULT_EXTS = {".csv", ".tsv"}

_MIME = {
    ".csv": "text/csv; charset=utf-8",
    ".tsv": "text/tab-separated-values; charset=utf-8",
}

# Directories whose contents are never analysis result files — skip them so the
# scan doesn't surface zarr chunk shards or VCS bookkeeping as "downloads".
_SKIP_DIR_SUFFIXES = (".zarr",)
_SKIP_DIR_NAMES = {".git", "__pycache__", ".ipynb_checkpoints"}


def _iter_result_files(study_dir: Path):
    """Yield every tabular result file under *study_dir*, skipping zarr/VCS dirs."""
    for path in sorted(study_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _RESULT_EXTS:
            continue
        rel_parts = path.relative_to(study_dir).parts[:-1]
        if any(
            part in _SKIP_DIR_NAMES or part.endswith(_SKIP_DIR_SUFFIXES)
            for part in rel_parts
        ):
            continue
        yield path


def list_analysis_outputs(ws_root, slug: str) -> dict:
    """List a study's downloadable Analysis result files.

    Returns ``{"study": slug, "files": [...], "total_bytes": int}`` where each
    file is ``{name, relpath, dir, size, download_url}``. ``dir`` is the file's
    parent relative to the study dir ("" for top-level) so the UI can group by
    folder (``ptools/``, ``analyses/<run>/`` …).

    Raises ``DownloadError(404)`` when the study is absent. An empty ``files``
    list (study exists but produced no CSV/TSV yet) is a normal 200, not an
    error.
    """
    slug = (slug or "").strip()
    if not slug:
        raise DownloadError({"error": "missing study"}, 400)
    wp = WorkspacePaths.load(ws_root)
    try:
        study_dir = wp.study_dir(slug)
    except FileNotFoundError:
        raise DownloadError({"error": f"study not found: {slug!r}"}, 404)

    import urllib.parse as _up

    files: list[dict] = []
    total = 0
    for path in _iter_result_files(study_dir):
        rel = path.relative_to(study_dir)
        relposix = rel.as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        total += size
        files.append({
            "name": path.name,
            "relpath": relposix,
            "dir": rel.parent.as_posix() if rel.parent != Path(".") else "",
            "size": size,
            "download_url": (
                "/api/study-analysis-file?study="
                + _up.quote(slug, safe="")
                + "&path="
                + _up.quote(relposix, safe="")
            ),
        })
    return {"study": slug, "files": files, "total_bytes": total}


def _safe_study_file(ws_root, slug: str, relpath: str) -> tuple[Path, Path]:
    """Resolve ``relpath`` under the study dir, rejecting traversal.

    Returns ``(study_dir, abs_file)``. Raises ``DownloadError`` for a missing
    study (404), an escaping/empty path (400), or a non-result extension (400).
    """
    slug = (slug or "").strip()
    relpath = (relpath or "").strip()
    if not slug:
        raise DownloadError({"error": "missing study"}, 400)
    if not relpath:
        raise DownloadError({"error": "missing ?path="}, 400)
    wp = WorkspacePaths.load(ws_root)
    try:
        study_dir = wp.study_dir(slug).resolve()
    except FileNotFoundError:
        raise DownloadError({"error": f"study not found: {slug!r}"}, 404)

    candidate = (study_dir / relpath).resolve()
    try:
        candidate.relative_to(study_dir)
    except ValueError:
        raise DownloadError({"error": "path escapes study dir"}, 400)
    if candidate.suffix.lower() not in _RESULT_EXTS:
        raise DownloadError({"error": "not a downloadable result file"}, 400)
    return study_dir, candidate


def resolve_analysis_output(
    ws_root, slug: str, relpath: str,
) -> tuple[bytes, str, str]:
    """Read one result file for download.

    Returns ``(data, content_type, filename)`` — served as an attachment.
    Raises ``DownloadError`` (400/404/500) via :func:`_safe_study_file` plus a
    404 when the resolved file is absent.
    """
    _study_dir, path = _safe_study_file(ws_root, slug, relpath)
    if not path.is_file():
        raise DownloadError({"error": f"file not found: {relpath}"}, 404)
    mime = _MIME.get(path.suffix.lower(), "application/octet-stream")
    try:
        data = path.read_bytes()
    except OSError as e:
        raise DownloadError({"error": f"read failed: {e}"}, 500)
    return data, mime, path.name


def build_analysis_outputs_zip(ws_root, slug: str) -> tuple[bytes, str]:
    """Zip all of a study's result files into ``<slug>-analyses.zip``.

    Returns ``(zip_bytes, filename)``. Raises ``DownloadError(404)`` for a
    missing study; a study with no result files yields a valid empty zip.
    """
    info = list_analysis_outputs(ws_root, slug)
    study_dir = WorkspacePaths.load(ws_root).study_dir(slug)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in info["files"]:
            src = study_dir / entry["relpath"]
            if src.is_file():
                zf.write(src, entry["relpath"])
    return buf.getvalue(), f"{slug}-analyses.zip"
