"""TDD tests for vivarium_workbench.lib.feedback_actions (SP3b — feedback → action).

The feedback loop dead-ends today at a free-text status string. SP3b adds a
deterministic ``actions:`` surface (parallel to ``responses:``) keyed by a
stable ``feedback_item_id`` and apply primitives that turn an open feedback
item into a tracked, applied action — the key join being a ``kind:
next_action`` action writing ``findings[].next_action`` (which SP3a then
seeds into a child study).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


@pytest.fixture
def ws(tmp_path) -> Path:
    """Workspace with a workspace.yaml so WorkspacePaths.load() works."""
    (tmp_path / "workspace.yaml").write_text("name: test-ws\n")
    (tmp_path / "investigations").mkdir()
    (tmp_path / "studies").mkdir()
    return tmp_path


def _make_study(ws: Path, slug: str, findings: list[dict]) -> Path:
    sdir = ws / "studies" / slug
    sdir.mkdir(parents=True, exist_ok=True)
    spec = {
        "schema_version": 4,
        "name": slug,
        "status": "active",
        "findings": findings,
    }
    p = sdir / "study.yaml"
    p.write_text(yaml.safe_dump(spec, sort_keys=False))
    return p


def _read_study(ws: Path, slug: str) -> dict:
    return yaml.safe_load((ws / "studies" / slug / "study.yaml").read_text())


# ── feedback_item_id ──────────────────────────────────────────────────────────


def test_feedback_item_id_stable():
    from vivarium_workbench.lib.feedback_actions import feedback_item_id

    a = feedback_item_id("study-s1", "2026-06-10T00:00", "alice")
    assert a == feedback_item_id("study-s1", "2026-06-10T00:00", "alice")  # stable
    assert a != feedback_item_id("study-s1", "2026-06-10T00:00", "bob")    # author
    assert a != feedback_item_id("study-s1", "2026-06-11T00:00", "alice")  # ts
    assert a != feedback_item_id("study-s2", "2026-06-10T00:00", "alice")  # section
    assert isinstance(a, str) and a


# ── study_feedback_actions (pure aggregator) ──────────────────────────────────


def test_actions_empty_when_no_investigations(ws):
    from vivarium_workbench.lib.feedback_actions import study_feedback_actions

    res = study_feedback_actions(ws, "foo")
    assert res == {"items": [], "summary": {"open": 0, "applied": 0, "dismissed": 0, "total": 0}}


def test_open_when_no_action(ws):
    from vivarium_workbench.lib.feedback_actions import study_feedback_actions

    _write(
        ws / "investigations" / "inv1" / "feedback" / "r1.yaml",
        {
            "meta": {"investigation": "inv1", "report_id": "rpt-1"},
            "annotations": {
                "study-foo": [
                    {"ts": "2026-01-01T10:00:00Z", "author": "Alice", "text": "Needs X"},
                ],
                "study-bar": [
                    {"ts": "2026-01-01T08:00:00Z", "author": "Bob", "text": "other study"},
                ],
            },
        },
    )
    res = study_feedback_actions(ws, "foo")
    assert len(res["items"]) == 1
    it = res["items"][0]
    assert it["item_id"]
    assert it["section"] == "study-foo"
    assert it["text"] == "Needs X"
    assert it["status"] == "open"
    assert it.get("action") is None
    assert res["summary"] == {"open": 1, "applied": 0, "dismissed": 0, "total": 1}


def test_actions_join_annotation_and_action(ws):
    from vivarium_workbench.lib.feedback_actions import feedback_item_id, study_feedback_actions

    iid = feedback_item_id("study-foo", "2026-01-01T10:00:00Z", "Alice")
    _write(
        ws / "investigations" / "inv1" / "feedback" / "r1.yaml",
        {
            "meta": {"investigation": "inv1"},
            "annotations": {
                "study-foo": [
                    {"ts": "2026-01-01T10:00:00Z", "author": "Alice", "text": "Calibrate"},
                ],
            },
            "actions": {
                iid: {
                    "kind": "next_action",
                    "target_study": "foo",
                    "target_finding": "F-01",
                    "proposed_text": "Calibrate Y to match Z",
                    "status": "open",
                },
            },
        },
    )
    res = study_feedback_actions(ws, "foo")
    it = res["items"][0]
    assert it["item_id"] == iid
    assert it["text"] == "Calibrate"
    assert it["status"] in ("open", "applied", "dismissed")
    assert it["action"]["kind"] in ("next_action", "finding", "design-edit", "study-seed")
    assert it["action"]["proposed_text"] == "Calibrate Y to match Z"
    assert "open" in res["summary"]


def test_actions_status_applied_and_dismissed(ws):
    from vivarium_workbench.lib.feedback_actions import feedback_item_id, study_feedback_actions

    iid_a = feedback_item_id("study-foo", "2026-01-02T10:00:00Z", "A")
    iid_d = feedback_item_id("study-foo", "2026-01-01T10:00:00Z", "D")
    _write(
        ws / "investigations" / "inv1" / "feedback" / "r1.yaml",
        {
            "meta": {"investigation": "inv1"},
            "annotations": {
                "study-foo": [
                    {"ts": "2026-01-02T10:00:00Z", "author": "A", "text": "applied one"},
                    {"ts": "2026-01-01T10:00:00Z", "author": "D", "text": "dismissed one"},
                ],
            },
            "actions": {
                iid_a: {"kind": "next_action", "target_study": "foo",
                        "target_finding": "F-01", "proposed_text": "do",
                        "status": "applied", "by": "claude", "at": "2026-01-03"},
                iid_d: {"kind": "design-edit", "target_study": "foo",
                        "proposed_text": "noop", "status": "dismissed"},
            },
        },
    )
    res = study_feedback_actions(ws, "foo")
    by_id = {i["item_id"]: i for i in res["items"]}
    assert by_id[iid_a]["status"] == "applied"
    assert by_id[iid_d]["status"] == "dismissed"
    assert res["summary"] == {"open": 0, "applied": 1, "dismissed": 1, "total": 2}


# ── apply_feedback_action ─────────────────────────────────────────────────────


def test_apply_next_action_writes_finding_next_action(ws):
    from vivarium_workbench.lib.feedback_actions import (
        apply_feedback_action,
        feedback_item_id,
        study_feedback_actions,
    )

    _make_study(ws, "foo", [{"id": "F-01", "statement": "X diverges"}])
    iid = feedback_item_id("study-foo", "2026-01-01T10:00:00Z", "Alice")
    _write(
        ws / "investigations" / "inv1" / "feedback" / "r1.yaml",
        {
            "meta": {"investigation": "inv1"},
            "annotations": {
                "study-foo": [
                    {"ts": "2026-01-01T10:00:00Z", "author": "Alice", "text": "fix it"},
                ],
            },
            "actions": {
                iid: {
                    "kind": "next_action",
                    "target_study": "foo",
                    "target_finding": "F-01",
                    "proposed_text": "test X",
                    "status": "open",
                },
            },
        },
    )
    res = apply_feedback_action(ws, iid)
    assert res.get("applied") is True
    assert res.get("kind") == "next_action"

    spec = _read_study(ws, "foo")
    f = next(f for f in spec["findings"] if f["id"] == "F-01")
    assert f["next_action"] == "test X"  # the SP3a join

    # action flipped to applied
    tracked = study_feedback_actions(ws, "foo")
    it = next(i for i in tracked["items"] if i["item_id"] == iid)
    assert it["status"] == "applied"
    assert it["action"].get("by")
    assert it["action"].get("at")

    # idempotent: re-apply is a no-op
    again = apply_feedback_action(ws, iid)
    assert again.get("already_applied") is True


def test_apply_preserves_comments(ws):
    """The actions-block write must be comment-preserving (ruamel)."""
    from vivarium_workbench.lib.feedback_actions import apply_feedback_action, feedback_item_id

    _make_study(ws, "foo", [{"id": "F-01", "statement": "X"}])
    iid = feedback_item_id("study-foo", "2026-01-01T10:00:00Z", "Alice")
    fb = ws / "investigations" / "inv1" / "feedback" / "r1.yaml"
    fb.parent.mkdir(parents=True, exist_ok=True)
    fb.write_text(
        "meta:\n"
        "  investigation: inv1\n"
        "# expert annotations below\n"
        "annotations:\n"
        "  study-foo:\n"
        "  - ts: '2026-01-01T10:00:00Z'\n"
        "    author: Alice\n"
        "    text: fix it\n"
        "actions:\n"
        f"  {iid}:\n"
        "    kind: next_action  # chosen by the responder skill\n"
        "    target_study: foo\n"
        "    target_finding: F-01\n"
        "    proposed_text: test X\n"
        "    status: open\n"
    )
    apply_feedback_action(ws, iid)
    txt = fb.read_text()
    assert "# expert annotations below" in txt
    assert "# chosen by the responder skill" in txt
    assert "status: applied" in txt


def test_apply_unknown_item_id(ws):
    from vivarium_workbench.lib.feedback_actions import apply_feedback_action

    res = apply_feedback_action(ws, "deadbeef")
    assert res.get("applied") is not True
    assert res.get("error")


def test_record_feedback_action_writes_block(ws):
    from vivarium_workbench.lib.feedback_actions import (
        feedback_item_id,
        record_feedback_action,
        study_feedback_actions,
    )

    iid = feedback_item_id("study-foo", "2026-01-01T10:00:00Z", "Alice")
    _write(
        ws / "investigations" / "inv1" / "feedback" / "r1.yaml",
        {
            "meta": {"investigation": "inv1"},
            "annotations": {
                "study-foo": [
                    {"ts": "2026-01-01T10:00:00Z", "author": "Alice", "text": "fix"},
                ],
            },
        },
    )
    record_feedback_action(
        ws, iid,
        kind="next_action", target_study="foo",
        target_finding="F-01", proposed_text="do the thing",
    )
    res = study_feedback_actions(ws, "foo")
    it = next(i for i in res["items"] if i["item_id"] == iid)
    assert it["action"]["kind"] == "next_action"
    assert it["action"]["proposed_text"] == "do the thing"
    assert it["status"] == "open"


# ── Golden: real v2e-invest workspace (read-only → tmp copy) ──────────────────

REAL_WS = Path("/Users/eranagmon/code/v2e-invest")


@pytest.mark.skipif(not REAL_WS.is_dir(), reason="v2e-invest workspace not present")
def test_golden_apply_on_tmp_copy(tmp_path):
    """A kind:next_action action applied on a TMP COPY writes the finding's
    next_action + flips the action applied; the real v2e-invest is untouched."""
    import shutil

    from vivarium_workbench.lib.feedback_actions import (
        apply_feedback_action,
        feedback_item_id,
        record_feedback_action,
        study_feedback_actions,
    )
    from discovery_engine.workspace_paths import WorkspacePaths

    slug = "dnaa-3-box-binding"
    wp_real = WorkspacePaths.load(REAL_WS)
    try:
        study_dir = wp_real.study_dir(slug)
    except FileNotFoundError:
        pytest.skip(f"study {slug} not present")

    # Find a real open feedback item for this study.
    tracked = study_feedback_actions(REAL_WS, slug)
    assert isinstance(tracked["items"], list)
    assert "open" in tracked["summary"]
    open_items = [i for i in tracked["items"] if i["status"] == "open"]
    if not open_items:
        pytest.skip("no open feedback items to exercise")
    src_item = open_items[0]

    # Mirror the workspace into a tmp copy and operate there.
    dst = tmp_path / "ws"
    shutil.copytree(REAL_WS, dst, symlinks=True, ignore=shutil.ignore_patterns(
        ".git", "out", "__pycache__", ".venv", "*.pyc"))

    spec = yaml.safe_load((wp_real.study_dir(slug) / "study.yaml").read_text())
    findings = spec.get("findings") or []
    if not findings:
        pytest.skip("study has no findings to target")
    target_finding = findings[0]["id"]

    iid = src_item["item_id"]
    record_feedback_action(
        dst, iid,
        kind="next_action", target_study=slug,
        target_finding=target_finding,
        proposed_text="GOLDEN: calibrate to literature",
    )
    res = apply_feedback_action(dst, iid)
    assert res.get("applied") is True

    wp_dst = WorkspacePaths.load(dst)
    new_spec = yaml.safe_load((wp_dst.study_dir(slug) / "study.yaml").read_text())
    f = next(f for f in new_spec["findings"] if f["id"] == target_finding)
    assert f["next_action"] == "GOLDEN: calibrate to literature"

    # Real workspace untouched.
    real_spec = yaml.safe_load((study_dir / "study.yaml").read_text())
    real_f = next(f for f in real_spec["findings"] if f["id"] == target_finding)
    assert real_f.get("next_action") != "GOLDEN: calibrate to literature"
