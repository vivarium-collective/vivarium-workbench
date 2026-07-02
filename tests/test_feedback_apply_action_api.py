"""SP3b: /api/feedback-apply-action applies a tracked feedback action via the
pbg-superpowers primitive (the dashboard never computes the action).
"""
from pathlib import Path

import pytest
import yaml

from vivarium_workbench.lib.lifecycle_mutations import feedback_apply_action


@pytest.fixture
def ws(tmp_path) -> Path:
    w = tmp_path / "ws"
    w.mkdir()
    (w / "workspace.yaml").write_text("name: test-ws\n")
    (w / "investigations").mkdir()
    (w / "studies").mkdir()
    return w


def _make_study(ws: Path, slug: str, findings: list[dict]) -> None:
    p = ws / "studies" / slug / "study.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(
        {"schema_version": 4, "name": slug, "status": "active", "findings": findings},
        sort_keys=False))


def _seed_feedback_with_action(ws: Path, slug: str, finding_id: str) -> str:
    from pbg_superpowers.feedback_actions import feedback_item_id

    iid = feedback_item_id(f"study-{slug}", "2026-01-01T10:00:00Z", "Alice")
    fb = ws / "investigations" / "inv1" / "feedback" / "r1.yaml"
    fb.parent.mkdir(parents=True, exist_ok=True)
    fb.write_text(yaml.safe_dump({
        "meta": {"investigation": "inv1"},
        "annotations": {
            f"study-{slug}": [
                {"ts": "2026-01-01T10:00:00Z", "author": "Alice", "text": "fix it"},
            ],
        },
        "actions": {
            iid: {
                "kind": "next_action",
                "target_study": slug,
                "target_finding": finding_id,
                "proposed_text": "calibrate to literature",
                "status": "open",
            },
        },
    }, sort_keys=False))
    return iid


def test_feedback_apply_action_endpoint(ws):
    _make_study(ws, "s1", [{"id": "F-01", "statement": "X diverges"}])
    item_id = _seed_feedback_with_action(ws, "s1", "F-01")

    payload, code = feedback_apply_action(
        ws, {"workspace": str(ws), "item_id": item_id})
    assert code == 200
    assert payload.get("applied") is True

    # The finding's next_action was written (the SP3a join).
    spec = yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())
    f = next(f for f in spec["findings"] if f["id"] == "F-01")
    assert f["next_action"] == "calibrate to literature"


def test_feedback_apply_action_missing_item_id(ws):
    payload, code = feedback_apply_action(ws, {"workspace": str(ws)})
    assert code == 400
    assert payload.get("error")


def test_feedback_apply_action_unknown_item(ws):
    payload, code = feedback_apply_action(
        ws, {"workspace": str(ws), "item_id": "fb-deadbeef"})
    assert code == 400
    assert payload.get("error")
