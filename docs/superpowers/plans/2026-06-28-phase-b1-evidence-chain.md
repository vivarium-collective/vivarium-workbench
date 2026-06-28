# Phase B1 — Evidence-Chain Node Types · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Type the scientific-evidence chain — add Evidence/Decision/Conclusion as bigraph-schema nodes (Finding exists from Phase A) with lifecycle, cross-node invariants, author endpoints, and events, so a published Conclusion provably traces to accepted Evidence via recorded Decisions.

**Architecture:** Three new node types + a `validate_chain` referential validator in `investigation-contracts`; three `POST` endpoints in `vivarium-dashboard` that write addressable `<study>/{evidence,decisions,conclusions}/<id>.yaml` node files then emit events (mirroring Phase A's `POST /api/finding`). A Decision is the single act that moves its evidence `proposed → accepted`; `POST /api/conclusion` is a hard 422 gate on `validate_chain`.

**Tech Stack:** Python 3.11, bigraph-schema (`Core(BASE_TYPES)`, `register_type`, `check`), pydantic, FastAPI, pytest.

## Global Constraints

- **Shared venv:** `/Users/eranagmon/code/venv`. Test base: `/Users/eranagmon/code/venv/bin/python -m pytest`.
- **bigraph-schema:** build cores with `bigraph_schema.Core(bigraph_schema.BASE_TYPES)` — NEVER `allocate_core()` (crashes this env). Composite type = `register_type(key, {field: type})`; `core.check(type, value) -> bool`; enum = `{'_type':'enum','_values':[...]}`. Base types include `string integer float boolean list tree map`; `list[string]` shorthand works.
- **investigation-contracts** at `/Users/eranagmon/code/investigation-contracts` (editable in the shared venv). Current: `EVENT_TYPES=("FindingCreated",)`; `ACTOR_KIND._values` already includes `"shared"`; node types `investigation_node`, `finding`; `LIFECYCLES` has `finding`; version `0.1.0`.
- **Dashboard work** in worktree `/Users/eranagmon/code/vdash-phaseB` (branch `feat/phase-b-evidence-chain`, off `origin/main` which includes merged Phase A). Run dashboard tests from there.
- **Mirror Phase A:** `lib/finding_views.create_finding` + `POST /api/finding` are the exact pattern for the new workers/routes (layout-aware study resolve via `WorkspacePaths`, `make_core().check`, `atomic_write_text`, `emit_event`).
- **Drift guard:** write the node atomically BEFORE `emit_event`. **Hard gate:** `/api/conclusion` runs `validate_chain` and returns 422 (no write, no emit) if the conclusion has any violation.
- **Node id format:** `finding/<id>`, `evidence/<id>`, `decision/<id>`, `conclusion/<id>` (the node's `id` field). References hold these full ids.
- **New event types:** `EvidenceLinked`, `DecisionRecorded`, `ConclusionPublished`.

---

### Task 1: contracts — node types + new event types

**Files:**
- Modify: `/Users/eranagmon/code/investigation-contracts/investigation_contracts/schema.py`
- Test: `/Users/eranagmon/code/investigation-contracts/tests/test_chain_types.py`

**Interfaces:**
- Produces: `make_core()` validates types `evidence`, `decision`, `conclusion`; `EVENT_TYPES` includes the 3 new names; module constant `INVESTIGATION_NODE_FIELDS`.

- [ ] **Step 1: Write the failing test**

`tests/test_chain_types.py`:
```python
from investigation_contracts.schema import make_core, EVENT_TYPES


def _node(t, **extra):
    base = {"id": f"{t}/x", "type": t, "lifecycle_state": "proposed", "owner": "shared",
            "provenance": {"actor": "agentic", "agent_id": "p", "timestamp": "t",
                           "source_objects": [], "justification": "j", "tool": "", "commit": ""},
            "validation_status": "ok"}
    base.update(extra); return base


def test_new_event_types_registered():
    for e in ("EvidenceLinked", "DecisionRecorded", "ConclusionPublished"):
        assert e in EVENT_TYPES


def test_evidence_type_checks():
    core = make_core()
    good = _node("evidence", findings=["finding/f1"], hypotheses=["H rises"], confidence=0.8, statement="s")
    assert core.check("evidence", good) is True
    bad = _node("evidence", findings=["finding/f1"], hypotheses=["H"], confidence="high", statement="s")  # confidence wrong type
    assert core.check("evidence", bad) is False


def test_decision_type_checks_outcome_enum():
    core = make_core()
    good = _node("decision", owner="human", lifecycle_state="recorded",
                 evidence=["evidence/e1"], outcome="accept", rationale="r", decided_by="curator")
    assert core.check("decision", good) is True
    bad = dict(good); bad["outcome"] = "maybe"
    assert core.check("decision", bad) is False


def test_conclusion_type_checks():
    core = make_core()
    good = _node("conclusion", owner="human", lifecycle_state="draft",
                 evidence=["evidence/e1"], decisions=["decision/d1"], hypotheses=["H"], statement="s")
    assert core.check("conclusion", good) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_chain_types.py -q`
Expected: FAIL — `EvidenceLinked` not in `EVENT_TYPES` / `core.check("evidence", ...)` errors on unknown type.

- [ ] **Step 3: Edit `schema.py`**

Change the `EVENT_TYPES` constant (currently `("FindingCreated",)`):
```python
EVENT_TYPES = ("FindingCreated", "EvidenceLinked", "DecisionRecorded", "ConclusionPublished")
```
Add the outcome enum next to the other enums (after `VALIDATION_STATUS`):
```python
DECISION_OUTCOME = {"_type": "enum", "_values": ["accept", "reject", "defer"]}
```
Add a shared base-fields constant above `make_core` (and use it to DRY the existing `investigation_node` + `finding` registrations):
```python
INVESTIGATION_NODE_FIELDS = {
    "id": "string",
    "type": "string",
    "lifecycle_state": "string",
    "owner": "actor_kind",
    "provenance": "provenance",
    "validation_status": "validation_status",
}
```
In `make_core`, register `decision_outcome` (alongside the other enum registrations) and replace the inline `investigation_node` + `finding` dicts with `INVESTIGATION_NODE_FIELDS`-based ones, then add the three new node types. The node-registration block becomes:
```python
    core.register_type("decision_outcome", DECISION_OUTCOME)
    core.register_type("investigation_node", dict(INVESTIGATION_NODE_FIELDS))
    core.register_type("finding", {**INVESTIGATION_NODE_FIELDS,
                                   "statement": "string", "runs": "list[string]"})
    core.register_type("evidence", {**INVESTIGATION_NODE_FIELDS,
                                    "findings": "list[string]", "hypotheses": "list[string]",
                                    "confidence": "float", "statement": "string"})
    core.register_type("decision", {**INVESTIGATION_NODE_FIELDS,
                                    "evidence": "list[string]", "outcome": "decision_outcome",
                                    "rationale": "string", "decided_by": "string"})
    core.register_type("conclusion", {**INVESTIGATION_NODE_FIELDS,
                                      "evidence": "list[string]", "decisions": "list[string]",
                                      "hypotheses": "list[string]", "statement": "string"})
```
(Keep `EVENT_TYPE = {"_type": "enum", "_values": list(EVENT_TYPES)}` — it auto-picks up the new event names. The existing Phase-A schema tests still pass: `finding` keeps the same fields, `EVENT_TYPES` only grows.)

- [ ] **Step 4: Run to verify pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/ -q`
Expected: PASS (new chain-types tests + all existing contract tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/investigation-contracts
git add -A && git commit -q -m "feat: evidence/decision/conclusion node types + new event types"
```

---

### Task 2: contracts — lifecycle tables

**Files:**
- Modify: `/Users/eranagmon/code/investigation-contracts/investigation_contracts/lifecycle.py`
- Test: `/Users/eranagmon/code/investigation-contracts/tests/test_chain_lifecycle.py`

**Interfaces:**
- Consumes: `lifecycle.check_transition`, `lifecycle.initial_state`.
- Produces: `LIFECYCLES` includes `evidence`, `decision`, `conclusion`.

- [ ] **Step 1: Write the failing test**

`tests/test_chain_lifecycle.py`:
```python
from investigation_contracts.lifecycle import check_transition, initial_state


def test_initial_states():
    assert initial_state("evidence") == "proposed"
    assert initial_state("decision") == "pending"
    assert initial_state("conclusion") == "draft"


def test_evidence_transitions():
    assert check_transition("evidence", "proposed", "accepted") is True
    assert check_transition("evidence", "proposed", "rejected") is True
    assert check_transition("evidence", "accepted", "proposed") is False


def test_decision_transitions():
    assert check_transition("decision", "pending", "recorded") is True
    assert check_transition("decision", "recorded", "pending") is False


def test_conclusion_transitions():
    assert check_transition("conclusion", "draft", "published") is True
    assert check_transition("conclusion", "published", "draft") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_chain_lifecycle.py -q`
Expected: FAIL — `initial_state("evidence")` raises `KeyError`.

- [ ] **Step 3: Edit `lifecycle.py`** — add to the `LIFECYCLES` dict (after the `finding` entry):
```python
    "evidence": {"": ["proposed"], "proposed": ["accepted", "rejected"],
                 "accepted": [], "rejected": []},
    "decision": {"": ["pending"], "pending": ["recorded"], "recorded": []},
    "conclusion": {"": ["draft"], "draft": ["published"], "published": []},
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_chain_lifecycle.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/investigation-contracts
git add -A && git commit -q -m "feat: evidence/decision/conclusion lifecycle tables"
```

---

### Task 3: contracts — `validate_chain` referential invariants

**Files:**
- Create: `/Users/eranagmon/code/investigation-contracts/investigation_contracts/chain.py`
- Modify: `investigation_contracts/__init__.py` (export `validate_chain`)
- Test: `tests/test_validate_chain.py`

**Interfaces:**
- Produces: `chain.validate_chain(nodes: dict[str, dict]) -> list[dict]` returning `{node_id, invariant, message}` violation dicts (`[]` = sound).

- [ ] **Step 1: Write the failing test**

`tests/test_validate_chain.py`:
```python
from investigation_contracts.chain import validate_chain


def _finding(i, runs=("run/1",)):
    return {"id": f"finding/{i}", "type": "finding", "lifecycle_state": "proposed", "runs": list(runs)}

def _evidence(i, findings=("finding/f1",), hyps=("H",), state="proposed"):
    return {"id": f"evidence/{i}", "type": "evidence", "lifecycle_state": state,
            "findings": list(findings), "hypotheses": list(hyps), "confidence": 0.5}

def _decision(i, evidence=("evidence/e1",), outcome="accept"):
    return {"id": f"decision/{i}", "type": "decision", "lifecycle_state": "recorded",
            "evidence": list(evidence), "outcome": outcome}

def _conclusion(i, evidence=("evidence/e1",), decisions=("decision/d1",)):
    return {"id": f"conclusion/{i}", "type": "conclusion", "lifecycle_state": "draft",
            "evidence": list(evidence), "decisions": list(decisions)}

def _idx(*nodes):
    return {n["id"]: n for n in nodes}


def test_sound_chain_has_no_violations():
    nodes = _idx(_finding("f1"), _evidence("e1", state="accepted"),
                 _decision("d1"), _conclusion("c1"))
    assert validate_chain(nodes) == []

def test_finding_without_run():
    nodes = _idx(_finding("f1", runs=()))
    v = validate_chain(nodes)
    assert any(x["invariant"] == "finding->run" for x in v)

def test_evidence_without_finding():
    nodes = _idx(_evidence("e1", findings=()))
    v = validate_chain(nodes)
    assert any(x["invariant"] == "evidence->finding" for x in v)

def test_evidence_finding_ref_unresolved():
    nodes = _idx(_evidence("e1", findings=("finding/nope",), hyps=("H",)))
    v = validate_chain(nodes)
    assert any(x["invariant"] == "evidence->finding" for x in v)

def test_evidence_without_hypothesis():
    nodes = _idx(_finding("f1"), _evidence("e1", hyps=("", "  ")))
    v = validate_chain(nodes)
    assert any(x["invariant"] == "evidence->hypothesis" for x in v)

def test_conclusion_evidence_not_accepted():
    nodes = _idx(_finding("f1"), _evidence("e1", state="proposed"),
                 _decision("d1"), _conclusion("c1"))
    v = validate_chain(nodes)
    assert any(x["invariant"] == "conclusion->accepted" and x["node_id"] == "conclusion/c1" for x in v)

def test_conclusion_no_accepting_decision():
    nodes = _idx(_finding("f1"), _evidence("e1", state="accepted"),
                 _conclusion("c1", decisions=()))   # no decision referenced
    v = validate_chain(nodes)
    assert any(x["invariant"] == "conclusion->decision" and x["node_id"] == "conclusion/c1" for x in v)
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_validate_chain.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `chain.py`**

```python
"""Cross-node referential invariants for the Actionable Investigation Graph
(RFC-0002 Phase B). Pure function over a resolved node set."""
from __future__ import annotations


def validate_chain(nodes: dict[str, dict]) -> list[dict]:
    """nodes: {node_id -> node dict}. Returns violation dicts
    {node_id, invariant, message}; [] means the chain is sound."""
    viol: list[dict] = []

    def add(nid, inv, msg):
        viol.append({"node_id": nid, "invariant": inv, "message": msg})

    # evidence_id -> [decision_id] where the decision accepted it
    accepts: dict[str, list[str]] = {}
    for nid, n in nodes.items():
        if n.get("type") == "decision" and n.get("outcome") == "accept":
            for eid in n.get("evidence", []) or []:
                accepts.setdefault(eid, []).append(nid)

    for nid, n in nodes.items():
        t = n.get("type")
        if t == "finding":
            if len(n.get("runs", []) or []) < 1:
                add(nid, "finding->run", "finding references no run")
        elif t == "evidence":
            fids = n.get("findings", []) or []
            if len(fids) < 1:
                add(nid, "evidence->finding", "evidence references no finding")
            for fid in fids:
                ref = nodes.get(fid)
                if ref is None or ref.get("type") != "finding":
                    add(nid, "evidence->finding", f"finding ref does not resolve: {fid}")
            if len([h for h in (n.get("hypotheses", []) or []) if str(h).strip()]) < 1:
                add(nid, "evidence->hypothesis", "evidence references no hypothesis")
        elif t == "conclusion":
            decs = set(n.get("decisions", []) or [])
            for eid in n.get("evidence", []) or []:
                ev = nodes.get(eid)
                if ev is None or ev.get("type") != "evidence":
                    add(nid, "conclusion->evidence", f"evidence ref does not resolve: {eid}")
                    continue
                if ev.get("lifecycle_state") != "accepted":
                    add(nid, "conclusion->accepted", f"evidence not accepted: {eid}")
                if not (set(accepts.get(eid, [])) & decs):
                    add(nid, "conclusion->decision",
                        f"no referenced accept-decision for evidence: {eid}")
    return viol
```
Add to `__init__.py`: `from investigation_contracts.chain import validate_chain` + `"validate_chain"` in `__all__`.

- [ ] **Step 4: Run to verify pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_validate_chain.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/investigation-contracts
git add -A && git commit -q -m "feat: validate_chain referential invariants"
```

---

### Task 4: contracts — pydantic mirror + version bump

**Files:**
- Modify: `investigation_contracts/models.py` (add models), `investigation_contracts/__init__.py` (export), `pyproject.toml` (version)
- Test: `tests/test_chain_models.py`

**Interfaces:**
- Produces: pydantic `Evidence`, `Decision`, `Conclusion`, `EvidenceCreateBody`, `DecisionCreateBody`, `ConclusionCreateBody`.

- [ ] **Step 1: Write the failing test**

`tests/test_chain_models.py`:
```python
from investigation_contracts.models import (EvidenceCreateBody, DecisionCreateBody, ConclusionCreateBody)


def test_evidence_body():
    b = EvidenceCreateBody(study="demo", findings=["finding/f1"], hypotheses=["H rises"],
                           confidence=0.7, statement="s")
    assert b.findings == ["finding/f1"] and b.confidence == 0.7


def test_decision_body_outcome_enum():
    DecisionCreateBody(study="demo", evidence=["evidence/e1"], outcome="accept", rationale="r")
    import pytest
    with pytest.raises(Exception):
        DecisionCreateBody(study="demo", evidence=["evidence/e1"], outcome="maybe")


def test_conclusion_body():
    c = ConclusionCreateBody(study="demo", evidence=["evidence/e1"], decisions=["decision/d1"],
                             hypotheses=["H"], statement="s")
    assert c.decisions == ["decision/d1"]
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/test_chain_models.py -q`
Expected: FAIL — names not importable.

- [ ] **Step 3: Add to `models.py`** (after the existing `Finding`/`FindingCreateBody`):
```python
class Evidence(BaseModel):
    id: str
    type: Literal["evidence"] = "evidence"
    lifecycle_state: str = "proposed"
    owner: Literal["shared"] = "shared"
    provenance: Provenance
    validation_status: Literal["ok", "unresolved", "invalid", "unverified"] = "ok"
    findings: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    statement: str = ""


class Decision(BaseModel):
    id: str
    type: Literal["decision"] = "decision"
    lifecycle_state: str = "recorded"
    owner: Literal["human"] = "human"
    provenance: Provenance
    validation_status: Literal["ok", "unresolved", "invalid", "unverified"] = "ok"
    evidence: list[str] = Field(default_factory=list)
    outcome: Literal["accept", "reject", "defer"]
    rationale: str = ""
    decided_by: str = ""


class Conclusion(BaseModel):
    id: str
    type: Literal["conclusion"] = "conclusion"
    lifecycle_state: str = "draft"
    owner: Literal["human"] = "human"
    provenance: Provenance
    validation_status: Literal["ok", "unresolved", "invalid", "unverified"] = "ok"
    evidence: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    statement: str = ""


class EvidenceCreateBody(BaseModel):
    study: str
    findings: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    statement: str = ""


class DecisionCreateBody(BaseModel):
    study: str
    evidence: list[str] = Field(default_factory=list)
    outcome: Literal["accept", "reject", "defer"]
    rationale: str = ""
    decided_by: str = ""


class ConclusionCreateBody(BaseModel):
    study: str
    evidence: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    statement: str = ""
```
Export the 6 names from `__init__.py` (add to the `from investigation_contracts.models import (...)` line + `__all__`). Bump `pyproject.toml` `version = "0.2.0"`.

- [ ] **Step 4: Run full contracts suite**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests/ -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/investigation-contracts
git add -A && git commit -q -m "feat: pydantic mirror for chain nodes + bodies; v0.2.0"
```

---

### Task 5: dashboard — `node_store.load_study_nodes`

**Files:**
- Create: `/Users/eranagmon/code/vdash-phaseB/vivarium_dashboard/lib/node_store.py`
- Test: `tests/test_node_store.py`

**Interfaces:**
- Produces: `node_store.load_study_nodes(ws_root: Path, slug: str) -> dict[str, dict]` (keyed by each node's `id`); `node_store.study_dir(ws_root, slug) -> Path | None` (layout-aware).

- [ ] **Step 1: Write the failing test**

`tests/test_node_store.py`:
```python
import yaml
from pathlib import Path
from vivarium_dashboard.lib.node_store import load_study_nodes, study_dir


def _seed(ws: Path):
    d = ws / "studies" / "demo"
    (d / "findings").mkdir(parents=True)
    (d / "evidence").mkdir()
    (d / "findings" / "f1.yaml").write_text(yaml.safe_dump(
        {"id": "finding/f1", "type": "finding", "runs": ["run/1"]}))
    (d / "evidence" / "e1.yaml").write_text(yaml.safe_dump(
        {"id": "evidence/e1", "type": "evidence", "findings": ["finding/f1"]}))


def test_loads_nodes_keyed_by_id(tmp_path):
    _seed(tmp_path)
    nodes = load_study_nodes(tmp_path, "demo")
    assert set(nodes) == {"finding/f1", "evidence/e1"}
    assert nodes["finding/f1"]["type"] == "finding"


def test_missing_study_returns_empty(tmp_path):
    assert load_study_nodes(tmp_path, "nope") == {}


def test_tolerates_missing_dirs_and_bad_yaml(tmp_path):
    d = tmp_path / "studies" / "demo" / "findings"; d.mkdir(parents=True)
    (d / "ok.yaml").write_text("id: finding/ok\ntype: finding\n")
    (d / "bad.yaml").write_text("{not: valid: yaml:")
    nodes = load_study_nodes(tmp_path, "demo")
    assert "finding/ok" in nodes  # bad file skipped, no crash
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/eranagmon/code/vdash-phaseB && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_node_store.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `node_store.py`**

```python
"""Load a study's typed AIG node files (RFC-0002 Phase B), keyed by node id."""
from __future__ import annotations

from pathlib import Path

import yaml

_NODE_DIRS = ("findings", "evidence", "decisions", "conclusions")


def study_dir(ws_root: Path, slug: str) -> Path | None:
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


def load_study_nodes(ws_root: Path, slug: str) -> dict[str, dict]:
    sdir = study_dir(ws_root, slug)
    if sdir is None:
        return {}
    nodes: dict[str, dict] = {}
    for sub in _NODE_DIRS:
        d = sdir / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            try:
                node = yaml.safe_load(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — skip malformed, never fatal
                continue
            if isinstance(node, dict) and node.get("id"):
                nodes[node["id"]] = node
    return nodes
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/eranagmon/code/vdash-phaseB && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_node_store.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/eranagmon/code/vdash-phaseB
git add vivarium_dashboard/lib/node_store.py tests/test_node_store.py
git commit -q -m "feat(chain): node_store.load_study_nodes"
```

---

### Task 6: dashboard — `POST /api/evidence`

**Files:**
- Create: `vivarium_dashboard/lib/chain_views.py`
- Modify: `vivarium_dashboard/api/app.py` (route + import)
- Test: `tests/test_evidence_route.py`

**Interfaces:**
- Consumes: `node_store.study_dir`, `event_log.emit_event`, `atomic_io.atomic_write_text`, `investigation_contracts` (`make_core`, `initial_state`, `EvidenceCreateBody`).
- Produces: `chain_views.create_evidence(ws_root, body) -> tuple[dict, int]`; route `POST /api/evidence`.

- [ ] **Step 1: Write the failing test**

`tests/test_evidence_route.py`:
```python
import pytest
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace
from vivarium_dashboard.lib.event_log import log_path
from investigation_contracts import read_log


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root(); active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "studies" / "demo").mkdir(parents=True)
    (tmp_path / "studies" / "demo" / "study.yaml").write_text("name: demo\n")
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def test_post_evidence_writes_and_emits(client, ws):
    r = client.post("/api/evidence", json={"study": "demo", "findings": ["finding/f1"],
                                           "hypotheses": ["H rises"], "confidence": 0.8, "statement": "s"})
    assert r.status_code == 200, r.text
    eid = r.json()["evidence_id"]
    assert (ws / "studies" / "demo" / "evidence" / f"{eid}.yaml").is_file()
    evs = read_log(log_path(ws), types=["EvidenceLinked"])
    assert len(evs) == 1 and r.json()["event_id"] == evs[0]["event_id"]


def test_post_evidence_requires_finding_and_hypothesis(client):
    r = client.post("/api/evidence", json={"study": "demo", "findings": [], "hypotheses": ["H"]})
    assert r.status_code == 400
    r = client.post("/api/evidence", json={"study": "demo", "findings": ["finding/f1"], "hypotheses": []})
    assert r.status_code == 400


def test_post_evidence_missing_study_404(client):
    r = client.post("/api/evidence", json={"study": "nope", "findings": ["finding/f1"], "hypotheses": ["H"]})
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/eranagmon/code/vdash-phaseB && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_evidence_route.py -q`
Expected: FAIL — route not registered.

- [ ] **Step 3: Implement `chain_views.create_evidence`**

`vivarium_dashboard/lib/chain_views.py`:
```python
"""Author endpoints for the evidence chain (RFC-0002 Phase B). Each writes an
addressable node file atomically, then emits its event (drift guard)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from vivarium_dashboard.lib.atomic_io import atomic_write_text
from vivarium_dashboard.lib.event_log import emit_event
from vivarium_dashboard.lib.node_store import study_dir
from investigation_contracts import make_core
from investigation_contracts.lifecycle import initial_state


def _prov(actor: str, agent_id: str, srcs: list[str], why: str, tool: str) -> dict:
    return {"actor": actor, "agent_id": agent_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_objects": list(srcs), "justification": why, "tool": tool, "commit": ""}


def _write_node(sdir: Path, subdir: str, fid: str, node: dict) -> None:
    d = sdir / subdir
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_text(d / f"{fid}.yaml", yaml.safe_dump(node, sort_keys=False))


def create_evidence(ws_root: Path, body: dict) -> tuple[dict, int]:
    slug = (body.get("study") or "").strip()
    findings = body.get("findings") or []
    hyps = [h for h in (body.get("hypotheses") or []) if str(h).strip()]
    sdir = study_dir(ws_root, slug)
    if sdir is None:
        return {"error": f"study not found: {slug}"}, 404
    if len(findings) < 1 or len(hyps) < 1:
        return {"error": "evidence requires >=1 finding and >=1 hypothesis"}, 400
    eid = "e" + uuid.uuid4().hex[:10]
    prov = _prov("agentic", body.get("agent_id", "unknown"), findings,
                 "evidence linked via /api/evidence", "api/evidence")
    node = {"id": f"evidence/{eid}", "type": "evidence",
            "lifecycle_state": initial_state("evidence"), "owner": "shared",
            "provenance": prov, "validation_status": "ok",
            "findings": list(findings), "hypotheses": list(hyps),
            "confidence": float(body.get("confidence") or 0.0),
            "statement": body.get("statement", "")}
    if not make_core().check("evidence", node):
        return {"error": "constructed evidence node failed contract validation"}, 500
    _write_node(sdir, "evidence", eid, node)
    event_id = emit_event(ws_root, type="EvidenceLinked", subject=f"evidence/{eid}",
                          transition={"from": "", "to": initial_state("evidence")},
                          actor="agentic", provenance=prov,
                          payload={"study": slug, "evidence_id": eid})
    return {"evidence_id": eid, "event_id": event_id}, 200
```

- [ ] **Step 4: Add the route to `app.py`**

Add `EvidenceCreateBody` to the `investigation_contracts` imports; add `from vivarium_dashboard.lib import chain_views as _chain_views` near the other `_*_views` aliases. Add the route near `POST /api/finding`:
```python
    @app.post("/api/evidence", tags=["Studies"],
              summary="Link Evidence (findings -> hypotheses) and emit EvidenceLinked")
    def create_evidence(req: EvidenceCreateBody, ws: Path = Depends(get_workspace)):
        body, status = _chain_views.create_evidence(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body
```

- [ ] **Step 5: Run to verify pass + import check**

Run: `cd /Users/eranagmon/code/vdash-phaseB && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_evidence_route.py -q && /Users/eranagmon/code/venv/bin/python -c "import vivarium_dashboard.api.app"`
Expected: PASS (3 passed); import OK.

- [ ] **Step 6: Commit**

```bash
cd /Users/eranagmon/code/vdash-phaseB
git add vivarium_dashboard/lib/chain_views.py vivarium_dashboard/api/app.py tests/test_evidence_route.py
git commit -q -m "feat(api): POST /api/evidence"
```

---

### Task 7: dashboard — `POST /api/decision` (records + advances evidence)

**Files:**
- Modify: `vivarium_dashboard/lib/chain_views.py` (add `create_decision`), `vivarium_dashboard/api/app.py` (route + import)
- Test: `tests/test_decision_route.py`

**Interfaces:**
- Consumes: `node_store.load_study_nodes`, `lifecycle.check_transition`, `investigation_contracts.DecisionCreateBody`.
- Produces: `chain_views.create_decision(ws_root, body) -> tuple[dict, int]`; route `POST /api/decision`. A `create_decision` with `outcome=accept` rewrites each referenced evidence node `proposed → accepted`.

- [ ] **Step 1: Write the failing test**

`tests/test_decision_route.py`:
```python
import pytest, yaml
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root(); active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "studies" / "demo"; (d).mkdir(parents=True)
    (d / "study.yaml").write_text("name: demo\n")
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def _evidence_id(client):
    r = client.post("/api/evidence", json={"study": "demo", "findings": ["finding/f1"],
                                           "hypotheses": ["H"], "confidence": 0.5})
    return r.json()["evidence_id"]


def test_accept_decision_advances_evidence(client, ws):
    eid = _evidence_id(client)
    r = client.post("/api/decision", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                           "outcome": "accept", "rationale": "ok", "decided_by": "curator"})
    assert r.status_code == 200, r.text
    did = r.json()["decision_id"]
    assert (ws / "studies" / "demo" / "decisions" / f"{did}.yaml").is_file()
    ev = yaml.safe_load((ws / "studies" / "demo" / "evidence" / f"{eid}.yaml").read_text())
    assert ev["lifecycle_state"] == "accepted"


def test_reject_decision_rejects_evidence(client, ws):
    eid = _evidence_id(client)
    client.post("/api/decision", json={"study": "demo", "evidence": [f"evidence/{eid}"], "outcome": "reject"})
    ev = yaml.safe_load((ws / "studies" / "demo" / "evidence" / f"{eid}.yaml").read_text())
    assert ev["lifecycle_state"] == "rejected"


def test_bad_outcome_422_from_pydantic(client):
    r = client.post("/api/decision", json={"study": "demo", "evidence": [], "outcome": "maybe"})
    assert r.status_code == 422  # pydantic Literal rejection
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/eranagmon/code/vdash-phaseB && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_decision_route.py -q`
Expected: FAIL — route not registered.

- [ ] **Step 3: Add `create_decision` to `chain_views.py`**

```python
def create_decision(ws_root: Path, body: dict) -> tuple[dict, int]:
    from vivarium_dashboard.lib.node_store import load_study_nodes, study_dir
    from investigation_contracts.lifecycle import check_transition
    slug = (body.get("study") or "").strip()
    outcome = body.get("outcome")
    evidence_refs = body.get("evidence") or []
    sdir = study_dir(ws_root, slug)
    if sdir is None:
        return {"error": f"study not found: {slug}"}, 404
    if outcome not in ("accept", "reject", "defer"):
        return {"error": "outcome must be accept|reject|defer"}, 400
    did = "d" + uuid.uuid4().hex[:10]
    prov = _prov("human", body.get("decided_by", "unknown"), evidence_refs,
                 "decision recorded via /api/decision", "api/decision")
    node = {"id": f"decision/{did}", "type": "decision", "lifecycle_state": "recorded",
            "owner": "human", "provenance": prov, "validation_status": "ok",
            "evidence": list(evidence_refs), "outcome": outcome,
            "rationale": body.get("rationale", ""), "decided_by": body.get("decided_by", "")}
    if not make_core().check("decision", node):
        return {"error": "constructed decision node failed contract validation"}, 500
    _write_node(sdir, "decisions", did, node)
    # The decision is the single act that moves its evidence.
    target = {"accept": "accepted", "reject": "rejected"}.get(outcome)
    if target:
        nodes = load_study_nodes(ws_root, slug)
        for eref in evidence_refs:
            ev = nodes.get(eref)
            if ev and check_transition("evidence", ev.get("lifecycle_state", ""), target):
                ev["lifecycle_state"] = target
                _write_node(sdir, "evidence", eref.split("/", 1)[-1], ev)
    event_id = emit_event(ws_root, type="DecisionRecorded", subject=f"decision/{did}",
                          transition={"from": "", "to": "recorded"}, actor="human",
                          provenance=prov, payload={"study": slug, "decision_id": did, "outcome": outcome})
    return {"decision_id": did, "event_id": event_id}, 200
```

- [ ] **Step 4: Add the route to `app.py`** — add `DecisionCreateBody` to the `investigation_contracts` import; add:
```python
    @app.post("/api/decision", tags=["Studies"],
              summary="Record a Decision on Evidence and emit DecisionRecorded")
    def create_decision(req: DecisionCreateBody, ws: Path = Depends(get_workspace)):
        body, status = _chain_views.create_decision(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body
```

- [ ] **Step 5: Run to verify pass**

Run: `cd /Users/eranagmon/code/vdash-phaseB && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_decision_route.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
cd /Users/eranagmon/code/vdash-phaseB
git add vivarium_dashboard/lib/chain_views.py vivarium_dashboard/api/app.py tests/test_decision_route.py
git commit -q -m "feat(api): POST /api/decision (records decision, advances evidence)"
```

---

### Task 8: dashboard — `POST /api/conclusion` (hard validate_chain gate)

**Files:**
- Modify: `vivarium_dashboard/lib/chain_views.py` (add `create_conclusion`), `vivarium_dashboard/api/app.py` (route + import)
- Test: `tests/test_conclusion_route.py`

**Interfaces:**
- Consumes: `node_store.load_study_nodes`, `investigation_contracts.validate_chain`, `ConclusionCreateBody`.
- Produces: `chain_views.create_conclusion(ws_root, body) -> tuple[dict, int]`; route `POST /api/conclusion` (422 + `{error, violations}` when the chain is unsound; 200 + `ConclusionPublished` when sound).

- [ ] **Step 1: Write the failing test**

`tests/test_conclusion_route.py`:
```python
import pytest
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace
from vivarium_dashboard.lib.event_log import log_path
from investigation_contracts import read_log


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root(); active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


@pytest.fixture
def ws(tmp_path):
    d = tmp_path / "studies" / "demo"; d.mkdir(parents=True)
    (d / "study.yaml").write_text("name: demo\n")
    (d / "findings").mkdir()
    import yaml
    (d / "findings" / "f1.yaml").write_text(yaml.safe_dump(
        {"id": "finding/f1", "type": "finding", "lifecycle_state": "proposed", "runs": ["run/1"]}))
    return tmp_path


@pytest.fixture
def client(ws):
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: ws
    return TestClient(app)


def _evidence(client):
    return client.post("/api/evidence", json={"study": "demo", "findings": ["finding/f1"],
                                              "hypotheses": ["H"], "confidence": 0.5}).json()["evidence_id"]


def test_conclusion_before_decision_is_422(client, ws):
    eid = _evidence(client)
    r = client.post("/api/conclusion", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                             "decisions": [], "statement": "C"})
    assert r.status_code == 422
    assert r.json()["violations"]
    # nothing written / emitted
    assert not (ws / "studies" / "demo" / "conclusions").exists()
    assert read_log(log_path(ws), types=["ConclusionPublished"]) == []


def test_conclusion_after_accept_decision_publishes(client, ws):
    eid = _evidence(client)
    did = client.post("/api/decision", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                             "outcome": "accept"}).json()["decision_id"]
    r = client.post("/api/conclusion", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                             "decisions": [f"decision/{did}"], "statement": "C"})
    assert r.status_code == 200, r.text
    cid = r.json()["conclusion_id"]
    import yaml
    node = yaml.safe_load((ws / "studies" / "demo" / "conclusions" / f"{cid}.yaml").read_text())
    assert node["lifecycle_state"] == "published"
    assert read_log(log_path(ws), types=["ConclusionPublished"])[-1]["event_id"] == r.json()["event_id"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/eranagmon/code/vdash-phaseB && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_conclusion_route.py -q`
Expected: FAIL — route not registered.

- [ ] **Step 3: Add `create_conclusion` to `chain_views.py`**

```python
def create_conclusion(ws_root: Path, body: dict) -> tuple[dict, int]:
    from vivarium_dashboard.lib.node_store import load_study_nodes, study_dir
    from investigation_contracts import validate_chain
    slug = (body.get("study") or "").strip()
    sdir = study_dir(ws_root, slug)
    if sdir is None:
        return {"error": f"study not found: {slug}"}, 404
    cid = "c" + uuid.uuid4().hex[:10]
    prov = _prov("human", body.get("decided_by", "unknown"),
                 list(body.get("evidence") or []) + list(body.get("decisions") or []),
                 "conclusion published via /api/conclusion", "api/conclusion")
    node = {"id": f"conclusion/{cid}", "type": "conclusion", "lifecycle_state": "draft",
            "owner": "human", "provenance": prov, "validation_status": "ok",
            "evidence": list(body.get("evidence") or []),
            "decisions": list(body.get("decisions") or []),
            "hypotheses": list(body.get("hypotheses") or []),
            "statement": body.get("statement", "")}
    if not make_core().check("conclusion", node):
        return {"error": "constructed conclusion node failed contract validation"}, 500
    # HARD GATE: the chain must be sound for THIS conclusion before publishing.
    nodes = load_study_nodes(ws_root, slug)
    nodes[node["id"]] = node
    violations = [v for v in validate_chain(nodes) if v["node_id"] == node["id"]]
    if violations:
        return {"error": "conclusion chain is unsound", "violations": violations}, 422
    node["lifecycle_state"] = "published"
    _write_node(sdir, "conclusions", cid, node)
    event_id = emit_event(ws_root, type="ConclusionPublished", subject=f"conclusion/{cid}",
                          transition={"from": "draft", "to": "published"}, actor="human",
                          provenance=prov, payload={"study": slug, "conclusion_id": cid})
    return {"conclusion_id": cid, "event_id": event_id}, 200
```

- [ ] **Step 4: Add the route to `app.py`** — add `ConclusionCreateBody` and `validate_chain` is used in the worker (not the route). Add:
```python
    @app.post("/api/conclusion", tags=["Studies"],
              summary="Publish a Conclusion (gated on validate_chain) and emit ConclusionPublished")
    def create_conclusion(req: ConclusionCreateBody, ws: Path = Depends(get_workspace)):
        body, status = _chain_views.create_conclusion(ws, req.model_dump())
        if status != 200:
            return JSONResponse(status_code=status, content=body)
        return body
```

- [ ] **Step 5: Run to verify pass + import check**

Run: `cd /Users/eranagmon/code/vdash-phaseB && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_conclusion_route.py -q && /Users/eranagmon/code/venv/bin/python -c "import vivarium_dashboard.api.app"`
Expected: PASS (2 passed); import OK.

- [ ] **Step 6: Commit**

```bash
cd /Users/eranagmon/code/vdash-phaseB
git add vivarium_dashboard/lib/chain_views.py vivarium_dashboard/api/app.py tests/test_conclusion_route.py
git commit -q -m "feat(api): POST /api/conclusion with hard validate_chain gate"
```

---

### Task 9: dashboard — end-to-end chain test + regression

**Files:**
- Create: `tests/test_phase_b1_e2e.py`

**Interfaces:** Consumes all of B1.

- [ ] **Step 1: Write the end-to-end test**

`tests/test_phase_b1_e2e.py`:
```python
import pytest, yaml
from fastapi.testclient import TestClient
from vivarium_dashboard.api.app import create_app, get_workspace
from vivarium_dashboard.lib import active_workspace
from vivarium_dashboard.lib.event_log import log_path
from investigation_contracts import read_log


@pytest.fixture(autouse=True)
def _reset_ws():
    saved = active_workspace.get_workspace_root(); active_workspace._WS_ROOT = None
    yield
    active_workspace._WS_ROOT = saved


def test_finding_to_conclusion_closed_chain(tmp_path):
    d = tmp_path / "studies" / "demo"; d.mkdir(parents=True)
    (d / "study.yaml").write_text("name: demo\n")
    app = create_app(); app.dependency_overrides[get_workspace] = lambda: tmp_path
    c = TestClient(app)

    fid = c.post("/api/finding", json={"study": "demo", "statement": "X up with Y", "runs": ["run/1"]}).json()["finding_id"]
    eid = c.post("/api/evidence", json={"study": "demo", "findings": [f"finding/{fid}"],
                                        "hypotheses": ["Y drives X"], "confidence": 0.9}).json()["evidence_id"]
    # conclusion before a decision is refused
    assert c.post("/api/conclusion", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                           "decisions": [], "statement": "C"}).status_code == 422
    did = c.post("/api/decision", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                        "outcome": "accept", "decided_by": "curator"}).json()["decision_id"]
    r = c.post("/api/conclusion", json={"study": "demo", "evidence": [f"evidence/{eid}"],
                                        "decisions": [f"decision/{did}"], "statement": "C"})
    assert r.status_code == 200, r.text
    # all four node files exist; ConclusionPublished is the last event
    for sub, nid in (("findings", fid), ("evidence", eid), ("decisions", did), ("conclusions", r.json()["conclusion_id"])):
        assert (tmp_path / "studies" / "demo" / sub / f"{nid}.yaml").is_file()
    events = read_log(log_path(tmp_path))
    assert events[-1]["type"] == "ConclusionPublished"
    assert [e["type"] for e in events] == ["FindingCreated", "EvidenceLinked", "DecisionRecorded", "ConclusionPublished"]
```

- [ ] **Step 2: Run it**

Run: `cd /Users/eranagmon/code/vdash-phaseB && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_phase_b1_e2e.py -q`
Expected: PASS (1 passed) — the typed chain publishes only after the decision accepts the evidence.

- [ ] **Step 3: Full B1 regression**

Run:
```bash
/Users/eranagmon/code/venv/bin/python -m pytest /Users/eranagmon/code/investigation-contracts/tests -q
cd /Users/eranagmon/code/vdash-phaseB && /Users/eranagmon/code/venv/bin/python -m pytest tests/test_node_store.py tests/test_evidence_route.py tests/test_decision_route.py tests/test_conclusion_route.py tests/test_phase_b1_e2e.py -q
```
Expected: all green.

- [ ] **Step 4: Commit**

```bash
cd /Users/eranagmon/code/vdash-phaseB
git add tests/test_phase_b1_e2e.py
git commit -q -m "test: Phase B1 end-to-end evidence chain (finding->evidence->decision->conclusion)"
```

---

## Self-review

**Spec coverage:** evidence/decision/conclusion types → Task 1; lifecycle → Task 2; validate_chain invariants → Task 3; pydantic mirror + version → Task 4; node_store → Task 5; POST /api/evidence → Task 6; POST /api/decision + evidence-advance → Task 7; POST /api/conclusion + hard 422 gate → Task 8; end-to-end → Task 9. Drift guard (write-then-emit) in Tasks 6/7/8. Hypotheses as free-text strings (Task 1 `list[string]`, Task 4 `list[str]`). ✓

**Placeholder scan:** No TBD/"add error handling". All code blocks complete.

**Type consistency:** node id format `<type>/<id>` consistent across contracts (Task 3), node_store keying (Task 5), and all routes/tests. `create_evidence/decision/conclusion(ws_root, body) -> (dict, int)` uniform. `_write_node(sdir, subdir, fid, node)` + `_prov(...)` shared helpers defined in Task 6, reused in 7/8. Event types (`EvidenceLinked`/`DecisionRecorded`/`ConclusionPublished`) match between Task 1 (`EVENT_TYPES`) and the emit calls. `validate_chain` violation keys (`node_id`/`invariant`/`message`) consistent between Task 3 and the Task 8 gate filter.

**Cross-repo ordering:** Tasks 1-4 (contracts) before Tasks 5-9 (dashboard import `investigation_contracts`). Task 7 depends on Task 6 (`chain_views` + the evidence written by `/api/evidence`); Task 8 depends on 6+7; Task 9 on all.
