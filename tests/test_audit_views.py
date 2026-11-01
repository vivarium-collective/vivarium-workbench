"""Tests for the read-only L0-L5 audit view (`lib/audit_views.build_audit`).

The FastAPI app import hits a pre-existing, unrelated
``process_bigraph.composite_spec`` ModuleNotFoundError in this environment, so
the endpoint cannot be exercised via the ``inproc_client`` fixture here. We
therefore test the library function directly (the route is a thin
``JSONResponse(build_audit(ws))`` passthrough, mirroring
``/api/investigation-graph``) and gate the route test behind an importability
check that skips with a clear reason when the app cannot import.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vivarium_workbench.lib.audit_views import build_audit, build_study_audit, StudyAuditViewError


def _has_study_audit() -> bool:
    """Whether the installed viva_superpowers carries the audit module.

    The workbench pins pbg-superpowers bare from PyPI; ``study_audit`` only
    lights up once a viva-superpowers release (or a git pin) includes it. When
    it is absent, ``build_audit`` DEGRADES to a 200 error-report — that is the
    contract these tests must hold in BOTH worlds, so the populated-report
    specifics are gated on availability.
    """
    try:
        import discovery_engine.study_audit  # noqa: F401
    except Exception:
        return False
    return True


_HAS_STUDY_AUDIT = _has_study_audit()


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _make_workspace(root: Path) -> Path:
    """A minimal but canonical workspace: workspace.yaml + one study.yaml."""
    _write(root / "workspace.yaml", "name: probe-ws\n")
    _write(
        root / "studies" / "s1" / "study.yaml",
        "name: s1\n"
        "conditions:\n"
        "  baseline:\n"
        "    composite: some.composite\n",
    )
    return root


def test_build_audit_returns_dict_and_200(tmp_path):
    ws = _make_workspace(tmp_path / "ws")
    body, status = build_audit(ws)

    # Contract that holds in BOTH worlds (audit module present or degraded):
    assert status == 200
    assert isinstance(body, dict)
    assert isinstance(body["studies"], list)
    # Fully JSON-serializable (the endpoint hands this straight to JSONResponse).
    assert json.loads(json.dumps(body)) == body

    if _HAS_STUDY_AUDIT:
        # Populated report: the one study is audited, summary carries counts.
        assert len(body["studies"]) == 1
        assert body["studies"][0]["slug"] == "s1"
        assert "summary" in body
        assert "hard_failures" in body["summary"]
    else:
        # Degraded (workbench-CI condition until viva-superpowers ships it):
        # 200 with an empty studies list + an explanatory error, never a 500.
        assert body["studies"] == []
        assert body.get("error")


def test_build_audit_empty_workspace_is_200_with_empty_studies(tmp_path):
    ws = tmp_path / "empty"
    _write(ws / "workspace.yaml", "name: empty\n")

    body, status = build_audit(ws)

    assert status == 200
    assert body["studies"] == []
    assert isinstance(body.get("investigations"), list)


def test_build_audit_is_tolerant_of_a_bad_path(tmp_path):
    """A non-existent / unreadable workspace must degrade to (dict, 200) with an
    empty studies list, never raise or 500."""
    body, status = build_audit(tmp_path / "does-not-exist")

    assert status == 200
    assert isinstance(body, dict)
    assert body.get("studies") == []


def _audit_study_workspace(root: Path) -> Path:
    """A workspace whose study.yaml is readable by BOTH ``load_study_detail_spec``
    (which build_study_audit uses for the 404 existence check -- it parses via
    ``lib.investigations.load_spec``, which requires either a top-level
    ``composite:`` or ``variants:``) AND ``discovery_engine.study_audit`` (which
    reads the file directly via ``study_io.load_yaml``, no such requirement).
    Deliberately NOT ``_make_workspace``'s ``conditions.baseline`` shape above --
    that shape has no top-level ``composite``/``variants`` and no
    ``schema_version: 4``, so ``load_spec`` raises ``InvestigationSpecError``
    before ``build_study_audit`` ever reaches the audit call. This is the same
    legacy single-composite shape ``tests/test_rigor_views_lib.py`` uses for its
    build_study_rigor fixture."""
    _write(root / "workspace.yaml", "name: probe-ws\n")
    _write(
        root / "studies" / "s1" / "study.yaml",
        "name: s1\ncomposite: some.composite\n",
    )
    return root


class TestBuildStudyAudit:
    """Unit tests for build_study_audit (Fable G6 Reproducibility check group)."""

    def test_missing_study_raises_400(self, tmp_path):
        with pytest.raises(StudyAuditViewError) as exc:
            build_study_audit(tmp_path, None)
        assert exc.value.status == 400
        assert exc.value.body == {"error": "missing ?study="}

    def test_not_found_raises_404(self, tmp_path):
        ws = _make_workspace(tmp_path / "ws")
        with pytest.raises(StudyAuditViewError) as exc:
            build_study_audit(ws, "nope")
        assert exc.value.status == 404
        assert exc.value.body == {"error": "study not found"}

    def test_happy_path_computes_for_fixture_study(self, tmp_path):
        """The study exists, so this MUST get either a real per-study audit
        block (when discovery_engine.study_audit is installed, which it is in
        this env) or an honest unavailable() -- never a raise, never a
        fabricated empty block."""
        ws = _audit_study_workspace(tmp_path / "ws")
        out = build_study_audit(ws, "s1")
        assert isinstance(out, dict)
        assert json.loads(json.dumps(out)) == out  # JSON-serializable

        if _HAS_STUDY_AUDIT:
            assert out.get("unavailable") is not True
            assert out["slug"] == "s1"
            assert out["worst"] in ("pass", "warn", "fail")
            assert isinstance(out["checks"], list) and out["checks"]
            for c in out["checks"]:
                assert c["level"] in ("L0", "L1", "L2", "L3", "L4", "L5")
                assert c["status"] in ("pass", "warn", "fail")
                assert c["tier"] in ("hard", "soft")
        else:
            assert out.get("unavailable") is True
            assert out.get("reason")

    def test_compute_failure_degrades_to_unavailable(self, tmp_path, monkeypatch):
        """An audit_workspace exception (unimportable dep, evaluator crash, …)
        never raises/500s -- it degrades to unavailable(reason), same contract
        as rigor_views.build_study_rigor (spec §2 R2)."""
        ws = _audit_study_workspace(tmp_path / "ws")

        import discovery_engine.study_audit as _sa_mod

        def _boom(*args, **kwargs):
            raise RuntimeError("audit exploded")

        monkeypatch.setattr(_sa_mod, "audit_workspace", _boom)
        out = build_study_audit(ws, "s1")
        assert out["unavailable"] is True
        assert "audit exploded" in out["reason"]

    def test_no_block_for_slug_degrades_to_unavailable(self, tmp_path, monkeypatch):
        """When audit_workspace succeeds but reports no block for this slug
        (an edge case, not expected in practice since the study exists), the
        wrapper degrades to unavailable rather than raising an IndexError-ish
        KeyError or fabricating an empty checks list."""
        ws = _audit_study_workspace(tmp_path / "ws")

        import discovery_engine.study_audit as _sa_mod

        class _EmptyReport:
            studies = []

        monkeypatch.setattr(_sa_mod, "audit_workspace", lambda *a, **k: _EmptyReport())
        out = build_study_audit(ws, "s1")
        assert out["unavailable"] is True
        assert "s1" in out["reason"]


class TestStudyAuditViewError:
    def test_body_and_status(self):
        err = StudyAuditViewError({"error": "oops"}, 404)
        assert err.status == 404
        assert err.body == {"error": "oops"}
        assert str(err) == "oops"


def _app_imports() -> bool:
    try:
        from vivarium_workbench.api.app import create_app  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.skipif(
    not _app_imports(),
    reason="FastAPI app import hits the pre-existing process_bigraph.composite_spec "
    "ModuleNotFoundError (unrelated to this view); route covered by the lib test.",
)
def test_api_audit_route(inproc_client, tmp_path):
    ws = _make_workspace(tmp_path / "ws")
    client = inproc_client(ws)
    r = client.get("/api/audit")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("studies"), list)


@pytest.mark.skipif(
    not _app_imports(),
    reason="FastAPI app import hits the pre-existing process_bigraph.composite_spec "
    "ModuleNotFoundError (unrelated to this view); route covered by the lib test.",
)
class TestStudyAuditRoute:
    """GET /api/study-audit -- Fable G6 (mirrors /api/study-rigor)."""

    def test_returns_payload_never_500(self, inproc_client, tmp_path):
        ws = _audit_study_workspace(tmp_path / "ws")
        client = inproc_client(ws)
        r = client.get("/api/study-audit?study=s1")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, dict)
        if _HAS_STUDY_AUDIT:
            assert body.get("unavailable") is not True
            assert body["slug"] == "s1"
        else:
            assert body.get("unavailable") is True

    def test_missing_study_param_is_400(self, inproc_client, tmp_path):
        ws = _make_workspace(tmp_path / "ws")
        client = inproc_client(ws)
        r = client.get("/api/study-audit")
        assert r.status_code == 400
        assert r.json() == {"error": "missing ?study="}

    def test_unknown_study_is_404(self, inproc_client, tmp_path):
        ws = _make_workspace(tmp_path / "ws")
        client = inproc_client(ws)
        r = client.get("/api/study-audit?study=nope")
        assert r.status_code == 404
        assert r.json() == {"error": "study not found"}


def test_publish_writes_parseable_audit_json(tmp_path):
    """Mirror the publish step: build_audit's body, written through publish's
    strict (`allow_nan=False`) JSON writer, must land on disk and parse back."""
    from vivarium_workbench.publish import _write_json

    ws = _make_workspace(tmp_path / "ws")
    body, status = build_audit(ws)
    assert status == 200

    out = tmp_path / "bundle" / "api" / "audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_json(out, body)

    assert out.is_file()
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(parsed.get("studies"), list)
    if _HAS_STUDY_AUDIT:
        assert "summary" in parsed
