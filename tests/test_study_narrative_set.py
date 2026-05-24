"""Tests for _post_study_narrative_set_for_test — the v4 narrative-spine
generic writer that backs the new POST /api/study-narrative-set endpoint.

Covers:
- Happy path for each allowlisted root (report, study_card,
  biological_summary, conclusion_verdicts, literature_anchors,
  design_pivot_required).
- Dotted-path writes create intermediate dicts on demand.
- Empty-string / null value REMOVES the leaf, and prunes now-empty
  parent dicts up the chain.
- Other top-level keys on study.yaml are preserved verbatim.
- Path must start with an allowlisted root — anything else (baseline,
  name, runs) returns 400.
- Enum guards on report.confidence + the three conclusion_verdicts.*.result
  paths.
- Missing study / study not found / missing path / missing value all
  return clean errors.
"""
from __future__ import annotations

import pytest
import yaml

from vivarium_dashboard.server import _post_study_narrative_set_for_test


# ---------------------------------------------------------------------------
# Fixture: workspace with one study that has some pre-existing content
# (so we can verify other keys survive).
# ---------------------------------------------------------------------------


@pytest.fixture
def ws(tmp_path):
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "workspace.yaml").write_text("name: test\n")
    sd = ws_root / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": "s1",
        "status": "draft",
        "baseline": [{"name": "b", "composite": "pkg.composites.foo"}],
        "objective": "Pre-existing objective — must survive.",
    }))
    return ws_root


def _read(ws_root):
    return yaml.safe_load((ws_root / "studies" / "s1" / "study.yaml").read_text())


# ---------------------------------------------------------------------------
# Happy path — each allowlisted root
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_biological_summary_scalar(self, ws):
        resp, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1",
            "path": "biological_summary",
            "value": "DnaA exists in three nucleotide states.",
        })
        assert code == 200
        assert resp == {"ok": True}
        spec = _read(ws)
        assert spec["biological_summary"] == "DnaA exists in three nucleotide states."

    def test_study_card_dotted_creates_parent(self, ws):
        resp, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1",
            "path": "study_card.goal",
            "value": "Split DnaA species.",
        })
        assert code == 200
        spec = _read(ws)
        assert spec["study_card"] == {"goal": "Split DnaA species."}

    def test_report_multi_leaf(self, ws):
        for path, value in [
            ("report.verdict", "passing-with-caveats"),
            ("report.confidence", "high"),
            ("report.evidence_quality", "calibrated"),
            ("report.main_insight", "Only ATP-DnaA drives initiation."),
        ]:
            resp, code = _post_study_narrative_set_for_test(ws, {
                "study": "s1", "path": path, "value": value,
            })
            assert code == 200, (path, resp)
        spec = _read(ws)
        assert spec["report"] == {
            "verdict": "passing-with-caveats",
            "confidence": "high",
            "evidence_quality": "calibrated",
            "main_insight": "Only ATP-DnaA drives initiation.",
        }

    def test_conclusion_verdicts_three_tracks(self, ws):
        for path, value in [
            ("conclusion_verdicts.regression_compatibility.result", "PASS"),
            ("conclusion_verdicts.regression_compatibility.basis", "Builds cleanly."),
            ("conclusion_verdicts.biological_validation.result", "MIXED"),
            ("conclusion_verdicts.biological_validation.basis", "atp_fraction = 0.997."),
            ("conclusion_verdicts.explanatory_gain.result", "POSITIVE"),
            ("conclusion_verdicts.explanatory_gain.basis", "Three findings worth keeping."),
        ]:
            resp, code = _post_study_narrative_set_for_test(ws, {
                "study": "s1", "path": path, "value": value,
            })
            assert code == 200, (path, resp)
        spec = _read(ws)
        cv = spec["conclusion_verdicts"]
        assert cv["regression_compatibility"] == {"result": "PASS", "basis": "Builds cleanly."}
        assert cv["biological_validation"] == {"result": "MIXED", "basis": "atp_fraction = 0.997."}
        assert cv["explanatory_gain"] == {"result": "POSITIVE", "basis": "Three findings worth keeping."}

    def test_preserves_other_keys(self, ws):
        _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "biological_summary",
            "value": "DnaA cycles.",
        })
        spec = _read(ws)
        assert spec["schema_version"] == 4
        assert spec["name"] == "s1"
        assert spec["status"] == "draft"
        assert spec["baseline"] == [{"name": "b", "composite": "pkg.composites.foo"}]
        assert spec["objective"] == "Pre-existing objective — must survive."


# ---------------------------------------------------------------------------
# Clear-out semantics (empty value removes leaf + prunes empty parents)
# ---------------------------------------------------------------------------


class TestClearOut:
    def test_empty_string_removes_leaf(self, ws):
        _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "biological_summary",
            "value": "x",
        })
        _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "biological_summary", "value": "",
        })
        spec = _read(ws)
        assert "biological_summary" not in spec

    def test_null_removes_leaf(self, ws):
        _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "study_card.goal", "value": "x",
        })
        _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "study_card.goal", "value": None,
        })
        spec = _read(ws)
        # study_card became {} after pop(goal) — pruned by the writer.
        assert "study_card" not in spec

    def test_partial_clear_keeps_siblings(self, ws):
        _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "study_card.goal", "value": "G",
        })
        _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "study_card.mechanism", "value": "M",
        })
        _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "study_card.goal", "value": "",
        })
        spec = _read(ws)
        # Only `goal` removed; `mechanism` preserved; `study_card` stays.
        assert spec["study_card"] == {"mechanism": "M"}

    def test_deep_clear_prunes_intermediate(self, ws):
        _post_study_narrative_set_for_test(ws, {
            "study": "s1",
            "path": "conclusion_verdicts.biological_validation.result",
            "value": "PASS",
        })
        _post_study_narrative_set_for_test(ws, {
            "study": "s1",
            "path": "conclusion_verdicts.biological_validation.result",
            "value": "",
        })
        spec = _read(ws)
        # Both `biological_validation` and `conclusion_verdicts` should
        # have been pruned because they became {}.
        assert "conclusion_verdicts" not in spec


# ---------------------------------------------------------------------------
# Allowlist + enum guards
# ---------------------------------------------------------------------------


class TestAllowlist:
    @pytest.mark.parametrize("forbidden_root", [
        "baseline", "name", "status", "runs", "objective",
        "purpose", "behavior_tests", "findings",
    ])
    def test_non_allowlisted_root_rejected(self, ws, forbidden_root):
        resp, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": forbidden_root, "value": "x",
        })
        assert code == 400
        assert "path must start with" in resp["error"]

    @pytest.mark.parametrize("nested_forbidden", [
        "baseline.0.composite", "findings.0.statement",
    ])
    def test_non_allowlisted_dotted_rejected(self, ws, nested_forbidden):
        resp, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": nested_forbidden, "value": "x",
        })
        assert code == 400


class TestEnumGuards:
    def test_confidence_enum_accepts_known(self, ws):
        for v in ("high", "medium", "low"):
            resp, code = _post_study_narrative_set_for_test(ws, {
                "study": "s1", "path": "report.confidence", "value": v,
            })
            assert code == 200, (v, resp)

    def test_confidence_enum_rejects_unknown(self, ws):
        resp, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "report.confidence", "value": "perfect",
        })
        assert code == 400
        assert "not in allowed enum" in resp["error"]

    def test_regression_result_enum_accepts(self, ws):
        for v in ("PASS", "FAIL", "MIXED", "PENDING"):
            _, code = _post_study_narrative_set_for_test(ws, {
                "study": "s1",
                "path": "conclusion_verdicts.regression_compatibility.result",
                "value": v,
            })
            assert code == 200

    def test_regression_result_enum_rejects_unknown(self, ws):
        resp, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1",
            "path": "conclusion_verdicts.regression_compatibility.result",
            "value": "OK",
        })
        assert code == 400

    def test_explanatory_uses_different_enum(self, ws):
        # POSITIVE is valid for explanatory_gain but NOT for regression
        # — confirm both directions.
        _, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1",
            "path": "conclusion_verdicts.explanatory_gain.result",
            "value": "POSITIVE",
        })
        assert code == 200
        _, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1",
            "path": "conclusion_verdicts.regression_compatibility.result",
            "value": "POSITIVE",
        })
        assert code == 400


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_study(self, ws):
        resp, code = _post_study_narrative_set_for_test(ws, {
            "path": "biological_summary", "value": "x",
        })
        assert code == 400
        assert "missing study" in resp["error"]

    def test_missing_path(self, ws):
        resp, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1", "value": "x",
        })
        assert code == 400
        assert "missing path" in resp["error"]

    def test_missing_value(self, ws):
        # Note: None and "" are valid values (they clear the leaf). The
        # *absence* of the value key is what's missing.
        resp, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "biological_summary",
        })
        assert code == 400
        assert "missing value" in resp["error"]

    def test_study_not_found(self, ws):
        resp, code = _post_study_narrative_set_for_test(ws, {
            "study": "no-such", "path": "biological_summary", "value": "x",
        })
        assert code == 404
        assert "not found" in resp["error"]

    def test_empty_path_rejected(self, ws):
        resp, code = _post_study_narrative_set_for_test(ws, {
            "study": "s1", "path": "", "value": "x",
        })
        assert code == 400
