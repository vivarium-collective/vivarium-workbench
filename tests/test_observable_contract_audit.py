"""observable-contract: a study's declared readouts must resolve to real
observables of its baseline composite(s) — graded as a study-audit check, the
readout-level analogue of `contract-face-wellformed`. Folds the existing
`build_study_observable_check` resolver into the L0-L5 audit block instead of
leaving readout coverage only in the readiness/needs-attention panel.
"""
from __future__ import annotations

from vivarium_workbench.lib import audit_views, observables_views


def _stub_obs(monkeypatch, readouts, code=200):
    monkeypatch.setattr(
        observables_views, "build_study_observable_check",
        lambda ws_root, slug: ({"readouts": readouts}, code))


def test_not_in_structure_is_a_fail(tmp_path, monkeypatch):
    _stub_obs(monkeypatch, [{"name": "phantom", "status": "not_in_structure"}])
    chk = audit_views._observable_contract_check(tmp_path, "s")
    assert chk["name"] == "observable-contract" and chk["status"] == "fail"
    assert "phantom" in chk["detail"]


def test_unresolved_is_a_fail(tmp_path, monkeypatch):
    _stub_obs(monkeypatch, [{"name": "x", "status": "unresolved"}])
    assert audit_views._observable_contract_check(tmp_path, "s")["status"] == "fail"


def test_aspirational_only_is_a_warn(tmp_path, monkeypatch):
    _stub_obs(monkeypatch, [{"name": "future", "status": "aspirational"},
                            {"name": "ok1", "status": "ok"}])
    assert audit_views._observable_contract_check(tmp_path, "s")["status"] == "warn"


def test_all_ok_is_a_pass(tmp_path, monkeypatch):
    _stub_obs(monkeypatch, [{"name": "a", "status": "ok"}, {"name": "b", "status": "ok"}])
    assert audit_views._observable_contract_check(tmp_path, "s")["status"] == "pass"


def test_no_readouts_skips(tmp_path, monkeypatch):
    _stub_obs(monkeypatch, [])
    assert audit_views._observable_contract_check(tmp_path, "s") is None


def test_resolver_unavailable_skips(tmp_path, monkeypatch):
    # 422 = no baseline composite could build; readout coverage is ungradeable
    # here (that's a composite-resolution problem graded elsewhere) — skip.
    _stub_obs(monkeypatch, [{"name": "x", "status": "ok"}], code=422)
    assert audit_views._observable_contract_check(tmp_path, "s") is None


def test_augment_appends_and_recomputes_worst(tmp_path, monkeypatch):
    _stub_obs(monkeypatch, [{"name": "phantom", "status": "not_in_structure"}])
    block = {"slug": "s", "worst": "pass", "checks": [
        {"level": "L0", "name": "contract-face-wellformed", "status": "pass",
         "tier": "hard"}]}
    out = audit_views._augment_with_observable_contract(block, tmp_path, "s")
    assert "observable-contract" in [c["name"] for c in out["checks"]]
    assert out["worst"] == "fail"  # the new fail dominates the prior pass


def test_augment_noop_when_skipped(tmp_path, monkeypatch):
    _stub_obs(monkeypatch, [])  # no readouts -> no check appended
    block = {"slug": "s", "worst": "pass", "checks": []}
    out = audit_views._augment_with_observable_contract(block, tmp_path, "s")
    assert out["checks"] == [] and out["worst"] == "pass"


def _stub_workspace_audit(monkeypatch, studies):
    """Make build_audit's upstream study_audit return a fixed report."""
    import viva_superpowers.study_audit as sa

    class _FakeReport:
        def as_dict(self):
            return {"studies": studies, "investigations": [],
                    "summary": {"n_studies": len(studies), "n_investigations": 0,
                                "hard_failures": 0}}

    monkeypatch.setattr(sa, "audit_workspace",
                        lambda ws_root, extra_packages=None: _FakeReport())


def test_build_audit_folds_observable_contract_into_each_study_block(tmp_path, monkeypatch):
    # The workspace-wide Audit tab must carry the SAME observable-contract check
    # the single-study Tests tab shows — not leave it only on build_study_audit.
    _stub_workspace_audit(monkeypatch, [
        {"slug": "s1", "worst": "pass",
         "checks": [{"level": "L0", "name": "contract-face-wellformed",
                     "status": "pass", "tier": "hard"}]}])
    _stub_obs(monkeypatch, [{"name": "phantom", "status": "not_in_structure"}])

    report, code = audit_views.build_audit(tmp_path)
    assert code == 200
    blk = report["studies"][0]
    assert "observable-contract" in [c["name"] for c in blk["checks"]]
    assert blk["worst"] == "fail"  # the soft fail dominates the prior pass


def test_build_audit_skips_study_with_no_readouts(tmp_path, monkeypatch):
    _stub_workspace_audit(monkeypatch, [{"slug": "s1", "worst": "pass", "checks": []}])
    _stub_obs(monkeypatch, [])  # no readouts -> nothing appended
    report, _ = audit_views.build_audit(tmp_path)
    assert [c["name"] for c in report["studies"][0]["checks"]] == []
