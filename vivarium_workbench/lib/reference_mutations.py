"""Reference POST mutation builders (PDF-first + BibTeX-paste flows).

Two endpoints add a paper reference (bib entry + optional PDF + claims.yaml +
investigation registration) to the workspace:

    (ws_root: Path, body: dict) -> tuple[dict, int]

File side-effects only — no HTTP, no server imports, no git operations.

Routes covered:
  - POST /api/reference-pdf  → ``register_reference_pdf``: drop-and-go PDF flow
                               (pypdf metadata extraction + bib append +
                               workspace.yaml ``references_pdfs`` + investigation
                               register + claims.yaml merge), augmenting the
                               response with ``bib_key`` / ``metadata_pending`` /
                               ``extracted``.
  - POST /api/reference-bibtex  (alias POST /api/reference)
                             → ``register_reference``: BibTeX-paste flow (append
                               if new, investigation-scoped key reuse, claims.yaml
                               merge, optional PDF save).

Each ``register_*`` builder is the FULL flow (validation + mutation) that the
FastAPI route calls directly.  The git-committing legacy server keeps its
``_active_branch_action`` wrapper and delegates ONLY the mutation to the private
``_apply_*`` functions here — so the live shim's pre/post-wrapper sections stay
byte-identical to before this batch.  ``_apply_*`` raises ``ValueError`` on a
papers.bib key conflict or a missing investigation (the live wrapper already
catches it → HTTP 500; the FastAPI ``register_*`` path maps it to 409 / 404).

Batch 26 of the FastAPI strangler-fig migration (POST phase, Phase C).
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

import yaml

from vivarium_workbench.lib.pdf_metadata import (
    auto_bib_key,
    build_bibtex,
    extract_pdf_metadata,
)
from vivarium_workbench.lib.study_spec import SLUG_RE as _SLUG_RE
from vivarium_workbench.lib.upload_mutations import (
    _append_investigation_input,
    _save_upload,
    _ws_add_to_sys_path,
)
from vivarium_workbench.lib.workspace_paths import WorkspacePaths
from vivarium_workbench.lib.workspace_yaml import load_workspace, save_workspace


# ---------------------------------------------------------------------------
# register_reference_pdf  (POST /api/reference-pdf)
# ---------------------------------------------------------------------------


def register_reference_pdf(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/reference-pdf — drop-and-go PDF reference flow.

    Body:
      {pdf_b64, title?, authors?, year?, journal?, doi?, bib_key?,
       investigation?, claim_mappings?}

    Extracts metadata from the PDF (pypdf), derives bib fields from the body or
    the extraction, saves the PDF + appends the BibTeX entry, registers the PDF
    in ``workspace.yaml.references_pdfs``, optionally registers the bare key in
    an investigation, and merges claim mappings into ``claims.yaml``.

    Returns:
      200  {ok: True, bib_key, metadata_pending, extracted}
      400  validation (pdf_b64 / investigation slug / bib_key)
      404  investigation not found
      409  BibTeX key already exists in papers.bib
    """
    pdf_b64 = (body.get("pdf_b64") or "").strip()
    if not pdf_b64:
        return {"error": "pdf_b64 is required"}, 400

    raw_pdf = base64.b64decode(pdf_b64)
    extracted = extract_pdf_metadata(raw_pdf)

    investigation = (body.get("investigation") or "").strip()
    if investigation and not _SLUG_RE.match(investigation):
        return {"error": f"invalid investigation slug: '{investigation}'"}, 400

    title = (body.get("title") or "").strip() or extracted.get("title", "")
    authors_input = (body.get("authors") or "").strip()
    if authors_input:
        authors = [a.strip() for a in re.split(r"[;|]| and ", authors_input) if a.strip()]
    else:
        authors = extracted.get("authors", [])
    year_raw = body.get("year")
    if year_raw is not None:
        try:
            year: "int | None" = int(year_raw)
        except (ValueError, TypeError):
            year = extracted.get("year")
    else:
        year = extracted.get("year")
    journal = (body.get("journal") or "").strip() or None
    doi = (body.get("doi") or "").strip() or None

    bib_key = (body.get("bib_key") or "").strip()
    if not bib_key:
        bib_key = auto_bib_key(authors, year)
    if not re.match(r"^[A-Za-z0-9_:\-]+$", bib_key):
        return {"error": f"invalid bib_key: '{bib_key}'"}, 400

    metadata_pending = (
        not title or not authors or not year or bib_key.startswith("_pending")
    )

    claim_mappings_raw = body.get("claim_mappings", [])
    if isinstance(claim_mappings_raw, str):
        claim_ids: list[str] = [c.strip() for c in claim_mappings_raw.split(",") if c.strip()]
    elif isinstance(claim_mappings_raw, list):
        claim_ids = [str(c).strip() for c in claim_mappings_raw if str(c).strip()]
    else:
        claim_ids = []

    try:
        _apply_reference_pdf(
            ws_root,
            bib_key=bib_key,
            title=title,
            authors=authors,
            year=year,
            journal=journal,
            doi=doi,
            investigation=investigation,
            claim_ids=claim_ids,
            metadata_pending=metadata_pending,
            pdf_b64=pdf_b64,
        )
    except ValueError as exc:
        msg = str(exc)
        return {"error": msg}, (404 if "not found" in msg else 409)

    response: dict = {"ok": True}
    response["bib_key"] = bib_key
    response["metadata_pending"] = metadata_pending
    response["extracted"] = {k: v for k, v in extracted.items() if k != "raw"}
    return response, 200


def _apply_reference_pdf(
    ws_root: Path,
    *,
    bib_key: str,
    title: str,
    authors: list,
    year: "int | None",
    journal: "str | None",
    doi: "str | None",
    investigation: str,
    claim_ids: list,
    metadata_pending: bool,
    pdf_b64: str,
) -> None:
    """Mutation for the PDF reference flow (formerly the server action() body).

    Raises ValueError on a papers.bib key conflict or a missing investigation.
    """
    refs_dir = WorkspacePaths.load(ws_root).references
    bib_file = refs_dir / "papers.bib"
    claims_file = refs_dir / "claims.yaml"
    if investigation:
        pdf_dest_rel = f"investigations/{investigation}/inputs/references/{bib_key}.pdf"
    else:
        pdf_dest_rel = f"references/papers/{bib_key}.pdf"
    pdf_dest = ws_root / pdf_dest_rel

    if bib_file.exists():
        existing_text = bib_file.read_text(encoding="utf-8")
        if re.search(rf"@\w+\{{{re.escape(bib_key)},", existing_text):
            raise ValueError(f"BibTeX key '{bib_key}' already exists in papers.bib")

    sha = _save_upload(pdf_b64, pdf_dest)

    bibtex_entry = build_bibtex(bib_key, title, authors, year, journal, doi)
    bib_file.parent.mkdir(parents=True, exist_ok=True)
    existing_bib = bib_file.read_text(encoding="utf-8") if bib_file.exists() else ""
    with bib_file.open("a") as f:
        if existing_bib and not existing_bib.endswith("\n"):
            f.write("\n")
        f.write(bibtex_entry + "\n")

    _ws_add_to_sys_path(ws_root)
    ws_file = ws_root / "workspace.yaml"
    ws = load_workspace(ws_file)
    refs_pdfs = ws.setdefault("references_pdfs", [])
    if refs_pdfs is None:
        refs_pdfs = []
        ws["references_pdfs"] = refs_pdfs
    if not any(e.get("bib_key") == bib_key for e in refs_pdfs):
        entry: dict = {"bib_key": bib_key, "path": pdf_dest_rel, "sha256": sha}
        if metadata_pending:
            entry["_metadata_pending"] = True
        refs_pdfs.append(entry)
    save_workspace(ws_file, ws)

    if investigation:
        if not _append_investigation_input(ws_root, investigation, "references", bib_key):
            raise ValueError(f"investigation '{investigation}' not found")

    if claim_ids:
        existing_claims: dict = {}
        if claims_file.exists():
            try:
                existing_claims = yaml.safe_load(claims_file.read_text(encoding="utf-8")) or {}
            except Exception:
                existing_claims = {}
        for claim_id in claim_ids:
            existing_claims.setdefault(claim_id, [])
            if bib_key not in existing_claims[claim_id]:
                existing_claims[claim_id].append(bib_key)
        claims_file.parent.mkdir(parents=True, exist_ok=True)
        claims_file.write_text(yaml.safe_dump(existing_claims, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# register_reference  (POST /api/reference-bibtex  AND  POST /api/reference)
# ---------------------------------------------------------------------------


def register_reference(ws_root: Path, body: dict[str, Any]) -> "tuple[dict, int]":
    """POST /api/reference-bibtex (alias /api/reference) — BibTeX-paste flow.

    Body:
      {bibtex_text, claim_mappings?, pdf_b64?, investigation?}

    Parses the bib key from the pasted entry, appends it to ``papers.bib`` (an
    investigation-scoped request MAY reuse an existing key; the global flow
    treats a duplicate as a conflict), optionally registers the bare key in an
    investigation, merges claim mappings into ``claims.yaml``, and optionally
    saves a PDF + registers it in ``workspace.yaml.references_pdfs``.

    Returns:
      200  {ok: True}
      400  validation (bibtex_text / unparseable key / investigation slug)
      404  investigation not found
      409  BibTeX key already exists in papers.bib (global flow only)
    """
    bibtex_text = (body.get("bibtex_text") or "").strip()
    claim_mappings_raw = body.get("claim_mappings", {})
    pdf_b64 = (body.get("pdf_b64") or "").strip()

    if not bibtex_text:
        return {"error": "bibtex_text is required"}, 400

    m = re.search(r"@\w+\{([^,\s]+)", bibtex_text)
    if not m:
        return {"error": "could not parse BibTeX key from bibtex_text"}, 400
    bibkey = m.group(1).strip()

    investigation = (body.get("investigation") or "").strip()
    if investigation and not _SLUG_RE.match(investigation):
        return {"error": f"invalid investigation slug: '{investigation}'"}, 400

    if isinstance(claim_mappings_raw, str):
        claim_mappings: dict = {}
        for pair in claim_mappings_raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                cid, bkey = pair.split(":", 1)
                claim_mappings[cid.strip()] = bkey.strip()
    else:
        claim_mappings = dict(claim_mappings_raw) if claim_mappings_raw else {}

    try:
        _apply_reference(
            ws_root,
            bibkey=bibkey,
            bibtex_text=bibtex_text,
            investigation=investigation,
            claim_mappings=claim_mappings,
            pdf_b64=pdf_b64,
        )
    except ValueError as exc:
        msg = str(exc)
        return {"error": msg}, (404 if "not found" in msg else 409)

    return {"ok": True}, 200


def _apply_reference(
    ws_root: Path,
    *,
    bibkey: str,
    bibtex_text: str,
    investigation: str,
    claim_mappings: dict,
    pdf_b64: str,
) -> None:
    """Mutation for the BibTeX-paste reference flow (formerly the action() body).

    Raises ValueError on a papers.bib key conflict (global flow) or a missing
    investigation.
    """
    refs_dir = WorkspacePaths.load(ws_root).references
    bib_file = refs_dir / "papers.bib"
    claims_file = refs_dir / "claims.yaml"

    already_in_bib = False
    if bib_file.exists():
        existing_text = bib_file.read_text(encoding="utf-8")
        if f"{{{bibkey}," in existing_text or f"{{{bibkey} " in existing_text:
            already_in_bib = True
            # Investigation-scoped references may reuse an existing key
            # (just add the bare key to the investigation block); the
            # global flow still treats a duplicate as an error.
            if not investigation:
                raise ValueError(f"BibTeX key '{bibkey}' already exists in papers.bib")

    if not already_in_bib:
        bib_file.parent.mkdir(parents=True, exist_ok=True)
        with bib_file.open("a") as f:
            f.write("\n" + bibtex_text + "\n")

    if investigation:
        if not _append_investigation_input(ws_root, investigation, "references", bibkey):
            raise ValueError(f"investigation '{investigation}' not found")

    if claim_mappings:
        existing_claims: dict = {}
        if claims_file.exists():
            try:
                existing_claims = yaml.safe_load(claims_file.read_text(encoding="utf-8")) or {}
            except Exception:
                existing_claims = {}
        for claim_id, bkey in claim_mappings.items():
            existing_claims.setdefault(claim_id, [])
            if bkey not in existing_claims[claim_id]:
                existing_claims[claim_id].append(bkey)
        claims_file.parent.mkdir(parents=True, exist_ok=True)
        claims_file.write_text(yaml.safe_dump(existing_claims, sort_keys=False), encoding="utf-8")

    if pdf_b64:
        pdf_dest_rel = f"references/papers/{bibkey}.pdf"
        pdf_dest = ws_root / pdf_dest_rel
        sha = _save_upload(pdf_b64, pdf_dest)

        _ws_add_to_sys_path(ws_root)
        ws_file = ws_root / "workspace.yaml"
        ws = load_workspace(ws_file)
        refs_pdfs = ws.setdefault("references_pdfs", [])
        if refs_pdfs is None:
            refs_pdfs = []
            ws["references_pdfs"] = refs_pdfs
        if not any(e.get("bib_key") == bibkey for e in refs_pdfs):
            refs_pdfs.append({"bib_key": bibkey, "path": pdf_dest_rel, "sha256": sha})
        save_workspace(ws_file, ws)
