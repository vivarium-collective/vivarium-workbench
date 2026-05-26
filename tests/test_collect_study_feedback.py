"""Tests for vivarium_dashboard.server._collect_study_feedback (B.1)."""
import pytest

pytest.importorskip("pbg_superpowers.feedback_import")
import yaml


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def _collect(monkeypatch, ws, slug):
    import vivarium_dashboard.server as srv
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return srv._collect_study_feedback(slug)


def test_empty_when_no_investigations(tmp_path, monkeypatch):
    assert _collect(monkeypatch, tmp_path, "dnaa-00") == []


def test_collects_study_feedback_across_investigation(tmp_path, monkeypatch):
    inv = tmp_path / "investigations" / "dnaa-replication"
    _write(inv / "feedback" / "r1.yaml", {
        "meta": {"investigation": "dnaa-replication", "report_id": "rpt-1"},
        "annotations": {
            "study-dnaa-00-parameter-foundation-embeds": [
                {"author": "Haochen", "text": "longer time please",
                 "ts": "2026-05-21T00:03:28Z"},
            ],
            "study-dnaa-01-expression-dynamics-charts": [
                {"author": "Haochen", "text": "params not implemented",
                 "ts": "2026-05-21T02:10:11Z"},
            ],
        },
    })
    out = _collect(monkeypatch, tmp_path, "dnaa-00-parameter-foundation")
    assert len(out) == 1
    assert out[0]["author"] == "Haochen"
    assert out[0]["text"] == "longer time please"
    assert out[0]["section"] == "study-dnaa-00-parameter-foundation-embeds"


def test_newest_first_and_dedup(tmp_path, monkeypatch):
    inv = tmp_path / "investigations" / "dnaa-replication"
    sect = "study-dnaa-02-atp-hydrolysis-charts"
    _write(inv / "feedback" / "early.yaml", {
        "meta": {"investigation": "dnaa-replication"},
        "annotations": {sect: [
            {"author": "H", "text": "earlier", "ts": "2026-05-20T10:00:00Z"}]},
    })
    _write(inv / "feedback-2026-05-21" / "feedback.yaml", {
        "meta": {"investigation": "dnaa-replication"},
        "annotations": {sect: [
            {"author": "H", "text": "later", "ts": "2026-05-21T10:00:00Z"}]},
    })
    out = _collect(monkeypatch, tmp_path, "dnaa-02-atp-hydrolysis")
    assert [a["text"] for a in out] == ["later", "earlier"]
