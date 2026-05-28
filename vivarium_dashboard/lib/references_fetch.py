"""Enrich bib entries with DOI / publisher URL / open-access PDF links.

Two outbound APIs are called, both with stdlib ``urllib`` (no extra deps):

- **Crossref** (``https://api.crossref.org/works``) — title/author/year lookup
  to fill in a missing DOI. Polite-pool requested via ``mailto`` param.
- **Unpaywall** (``https://api.unpaywall.org/v2/<doi>``) — DOI lookup that
  returns whether an open-access PDF is available and its URL. Required
  ``email`` query param.

Enrichment data is persisted to ``references/.cache.json`` under the
workspace root so the dashboard doesn't re-query on every request. The
cache is gitignored — it's a derived artifact, not a source of truth.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# Default contact email for Crossref polite-pool + Unpaywall. The dashboard
# overrides via workspace.yaml maintainer_email / git config user.email.
_DEFAULT_EMAIL = "no-reply@vivarium-dashboard.local"
_USER_AGENT = "vivarium-dashboard-references-fetch/1.0"

# Polite-pool throttling between bulk requests. Crossref allows ~50/s for
# unauthenticated polite traffic; we go well under that to be a good citizen.
_BULK_DELAY_S = 0.5

_CACHE_FILENAME = ".cache.json"


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass
class EnrichmentRecord:
    """Per-bib-key enrichment data persisted in references/.cache.json."""
    doi: Optional[str] = None
    publisher_url: Optional[str] = None       # canonical website (DOI redirect or original URL)
    oa_pdf_url: Optional[str] = None          # Unpaywall best-OA-location URL
    oa_status: Optional[str] = None           # "gold" / "green" / "bronze" / "closed" / None
    fetched_at: Optional[float] = None        # epoch seconds; None == never fetched
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Email resolution
# ---------------------------------------------------------------------------


def resolve_contact_email(ws_root: Path) -> str:
    """Pick the email used for Crossref polite-pool + Unpaywall.

    Order: workspace.yaml maintainer_email -> git config user.email ->
    default placeholder. Both Crossref and Unpaywall require *some* email;
    a bad one just downgrades request priority, it doesn't fail.
    """
    ws_file = ws_root / "workspace.yaml"
    if ws_file.exists():
        try:
            import yaml  # local import; the dashboard imports yaml elsewhere
            data = yaml.safe_load(ws_file.read_text(encoding="utf-8")) or {}
            email = data.get("maintainer_email")
            if isinstance(email, str) and "@" in email:
                return email
        except Exception:
            pass
    try:
        r = subprocess.run(
            ["git", "config", "user.email"],
            cwd=ws_root, capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            email = r.stdout.strip()
            if "@" in email:
                return email
    except (OSError, subprocess.SubprocessError):
        pass
    return _DEFAULT_EMAIL


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------


def _cache_path(ws_root: Path) -> Path:
    return ws_root / "references" / _CACHE_FILENAME


def load_cache(ws_root: Path) -> dict[str, EnrichmentRecord]:
    """Return {bib_key: EnrichmentRecord}; empty dict if file is missing."""
    p = _cache_path(ws_root)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return {}
    return {k: EnrichmentRecord(**v) for k, v in raw.items() if isinstance(v, dict)}


def save_cache(ws_root: Path, cache: dict[str, EnrichmentRecord]) -> None:
    p = _cache_path(ws_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Drop a local .gitignore so the cache file never gets committed even if
    # the workspace's root .gitignore doesn't know about us.
    gi = p.parent / ".gitignore"
    if not gi.exists():
        gi.write_text(f"{_CACHE_FILENAME}\n")
    serializable = {k: asdict(v) for k, v in cache.items()}
    p.write_text(json.dumps(serializable, indent=2, sort_keys=True) + "\n")


def enrich_entries(entries: list[dict], cache: dict[str, EnrichmentRecord]) -> list[dict]:
    """Merge enrichment data into the bib entries returned by /api/references-bib.

    Mutates each entry in place with: ``enriched_doi``, ``publisher_url``,
    ``oa_pdf_url``, ``oa_status``, ``enrichment_fetched_at``,
    ``enrichment_pending`` (True when entry has neither DOI nor a fetch attempt).
    The original ``doi``/``url`` fields parsed from papers.bib are left untouched.
    """
    for e in entries:
        key = e.get("key")
        record = cache.get(key) if key else None
        if record is None:
            # Pending if there's no DOI and no cached attempt.
            e["enrichment_pending"] = not e.get("doi")
            continue
        e["enriched_doi"] = record.doi or e.get("doi")
        e["publisher_url"] = record.publisher_url
        e["oa_pdf_url"] = record.oa_pdf_url
        e["oa_status"] = record.oa_status
        e["enrichment_fetched_at"] = record.fetched_at
        e["enrichment_errors"] = list(record.errors)
        e["enrichment_pending"] = False
    return entries


# ---------------------------------------------------------------------------
# Outbound HTTP
# ---------------------------------------------------------------------------


class FetchError(Exception):
    pass


def _http_get_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}: {e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(f"network: {e}") from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise FetchError(f"bad JSON: {e}") from None


def _normalize_author(author: str) -> str:
    """First listed author surname only — Crossref's bibliographic query
    matches single-surname queries better than full author lists."""
    if not author:
        return ""
    # Strip {} groups and pick first comma-separated entry, then first word.
    cleaned = re.sub(r"[{}]", "", author).strip()
    first = cleaned.split(" and ")[0].split(",")[0].strip()
    return first.split()[0] if first else ""


def fetch_crossref_doi(entry: dict, email: str, timeout: float = 10.0) -> Optional[str]:
    """Query Crossref for an entry's DOI by title + first-author + year.

    Returns the DOI string on a confident match, else None. "Confident"
    means Crossref's score >= 50 AND the year matches (when both sides have one).
    Crossref's score is 0-100; <50 typically means the top hit is a wrong paper.
    """
    title = (entry.get("title") or "").strip()
    if not title:
        return None
    params = {
        "query.bibliographic": title,
        "rows": "5",
        "mailto": email,
    }
    author_surname = _normalize_author(entry.get("author") or "")
    if author_surname:
        params["query.author"] = author_surname
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = _http_get_json(url, timeout=timeout)
    items = (data.get("message") or {}).get("items") or []
    expected_year = str(entry.get("year") or "").strip()
    for item in items:
        score = float(item.get("score", 0))
        if score < 50:
            continue
        if expected_year:
            issued = (item.get("issued") or {}).get("date-parts") or [[]]
            year = str(issued[0][0]) if issued and issued[0] else ""
            if year and year != expected_year:
                continue
        doi = item.get("DOI")
        if isinstance(doi, str) and doi:
            return doi
    return None


def fetch_unpaywall(doi: str, email: str, timeout: float = 10.0) -> dict:
    """Query Unpaywall for OA status + best-OA-location URL.

    Returns ``{oa_pdf_url, oa_status, publisher_url}``; values may be None when
    no OA copy is available or the DOI isn't indexed. Raises FetchError on
    network/HTTP failure (so the caller can record it).
    """
    if not doi:
        return {"oa_pdf_url": None, "oa_status": None, "publisher_url": None}
    safe_doi = urllib.parse.quote(doi, safe="/")
    url = f"https://api.unpaywall.org/v2/{safe_doi}?email={urllib.parse.quote(email)}"
    data = _http_get_json(url, timeout=timeout)
    best = data.get("best_oa_location") or {}
    return {
        "oa_pdf_url": best.get("url_for_pdf") or best.get("url"),
        "oa_status": data.get("oa_status"),
        "publisher_url": data.get("doi_url"),  # canonical https://doi.org/<doi>
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def fetch_one(entry: dict, email: str, timeout: float = 10.0) -> EnrichmentRecord:
    """Fetch DOI (if missing) then Unpaywall enrichment for a single entry.

    Always returns an EnrichmentRecord — errors are stored on the record,
    not raised, so callers can persist partial progress.
    """
    record = EnrichmentRecord(fetched_at=time.time())
    doi = (entry.get("doi") or "").strip()
    if not doi:
        try:
            found = fetch_crossref_doi(entry, email=email, timeout=timeout)
            if found:
                doi = found
                record.doi = found
        except FetchError as e:
            record.errors.append(f"crossref: {e}")
    else:
        record.doi = doi
    if doi:
        try:
            up = fetch_unpaywall(doi, email=email, timeout=timeout)
            record.oa_pdf_url = up.get("oa_pdf_url")
            record.oa_status = up.get("oa_status")
            record.publisher_url = up.get("publisher_url") or f"https://doi.org/{doi}"
        except FetchError as e:
            record.errors.append(f"unpaywall: {e}")
            # Even without Unpaywall, surface the canonical DOI link.
            record.publisher_url = f"https://doi.org/{doi}"
    return record


def fetch_missing(
    entries: list[dict], ws_root: Path, *, only_key: Optional[str] = None,
    email: Optional[str] = None, force: bool = False,
) -> dict[str, EnrichmentRecord]:
    """Iterate over entries, fetch + cache enrichment for those missing it.

    ``only_key``: restrict to a single bib key (per-entry button path).
    ``force``: re-fetch even if cache already has a record for the key.
    Returns the updated full cache so callers can return it as JSON.
    """
    cache = load_cache(ws_root)
    email = email or resolve_contact_email(ws_root)
    targets = [e for e in entries if e.get("key")]
    if only_key:
        targets = [e for e in targets if e.get("key") == only_key]
    elif not force:
        # Only fetch entries that don't already have a cached record AND
        # are missing both DOI and a cached enrichment. With DOI present and
        # no cache entry we still fetch (to get Unpaywall OA info).
        targets = [e for e in targets if e["key"] not in cache]

    for i, e in enumerate(targets):
        if i > 0:
            time.sleep(_BULK_DELAY_S)
        cache[e["key"]] = fetch_one(e, email=email)
    save_cache(ws_root, cache)
    return cache
