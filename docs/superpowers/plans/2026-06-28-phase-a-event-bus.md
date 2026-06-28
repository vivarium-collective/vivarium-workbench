# Phase A — Domain Event Bus · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the RFC-0002 closed loop with one event type — `FindingCreated` — flowing from a dashboard write, through a durable bigraph-schema-typed log, to a pbg-superpowers reactor.

**Architecture:** A new `investigation-contracts` package defines the event envelope, provenance, and a minimal Finding node as bigraph-schema types (validated by `core.check`) with a thin lifecycle-transition table; the dashboard appends typed events to `workspace/.pbg/events.jsonl` after each committed write and serves them over a new SSE; a pbg-superpowers `EventClient` tails the log from a persisted cursor and reacts.

**Tech Stack:** Python 3.11, bigraph-schema (`Core(BASE_TYPES)` — discovery-free), pydantic (transport mirror), FastAPI (dashboard), pytest.

## Global Constraints

- **Shared venv:** all three repos use `/Users/eranagmon/code/venv` (python 3.11). Test command base: `/Users/eranagmon/code/venv/bin/python -m pytest`.
- **bigraph-schema discovery is broken in this env** (a pre-existing py3.12 f-string in `pbg-emitters`). NEVER call `allocate_core()`. Build the contract core with `bigraph_schema.Core(bigraph_schema.BASE_TYPES)` — discovery-free.
- **Verified bigraph-schema API:** `core = Core(BASE_TYPES)`; `core.register_type(key, {field: type_name, ...})`; `core.check(type_name, value) -> bool`; enum type = `{'_type':'enum','_values':[...]}`; base types include `string integer float boolean list tree map`.
- **Contracts package home:** new repo at `/Users/eranagmon/code/investigation-contracts`, importable as `investigation_contracts`, installed `-e` into the shared venv.
- **Dashboard work** lands in the worktree `/Users/eranagmon/code/vdash-phaseA` (branch `feat/phase-a-event-bus`, off `origin/main`). Run dashboard tests from there: `cd /Users/eranagmon/code/vdash-phaseA && /Users/eranagmon/code/venv/bin/python -m pytest`.
- **pbg-superpowers work** lands on branch `feat/phase-a-event-client` in `/Users/eranagmon/code/pbg-superpowers`.
- **Drift guard:** `emit_event` is called ONLY after the caller's atomic write returns. State (YAML) is truth; the log is the notification.
- **At-least-once delivery:** handlers must be idempotent, keyed by `event_id`.
- **Do not touch** the legacy `server.py` or the existing polling `/api/events` workspace-state SSE.
- **Envelope `schema_version` = 1.** `event_type` enum starts as `['FindingCreated']`.

---

### Task 1: `investigation-contracts` — scaffold + envelope/provenance types + core

**Files:**
- Create: `/Users/eranagmon/code/investigation-contracts/pyproject.toml`
- Create: `/Users/eranagmon/code/investigation-contracts/investigation_contracts/__init__.py`
- Create: `/Users/eranagmon/code/investigation-contracts/investigation_contracts/schema.py`
- Test: `/Users/eranagmon/code/investigation-contracts/tests/test_schema.py`

**Interfaces:**
- Produces: `schema.make_core() -> bigraph_schema.Core` (contract types registered); `schema.validate_envelope(d: dict) -> tuple[bool, str|None]`; module constants `SCHEMA_VERSION = 1`, `EVENT_TYPES = ('FindingCreated',)`.

- [ ] **Step 1: Scaffold the repo**

```bash
mkdir -p /Users/eranagmon/code/investigation-contracts/investigation_contracts /Users/eranagmon/code/investigation-contracts/tests
cd /Users/eranagmon/code/investigation-contracts && git init -q
```

`pyproject.toml`:
```toml
[project]
name = "investigation-contracts"
version = "0.1.0"
description = "Shared bigraph-schema types for the Actionable Investigation Graph (RFC-0002)."
requires-python = ">=3.11"
dependencies = ["bigraph-schema", "pydantic>=2"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["investigation_contracts"]
```

`investigation_contracts/__init__.py`:
```python
from investigation_contracts.schema import make_core, validate_envelope, SCHEMA_VERSION, EVENT_TYPES

__all__ = ["make_core", "validate_envelope", "SCHEMA_VERSION", "EVENT_TYPES"]
```

- [ ] **Step 2: Write the failing test**

`tests/test_schema.py`:
```python
from investigation_contracts.schema import make_core, validate_envelope, SCHEMA_VERSION


def _good_envelope():
    return {
        "event_id": "0000000001",
        "type": "FindingCreated",
        "occurred_at": "2026-06-28T00:00:00Z",
        "actor": "agentic",
        "subject": "finding/abc123",
        "transition": {"from": "", "to": "proposed"},
        "provenance": {"actor": "agentic", "agent_id": "planner", "timestamp": "t",
                       "source_objects": [], "justification": "j", "tool": "", "commit": ""},
        "payload": {"study": "demo"},
        "schema_version": SCHEMA_VERSION,
    }


def test_core_registers_contract_types():
    core = make_core()
    assert core.check("event_envelope", _good_envelope()) is True
    assert core.check("provenance", _good_envelope()["provenance"]) is True


def test_validate_envelope_accepts_good():
    ok, err = validate_envelope(_good_envelope())
    assert ok is True and err is None


def test_validate_envelope_rejects_bad_type_and_missing_fields():
    bad = {"event_id": 123, "type": "FindingCreated"}  # wrong type + missing fields
    ok, err = validate_envelope(bad)
    assert ok is False and isinstance(err, str)


def test_validate_envelope_rejects_unknown_event_type():
    e = _good_envelope(); e["type"] = "NotARealEvent"
    ok, err = validate_envelope(e)
    assert ok is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'investigation_contracts'`.

- [ ] **Step 4: Implement `schema.py`**

```python
"""Bigraph-schema types for the Actionable Investigation Graph contracts (RFC-0002).

Built on a discovery-free ``Core(BASE_TYPES)`` — NEVER ``allocate_core()`` (it
triggers package discovery, which is broken in this environment).
"""
from __future__ import annotations

import bigraph_schema

SCHEMA_VERSION = 1
EVENT_TYPES = ("FindingCreated",)

ACTOR_KIND = {"_type": "enum", "_values": ["human", "agentic", "computational"]}
EVENT_TYPE = {"_type": "enum", "_values": list(EVENT_TYPES)}
VALIDATION_STATUS = {"_type": "enum", "_values": ["ok", "unresolved", "invalid", "unverified"]}

PROVENANCE = {
    "actor": "actor_kind",
    "agent_id": "string",
    "timestamp": "string",
    "source_objects": "list[string]",
    "justification": "string",
    "tool": "string",
    "commit": "string",
}

TRANSITION = {"from": "string", "to": "string"}

EVENT_ENVELOPE = {
    "event_id": "string",
    "type": "event_type",
    "occurred_at": "string",
    "actor": "actor_kind",
    "subject": "string",
    "transition": "transition",
    "provenance": "provenance",
    "payload": "tree",
    "schema_version": "integer",
}


def make_core() -> "bigraph_schema.Core":
    core = bigraph_schema.Core(bigraph_schema.BASE_TYPES)
    core.register_type("actor_kind", ACTOR_KIND)
    core.register_type("event_type", EVENT_TYPE)
    core.register_type("validation_status", VALIDATION_STATUS)
    core.register_type("provenance", PROVENANCE)
    core.register_type("transition", TRANSITION)
    core.register_type("event_envelope", EVENT_ENVELOPE)
    return core


_CORE = None


def _core() -> "bigraph_schema.Core":
    global _CORE
    if _CORE is None:
        _CORE = make_core()
    return _CORE


def validate_envelope(d: dict) -> tuple[bool, str | None]:
    """Return ``(ok, error)`` — structural validation against ``event_envelope``."""
    try:
        if not isinstance(d, dict):
            return False, "envelope must be a dict"
        if d.get("type") not in EVENT_TYPES:
            return False, f"unknown event type: {d.get('type')!r}"
        ok = _core().check("event_envelope", d)
        return (True, None) if ok else (False, "envelope failed event_envelope type check")
    except Exception as e:  # noqa: BLE001 — validation must never raise
        return False, f"validation error: {e}"
```

> Note: `list[string]` and `tree` are base parametric/base types in `BASE_TYPES`. If `core.register_type` rejects the `"list[string]"` shorthand, register `source_objects` as `"list"` (untyped element) — confirm with a one-line REPL check before settling; the test only requires the good envelope to pass and the bad one to fail.

- [ ] **Step 5: Run test to verify it passes**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_schema.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Install editable + commit**

```bash
/Users/eranagmon/code/venv/bin/python -m pip install -e /Users/eranagmon/code/investigation-contracts --no-deps
cd /Users/eranagmon/code/investigation-contracts
printf '__pycache__/\n*.egg-info/\n.pytest_cache/\n' > .gitignore
git add -A && git commit -q -m "feat: investigation-contracts — event envelope + provenance as bigraph-schema types"
```

---

### Task 2: `investigation-contracts` — Finding node + lifecycle transition table

**Files:**
- Modify: `investigation_contracts/schema.py` (add node types to `make_core`)
- Create: `investigation_contracts/lifecycle.py`
- Test: `tests/test_lifecycle.py`

**Interfaces:**
- Consumes: `schema.make_core`.
- Produces: `lifecycle.LIFECYCLES: dict[str, dict[str, list[str]]]`; `lifecycle.check_transition(node_type: str, frm: str, to: str) -> bool`; `lifecycle.initial_state(node_type: str) -> str`. `schema` core also validates type `finding`.

- [ ] **Step 1: Write the failing test**

`tests/test_lifecycle.py`:
```python
from investigation_contracts.lifecycle import check_transition, initial_state
from investigation_contracts.schema import make_core


def test_finding_initial_state():
    assert initial_state("finding") == "proposed"


def test_finding_legal_transition():
    assert check_transition("finding", "proposed", "reviewed") is True
    assert check_transition("finding", "reviewed", "accepted") is True


def test_finding_illegal_transition_rejected():
    assert check_transition("finding", "accepted", "proposed") is False
    assert check_transition("finding", "proposed", "accepted") is False  # must go via reviewed


def test_unknown_node_type_rejects():
    assert check_transition("does_not_exist", "a", "b") is False


def test_finding_node_type_checks():
    core = make_core()
    good = {"id": "finding/x", "type": "finding", "lifecycle_state": "proposed",
            "owner": "shared", "provenance": {"actor": "agentic", "agent_id": "p",
            "timestamp": "t", "source_objects": [], "justification": "j", "tool": "", "commit": ""},
            "validation_status": "ok", "statement": "X rises with Y", "runs": ["run/1"]}
    assert core.check("finding", good) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_lifecycle.py -q`
Expected: FAIL — `ModuleNotFoundError: investigation_contracts.lifecycle`.

- [ ] **Step 3: Implement `lifecycle.py`**

```python
"""The thin lifecycle transition-table layer (RFC-0002 §2 caveat).

State machines per node type. ``check_transition`` is the enforcement the
graph's apply-path calls; promoting it to a bigraph-schema custom ``_apply`` is
a Phase-B refinement.
"""
from __future__ import annotations

# node_type -> {from_state: [allowed_to_states]}; "" is the pre-creation state.
LIFECYCLES: dict[str, dict[str, list[str]]] = {
    "finding": {
        "": ["proposed"],
        "proposed": ["reviewed"],
        "reviewed": ["accepted", "rejected"],
        "accepted": [],
        "rejected": [],
    },
}


def initial_state(node_type: str) -> str:
    table = LIFECYCLES.get(node_type)
    if not table:
        raise KeyError(f"no lifecycle for node type {node_type!r}")
    return table[""][0]


def check_transition(node_type: str, frm: str, to: str) -> bool:
    table = LIFECYCLES.get(node_type)
    if not table:
        return False
    return to in table.get(frm, [])
```

- [ ] **Step 4: Add the `finding` + `investigation_node` types to `make_core`**

In `schema.py`, add before the `event_envelope` registration in `make_core` (after `provenance`):
```python
    core.register_type("investigation_node", {
        "id": "string",
        "type": "string",
        "lifecycle_state": "string",
        "owner": "actor_kind",
        "provenance": "provenance",
        "validation_status": "validation_status",
    })
    core.register_type("finding", {
        "id": "string",
        "type": "string",
        "lifecycle_state": "string",
        "owner": "actor_kind",
        "provenance": "provenance",
        "validation_status": "validation_status",
        "statement": "string",
        "runs": "list[string]",
    })
```
> `owner` on `finding` is `actor_kind` whose enum includes `shared`? It does not — extend `ACTOR_KIND` `_values` to `["human","agentic","computational","shared"]` so `owner: "shared"` checks. Update Task-1 constant accordingly (and its test still passes: `actor` values stay a subset).

- [ ] **Step 5: Run tests to verify pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/ -q`
Expected: PASS (all schema + lifecycle tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/eranagmon/code/investigation-contracts
git add -A && git commit -q -m "feat: Finding node type + lifecycle transition table"
```

---

### Task 3: `investigation-contracts` — canonical `read_log` reader

**Files:**
- Create: `investigation_contracts/log.py`
- Modify: `investigation_contracts/__init__.py` (export `read_log`)
- Test: `tests/test_log.py`

**Interfaces:**
- Produces: `log.read_log(path: str|Path, cursor: str|None=None, types: list[str]|None=None) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

`tests/test_log.py`:
```python
import json
from pathlib import Path
from investigation_contracts.log import read_log


def _write(p: Path, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_read_all(tmp_path):
    p = tmp_path / "events.jsonl"
    _write(p, [{"event_id": "01", "type": "A"}, {"event_id": "02", "type": "B"}])
    assert [e["event_id"] for e in read_log(p)] == ["01", "02"]


def test_cursor_is_exclusive(tmp_path):
    p = tmp_path / "events.jsonl"
    _write(p, [{"event_id": "01", "type": "A"}, {"event_id": "02", "type": "A"}])
    assert [e["event_id"] for e in read_log(p, cursor="01")] == ["02"]


def test_type_filter(tmp_path):
    p = tmp_path / "events.jsonl"
    _write(p, [{"event_id": "01", "type": "A"}, {"event_id": "02", "type": "B"}])
    assert [e["event_id"] for e in read_log(p, types=["B"])] == ["02"]


def test_missing_file_and_malformed_line(tmp_path):
    assert read_log(tmp_path / "nope.jsonl") == []
    p = tmp_path / "events.jsonl"
    p.write_text('{"event_id":"01","type":"A"}\nnot json\n{"event_id":"02","type":"A"}\n')
    assert [e["event_id"] for e in read_log(p)] == ["01", "02"]
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_log.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `log.py`**

```python
"""Canonical reader for the append-only event log (shared by both spines)."""
from __future__ import annotations

import json
from pathlib import Path


def read_log(path, cursor: str | None = None, types: list[str] | None = None) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    out: list[dict] = []
    passed_cursor = cursor is None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:  # noqa: BLE001 — skip malformed, never fatal
            continue
        if not passed_cursor:
            if ev.get("event_id") == cursor:
                passed_cursor = True
            continue
        if types is not None and ev.get("type") not in types:
            continue
        out.append(ev)
    return out
```
Add to `__init__.py`: `from investigation_contracts.log import read_log` and add `"read_log"` to `__all__`.

- [ ] **Step 4: Run to verify pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_log.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/investigation-contracts
git add -A && git commit -q -m "feat: canonical read_log reader"
```

---

### Task 4: `investigation-contracts` — pydantic transport mirror

**Files:**
- Create: `investigation_contracts/models.py`
- Modify: `investigation_contracts/__init__.py` (export models)
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: pydantic `EventEnvelope`, `Provenance`, `Finding`, `FindingCreateBody`.

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
from investigation_contracts.models import FindingCreateBody, EventEnvelope


def test_finding_create_body():
    b = FindingCreateBody(study="demo", statement="X up with Y", runs=["run/1"])
    assert b.study == "demo" and b.runs == ["run/1"]


def test_event_envelope_roundtrips_to_contract_dict():
    from investigation_contracts.schema import validate_envelope
    env = EventEnvelope(
        event_id="01", type="FindingCreated", occurred_at="t", actor="agentic",
        subject="finding/x", transition={"from": "", "to": "proposed"},
        provenance={"actor": "agentic", "agent_id": "p", "timestamp": "t",
                    "source_objects": [], "justification": "j", "tool": "", "commit": ""},
        payload={"study": "demo"}, schema_version=1)
    ok, err = validate_envelope(env.model_dump())
    assert ok is True, err
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_models.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `models.py`**

```python
"""Pydantic transport mirror of the bigraph-schema contracts.

The bigraph-schema types (schema.py) are canonical; these mirror them for
request/response validation at API boundaries (FastAPI). Keep field names
identical — they ARE the same contract in two representations.
"""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class Provenance(BaseModel):
    actor: Literal["human", "agentic", "computational"]
    agent_id: str = ""
    timestamp: str = ""
    source_objects: list[str] = Field(default_factory=list)
    justification: str = ""
    tool: str = ""
    commit: str = ""


class EventEnvelope(BaseModel):
    event_id: str
    type: Literal["FindingCreated"]
    occurred_at: str
    actor: Literal["human", "agentic", "computational"]
    subject: str
    transition: dict
    provenance: Provenance
    payload: dict
    schema_version: int


class Finding(BaseModel):
    id: str
    type: Literal["finding"] = "finding"
    lifecycle_state: str = "proposed"
    owner: Literal["shared"] = "shared"
    provenance: Provenance
    validation_status: Literal["ok", "unresolved", "invalid", "unverified"] = "ok"
    statement: str
    runs: list[str] = Field(default_factory=list)


class FindingCreateBody(BaseModel):
    study: str
    statement: str
    runs: list[str] = Field(default_factory=list)
    hypothesis: Optional[str] = None
```
Export from `__init__.py`: `from investigation_contracts.models import EventEnvelope, Provenance, Finding, FindingCreateBody` + add to `__all__`.

- [ ] **Step 4: Run to verify pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/ -q`
Expected: PASS (all contract tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/investigation-contracts
git add -A && git commit -q -m "feat: pydantic transport mirror (EventEnvelope, Finding, FindingCreateBody)"
```

---

### Task 5: dashboard — `event_log` writer + `emit_event`

**Files:**
- Create: `vivarium_dashboard/lib/event_log.py`
- Test: `tests/test_event_log.py`

**Interfaces:**
- Consumes: `investigation_contracts.validate_envelope`, `investigation_contracts.read_log`; `vivarium_dashboard.lib.atomic_io.atomic_write_text`.
- Produces: `event_log.append(ws_root: Path, envelope: dict) -> str`; `event_log.emit_event(ws_root, *, type, subject, transition, actor, provenance, payload) -> str`; `event_log.log_path(ws_root) -> Path`.

- [ ] **Step 1: Write the failing test**

`tests/test_event_log.py`:
```python
from pathlib import Path
from vivarium_dashboard.lib import event_log
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/eranagmon/code/vdash-phaseA && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_event_log.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `event_log.py`**

```python
"""Durable append-only event log writer (RFC-0002 Phase A).

Writes typed events to ``workspace/.pbg/events.jsonl`` AFTER a committed state
write (the drift guard). The canonical READER lives in
``investigation_contracts.read_log``; this module owns only the writer + the
``emit_event`` envelope builder.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from investigation_contracts import validate_envelope, SCHEMA_VERSION


def _pbg_dir(ws_root: Path) -> Path:
    d = Path(ws_root) / ".pbg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path(ws_root: Path) -> Path:
    return _pbg_dir(ws_root) / "events.jsonl"


def _next_event_id(ws_root: Path) -> str:
    seq = _pbg_dir(ws_root) / "events.seq"
    n = 0
    if seq.is_file():
        try:
            n = int(seq.read_text().strip() or "0")
        except ValueError:
            n = 0
    n += 1
    tmp = seq.with_suffix(".seq.tmp")
    tmp.write_text(str(n), encoding="utf-8")
    os.replace(tmp, seq)
    return f"{n:012d}"


def append(ws_root: Path, envelope: dict) -> str:
    ok, err = validate_envelope(envelope)
    if not ok:
        raise ValueError(f"invalid event envelope: {err}")
    line = json.dumps(envelope, separators=(",", ":")) + "\n"
    path = log_path(ws_root)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return envelope["event_id"]


def emit_event(ws_root: Path, *, type: str, subject: str, transition: dict,
               actor: str, provenance: dict, payload: dict) -> str:
    """Build + append a typed event. Call ONLY after the state write commits."""
    envelope = {
        "event_id": _next_event_id(ws_root),
        "type": type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "subject": subject,
        "transition": transition,
        "provenance": provenance,
        "payload": payload,
        "schema_version": SCHEMA_VERSION,
    }
    return append(ws_root, envelope)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/eranagmon/code/vdash-phaseA && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_event_log.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vdash-phaseA
git add vivarium_dashboard/lib/event_log.py tests/test_event_log.py
git commit -q -m "feat(events): durable append-only event log writer + emit_event"
```

---

### Task 6: dashboard — `finding_views` worker + `POST /api/finding` route

**Files:**
- Create: `vivarium_dashboard/lib/finding_views.py`
- Modify: `vivarium_dashboard/api/app.py` (add the route + import `FindingCreateBody`)
- Test: `tests/test_finding_route.py`

**Interfaces:**
- Consumes: `event_log.emit_event`; `atomic_io.atomic_write_text`; `WorkspacePaths` (layout-aware study resolution — see `lib/readouts_views.build_study_readouts`); `investigation_contracts.FindingCreateBody`.
- Produces: `finding_views.create_finding(ws_root: Path, body: dict) -> tuple[dict, int]`; route `POST /api/finding`.

- [ ] **Step 1: Write the failing test**

`tests/test_finding_route.py`:
```python
import pytest
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace
from investigation_contracts import read_log
from vivarium_dashboard.lib.event_log import log_path


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root()
    active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "studies" / "demo").mkdir(parents=True)
    (tmp_path / "studies" / "demo" / "study.yaml").write_text("name: demo\n")
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app()
    app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def test_post_finding_writes_node_and_emits_after(client, ws):
    r = client.post("/api/finding", json={"study": "demo", "statement": "X up with Y", "runs": ["run/1"]})
    assert r.status_code == 200, r.text
    body = r.json()
    fid = body["finding_id"]
    # finding node file exists
    assert (ws / "studies" / "demo" / "findings" / f"{fid}.yaml").is_file()
    # event emitted, references the finding (emit-after-commit: file exists too)
    events = read_log(log_path(ws), types=["FindingCreated"])
    assert len(events) == 1 and events[0]["payload"]["study"] == "demo"
    assert body["event_id"] == events[0]["event_id"]


def test_post_finding_missing_study_404(client):
    r = client.post("/api/finding", json={"study": "nope", "statement": "s", "runs": []})
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/eranagmon/code/vdash-phaseA && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_finding_route.py -q`
Expected: FAIL — route not registered → 404 on the happy path (or import error).

- [ ] **Step 3: Implement `finding_views.py`**

```python
"""POST /api/finding worker — write a Finding node, then emit FindingCreated."""
from __future__ import annotations

import uuid
from pathlib import Path

import yaml

from vivarium_dashboard.lib.atomic_io import atomic_write_text
from vivarium_dashboard.lib.event_log import emit_event
from investigation_contracts.lifecycle import initial_state


def _study_dir(ws_root: Path, slug: str) -> Path | None:
    try:
        from vivarium_dashboard.lib.workspace_paths import WorkspacePaths
        wp = WorkspacePaths.load(ws_root)
        d = wp.studies / slug
        if d.is_dir():
            return d
    except Exception:  # noqa: BLE001
        pass
    d = Path(ws_root) / "studies" / slug
    return d if d.is_dir() else None


def create_finding(ws_root: Path, body: dict) -> tuple[dict, int]:
    slug = (body.get("study") or "").strip()
    statement = (body.get("statement") or "").strip()
    runs = body.get("runs") or []
    if not slug or not statement:
        return {"error": "study and statement are required"}, 400
    sdir = _study_dir(ws_root, slug)
    if sdir is None:
        return {"error": f"study not found: {slug}"}, 404

    fid = "f" + uuid.uuid4().hex[:10]
    prov = {"actor": "agentic", "agent_id": body.get("agent_id", "unknown"),
            "timestamp": "", "source_objects": list(runs),
            "justification": "finding proposed via /api/finding", "tool": "api/finding", "commit": ""}
    node = {
        "id": f"finding/{fid}", "type": "finding",
        "lifecycle_state": initial_state("finding"), "owner": "shared",
        "provenance": prov, "validation_status": "ok",
        "statement": statement, "runs": list(runs),
    }
    # 1) commit the state write (atomic)
    fdir = sdir / "findings"
    fdir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(fdir / f"{fid}.yaml", yaml.safe_dump(node, sort_keys=False))
    # 2) emit AFTER the commit
    event_id = emit_event(
        ws_root, type="FindingCreated", subject=f"finding/{fid}",
        transition={"from": "", "to": initial_state("finding")}, actor="agentic",
        provenance=prov, payload={"study": slug, "finding_id": fid, "statement": statement},
    )
    return {"finding_id": fid, "event_id": event_id}, 200
```

- [ ] **Step 4: Add the route to `app.py`**

Add `FindingCreateBody` to the imports (near the other model imports): `from investigation_contracts import FindingCreateBody`. Add the worker alias near the other `_*_views` aliases: `from vivarium_dashboard.lib import finding_views as _finding_views`. Add the route (place it near the other study POST routes, e.g. after `study_create_from_run`):
```python
    @app.post("/api/finding", tags=["Studies"],
              summary="Create a Finding node and emit FindingCreated")
    def create_finding(req: FindingCreateBody, ws: Path = Depends(get_workspace)):
        """Write a minimal Finding node into the study, then emit FindingCreated
        (RFC-0002 Phase A). 200 ``{finding_id, event_id}``; 400 invalid; 404 study
        not found."""
        body, status = _finding_views.create_finding(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body
```

- [ ] **Step 5: Run to verify pass + import check**

Run: `cd /Users/eranagmon/code/vdash-phaseA && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_finding_route.py -q && /Users/eranagmon/code/venv/bin/python -c "import vivarium_dashboard.api.app"`
Expected: PASS (2 passed); import OK.

- [ ] **Step 6: Commit**

```bash
cd /Users/eranagmon/code/vdash-phaseA
git add vivarium_dashboard/lib/finding_views.py vivarium_dashboard/api/app.py tests/test_finding_route.py
git commit -q -m "feat(api): POST /api/finding — write Finding node + emit FindingCreated"
```

---

### Task 7: dashboard — `GET /api/events/log` SSE

**Files:**
- Modify: `vivarium_dashboard/api/app.py` (add the SSE route)
- Test: `tests/test_events_log_sse.py`

**Interfaces:**
- Consumes: `investigation_contracts.read_log`; `event_log.log_path`.
- Produces: route `GET /api/events/log?since=&type=` honoring `Last-Event-ID`.

- [ ] **Step 1: Write the failing test** (bounded: emit events first, then assert one replay pass — no infinite stream)

`tests/test_events_log_sse.py`:
```python
import pytest
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace, event_log


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root(); active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


def _prov():
    return {"actor": "agentic", "agent_id": "p", "timestamp": "t",
            "source_objects": [], "justification": "j", "tool": "", "commit": ""}


@pytest.fixture
def ws(tmp_path):
    for s in ("f/1", "f/2"):
        event_log.emit_event(tmp_path, type="FindingCreated", subject=s,
                             transition={"from": "", "to": "proposed"}, actor="agentic",
                             provenance=_prov(), payload={"study": "demo"})
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def test_replay_all_then_close(client):
    # ?once=1 returns history and closes (test-only bounded mode)
    r = client.get("/api/events/log?once=1")
    assert r.status_code == 200
    ids = [ln[4:] for ln in r.text.splitlines() if ln.startswith("id: ")]
    assert ids == ["000000000001", "000000000002"]


def test_since_cursor(client):
    r = client.get("/api/events/log?once=1&since=000000000001")
    ids = [ln[4:] for ln in r.text.splitlines() if ln.startswith("id: ")]
    assert ids == ["000000000002"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/eranagmon/code/vdash-phaseA && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_events_log_sse.py -q`
Expected: FAIL — route not registered.

- [ ] **Step 3: Add the SSE route to `app.py`**

```python
    @app.get("/api/events/log", tags=["System"],
             summary="SSE typed-event stream (RFC-0002 Phase A)")
    def events_log(request: Request, since: str = "", type: str = "",
                   once: str = "", ws: Path = Depends(get_workspace)) -> StreamingResponse:
        """Tail workspace/.pbg/events.jsonl as SSE. ?since=<event_id> replay,
        Last-Event-ID header resume (wins over ?since), ?type filter. ?once=1 is a
        test-only bounded mode that replays history and closes."""
        from investigation_contracts import read_log
        from vivarium_dashboard.lib.event_log import log_path
        cursor = request.headers.get("Last-Event-ID") or (since or None)
        types = [type] if type else None
        bounded = bool(once)

        def gen():
            cur = cursor
            for ev in read_log(log_path(ws), cur, types):
                yield (f"id: {ev['event_id']}\nevent: {ev['type']}\n"
                       f"data: {json.dumps(ev, separators=(',', ':'))}\n\n").encode()
                cur = ev["event_id"]
            if bounded:
                return
            import time
            while True:
                for ev in read_log(log_path(ws), cur, types):
                    yield (f"id: {ev['event_id']}\nevent: {ev['type']}\n"
                           f"data: {json.dumps(ev, separators=(',', ':'))}\n\n").encode()
                    cur = ev["event_id"]
                time.sleep(1.0)

        return StreamingResponse(gen(), media_type="text/event-stream")
```
> `json`, `Request`, `StreamingResponse`, `Depends`, `Path` are already imported in `app.py` (confirm `Request` — it is used by the workspace-state events route).

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/eranagmon/code/vdash-phaseA && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_events_log_sse.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vdash-phaseA
git add vivarium_dashboard/api/app.py tests/test_events_log_sse.py
git commit -q -m "feat(api): GET /api/events/log SSE (replay + Last-Event-ID + type filter)"
```

---

### Task 8: pbg-superpowers — `EventClient` + `FindingCreated` handler

**Files:**
- Create: `/Users/eranagmon/code/pbg-superpowers/pbg_superpowers/event_client.py`
- Test: `/Users/eranagmon/code/pbg-superpowers/tests/test_event_client.py`

**Interfaces:**
- Consumes: `investigation_contracts.read_log`.
- Produces: `EventClient(ws_root, consumer)` with `.on(type, handler)`, `.poll_once() -> int`, `.run(poll_interval)`; module fn `on_finding_created(ws_root, envelope) -> Path` (writes the reaction record).

- [ ] **Step 1: Create the branch + write the failing test**

```bash
cd /Users/eranagmon/code/pbg-superpowers && git fetch origin -q && git worktree add -b feat/phase-a-event-client /Users/eranagmon/code/pbg-phaseA origin/main 2>/dev/null || git checkout -b feat/phase-a-event-client origin/main
```
Work in the pbg-superpowers checkout (or the `pbg-phaseA` worktree). `tests/test_event_client.py`:
```python
import json
from pathlib import Path
from pbg_superpowers.event_client import EventClient, on_finding_created


def _event(eid, etype="FindingCreated", study="demo", fid="fX"):
    return {"event_id": eid, "type": etype, "occurred_at": "t", "actor": "agentic",
            "subject": f"finding/{fid}", "transition": {"from": "", "to": "proposed"},
            "provenance": {"actor": "agentic", "agent_id": "p", "timestamp": "t",
                           "source_objects": [], "justification": "j", "tool": "", "commit": ""},
            "payload": {"study": study, "finding_id": fid, "statement": "s"}, "schema_version": 1}


def _seed(ws: Path, events):
    d = ws / ".pbg"; d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def test_dispatch_and_cursor_advance(tmp_path):
    _seed(tmp_path, [_event("01"), _event("02")])
    seen = []
    c = EventClient(tmp_path, consumer="test")
    c.on("FindingCreated", lambda ev: seen.append(ev["event_id"]))
    assert c.poll_once() == 2
    assert seen == ["01", "02"]
    # cursor persisted → a second poll handles nothing new
    assert c.poll_once() == 0


def test_type_filter(tmp_path):
    _seed(tmp_path, [_event("01", etype="OtherEvent"), _event("02")])
    seen = []
    c = EventClient(tmp_path, consumer="t2")
    c.on("FindingCreated", lambda ev: seen.append(ev["event_id"]))
    c.poll_once()
    assert seen == ["02"]


def test_handler_writes_reaction_record(tmp_path):
    p = on_finding_created(tmp_path, _event("07", fid="fAbc"))
    assert p.is_file()
    import yaml
    rec = yaml.safe_load(p.read_text())
    assert rec["finding_id"] == "fAbc" and rec["study"] == "demo"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/eranagmon/code/pbg-superpowers && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_event_client.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `event_client.py`**

```python
"""Agentic-spine event reactor (RFC-0002 Phase A).

Tails workspace/.pbg/events.jsonl from a persisted cursor and dispatches typed
events to handlers. At-least-once: handlers must be idempotent on event_id.
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from investigation_contracts import read_log


class EventClient:
    def __init__(self, ws_root, consumer: str):
        self.ws_root = Path(ws_root)
        self.consumer = consumer
        self._handlers: dict[str, list] = {}

    @property
    def _log(self) -> Path:
        return self.ws_root / ".pbg" / "events.jsonl"

    @property
    def _cursor_file(self) -> Path:
        return self.ws_root / ".pbg" / f"event_cursor.{self.consumer}"

    def _cursor(self):
        return self._cursor_file.read_text().strip() if self._cursor_file.is_file() else None

    def _set_cursor(self, event_id: str):
        self._cursor_file.parent.mkdir(parents=True, exist_ok=True)
        self._cursor_file.write_text(event_id, encoding="utf-8")

    def on(self, event_type: str, handler):
        self._handlers.setdefault(event_type, []).append(handler)
        return self

    def poll_once(self) -> int:
        types = list(self._handlers) or None
        n = 0
        for ev in read_log(self._log, self._cursor(), types):
            for h in self._handlers.get(ev.get("type"), []):
                h(ev)
            self._set_cursor(ev["event_id"])   # advance after handling (at-least-once)
            n += 1
        return n

    def run(self, poll_interval: float = 1.0):
        while True:
            self.poll_once()
            time.sleep(poll_interval)


def on_finding_created(ws_root, envelope: dict) -> Path:
    """Reaction handler: write a structured 'finding observed' record.

    Idempotent — keyed by event_id (overwrite-stable)."""
    ws_root = Path(ws_root)
    rdir = ws_root / ".pbg" / "reactions"
    rdir.mkdir(parents=True, exist_ok=True)
    payload = envelope.get("payload", {})
    record = {
        "observed_event": envelope["event_id"],
        "event_type": envelope["type"],
        "finding_id": payload.get("finding_id"),
        "study": payload.get("study"),
        "noted_at": envelope.get("occurred_at"),
        "next_action": "stub: agentic spine would propose the next study here",
    }
    out = rdir / f"{envelope['event_id']}.yaml"
    out.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/eranagmon/code/pbg-superpowers && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_event_client.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/pbg-superpowers
git add pbg_superpowers/event_client.py tests/test_event_client.py
git commit -q -m "feat: EventClient + FindingCreated reaction handler (RFC-0002 Phase A)"
```

---

### Task 9: End-to-end de-risking test (the closed loop)

**Files:**
- Create: `/Users/eranagmon/code/vdash-phaseA/tests/test_phase_a_e2e.py`

**Interfaces:**
- Consumes: everything — the dashboard `POST /api/finding`, the log, and the pbg `EventClient` + handler.

- [ ] **Step 1: Write the end-to-end test**

`tests/test_phase_a_e2e.py`:
```python
import pytest
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace
from pbg_superpowers.event_client import EventClient, on_finding_created


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root(); active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


def test_finding_to_reaction_closed_loop(tmp_path):
    (tmp_path / "studies" / "demo").mkdir(parents=True)
    (tmp_path / "studies" / "demo" / "study.yaml").write_text("name: demo\n")
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: tmp_path
    client = TestClient(app)

    # computational spine: write a Finding → emit FindingCreated
    r = client.post("/api/finding", json={"study": "demo", "statement": "X up with Y", "runs": ["run/1"]})
    assert r.status_code == 200, r.text
    fid = r.json()["finding_id"]

    # agentic spine: react
    c = EventClient(tmp_path, consumer="e2e")
    c.on("FindingCreated", lambda ev: on_finding_created(tmp_path, ev))
    assert c.poll_once() == 1

    # the loop closed: a reaction record references the finding
    eid = r.json()["event_id"]
    rec = (tmp_path / ".pbg" / "reactions" / f"{eid}.yaml")
    assert rec.is_file()
    import yaml
    assert yaml.safe_load(rec.read_text())["finding_id"] == fid
    # idempotent: re-poll from scratch cursor handles nothing new
    assert c.poll_once() == 0
```

- [ ] **Step 2: Run it**

Run: `cd /Users/eranagmon/code/vdash-phaseA && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_phase_a_e2e.py -q`
Expected: PASS (1 passed) — the closed loop works end to end.

- [ ] **Step 3: Full Phase-A regression**

Run:
```bash
/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests -q
cd /Users/eranagmon/code/vdash-phaseA && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_event_log.py tests/test_finding_route.py tests/test_events_log_sse.py tests/test_phase_a_e2e.py -q
cd /Users/eranagmon/code/pbg-superpowers && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_event_client.py -q
```
Expected: all green.

- [ ] **Step 4: Commit**

```bash
cd /Users/eranagmon/code/vdash-phaseA
git add tests/test_phase_a_e2e.py
git commit -q -m "test: Phase-A end-to-end closed loop (finding → event → reaction)"
```

---

## Self-review

**Spec coverage:**
- investigation-contracts: envelope/provenance/finding as bigraph-schema types → Tasks 1, 2; lifecycle transition table → Task 2; read_log → Task 3; pydantic mirror → Task 4. ✓
- dashboard emitter: event_log writer + emit-after-commit → Task 5; POST /api/finding (write node then emit) → Task 6; new SSE (replay + Last-Event-ID + type filter) → Task 7; existing /api/events untouched → not modified. ✓
- pbg reactor: EventClient (cursor, dispatch, at-least-once) + reaction handler → Task 8. ✓
- drift guard (emit-after-commit) → Task 6 ordering + Task 5/6 tests; idempotency → Task 8 + Task 9. ✓
- end-to-end de-risking test → Task 9. ✓
- findings stored as addressable `findings/<id>.yaml` → Task 6. ✓

**Placeholder scan:** No TBD/"add error handling". Two flagged verification notes (the `list[string]` registration shorthand in Task 1; `Request` already imported in Task 7) are deliberate one-line REPL/grep checks against real APIs this plan can't fully quote, each with the exact fallback — not logic placeholders.

**Type consistency:** `event_id` is a 12-digit zero-padded string everywhere (Task 5 `f"{n:012d}"` ↔ Task 7 test ids `000000000001` ↔ Task 8). `emit_event(...)` keyword signature identical in Tasks 5/6. `read_log(path, cursor, types)` identical in Tasks 3/5-test/7/8. `create_finding(ws_root, body) -> (dict, int)` consistent in Tasks 6. `on_finding_created(ws_root, envelope) -> Path` consistent in Tasks 8/9. Envelope field names identical across schema.py (Task 1), models.py (Task 4), emit_event (Task 5), and all tests.

**Cross-repo ordering:** Tasks 1–4 (contracts) must complete + `pip install -e` (Task 1 step 6) before Tasks 5–9 can import `investigation_contracts`. Task 9 depends on Tasks 6 + 8.
