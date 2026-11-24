"""Tests for the needs-attention -> GenerationRequest bridge."""

from vivarium_workbench.lib.needs_attention_requests import bridge_items


def _item(kind, *, study="s1", ref=None, severity="high", title="t", detail="d",
          hint="h", **extra):
    it = {"kind": kind, "study": study, "ref": ref if ref is not None else study,
          "severity": severity, "title": title, "detail": detail, "action_hint": hint}
    it.update(extra)
    return it


def test_safe_kind_defaults_to_auto():
    out = bridge_items([_item("verdict_divergence")])
    assert len(out["requests"]) == 1
    r = out["requests"][0]
    assert r.kind == "interpret_finding"
    assert r.gate == "auto"            # safe kind auto-fixes unattended
    assert r.produces == "finding/v1"
    assert r.target == "s1"
    assert r.provenance["source"] == "needs_attention"
    assert r.provenance["scan_kind"] == "verdict_divergence"
    # a filler skill is stamped on engines that have the field (post-0.6.0);
    # absent-field engines return the getattr default, so this stays cross-version.
    assert getattr(r, "skill", "stamped")


def test_structural_kind_stays_review():
    out = bridge_items([_item("uncovered_ac")])
    r = out["requests"][0]
    assert r.kind == "propose_study"
    assert r.gate == "review"           # branch-point stays gated behind a click


def test_deterministic_kind_is_a_rerun_not_a_request():
    out = bridge_items([_item("param_drift")])
    assert out["requests"] == []
    assert len(out["reruns"]) == 1
    rr = out["reruns"][0]
    assert rr["scan_kind"] == "param_drift"
    assert "Re-run" in rr["remedy"]


def test_unmapped_kind_reported_not_dropped():
    out = bridge_items([_item("some_future_signal")])
    assert out["requests"] == []
    assert out["reruns"] == []
    assert out["unmapped"][0]["scan_kind"] == "some_future_signal"


def test_investigation_gates_are_respected_verbatim():
    # all_gated => even a safe kind is review-gated (operator opted out of auto).
    inv = {"discovery": {"gates": {"default": "review", "by_kind": {}}}}
    out = bridge_items([_item("verdict_divergence")], inv)
    assert out["requests"][0].gate == "review"

    # free_run => even a structural kind auto-fills (operator opted in).
    inv2 = {"discovery": {"gates": {"default": "auto", "by_kind": {}}}}
    out2 = bridge_items([_item("uncovered_ac")], inv2)
    assert out2["requests"][0].gate == "auto"


def test_ids_are_stable_and_distinct_per_issue():
    items = [_item("verdict_divergence", ref="s1"),
             _item("hard_gate", ref="s1")]      # same study, different issue
    out = bridge_items(items)
    ids = [r.id for r in out["requests"]]
    assert ids[0] != ids[1]                       # distinct per scan_kind
    # idempotent: re-bridging the same items yields the same ids
    out2 = bridge_items(items)
    assert [r.id for r in out2["requests"]] == ids


def test_next_action_type_carried_into_context():
    out = bridge_items([_item("stale_finding", next_action_type="seed_diagnostic")])
    r = out["requests"][0]
    assert r.kind == "propose_study"
    assert r.context.get("next_action_type") == "seed_diagnostic"


def test_skill_for_absent_falls_back_to_empty(monkeypatch):
    # `skill_for` was added after the 0.6.0 PyPI release; the bridge must still
    # work (skill == "") on an engine version without it.
    import discovery_engine.phases.generation as g
    monkeypatch.delattr(g, "skill_for", raising=False)
    out = bridge_items([_item("verdict_divergence")])
    # "" whether from the fallback stamp or an engine without the skill field.
    assert getattr(out["requests"][0], "skill", "") == ""
    assert out["requests"][0].kind == "interpret_finding"


def test_viz_kinds_map_to_visualize_auto():
    for k in ("viz_broken", "viz_missing", "viz_stale"):
        out = bridge_items([_item(k, severity="medium")])
        r = out["requests"][0]
        assert r.kind == "visualize"
        assert r.gate == "auto"
