"""File-download / binary-serving view builders extracted from server.py.

These are the ``ws_root``-parameterised builders behind the Phase-A, Batch-14
**download** routes.  Unlike the JSON view builders, these return raw bytes (or
a file path) plus the serving metadata — Content-Type, the inline-vs-attachment
decision, and the download filename — so a single implementation drives both the
legacy stdlib ``server.py`` handlers (thin shims) and the FastAPI seam.

Error contract: builders raise :class:`DownloadError` (``body`` dict + HTTP
``status``) for the non-200 paths, exactly mirroring the legacy handlers'
``self._json({"error": ...}, status)`` responses.  The path-resolving helpers
(``resolve_iset_report`` / ``resolve_guidance``) return ``None`` instead — the
caller maps that to its own 404/204.

Builders
--------
build_study_export          → GET /api/study-export
resolve_data_source_file    → GET /api/data-source-file
resolve_iset_report         → GET /api/investigation/{slug}/report
resolve_guidance            → GET /api/guidance
build_investigation_notebook→ GET /api/investigation-notebook/{slug}
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Optional

from vivarium_workbench.lib import data_sources as _data_sources
from vivarium_workbench.lib.notebook_export import export_investigation_notebook
from vivarium_workbench.lib.workspace_paths import WorkspacePaths


# ---------------------------------------------------------------------------
# Error signal
# ---------------------------------------------------------------------------

class DownloadError(Exception):
    """Raised by builders to signal a non-200 HTTP response (mirrors
    ``InvViewError``).  ``body`` is the JSON-serialisable error dict and
    ``status`` the HTTP status (400 / 404 / 500).  Both the stdlib shim and the
    FastAPI route return the body verbatim so the error contract lives once,
    here in the builder.
    """

    def __init__(self, body: dict, status: int) -> None:
        super().__init__(body.get("error", ""))
        self.body = body
        self.status = status


# Map of file extension → (content-type, inline?) for serving a data-source
# file.  Anything not listed is offered as a binary download (attachment).
# Moved verbatim from server.py.
_DATA_SOURCE_MIME: dict[str, tuple[str, bool]] = {
    ".tsv": ("text/tab-separated-values; charset=utf-8", True),
    ".csv": ("text/csv; charset=utf-8", True),
    ".json": ("application/json; charset=utf-8", True),
    ".txt": ("text/plain; charset=utf-8", True),
    ".text": ("text/plain; charset=utf-8", True),
    ".md": ("text/markdown; charset=utf-8", True),
    ".fasta": ("text/plain; charset=utf-8", True),
    ".fa": ("text/plain; charset=utf-8", True),
    ".fna": ("text/plain; charset=utf-8", True),
    ".faa": ("text/plain; charset=utf-8", True),
    ".yaml": ("text/yaml; charset=utf-8", True),
    ".yml": ("text/yaml; charset=utf-8", True),
}


# ---------------------------------------------------------------------------
# study-export
# ---------------------------------------------------------------------------

def study_export_zip(ws_root: Path, name: str) -> bytes:
    """Zip ``studies/<name>/`` to bytes and return the zip content.

    Moved verbatim from ``server._study_export_zip`` (``server`` keeps a thin
    re-export shim for its existing call-sites / tests).
    """
    src = ws_root / "studies" / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in src.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(src.parent))
    return buf.getvalue()


def build_study_export(ws_root: Path, name: str) -> tuple[bytes, str, str]:
    """Build the GET /api/study-export response for *ws_root*.

    Returns ``(zip_bytes, "application/zip", f"{name}.zip")`` — served as an
    attachment.

    Raises ``DownloadError``:
    - 400 ``{"error": "missing study"}`` when ``name`` is empty.
    - 404 ``{"error": "study not found"}`` when the study dir does not exist.

    Mirrors ``server.Handler._get_study_export``.
    """
    name = (name or "").strip()
    if not name:
        raise DownloadError({"error": "missing study"}, 400)
    src = WorkspacePaths.load(ws_root).studies / name
    if not src.is_dir():
        raise DownloadError({"error": "study not found"}, 404)
    data = study_export_zip(ws_root, name)
    return data, "application/zip", f"{name}.zip"


# ---------------------------------------------------------------------------
# data-source-file
# ---------------------------------------------------------------------------

def resolve_data_source_file(
    ws_root: Path, key: Optional[str],
) -> tuple[bytes, str, bool, str]:
    """Resolve + read one data-source bundle file by ``key`` for *ws_root*.

    Re-runs the provider enumeration and reads the bytes of the entry whose
    ``key`` matches.  The path comes ONLY from the enumeration (never a
    client-supplied path), so there is no traversal surface.  Returns
    ``(data, content_type, inline, filename)`` — text kinds (tsv/csv/json/…)
    are ``inline=True``; anything else is offered as a download.

    Raises ``DownloadError``:
    - 400 ``{"error": "missing ?key="}`` when ``key`` is empty.
    - 404 when ``key`` is not in the enumeration / its file is missing.
    - 500 ``{"error": "read failed: ..."}`` on an OS read error.

    Mirrors ``server.Handler._get_data_source_file``.
    """
    if not key:
        raise DownloadError({"error": "missing ?key="}, 400)

    payload = _data_sources.enumerate_data_sources(ws_root)
    entry = next(
        (s for s in payload.get("sources", []) if s.get("key") == key),
        None,
    )
    if entry is None:
        raise DownloadError(
            {"error": f"key not in data-source bundle: {key!r}"}, 404
        )

    path = Path(entry.get("path") or "")
    if not path.is_file():
        raise DownloadError(
            {"error": f"file for key {key!r} not found: {path}"}, 404
        )

    ext = path.suffix.lower()
    mime, inline = _DATA_SOURCE_MIME.get(ext, ("application/octet-stream", False))
    try:
        data = path.read_bytes()
    except OSError as e:
        raise DownloadError({"error": f"read failed: {e}"}, 500)
    return data, mime, inline, path.name


# ---------------------------------------------------------------------------
# iset report + guidance (path resolvers — caller maps None → 404 / 204)
# ---------------------------------------------------------------------------

def resolve_iset_report(ws_root: Path, slug: str) -> Optional[Path]:
    """Per-investigation report ``index.html`` (or ``None`` if absent).

    Mirrors ``server._iset_report_file``.
    """
    f = WorkspacePaths.load(ws_root).report_dir(slug) / "index.html"
    return f if f.is_file() else None


def resolve_guidance(ws_root: Path) -> Optional[Path]:
    """Latest ``*.html`` in ``<pbg>/server/content`` (or ``None`` if absent).

    Mirrors the file-selection logic of ``server.Handler._serve_guidance``
    (the caller maps ``None`` to a 204 No Content).
    """
    content_dir = WorkspacePaths.load(ws_root).pbg / "server" / "content"
    if not content_dir.exists():
        return None
    files = sorted(
        content_dir.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not files:
        return None
    return files[0]


# ---------------------------------------------------------------------------
# investigation-notebook
# ---------------------------------------------------------------------------

def build_investigation_notebook(
    ws_root: Path, slug: str, fmt: str,
) -> tuple[bytes, str, str]:
    """Build the GET /api/investigation-notebook/<slug> download for *ws_root*.

    Deterministically generates the investigation's ``.ipynb`` + ``.py`` and
    returns ``(data, content_type, filename)`` for the requested ``fmt``
    (``"py"`` → ``text/x-python``; anything else → ``application/x-ipynb+json``),
    served as an attachment.

    Raises ``DownloadError``:
    - 400 ``{"error": "investigation slug required"}`` when ``slug`` is empty.
    - 404 ``{"error": "no investigation '<slug>'"}`` when the investigation is
      absent (``FileNotFoundError`` from the exporter).
    - 500 ``{"error": "notebook export failed: ..."}`` on any other failure.

    Mirrors ``server.Handler._get_investigation_notebook``.
    """
    slug = (slug or "").strip()
    if not slug:
        raise DownloadError({"error": "investigation slug required"}, 400)
    try:
        paths = export_investigation_notebook(ws_root, slug)
    except FileNotFoundError:
        raise DownloadError({"error": f"no investigation {slug!r}"}, 404)
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the server
        raise DownloadError({"error": f"notebook export failed: {exc}"}, 500)
    path = paths["py"] if fmt == "py" else paths["ipynb"]
    mime = "text/x-python" if fmt == "py" else "application/x-ipynb+json"
    data = path.read_bytes()
    return data, mime, path.name
