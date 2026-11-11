"""`/api/prose-edit` — authoring a top-level scalar that doesn't yet exist.

The Overview's Claim / Model / Result blocks are inline-editable even when their
underlying field (`claim:` / `objective:` / `result:`) is absent from study.yaml
(see test_study_detail_render.test_overview_text_blocks_are_editable_even_when_fields_empty).
For that pencil to actually save, prose_edit must (a) whitelist `claim`/`result`
and (b) create a whitelisted TOP-LEVEL key when it's missing — while still
refusing to invent nested/indexed leaves.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from vivarium_workbench.lib import prose_edit_views as pe


def _study_ws(tmp_path: Path, spec: dict) -> Path:
    ws = tmp_path / "ws"
    sd = ws / "studies" / spec["name"]
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    return ws


def test_claim_and_result_are_whitelisted():
    assert pe._allowed("study", "claim")
    assert pe._allowed("study", "result")
    assert not pe._allowed("study", "not_a_field")


def test_set_path_creates_a_missing_top_level_scalar():
    data = {"name": "s1", "question": "q?"}
    pe._set_path(data, "claim", "Area caps abundance, not richness.")
    assert data["claim"] == "Area caps abundance, not richness."


def test_set_path_still_refuses_to_create_a_missing_nested_leaf():
    data = {"name": "s1", "purpose": {}}
    try:
        pe._set_path(data, "purpose.question", "q?")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("nested key creation should be refused")


def test_prose_edit_writes_claim_when_absent(tmp_path):
    ws = _study_ws(tmp_path, {"schema_version": 3, "name": "s1", "question": "q?"})
    body, status = pe.prose_edit(ws, {
        "kind": "study", "slug": "s1", "field": "claim",
        "value": "Area caps abundance, not richness.",
    })
    assert status == 200 and body["saved"] is True
    saved = yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())
    assert saved["claim"] == "Area caps abundance, not richness."
    assert saved["question"] == "q?"  # untouched


def test_prose_edit_writes_result_when_absent(tmp_path):
    ws = _study_ws(tmp_path, {"schema_version": 3, "name": "s1", "question": "q?"})
    body, status = pe.prose_edit(ws, {
        "kind": "study", "slug": "s1", "field": "result", "value": "Richness ~ constant in area.",
    })
    assert status == 200 and body["saved"] is True
    saved = yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())
    assert saved["result"] == "Richness ~ constant in area."
