"""Tiny BibTeX entry generator. No external deps."""
from __future__ import annotations


def render_bibtex(bib_key: str, title: str, authors: str, year: int,
                  journal: str | None = None, doi: str | None = None) -> str:
    """Render a minimal @article BibTeX entry from typed metadata.

    authors: semicolon-separated input ('Smith, J.; Doe, J.') -> BibTeX
             ' and '-joined ('Smith, J. and Doe, J.')
    """
    author_list = [a.strip() for a in authors.split(";") if a.strip()]
    author_str = " and ".join(author_list)
    lines = [
        f"@article{{{bib_key},",
        f"  title = {{{_escape(title)}}},",
        f"  author = {{{_escape(author_str)}}},",
        f"  year = {{{int(year)}}},",
    ]
    if journal:
        lines.append(f"  journal = {{{_escape(journal)}}},")
    if doi:
        lines.append(f"  doi = {{{_escape(doi)}}},")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _escape(s: str) -> str:
    """Escape characters that break BibTeX braces."""
    return s.replace("{", "\\{").replace("}", "\\}")
