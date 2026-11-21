"""Study-completeness gate — the vivarium-workbench publish-side consume.

Collects the engine's study-completeness lint findings (rules 1 & 2) and computes
rule 3 (a declared composite that does not resolve), then exposes the combined
signal for the read-only publish (baked JSON + WARN summary + CI annotations +
``--strict`` REFUSE).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib import study_completeness as sc


# ---------------------------------------------------------------------------
# A minimal workspace: one investigation with two member studies.
# ---------------------------------------------------------------------------

def _ws(tmp_path: Path, studies: dict) -> Path:
    ws = tmp_path / "ws"
    (ws).mkdir(parents=True)
    (ws / "workspace.yaml").write_text(yaml.safe_dump({"name": "ws", "description": "t"}))
    inv = ws / "investigations" / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "inv", "studies": list(studies.keys()),
    }))
    for slug, spec in studies.items():
        sd = ws / "studies" / slug
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    from vivarium_workbench.lib import _root
    _root.set_workspace_root(ws)
    return ws


def test_declared_composite_refs_collects_every_site():
    spec = {
        "baseline": [{"name": "b", "composite": "pkg.A"}],
        "conditions": {"baseline": {"composite": "pkg.B"},
                       "variants": [{"composite": "pkg.C"}]},
        "simulation_set": [{"composite": "pkg.D", "base_model": "pkg.E"}],
    }
    assert sc._declared_composite_refs(spec) == ["pkg.A", "pkg.B", "pkg.C", "pkg.D", "pkg.E"]


def test_rule3_fires_for_unresolvable_composite_not_for_real(tmp_path, monkeypatch):
    ws = _ws(tmp_path, {
        "s-ghost": {"name": "s-ghost", "baseline": [{"name": "b", "composite": "pkg.Ghost"}]},
        "s-real": {"name": "s-real", "baseline": [{"name": "b", "composite": "pkg.Real"}]},
    })
    # pkg.Real resolves to a real payload; pkg.Ghost does not.
    monkeypatch.setattr(sc, "_resolves_real", lambda ws_root, ref: ref == "pkg.Real")
    # no engine findings in play — isolate rule 3
    res = sc.collect(ws, lint_findings=[])
    checks = [(f["study"], f["check"]) for f in res["findings"]]
    assert ("s-ghost", "declared_composite_unresolved") in checks
    assert ("s-real", "declared_composite_unresolved") not in checks
    # the finding carries the investigation it belongs to
    ghost = [f for f in res["findings"] if f["study"] == "s-ghost"][0]
    assert ghost["investigation"] == "inv"
    assert ghost["level"] == "warning"


def test_collect_picks_up_engine_family_findings(tmp_path, monkeypatch):
    ws = _ws(tmp_path, {
        "s1": {"name": "s1", "baseline": [{"name": "b", "composite": "pkg.Real"}]},
    })
    monkeypatch.setattr(sc, "_resolves_real", lambda ws_root, ref: True)  # no rule 3

    class _F:  # mimics discovery_engine LintFinding.to_dict()
        def __init__(self, check, slug):
            self._d = {"check": check, "study_slug": slug, "level": "warning",
                       "field_path": "runs", "message": "m"}
        def to_dict(self):
            return self._d

    # a family finding (rule 1) + a non-family finding (must be filtered out)
    lint = [_F("status_claims_done_no_runs_recorded", "s1"), _F("render_blocked", "s1")]
    res = sc.collect(ws, lint_findings=lint)
    family = [f for f in res["findings"] if f["check"] in sc.STUDY_COMPLETENESS_CHECKS]
    assert any(f["check"] == "status_claims_done_no_runs_recorded" for f in family)
    assert all(f["check"] != "render_blocked" for f in res["findings"])
    # family-finding is tagged with its investigation
    assert [f for f in family][0]["investigation"] == "inv"


def test_family_constant_is_the_three_checks():
    assert sc.STUDY_COMPLETENESS_CHECKS == frozenset({
        "status_claims_done_no_runs_recorded",
        "completed_but_verdict_pending",
        "declared_composite_unresolved",
    })


def test_is_strict_flag_and_env(monkeypatch):
    monkeypatch.delenv(sc.STRICT_ENV, raising=False)
    assert sc.is_strict(False) is False
    assert sc.is_strict(True) is True
    monkeypatch.setenv(sc.STRICT_ENV, "1")
    assert sc.is_strict(False) is True
    monkeypatch.setenv(sc.STRICT_ENV, "off")
    assert sc.is_strict(False) is False


def test_ci_annotations_and_summary_shapes():
    res = {
        "findings": [{"investigation": "inv", "study": "s1",
                      "check": "completed_but_verdict_pending", "level": "warning",
                      "field_path": "runs[].outcomes", "message": "ran but pending"}],
        "total": 1, "by_check": {"completed_but_verdict_pending": 1},
        "by_investigation": {"inv": [{"study": "s1", "check": "completed_but_verdict_pending"}]},
    }
    ann = sc.ci_annotations(res)
    assert len(ann) == 1
    assert ann[0].startswith("::warning title=study-completeness:")
    assert "inv/s1" in ann[0]
    summ = sc.summary_lines(res)
    assert "1 finding(s)" in summ[0]
    # clean result → a single "all clear" line, no annotations
    clean = {"findings": [], "total": 0, "by_check": {}, "by_investigation": {}}
    assert sc.ci_annotations(clean) == []
    assert "all clear" in sc.summary_lines(clean)[0]
