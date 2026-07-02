"""Read embedded PDF metadata (Title, Author, CreationDate) for auto-generated BibTeX.

v0.1.12: used by /api/reference-pdf to extract metadata from the dropped PDF so
the user doesn't have to type anything.
"""
from __future__ import annotations
from datetime import datetime
import re
import io

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # graceful fallback


def extract_pdf_metadata(pdf_bytes: bytes) -> dict:
    """Extract title, authors, year from the PDF's embedded metadata fields.

    Returns a dict with keys (all optional / may be empty):
      title: str
      authors: list[str]   (semicolon-or-comma-or-and-split)
      year: int | None
      raw: dict             (the unparsed PDF metadata, for debugging)
      error: str            (only present if extraction failed)
    """
    if PdfReader is None:
        return {"title": "", "authors": [], "year": None, "raw": {}, "error": "pypdf not installed"}
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        info = reader.metadata or {}
    except Exception as e:
        return {"title": "", "authors": [], "year": None, "raw": {}, "error": str(e)}

    title = (info.get("/Title") or "").strip()
    author_raw = (info.get("/Author") or "").strip()
    creation_date = info.get("/CreationDate") or info.get("/ModDate") or ""

    authors = _split_authors(author_raw) if author_raw else []
    year = _extract_year(str(creation_date)) or _extract_year(str(info.get("/CreationDate") or ""))

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "raw": {k: str(v) for k, v in info.items()},
    }


def _split_authors(s: str) -> list[str]:
    """Split an author string into a list. Handles semicolons and ' and '."""
    if ";" in s:
        parts = [p.strip() for p in s.split(";")]
    elif " and " in s:
        parts = [p.strip() for p in s.split(" and ")]
    else:
        # Treat the whole string as one author entry.
        parts = [s.strip()]
    return [p for p in parts if p]


def _extract_year(s: str) -> int | None:
    """Pull a 4-digit year (1900-2099) from a string.

    PDF dates look like 'D:20240115120000+00'00''.
    """
    if not s:
        return None
    m = re.search(r"(19\d{2}|20\d{2})", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def auto_bib_key(authors: list[str], year: int | None, fallback_prefix: str = "_pending") -> str:
    """Generate a BibTeX key: <FirstAuthorSurname><Year>, fallback if missing."""
    if not authors or not year:
        return f"{fallback_prefix}_{int(datetime.now().timestamp())}"
    first = authors[0]
    # Prefer surname: text before first comma (e.g. "Smith, J.") or last word.
    if "," in first:
        surname = first.split(",", 1)[0].strip()
    else:
        surname = first.strip().split()[-1]
    surname = re.sub(r"[^A-Za-z0-9]", "", surname)
    if not surname:
        return f"{fallback_prefix}_{int(datetime.now().timestamp())}"
    return f"{surname}{year}"


def build_bibtex(bib_key: str, title: str, authors: list[str], year: int | None,
                 journal: str | None = None, doi: str | None = None) -> str:
    """Generate a minimal @article BibTeX entry from extracted/typed metadata."""
    author_str = " and ".join(authors) if authors else ""
    lines = [f"@article{{{bib_key},"]
    if title:
        lines.append(f"  title = {{{_escape(title)}}},")
    if author_str:
        lines.append(f"  author = {{{_escape(author_str)}}},")
    if year:
        lines.append(f"  year = {{{int(year)}}},")
    if journal:
        lines.append(f"  journal = {{{_escape(journal)}}},")
    if doi:
        lines.append(f"  doi = {{{_escape(doi)}}},")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _escape(s: str) -> str:
    return s.replace("{", "\\{").replace("}", "\\}")
