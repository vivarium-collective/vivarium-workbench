# Phase B2 — Chain Derivation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the B4 investigation graph show each study's real Finding→Evidence→Decision→Conclusion chain, derived deterministically from existing `study.yaml` fields, with no new API endpoint and no workspace writes.

**Architecture:** A new pure module `lib/chain_derivation.py` synthesizes typed chain nodes from a study's `conclusion_verdicts[]`/`findings.entries[]`/`gate_status`, in the same dict shape `node_store.load_study_nodes` returns. The existing B4 builder calls it as a fallback when a study has no authored nodes; the existing `validate_chain`/`_build_chain` run unchanged. The frontend `_chainBlockHtml` marks a derived chain with a "· derived" hint.

**Tech Stack:** Python 3.11, pytest; `investigation_contracts.validate_chain` (already a dep); vanilla browser JS + `node`/`assert` for the one JS test.

## Global Constraints

- **No new API endpoints; no workspace writes.** Derivation is a read-time view behind the existing `GET /api/investigation-graph`.
- **Derive only when authored nodes are absent.** Per study: `nodes = load_study_nodes(ws, slug); if not nodes: nodes = derive_chain_nodes(study_spec, slug)`. Authored nodes always win.
- **Every derived chain must pass `validate_chain` with zero violations.** A Conclusion is emitted only for a sound `supported + passed` chain; evidence is `accepted` exactly when an accept-decision references it.
- **Deterministic & pure:** `derive_chain_nodes` does no I/O, no clock, no randomness — same `(study_spec, slug)` → identical output. Node ids are namespaced `…/derived-<slug>-cv<i>` / `…/derived-<slug>-fe<j>`.
- **FQ ids:** node ids and all reference fields use fully-qualified ids (`finding/…`, `evidence/…`, `decision/…`, `conclusion/…`).
- **Honesty marker:** every derived node carries `provenance.actor == "derived"`; the graph payload exposes a per-chain `derived` boolean; the card shows "· derived".
- **Never call `allocate_core()`** anywhere (crashes this env).
- **Run tests with the workspace venv:** `/Users/eranagmon/code/venv/bin/python -m pytest` (bare `python`/`pytest` fail; scope runs to the new/edited test files).
- **Lifecycle mapping table (binding):**

  | verdict | gate_status | Finding | Evidence | Decision | Conclusion |
  |---|---|---|---|---|---|
  | supported | passed | asserted | accepted | accept | published |
  | refuted | passed | asserted | rejected | reject | — none |
  | partial | passed | asserted | proposed | defer | — none |
  | any | not passed | asserted | proposed | — none | — none |

---

### Task 1: The deriver (`lib/chain_derivation.py`)

**Files:**
- Create: `vivarium_dashboard/lib/chain_derivation.py`
- Test: `tests/test_chain_derivation.py`

**Interfaces:**
- Consumes: `investigation_contracts.validate_chain(nodes) -> list[dict]` (for the test's soundness assertions only — the deriver itself does not call it).
- Produces: `derive_chain_nodes(study_spec: dict, slug: str) -> dict[str, dict]` — synthesized typed nodes keyed by FQ id, same shape as `node_store.load_study_nodes`. Every node has `id`, `type`, `lifecycle_state`, `provenance` (with `actor="derived"`), plus its type-specific reference fields. Returns `{}` when the study has no `conclusion_verdicts` and no `findings.entries`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chain_derivation.py
from investigation_contracts import validate_chain
from vivarium_dashboard.lib.chain_derivation import derive_chain_nodes


def _cv(study_extra=None, verdicts=None):
    spec = {"name": "s1", "gate_status": "passed"}
    if study_extra:
        spec.update(study_extra)
    if verdicts is not None:
        spec["conclusion_verdicts"] = verdicts
    return spec


def test_supported_passed_full_valid_chain():
    spec = _cv(verdicts=[{"claim": "X dominates", "verdict": "supported",
                          "basis": "Sobol 0.97"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert set(nodes) == {
        "finding/derived-s1-cv0", "evidence/derived-s1-cv0",
        "decision/derived-s1-cv0", "conclusion/derived-s1-cv0"}
    f = nodes["finding/derived-s1-cv0"]
    e = nodes["evidence/derived-s1-cv0"]
    d = nodes["decision/derived-s1-cv0"]
    c = nodes["conclusion/derived-s1-cv0"]
    assert f["type"] == "finding" and f["lifecycle_state"] == "asserted"
    assert f["statement"] == "X dominates" and len(f["runs"]) >= 1
    assert e["type"] == "evidence" and e["lifecycle_state"] == "accepted"
    assert e["statement"] == "Sobol 0.97"
    assert e["findings"] == ["finding/derived-s1-cv0"] and len(e["hypotheses"]) >= 1
    assert d["type"] == "decision" and d["outcome"] == "accept"
    assert d["evidence"] == ["evidence/derived-s1-cv0"]
    assert c["type"] == "conclusion" and c["lifecycle_state"] == "published"
    assert c["evidence"] == ["evidence/derived-s1-cv0"]
    assert c["decisions"] == ["decision/derived-s1-cv0"]
    assert validate_chain(nodes) == []  # sound


def test_refuted_passed_no_conclusion_valid():
    spec = _cv(verdicts=[{"claim": "Y holds", "verdict": "refuted", "basis": "b"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert "conclusion/derived-s1-cv0" not in nodes
    assert nodes["evidence/derived-s1-cv0"]["lifecycle_state"] == "rejected"
    assert nodes["decision/derived-s1-cv0"]["outcome"] == "reject"
    assert validate_chain(nodes) == []


def test_partial_passed_defer_no_conclusion():
    spec = _cv(verdicts=[{"claim": "Z partly", "verdict": "partial", "basis": "b"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert "conclusion/derived-s1-cv0" not in nodes
    assert nodes["decision/derived-s1-cv0"]["outcome"] == "defer"
    assert nodes["evidence/derived-s1-cv0"]["lifecycle_state"] == "proposed"
    assert validate_chain(nodes) == []


def test_not_passed_gate_proposed_no_decision():
    spec = _cv({"gate_status": "pending"},
               verdicts=[{"claim": "W maybe", "verdict": "supported", "basis": "b"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert set(nodes) == {"finding/derived-s1-cv0", "evidence/derived-s1-cv0"}
    assert nodes["evidence/derived-s1-cv0"]["lifecycle_state"] == "proposed"
    assert validate_chain(nodes) == []


def test_multiple_verdicts_distinct_chains():
    spec = _cv(verdicts=[
        {"claim": "A", "verdict": "supported", "basis": "ba"},
        {"claim": "B", "verdict": "supported", "basis": "bb"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert "conclusion/derived-s1-cv0" in nodes
    assert "conclusion/derived-s1-cv1" in nodes
    assert nodes["finding/derived-s1-cv1"]["statement"] == "B"


def test_findings_entries_lift_to_findings():
    spec = {"name": "s1", "gate_status": "pending",
            "findings": {"entries": [
                {"signature": "sig-1", "description": "a transport gap"}]}}
    nodes = derive_chain_nodes(spec, "s1")
    assert "finding/derived-s1-fe0" in nodes
    f = nodes["finding/derived-s1-fe0"]
    assert f["type"] == "finding" and f["statement"] == "a transport gap"
    assert validate_chain({k: v for k, v in nodes.items() if v["type"] == "finding"}) == []


def test_no_sources_empty():
    assert derive_chain_nodes({"name": "s1", "gate_status": "passed"}, "s1") == {}


def test_missing_basis_falls_back_to_claim():
    spec = _cv(verdicts=[{"claim": "claimtext", "verdict": "supported"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert nodes["evidence/derived-s1-cv0"]["statement"] == "claimtext"


def test_all_nodes_marked_derived():
    spec = _cv(verdicts=[{"claim": "X", "verdict": "supported", "basis": "b"}])
    nodes = derive_chain_nodes(spec, "s1")
    assert all(n["provenance"]["actor"] == "derived" for n in nodes.values())


def test_skips_empty_claim_and_tolerates_non_list():
    assert derive_chain_nodes({"conclusion_verdicts": "garbage"}, "s1") == {}
    nodes = derive_chain_nodes(
        {"gate_status": "passed",
         "conclusion_verdicts": [{"claim": "  ", "verdict": "supported"},
                                 {"claim": "real", "verdict": "supported", "basis": "b"}]}, "s1")
    assert set(k.split("-cv")[-1] for k in nodes) == {"0"}  # only cv index 0 (the real one)


def test_deterministic():
    spec = _cv(verdicts=[{"claim": "X", "verdict": "supported", "basis": "b"}])
    assert derive_chain_nodes(spec, "s1") == derive_chain_nodes(spec, "s1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_chain_derivation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'vivarium_dashboard.lib.chain_derivation'`.

- [ ] **Step 3: Write the deriver**

Note on the empty-claim test: `test_skips_empty_claim_and_tolerates_non_list` expects the surviving node ids to all carry `cv` index `0`. Skipped entries do **not** consume an index — enumerate over the *filtered* list of entries with valid claims so the first valid entry is `cv0`.

```python
# vivarium_dashboard/lib/chain_derivation.py
"""Derive typed chain nodes from a study's existing result fields (RFC-0002 B2).

Read-time view: lifts each ``conclusion_verdicts[]`` entry into a deterministic
Finding->Evidence->Decision->Conclusion micro-chain (verdict + gate_status drive
the lifecycle states) and each ``findings.entries[]`` into a Finding node. Pure:
no I/O, no clock, no randomness. Every node is stamped ``provenance.actor =
"derived"`` so a lifted chain is never mistaken for a human-gated one. By
construction every emitted chain passes ``investigation_contracts.validate_chain``.
"""
from __future__ import annotations

_DECISION_OUTCOME = {"supported": "accept", "refuted": "reject", "partial": "defer"}
_EVIDENCE_STATE = {"accept": "accepted", "reject": "rejected", "defer": "proposed"}


def _prov(slug: str, source: str) -> dict:
    return {"actor": "derived", "agent_id": "chain-derivation", "timestamp": "",
            "source_objects": [f"study/{slug}"],
            "justification": f"derived from study.yaml {source}",
            "tool": "b2/chain-derivation", "commit": ""}


def derive_chain_nodes(study_spec: dict, slug: str) -> dict[str, dict]:
    nodes: dict[str, dict] = {}
    gate = str((study_spec or {}).get("gate_status", "")).strip().lower()
    passed = gate in ("passed", "pass")

    raw = (study_spec or {}).get("conclusion_verdicts")
    verdicts = [v for v in raw if isinstance(v, dict) and str(v.get("claim", "")).strip()] \
        if isinstance(raw, list) else []
    for i, cv in enumerate(verdicts):
        claim = str(cv["claim"]).strip()
        basis = str(cv.get("basis") or claim).strip()
        verdict = str(cv.get("verdict", "")).strip().lower()
        fid, eid = f"finding/derived-{slug}-cv{i}", f"evidence/derived-{slug}-cv{i}"
        did, cid = f"decision/derived-{slug}-cv{i}", f"conclusion/derived-{slug}-cv{i}"
        p = _prov(slug, f"conclusion_verdicts[{i}]")

        outcome = _DECISION_OUTCOME.get(verdict) if passed else None
        ev_state = _EVIDENCE_STATE.get(outcome, "proposed") if outcome else "proposed"

        nodes[fid] = {"id": fid, "type": "finding", "lifecycle_state": "asserted",
                      "owner": "derived", "provenance": p, "validation_status": "derived",
                      "statement": claim, "runs": [f"run/{slug}"]}
        nodes[eid] = {"id": eid, "type": "evidence", "lifecycle_state": ev_state,
                      "owner": "derived", "provenance": p, "validation_status": "derived",
                      "findings": [fid], "hypotheses": [f"hyp/derived-{slug}-cv{i}"],
                      "confidence": 0.0, "statement": basis}
        if outcome:
            nodes[did] = {"id": did, "type": "decision", "lifecycle_state": "recorded",
                          "owner": "derived", "provenance": p, "validation_status": "derived",
                          "evidence": [eid], "outcome": outcome,
                          "rationale": basis, "decided_by": "chain-derivation"}
        if verdict == "supported" and passed:
            nodes[cid] = {"id": cid, "type": "conclusion", "lifecycle_state": "published",
                          "owner": "derived", "provenance": p, "validation_status": "derived",
                          "evidence": [eid], "decisions": [did], "hypotheses": [],
                          "statement": claim}

    findings = (study_spec or {}).get("findings")
    entries = findings.get("entries") if isinstance(findings, dict) else None
    if isinstance(entries, list):
        for j, fe in enumerate(entries):
            if not isinstance(fe, dict):
                continue
            stmt = str(fe.get("description") or fe.get("signature") or "").strip()
            if not stmt:
                continue
            fid = f"finding/derived-{slug}-fe{j}"
            nodes[fid] = {"id": fid, "type": "finding", "lifecycle_state": "asserted",
                          "owner": "derived", "provenance": _prov(slug, f"findings.entries[{j}]"),
                          "validation_status": "derived",
                          "statement": stmt, "runs": [f"run/{slug}"]}
    return nodes
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_chain_derivation.py -q`
Expected: PASS (10 passed, output pristine).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/chain_derivation.py tests/test_chain_derivation.py
git commit -m "feat(b2): derive typed chain nodes from study.yaml fields (pure, verdict-driven)"
```

---

### Task 2: Builder integration (`investigation_graph_views.py`)

**Files:**
- Modify: `vivarium_dashboard/lib/investigation_graph_views.py`
- Test: `tests/test_investigation_graph_views.py` (extend the existing file)

**Interfaces:**
- Consumes: `chain_derivation.derive_chain_nodes(study_spec, slug)` (Task 1); the existing `node_store.load_study_nodes(ws_root, slug)`, `_build_chain(slug, nodes)`, and the per-study loop in `build_investigation_graph`.
- Produces: the `chains[slug]` dict gains a `derived: bool` key. The chain payload otherwise unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_investigation_graph_views.py` (it already has `_ws`, `_seed_full_chain` helpers from B4):

```python
import yaml  # already imported at top of the file


def test_derives_chain_when_no_authored_nodes(tmp_path):
    ws = _ws(tmp_path)
    # give s2 a conclusion_verdicts field and a passed gate; NO authored node files
    sp = ws / "studies" / "s2" / "study.yaml"
    spec = yaml.safe_load(sp.read_text())
    spec["gate_status"] = "passed"
    spec["conclusion_verdicts"] = [{"claim": "derived claim", "verdict": "supported",
                                    "basis": "the basis"}]
    sp.write_text(yaml.safe_dump(spec))
    body, status = build_investigation_graph(ws, "demo-inv")
    chain = body["chains"]["s2"]
    assert chain["derived"] is True
    types = {n["type"] for n in chain["nodes"]}
    assert types == {"finding", "evidence", "decision", "conclusion"}
    assert chain["violations"] == []


def test_authored_nodes_suppress_derivation(tmp_path):
    ws = _ws(tmp_path)
    _seed_full_chain(ws)  # writes authored node files into s2
    sp = ws / "studies" / "s2" / "study.yaml"
    spec = yaml.safe_load(sp.read_text())
    spec["gate_status"] = "passed"
    spec["conclusion_verdicts"] = [{"claim": "should be ignored", "verdict": "supported",
                                    "basis": "b"}]
    sp.write_text(yaml.safe_dump(spec))
    body, _ = build_investigation_graph(ws, "demo-inv")
    chain = body["chains"]["s2"]
    assert chain.get("derived") is False
    # authored ids present, derived ids absent
    ids = {n["id"] for n in chain["nodes"]}
    assert "finding/f1" in ids
    assert not any(i.startswith("finding/derived-") for i in ids)


def test_study_without_verdicts_has_empty_non_derived_chain(tmp_path):
    ws = _ws(tmp_path)  # s1 has no conclusion_verdicts, no authored nodes
    body, _ = build_investigation_graph(ws, "demo-inv")
    chain = body["chains"]["s1"]
    assert chain["nodes"] == [] and chain.get("derived") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_graph_views.py -q`
Expected: FAIL — the new tests fail (`KeyError: 'derived'` / empty chain where derivation expected); the pre-existing B4 tests still pass.

- [ ] **Step 3: Add the derivation fallback + `derived` flag**

In `vivarium_dashboard/lib/investigation_graph_views.py`, add the import near the top (with the other `from vivarium_dashboard.lib import …` imports):

```python
from vivarium_dashboard.lib.chain_derivation import derive_chain_nodes
```

In `build_investigation_graph`, find the per-study line that loads nodes and builds the chain. It currently reads:

```python
        chains[slug] = _build_chain(slug, load_study_nodes(ws_root, slug))
```

Replace it with:

```python
        nodes = load_study_nodes(ws_root, slug)
        derived = False
        if not nodes:
            nodes = derive_chain_nodes(study_spec, slug)
            derived = bool(nodes)
        chain = _build_chain(slug, nodes)
        chain["derived"] = derived
        chains[slug] = chain
```

(Confirmed: the loop variable is `study_spec` — `study_spec = yaml.safe_load(sp.read_text(...))` earlier in the same `for slug in …` loop — and `slug` is the directory slug. The line you are replacing is the last line of that loop body.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_investigation_graph_views.py -q`
Expected: PASS (all B4 tests + the 3 new tests; output pristine).

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/lib/investigation_graph_views.py tests/test_investigation_graph_views.py
git commit -m "feat(b2): derive chains when a study has no authored nodes; expose chains[].derived"
```

---

### Task 3: Frontend "· derived" hint (`aig-graph.js`)

**Files:**
- Modify: `vivarium_dashboard/static/aig-graph.js`
- Test: `tests/js/test_chain_block.js` (extend)

**Interfaces:**
- Consumes: the `chain.derived` boolean now present in the payload (Task 2); the existing `_chainBlockHtml(chain)` from B4.
- Produces: `_chainBlockHtml` renders "· derived" in the block header when `chain.derived` is truthy; behavior otherwise unchanged (empty/absent chain still returns `''`).

- [ ] **Step 1: Write the failing test**

Append to `tests/js/test_chain_block.js` (before the final `console.log('ok')`):

```javascript
// derived hint
const derivedChain = {
  nodes: [{ id: 'finding/derived-s1-cv0', type: 'finding', label: 'F', lifecycle_state: 'asserted' }],
  edges: [], violations: [], derived: true,
};
assert(_chainBlockHtml(derivedChain).indexOf('derived') !== -1, 'derived hint shown when derived');

const authoredChain = {
  nodes: [{ id: 'finding/f1', type: 'finding', label: 'F', lifecycle_state: 'asserted' }],
  edges: [], violations: [], derived: false,
};
assert(_chainBlockHtml(authoredChain).indexOf('· derived') === -1, 'no derived hint when authored');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/js/test_chain_block.js`
Expected: FAIL — assertion "derived hint shown when derived" throws (the header has no "derived" text yet).

- [ ] **Step 3: Add the hint to the header**

In `vivarium_dashboard/static/aig-graph.js`, inside `_chainBlockHtml`, find the header line:

```javascript
      '<div style="font-weight:600;color:#475569;margin-bottom:2px">Evidence chain</div>' +
```

Replace it with:

```javascript
      '<div style="font-weight:600;color:#475569;margin-bottom:2px">Evidence chain' +
        (chain.derived ? '<span style="font-weight:400;color:#94a3b8"> · derived</span>' : '') +
      '</div>' +
```

- [ ] **Step 4: Run test + syntax check**

Run: `node tests/js/test_chain_block.js && node --check vivarium_dashboard/static/aig-graph.js`
Expected: prints `ok`; `node --check` exits 0.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/aig-graph.js tests/js/test_chain_block.js
git commit -m "feat(b2): mark derived chains with a '· derived' hint in the card"
```

---

## Self-Review

**Spec coverage:**
- Deriver module + verdict-driven mapping table + provenance marker + `findings.entries` lift + tolerance + determinism → Task 1. ✓
- Builder fallback (`if not nodes: derive`), suppression by authored nodes, `chains[].derived` flag → Task 2. ✓
- Frontend "· derived" hint → Task 3. ✓
- "Every derived chain passes validate_chain" → asserted in Task 1 tests (`validate_chain(nodes) == []` for supported/refuted/partial/pending). ✓
- No new endpoint / no workspace writes → nothing in the plan adds a route or writes files; derivation is in-memory. ✓
- Out-of-scope items (B2a/B2b/B2c, persisting, verdict-badge linkage) → not implemented. ✓

**Placeholder scan:** No TBD/TODO; every code step is complete; commands have expected output. ✓

**Type consistency:** `derive_chain_nodes(study_spec, slug) -> dict[str,dict]` is produced in Task 1 and consumed in Task 2 with that exact signature. Node dict shape (keys `id/type/lifecycle_state/provenance/…` + reference fields `findings`/`evidence`/`decisions`/`hypotheses`/`runs`/`outcome`) matches the B1 shapes `validate_chain` reads, which is why Task 1's `validate_chain(nodes) == []` assertions hold. The `chains[slug]["derived"]` boolean produced in Task 2 is the exact key Task 3's `_chainBlockHtml` reads (`chain.derived`). Node id namespacing (`derived-<slug>-cv<i>` / `-fe<j>`) is consistent between the deriver and the suppression test's `startswith("finding/derived-")` check. ✓

**Note for the implementer (Task 1 indexing):** the deriver enumerates the *filtered* `verdicts` list (entries with a non-empty `claim`), so a skipped empty-claim entry does not consume a `cv` index — matching `test_skips_empty_claim_and_tolerates_non_list`, which expects the one real entry to be `cv0`.
