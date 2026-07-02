from pathlib import Path
from vivarium_workbench.lib import event_log
from investigation_contracts import read_log, SCHEMA_VERSION


def _prov():
    return {"actor": "agentic", "agent_id": "p", "timestamp": "t",
            "source_objects": [], "justification": "j", "tool": "", "commit": ""}


def test_emit_then_read_roundtrip(tmp_path: Path):
    eid = event_log.emit_event(tmp_path, type="FindingCreated", subject="finding/x",
                               transition={"from": "", "to": "proposed"}, actor="agentic",
                               provenance=_prov(), payload={"study": "demo"})
    rows = read_log(event_log.log_path(tmp_path))
    assert len(rows) == 1
    assert rows[0]["event_id"] == eid
    assert rows[0]["type"] == "FindingCreated"
    assert rows[0]["schema_version"] == SCHEMA_VERSION


def test_event_ids_monotonic(tmp_path: Path):
    a = event_log.emit_event(tmp_path, type="FindingCreated", subject="f/1",
                             transition={"from": "", "to": "proposed"}, actor="agentic",
                             provenance=_prov(), payload={})
    b = event_log.emit_event(tmp_path, type="FindingCreated", subject="f/2",
                             transition={"from": "", "to": "proposed"}, actor="agentic",
                             provenance=_prov(), payload={})
    assert a < b


def test_append_rejects_malformed_envelope(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        event_log.append(tmp_path, {"event_id": "x", "type": "Nope"})
