"""Tests for vivarium_workbench.lib.references_fetch.

Outbound HTTP is mocked at the urlopen layer so the test suite doesn't
depend on crossref.org / unpaywall.org being reachable.
"""
import io
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from vivarium_workbench.lib import references_fetch as rf


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "references").mkdir()
    return tmp_path


def _http_response(payload: dict) -> io.BytesIO:
    """Build a fake urlopen() return value (a file-like with .read())."""
    buf = io.BytesIO(json.dumps(payload).encode("utf-8"))
    return buf


@contextmanager
def _mock_urlopen(payloads):
    """payloads: list of dicts (each call returns the next)."""
    calls = []

    class _CM:
        def __init__(self, payload): self._payload = payload
        def __enter__(self): return _http_response(self._payload)
        def __exit__(self, *args): return False

    def fake(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        payload = payloads.pop(0) if payloads else {}
        calls.append({"url": url, "payload": payload})
        return _CM(payload)

    with patch("urllib.request.urlopen", side_effect=fake):
        yield calls


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------


def test_load_cache_empty_when_missing(ws):
    assert rf.load_cache(ws) == {}


def test_save_then_load_cache_roundtrip(ws):
    cache = {"Foo2024": rf.EnrichmentRecord(doi="10.1/x", oa_pdf_url="https://e/p.pdf")}
    rf.save_cache(ws, cache)
    loaded = rf.load_cache(ws)
    assert loaded["Foo2024"].doi == "10.1/x"
    assert loaded["Foo2024"].oa_pdf_url == "https://e/p.pdf"


def test_save_cache_creates_gitignore(ws):
    rf.save_cache(ws, {"Foo": rf.EnrichmentRecord()})
    assert (ws / "references" / ".gitignore").read_text() == ".cache.json\n"


def test_load_cache_tolerates_garbage(ws):
    (ws / "references" / ".cache.json").write_text("not json {")
    assert rf.load_cache(ws) == {}


# ---------------------------------------------------------------------------
# enrich_entries
# ---------------------------------------------------------------------------


def test_enrich_marks_pending_when_no_doi_and_no_cache():
    entries = [{"key": "Foo2024", "title": "x"}]
    out = rf.enrich_entries(entries, {})
    assert out[0]["enrichment_pending"] is True


def test_enrich_not_pending_when_entry_has_doi_even_without_cache():
    entries = [{"key": "Foo2024", "title": "x", "doi": "10.1/x"}]
    out = rf.enrich_entries(entries, {})
    assert out[0]["enrichment_pending"] is False


def test_enrich_merges_cache_fields():
    entries = [{"key": "Foo2024", "title": "x"}]
    cache = {"Foo2024": rf.EnrichmentRecord(
        doi="10.1/x", publisher_url="https://doi.org/10.1/x",
        oa_pdf_url="https://e/p.pdf", oa_status="green", fetched_at=1.0,
    )}
    out = rf.enrich_entries(entries, cache)
    assert out[0]["enriched_doi"] == "10.1/x"
    assert out[0]["oa_pdf_url"] == "https://e/p.pdf"
    assert out[0]["oa_status"] == "green"
    assert out[0]["enrichment_fetched_at"] == 1.0
    assert out[0]["enrichment_pending"] is False


# ---------------------------------------------------------------------------
# Crossref lookup
# ---------------------------------------------------------------------------


def test_crossref_returns_doi_on_confident_match():
    entry = {"title": "DnaA initiation", "author": "Katayama, T", "year": "2017"}
    with _mock_urlopen([{
        "message": {"items": [
            {"score": 92.5, "DOI": "10.1234/found",
             "issued": {"date-parts": [[2017]]}},
        ]},
    }]):
        doi = rf.fetch_crossref_doi(entry, email="t@t")
    assert doi == "10.1234/found"


def test_crossref_skips_low_score_hits():
    entry = {"title": "DnaA initiation", "author": "Katayama", "year": "2017"}
    with _mock_urlopen([{
        "message": {"items": [
            {"score": 25.0, "DOI": "10.1234/low", "issued": {"date-parts": [[2017]]}},
        ]},
    }]):
        assert rf.fetch_crossref_doi(entry, email="t@t") is None


def test_crossref_skips_year_mismatch():
    entry = {"title": "DnaA initiation", "author": "Katayama", "year": "2017"}
    with _mock_urlopen([{
        "message": {"items": [
            {"score": 90.0, "DOI": "10.1234/wrong-year",
             "issued": {"date-parts": [[2024]]}},
        ]},
    }]):
        assert rf.fetch_crossref_doi(entry, email="t@t") is None


# ---------------------------------------------------------------------------
# Unpaywall lookup
# ---------------------------------------------------------------------------


def test_unpaywall_returns_oa_pdf_when_available():
    with _mock_urlopen([{
        "oa_status": "gold",
        "doi_url": "https://doi.org/10.1/x",
        "best_oa_location": {"url_for_pdf": "https://repo/p.pdf"},
    }]):
        out = rf.fetch_unpaywall("10.1/x", email="t@t")
    assert out["oa_pdf_url"] == "https://repo/p.pdf"
    assert out["oa_status"] == "gold"
    assert out["publisher_url"] == "https://doi.org/10.1/x"


def test_unpaywall_returns_none_for_paywalled():
    with _mock_urlopen([{
        "oa_status": "closed",
        "doi_url": "https://doi.org/10.1/x",
        "best_oa_location": None,
    }]):
        out = rf.fetch_unpaywall("10.1/x", email="t@t")
    assert out["oa_pdf_url"] is None
    assert out["oa_status"] == "closed"


# ---------------------------------------------------------------------------
# Orchestration: fetch_one + fetch_missing
# ---------------------------------------------------------------------------


def test_fetch_one_full_path_crossref_then_unpaywall():
    entry = {"key": "Foo2024", "title": "X", "author": "Bar", "year": "2024"}
    with _mock_urlopen([
        {"message": {"items": [
            {"score": 80, "DOI": "10.1/x", "issued": {"date-parts": [[2024]]}}]}},
        {"oa_status": "green", "doi_url": "https://doi.org/10.1/x",
         "best_oa_location": {"url_for_pdf": "https://e/p.pdf"}},
    ]):
        rec = rf.fetch_one(entry, email="t@t")
    assert rec.doi == "10.1/x"
    assert rec.oa_pdf_url == "https://e/p.pdf"
    assert rec.oa_status == "green"
    assert rec.fetched_at is not None
    assert rec.errors == []


def test_fetch_one_skips_crossref_when_doi_present():
    entry = {"key": "Foo2024", "title": "X", "doi": "10.1/x"}
    with _mock_urlopen([
        # Only Unpaywall is queried.
        {"oa_status": "bronze", "doi_url": "https://doi.org/10.1/x",
         "best_oa_location": {"url_for_pdf": "https://e/p.pdf"}},
    ]) as calls:
        rec = rf.fetch_one(entry, email="t@t")
    assert len(calls) == 1
    assert "unpaywall" in calls[0]["url"]
    assert rec.doi == "10.1/x"
    assert rec.oa_pdf_url == "https://e/p.pdf"


def test_fetch_one_records_error_on_crossref_failure():
    entry = {"key": "Foo2024", "title": "X", "author": "Bar", "year": "2024"}

    def boom(req, timeout=None):
        raise rf.urllib.error.URLError("dns")

    with patch("urllib.request.urlopen", side_effect=boom):
        rec = rf.fetch_one(entry, email="t@t")
    assert rec.doi is None
    assert rec.oa_pdf_url is None
    assert any("crossref" in e for e in rec.errors)


def test_fetch_missing_only_targets_uncached(ws, monkeypatch):
    entries = [
        {"key": "Have", "title": "x", "doi": "10.1/have"},
        {"key": "Cached", "title": "x"},
        {"key": "New", "title": "x"},
    ]
    # Pre-seed cache: Cached already fetched, others not.
    rf.save_cache(ws, {"Cached": rf.EnrichmentRecord(doi="10.1/c", fetched_at=1)})

    # Patch fetch_one to count invocations + return a stub.
    seen = []
    monkeypatch.setattr(rf, "fetch_one",
                        lambda entry, email, timeout=10.0: seen.append(entry["key"]) or rf.EnrichmentRecord(doi="10.1/stub", fetched_at=2))
    # Patch sleep so tests are fast.
    monkeypatch.setattr(rf.time, "sleep", lambda *_: None)

    rf.fetch_missing(entries, ws, email="t@t")
    assert sorted(seen) == ["Have", "New"]  # "Cached" skipped


def test_fetch_missing_force_refetches_all(ws, monkeypatch):
    entries = [{"key": "Foo", "title": "x", "doi": "10.1/x"}]
    rf.save_cache(ws, {"Foo": rf.EnrichmentRecord(doi="10.1/old", fetched_at=1)})
    monkeypatch.setattr(rf, "fetch_one",
                        lambda entry, email, timeout=10.0: rf.EnrichmentRecord(doi="10.1/new", fetched_at=2))
    monkeypatch.setattr(rf.time, "sleep", lambda *_: None)

    cache = rf.fetch_missing(entries, ws, email="t@t", force=True)
    assert cache["Foo"].doi == "10.1/new"


def test_fetch_missing_only_key_restricts_to_one(ws, monkeypatch):
    entries = [{"key": "A", "title": "x"}, {"key": "B", "title": "x"}]
    seen = []
    monkeypatch.setattr(rf, "fetch_one",
                        lambda entry, email, timeout=10.0: seen.append(entry["key"]) or rf.EnrichmentRecord(fetched_at=1))
    monkeypatch.setattr(rf.time, "sleep", lambda *_: None)

    rf.fetch_missing(entries, ws, email="t@t", only_key="B")
    assert seen == ["B"]


# ---------------------------------------------------------------------------
# Email resolution
# ---------------------------------------------------------------------------


def test_resolve_email_prefers_workspace_yaml(ws):
    (ws / "workspace.yaml").write_text("maintainer_email: me@lab.org\n")
    assert rf.resolve_contact_email(ws) == "me@lab.org"


def test_resolve_email_falls_back_to_default_when_unset(ws):
    (ws / "workspace.yaml").write_text("name: ws\n")
    # In CI git config might be set; just check it returns *something* with an @.
    got = rf.resolve_contact_email(ws)
    assert "@" in got
