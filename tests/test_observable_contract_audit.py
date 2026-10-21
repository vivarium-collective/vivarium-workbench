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
