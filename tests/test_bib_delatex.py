"""References BibTeX display: LaTeX-accent → Unicode, and that a \\" umlaut inside
a brace value does not bleed later fields into the author (report._parse_bib_entries)."""
from pathlib import Path

import yaml

from vivarium_workbench.lib.report import _delatex, _parse_bib_entries


def test_delatex_accents_and_special_letters():
    assert _delatex(r"Wo\los") == "Wołos"
    assert _delatex(r"Rafa\l") == "Rafał"
    assert _delatex(r"Szathm\'ary") == "Szathmáry"
    assert _delatex(r'E\"ors') == "Eörs"
    assert _delatex(r'D\"orr') == "Dörr"
    assert _delatex(r"K\"onig") == "König"
    assert _delatex(r"{\.Z}\k{a}d\lo") == "{Żąd" + "ło"  # ł via special
    assert _delatex("plain text, no escapes") == "plain text, no escapes"
    assert _delatex("https://doi.org/10.1/x") == "https://doi.org/10.1/x"


def _ws(tmp_path: Path, bib: str) -> Path:
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump({"name": "t", "schema_version": 2}))
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "papers.bib").write_text(bib)
    return tmp_path


def test_umlaut_in_braces_does_not_bleed_following_fields(tmp_path):
    bib = (
        "@article{vasas2010lack,\n"
        "  author  = {Vasas, Vera and Szathm{\\'a}ry, E{\\\"o}rs and Santos, Mauro},\n"
        "  journal = {Proceedings of the National Academy of Sciences},\n"
        "  year    = {2010},\n"
        "}\n"
    )
    (e,) = _parse_bib_entries(_ws(tmp_path, bib))
    assert e["author"] == "Vasas, Vera and Szathmáry, Eörs and Santos, Mauro"
    assert e["journal"] == "Proceedings of the National Academy of Sciences"
    assert e["year"] == "2010"
    assert "journal" not in e["author"] and "\\" not in e["author"]


def test_polish_accents_parse_clean(tmp_path):
    bib = (
        "@article{wolos2020synthetic,\n"
        "  author  = {Wo{\\l}os, Agnieszka and Roszak, Rafa{\\l} and Dygas, Miros{\\l}aw},\n"
        "  journal = {Science},\n"
        "}\n"
    )
    (e,) = _parse_bib_entries(_ws(tmp_path, bib))
    assert e["author"] == "Wołos, Agnieszka and Roszak, Rafał and Dygas, Mirosław"
    assert e["journal"] == "Science"
