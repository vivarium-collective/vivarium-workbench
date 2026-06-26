"""Tests for lib.reference_mutations — reference POST pure builders.

Covers (per builder):
  - Happy paths: bib append + workspace.yaml ``references_pdfs`` + claims.yaml +
    investigation register + ``(dict, status)`` return.
  - Every 400/404/409 validation path + the papers.bib dup-conflict
    (global raises → 409; investigation-scoped reuses the key).
  - For reference-pdf, the response augmentation (``bib_key`` /
    ``metadata_pending`` / ``extracted``).

Behavioral commit-path tests: drive the REAL ``server._post_reference_pdf`` /
``server._post_reference`` handlers with ``server._active_branch_action``
monkeypatched to a recorder, asserting:
  (a) ``_active_branch_action`` IS called with the exact commit_msg (including
      the ``(metadata pending)`` suffix for reference-pdf),
  (b) validation 400 returns BEFORE the wrapper is ever called,
  (c) the inner action() re-raises on a papers.bib conflict.

``extract_pdf_metadata`` degrades gracefully on unparseable bytes (returns
empty metadata + an ``error`` key, no raise), so the tests feed fake PDF bytes
and supply the bib metadata explicitly.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
import yaml

import pbg_superpowers

from vivarium_dashboard.lib import reference_mutations as rm


_SCHEMA_SRC = Path(pbg_superpowers.__file__).parent / "schemas" / "workspace.schema.json"

_WS_YAML = """\
schema_version: 3
name: testws
created: "2026-01-01"
plugin_version: "0.14.0"
package_path: pbg_testws
datasets: []
expert_docs: []
imports: {}
"""

_INV_SLUG = "dnaa-replication"


def _make_ws(tmp_path: Path) -> Path:
    """Schema-valid workspace + an empty investigation for the scoped paths."""
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text(_WS_YAML, encoding="utf-8")
    schemas = w / ".pbg" / "schemas"
    schemas.mkdir(parents=True)
    (schemas / "workspace.schema.json").write_text(
        _SCHEMA_SRC.read_text(encoding="utf-8"), encoding="utf-8"
    )
    inv = w / "investigations" / _INV_SLUG
    (inv / "studies").mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        f"name: {_INV_SLUG}\ntitle: {_INV_SLUG}\nstudies: []\n", encoding="utf-8"
    )
    return w


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: Any) -> Path:
    """Workspace fixture; registers the workspace root so schema validation
    (load_workspace / save_workspace) resolves the bundled schema."""
    w = _make_ws(tmp_path)
    import vivarium_dashboard.lib._root as _root
    monkeypatch.setattr(_root, "_WS_ROOT", w.resolve())
    monkeypatch.setattr(_root, "_WS_PATHS", None)
    return w


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


_FAKE_PDF = _b64(b"%PDF-1.4 not really a parseable pdf")


def _read_ws(ws: Path) -> dict:
    return yaml.safe_load((ws / "workspace.yaml").read_text(encoding="utf-8"))


def _read_inv(ws: Path) -> dict:
    return yaml.safe_load(
        (ws / "investigations" / _INV_SLUG / "investigation.yaml").read_text(encoding="utf-8")
    )


def _read_bib(ws: Path) -> str:
    return (ws / "references" / "papers.bib").read_text(encoding="utf-8")


def _read_claims(ws: Path) -> dict:
    return yaml.safe_load((ws / "references" / "claims.yaml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# register_reference_pdf
# ---------------------------------------------------------------------------


class TestRegisterReferencePdf:
    def test_happy_global_full_metadata(self, ws: Path) -> None:
        resp, code = rm.register_reference_pdf(ws, {
            "pdf_b64": _FAKE_PDF, "title": "A Paper", "authors": "Smith, J.",
            "year": 2020, "bib_key": "Smith2020", "journal": "J. Bio", "doi": "10.1/x",
        })
        assert code == 200, resp
        assert resp["ok"] is True
        assert resp["bib_key"] == "Smith2020"
        assert resp["metadata_pending"] is False
        assert isinstance(resp["extracted"], dict) and "raw" not in resp["extracted"]
        # PDF written under references/papers/<key>.pdf.
        assert (ws / "references" / "papers" / "Smith2020.pdf").is_file()
        # BibTeX appended.
        assert "Smith2020" in _read_bib(ws)
        # workspace.yaml references_pdfs entry (no _metadata_pending).
        refs = _read_ws(ws)["references_pdfs"]
        assert refs[0]["bib_key"] == "Smith2020"
        assert refs[0]["path"] == "references/papers/Smith2020.pdf"
        assert len(refs[0]["sha256"]) == 64
        assert "_metadata_pending" not in refs[0]

    def test_happy_metadata_pending_auto_bib_key(self, ws: Path) -> None:
        # No metadata → auto bib_key starts with _pending, metadata_pending True.
        resp, code = rm.register_reference_pdf(ws, {"pdf_b64": _FAKE_PDF})
        assert code == 200, resp
        assert resp["metadata_pending"] is True
        assert resp["bib_key"].startswith("_pending")
        refs = _read_ws(ws)["references_pdfs"]
        assert refs[0]["_metadata_pending"] is True

    def test_happy_investigation_scoped(self, ws: Path) -> None:
        resp, code = rm.register_reference_pdf(ws, {
            "pdf_b64": _FAKE_PDF, "title": "T", "authors": "A B", "year": 2021,
            "bib_key": "AB2021", "investigation": _INV_SLUG,
        })
        assert code == 200, resp
        # PDF under the investigation inputs dir.
        assert (ws / "investigations" / _INV_SLUG / "inputs" / "references"
                / "AB2021.pdf").is_file()
        # Bare key registered in the investigation references.
        assert "AB2021" in _read_inv(ws)["inputs"]["references"]

    def test_happy_claims_merge(self, ws: Path) -> None:
        resp, code = rm.register_reference_pdf(ws, {
            "pdf_b64": _FAKE_PDF, "title": "T", "authors": "A B", "year": 2021,
            "bib_key": "AB2021", "claim_mappings": "c1,c2",
        })
        assert code == 200, resp
        claims = _read_claims(ws)
        assert "AB2021" in claims["c1"]
        assert "AB2021" in claims["c2"]

    def test_400_missing_pdf_b64(self, ws: Path) -> None:
        resp, code = rm.register_reference_pdf(ws, {"title": "T"})
        assert code == 400
        assert "pdf_b64 is required" in resp["error"]

    def test_400_invalid_investigation_slug(self, ws: Path) -> None:
        resp, code = rm.register_reference_pdf(ws, {
            "pdf_b64": _FAKE_PDF, "investigation": "Bad Slug",
        })
        assert code == 400
        assert "invalid investigation slug" in resp["error"]

    def test_400_invalid_bib_key(self, ws: Path) -> None:
        resp, code = rm.register_reference_pdf(ws, {
            "pdf_b64": _FAKE_PDF, "bib_key": "bad key!",
        })
        assert code == 400
        assert "invalid bib_key" in resp["error"]

    def test_409_duplicate_global(self, ws: Path) -> None:
        base = {"pdf_b64": _FAKE_PDF, "title": "T", "authors": "A B",
                "year": 2021, "bib_key": "Dup2021"}
        resp1, code1 = rm.register_reference_pdf(ws, base)
        assert code1 == 200, resp1
        resp2, code2 = rm.register_reference_pdf(ws, base)
        assert code2 == 409
        assert "already exists in papers.bib" in resp2["error"]

    def test_404_investigation_not_found(self, ws: Path) -> None:
        resp, code = rm.register_reference_pdf(ws, {
            "pdf_b64": _FAKE_PDF, "title": "T", "authors": "A B", "year": 2021,
            "bib_key": "AB2021", "investigation": "ghost-inv",
        })
        assert code == 404
        assert "investigation 'ghost-inv' not found" in resp["error"]


# ---------------------------------------------------------------------------
# register_reference (BibTeX paste)
# ---------------------------------------------------------------------------


class TestRegisterReference:
    def test_happy_global_new_key(self, ws: Path) -> None:
        resp, code = rm.register_reference(ws, {
            "bibtex_text": "@article{Foo2020, title = {A foo}, year = {2020}}",
        })
        assert code == 200, resp
        assert resp["ok"] is True
        assert "Foo2020" in _read_bib(ws)

    def test_happy_investigation_scoped_reuses_key(self, ws: Path) -> None:
        # Create the global key first.
        rm.register_reference(ws, {
            "bibtex_text": "@article{Foo2020, title = {A foo}, year = {2020}}",
        })
        bib_before = _read_bib(ws)
        # Re-submit the same key WITH an investigation: must NOT error, and the
        # bare key is added to the investigation references (no second append).
        resp, code = rm.register_reference(ws, {
            "bibtex_text": "@article{Foo2020, title = {A foo}, year = {2020}}",
            "investigation": _INV_SLUG,
        })
        assert code == 200, resp
        assert "Foo2020" in _read_inv(ws)["inputs"]["references"]
        # Key still present; not appended a second time.
        assert _read_bib(ws).count("@article{Foo2020") == 1
        assert _read_bib(ws) == bib_before

    def test_happy_claims_str_form(self, ws: Path) -> None:
        resp, code = rm.register_reference(ws, {
            "bibtex_text": "@article{Bar2021, year = {2021}}",
            "claim_mappings": "c1:Bar2021, c2:Bar2021",
        })
        assert code == 200, resp
        claims = _read_claims(ws)
        assert claims["c1"] == ["Bar2021"]
        assert claims["c2"] == ["Bar2021"]

    def test_happy_pdf_saved(self, ws: Path) -> None:
        resp, code = rm.register_reference(ws, {
            "bibtex_text": "@article{Baz2022, year = {2022}}",
            "pdf_b64": _FAKE_PDF,
        })
        assert code == 200, resp
        assert (ws / "references" / "papers" / "Baz2022.pdf").is_file()
        refs = _read_ws(ws)["references_pdfs"]
        assert any(e["bib_key"] == "Baz2022" for e in refs)

    def test_400_missing_bibtex_text(self, ws: Path) -> None:
        resp, code = rm.register_reference(ws, {})
        assert code == 400
        assert "bibtex_text is required" in resp["error"]

    def test_400_unparseable_key(self, ws: Path) -> None:
        resp, code = rm.register_reference(ws, {"bibtex_text": "no bib key here"})
        assert code == 400
        assert "could not parse BibTeX key" in resp["error"]

    def test_400_invalid_investigation_slug(self, ws: Path) -> None:
        resp, code = rm.register_reference(ws, {
            "bibtex_text": "@article{Foo2020, year = {2020}}",
            "investigation": "Bad Slug",
        })
        assert code == 400
        assert "invalid investigation slug" in resp["error"]

    def test_409_duplicate_global(self, ws: Path) -> None:
        bib = {"bibtex_text": "@article{Dup2020, year = {2020}}"}
        resp1, code1 = rm.register_reference(ws, bib)
        assert code1 == 200, resp1
        resp2, code2 = rm.register_reference(ws, bib)
        assert code2 == 409
        assert "already exists in papers.bib" in resp2["error"]

    def test_404_investigation_not_found(self, ws: Path) -> None:
        resp, code = rm.register_reference(ws, {
            "bibtex_text": "@article{Foo2020, year = {2020}}",
            "investigation": "ghost-inv",
        })
        assert code == 404
        assert "investigation 'ghost-inv' not found" in resp["error"]


# ---------------------------------------------------------------------------
# Behavioral commit-path tests (drive real server.Handler.* shims)
# ---------------------------------------------------------------------------


def _make_handler_and_capture():
    """Return (handler_instance, captured_dict). The handler's _json is replaced
    with a recorder that stores (resp, code) and returns resp."""
    import vivarium_dashboard.server as _srv
    handler = object.__new__(_srv.Handler)
    captured: dict[str, Any] = {}

    def _capture_json(resp, code):
        captured["resp"] = resp
        captured["code"] = code
        return resp

    handler._json = _capture_json  # type: ignore[method-assign]
    return handler, captured


class TestReferencePdfCommitPath:
    def test_shim_calls_active_branch_action_with_commit_msg(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        calls: dict[str, Any] = {}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)
        handler, captured = _make_handler_and_capture()
        handler._post_reference_pdf({
            "pdf_b64": _FAKE_PDF, "title": "T", "authors": "A B", "year": 2021,
            "bib_key": "AB2021",
        })
        # Full metadata → no "(metadata pending)" suffix.
        assert calls["commit_msg"] == "feat(5): add reference 'AB2021'"
        assert captured["code"] == 200
        # Post-wrapper augmentation survives.
        assert captured["resp"]["bib_key"] == "AB2021"
        assert captured["resp"]["metadata_pending"] is False
        assert "extracted" in captured["resp"]

    def test_commit_msg_metadata_pending_suffix(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        calls: dict[str, Any] = {}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)
        handler, captured = _make_handler_and_capture()
        handler._post_reference_pdf({"pdf_b64": _FAKE_PDF})  # no metadata
        assert calls["commit_msg"].startswith("feat(5): add reference '_pending")
        assert calls["commit_msg"].endswith(" (metadata pending)")
        assert captured["resp"]["metadata_pending"] is True

    def test_400_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 400")

        monkeypatch.setattr(_srv, "_active_branch_action", _boom)
        handler, captured = _make_handler_and_capture()
        handler._post_reference_pdf({})  # missing pdf_b64
        assert captured["code"] == 400
        assert "pdf_b64 is required" in captured["resp"]["error"]

    def test_action_reraises_on_conflict(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        # Pre-seed papers.bib with the key so the action conflicts.
        refs = ws / "references"
        refs.mkdir(parents=True, exist_ok=True)
        (refs / "papers.bib").write_text("@article{AB2021,\n}\n", encoding="utf-8")

        captured_action: dict[str, Any] = {}

        def _record_and_run(commit_message, action_fn):
            try:
                action_fn()
            except Exception as exc:
                captured_action["raised"] = exc
                return {"error": str(exc)}, 500
            return {"branch": "b"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _record_and_run)
        handler, captured = _make_handler_and_capture()
        handler._post_reference_pdf({
            "pdf_b64": _FAKE_PDF, "title": "T", "authors": "A B", "year": 2021,
            "bib_key": "AB2021",
        })
        assert "raised" in captured_action
        assert isinstance(captured_action["raised"], ValueError)
        assert captured["code"] == 500


class TestReferenceCommitPath:
    def test_shim_calls_active_branch_action_with_commit_msg(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        calls: dict[str, Any] = {}

        def _recorder(commit_message, action_fn):
            calls["commit_msg"] = commit_message
            action_fn()
            return {"branch": "b", "commit": "c"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _recorder)
        handler, captured = _make_handler_and_capture()
        handler._post_reference({
            "bibtex_text": "@article{Foo2020, year = {2020}}",
        })
        assert calls["commit_msg"] == "feat(5): add reference 'Foo2020'"
        assert captured["code"] == 200

    def test_400_before_wrapper(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)

        def _boom(commit_message, action_fn):
            raise AssertionError("_active_branch_action must NOT be called on 400")

        monkeypatch.setattr(_srv, "_active_branch_action", _boom)
        handler, captured = _make_handler_and_capture()
        handler._post_reference({})  # missing bibtex_text
        assert captured["code"] == 400
        assert "bibtex_text is required" in captured["resp"]["error"]

    def test_action_reraises_on_conflict(self, ws: Path, monkeypatch: Any) -> None:
        import vivarium_dashboard.server as _srv
        monkeypatch.setattr(_srv, "WORKSPACE", ws)
        refs = ws / "references"
        refs.mkdir(parents=True, exist_ok=True)
        (refs / "papers.bib").write_text("@article{Foo2020,\n}\n", encoding="utf-8")

        captured_action: dict[str, Any] = {}

        def _record_and_run(commit_message, action_fn):
            try:
                action_fn()
            except Exception as exc:
                captured_action["raised"] = exc
                return {"error": str(exc)}, 500
            return {"branch": "b"}, 200

        monkeypatch.setattr(_srv, "_active_branch_action", _record_and_run)
        handler, captured = _make_handler_and_capture()
        handler._post_reference({"bibtex_text": "@article{Foo2020, year = {2020}}"})
        assert "raised" in captured_action
        assert isinstance(captured_action["raised"], ValueError)
        assert captured["code"] == 500
