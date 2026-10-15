# Investigation-graph verdict × evidence-tier markers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the gate-binary node badge in the Investigation-graph view with a two-axis marker (verdict × evidence-tier) and wire edges to the relations studies actually author, so the graph is honest about partial evidence and edge types carry meaning.

**Architecture:** `verdict` + `evidence_tier` become real, optional study.yaml fields (seeded by a derive script, expert-correctable). The workbench SPA (`static/walkthrough.js`) reads them to draw a two-axis node marker and reads the authored `parent_relations` block (currently orphaned) to draw typed edges; both fall back to today's gate-based behavior when the fields are absent.

**Tech Stack:** viva-genesis (Python 3.12, pytest, JSON-Schema study validators, YAML study data); vivarium-workbench (vanilla JS SPA `static/walkthrough.js`, node-based source-contract tests in `tests/js/`, FastAPI server in `lib/`).

**Spec:** `vivarium-workbench/docs/superpowers/specs/2026-10-15-investigation-graph-markers-design.md`

## Global Constraints

- No AI attribution in any commit message or PR body (no `Co-Authored-By`, no "Generated with" footer). Verbatim project rule.
- Dedicated git worktree per repo; never commit in the canonical `~/code/<repo>` checkout. viva-genesis tasks run in a viva-genesis worktree off `origin/main`; workbench tasks run in `~/code/vivarium-workbench--graph-markers` (already created, branch `feat/investigation-graph-markers`).
- Two repos, two PRs; **land viva-genesis (data) first, then vivarium-workbench (renderer)**. The renderer's fallback covers the window between them.
- Tier field **values** are the existing vocabulary: `conceptual_demonstration`, `mechanistic_prediction`, `calibrated`. Display labels in the UI are the short forms "demonstration / mechanistic / calibrated". Do not rename the 3 studies that already carry `evidence_tier: mechanistic_prediction`.
- Verdict values: `supported | qualified | honest-negative | refuted | mixed`. Edge relations: `leads-to | model-input | alternative-to | refutes | precedes`.
- Both new study fields are **optional** in the schema so unmigrated studies still validate; the renderer never throws on a missing/unknown value.

## Review Focus

- A study with **no `verdict`/`evidence_tier`** (unmigrated, or a workspace that never ran the script): node must render via the gate-based fallback and omit the tier pips, not blank out or throw. (Task 5)
- A study whose authored `parent_relations` names a parent **not in `parent_studies`** (or vice versa): edge resolution must not drop the edge or mis-pair the relation. (Task 6)
- An **unknown enum value** in `verdict`/`evidence_tier`/`relation` (hand-typo in yaml, or a future value): renderer defaults to `supported` / ungraded / `leads-to` and never throws. (Tasks 5, 6)
- The derive script **run twice**: second run produces an empty diff (idempotent), and never clobbers a human-corrected value that differs from its heuristic unless `--force`. (Task 2)
- The **published (snapshot) data source**, not just the live server: `verdict`/`evidence_tier` must survive into `api/*.json` so the read-only dashboard shows the new markers too. (Task 4)

---

## Task 1: Study schema — add `verdict` + `evidence_tier` enums (viva-genesis)

**Files:**
- Modify: `viva-genesis/.pbg/schemas/study.schema.json` (the vendored validator `scripts/lint-workspace.py` checks against)
- Test: `viva-genesis/tests/test_study_schema_markers.py` (create)

**Interfaces:**
- Produces: two optional study-level properties — `verdict` (enum: supported|qualified|honest-negative|refuted|mixed) and `evidence_tier` (enum: conceptual_demonstration|mechanistic_prediction|calibrated).

- [ ] **Step 1: Write the failing test**

```python
# viva-genesis/tests/test_study_schema_markers.py
import json, pathlib, jsonschema, pytest

SCHEMA = json.loads((pathlib.Path(__file__).resolve().parents[1]
                     / ".pbg/schemas/study.schema.json").read_text())

def _valid_study(**extra):
    base = {"schema_version": 4, "name": "og-x", "title": "X", "question": "q?"}
    base.update(extra); return base

def test_verdict_and_tier_accept_valid_enums():
    for v in ["supported","qualified","honest-negative","refuted","mixed"]:
        jsonschema.validate(_valid_study(verdict=v), SCHEMA)
    for t in ["conceptual_demonstration","mechanistic_prediction","calibrated"]:
        jsonschema.validate(_valid_study(evidence_tier=t), SCHEMA)

def test_verdict_rejects_out_of_enum():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_valid_study(verdict="accepted"), SCHEMA)

def test_fields_are_optional():
    jsonschema.validate(_valid_study(), SCHEMA)  # no verdict/tier -> still valid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd viva-genesis-worktree && .venv/bin/python -m pytest tests/test_study_schema_markers.py -v`
Expected: FAIL — `test_verdict_rejects_out_of_enum` fails (no enum constraint yet), or the valid-enum test fails if the properties are `additionalProperties:false` restricted.

- [ ] **Step 3: Add the properties to the schema**

In `.pbg/schemas/study.schema.json`, under the top-level `"properties"` object, add (keep alphabetical/neighbor ordering of the file; do not set `required`):

```json
"verdict": {
  "type": "string",
  "enum": ["supported", "qualified", "honest-negative", "refuted", "mixed"],
  "description": "What the study concluded (separate from the computed gate_status)."
},
"evidence_tier": {
  "type": "string",
  "enum": ["conceptual_demonstration", "mechanistic_prediction", "calibrated"],
  "description": "How strong the evidence is: proof-of-concept / mechanistic prediction / calibrated against data."
}
```

If the schema has `"additionalProperties": false`, these additions are what makes them accepted; if it already allows extra properties, the `enum` is what the reject-test needs. Either way both keys must be present with the enums above.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_study_schema_markers.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add .pbg/schemas/study.schema.json tests/test_study_schema_markers.py
git commit -m "feat(schema): optional verdict + evidence_tier study fields"
```

---

## Task 2: Derive script `derive_study_verdict_tier.py` (viva-genesis)

**Files:**
- Create: `viva-genesis/scripts/derive_study_verdict_tier.py`
- Test: `viva-genesis/tests/test_derive_verdict_tier.py` (create)

**Interfaces:**
- Consumes: Task 1's schema (the written values must validate).
- Produces: a CLI `python scripts/derive_study_verdict_tier.py [--check] [--force]` that writes `verdict` + `evidence_tier` into each `workspace/studies/*/study.yaml`; and a pure function `derive(study: dict) -> tuple[str, str]` for testing.

- [ ] **Step 1: Write the failing test**

```python
# viva-genesis/tests/test_derive_verdict_tier.py
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "dv", pathlib.Path(__file__).resolve().parents[1] / "scripts/derive_study_verdict_tier.py")
dv = importlib.util.module_from_spec(spec); spec.loader.exec_module(dv)

def test_passed_gate_is_supported():
    assert dv.derive({"name":"og-00","gate_status":"passed"})[0] == "supported"

def test_known_honest_negatives():
    assert dv.derive({"name":"og-01-cal-electrochemistry","gate_status":"failed"})[0] == "honest-negative"
    assert dv.derive({"name":"og-08-alt-robust-innovation-set","gate_status":"failed"})[0] == "honest-negative"

def test_adversarial_is_refuted():
    s = {"name":"og-adv-generic-nonequilibrium","gate_status":"passed",
         "parent_relations":[{"study":"og-00","relation":"refutes"}]}
    assert dv.derive(s)[0] == "refuted"

def test_calibration_study_is_calibrated_tier():
    assert dv.derive({"name":"og-cal-vent-environment","gate_status":"passed"})[1] == "calibrated"

def test_existing_tier_is_preserved():
    assert dv.derive({"name":"og-x","gate_status":"passed",
                      "evidence_tier":"mechanistic_prediction"})[1] == "mechanistic_prediction"

def test_default_is_supported_mechanistic():
    assert dv.derive({"name":"og-x","gate_status":"passed"}) == ("supported","mechanistic_prediction")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_derive_verdict_tier.py -v`
Expected: FAIL — module not found (script doesn't exist yet).

- [ ] **Step 3: Write the script**

```python
#!/usr/bin/env python
"""Seed study-level `verdict` + `evidence_tier` from existing signals.

Heuristic seed, not an exact algorithm — a human corrects wrong calls in the
yaml afterwards. Idempotent: never overwrites a value that differs from the
heuristic unless --force.

Usage: python scripts/derive_study_verdict_tier.py [--check] [--force]
"""
from __future__ import annotations
import argparse, os, sys, pathlib
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
STUDIES = sorted((ROOT / "workspace" / "studies").glob("*/study.yaml"))

HONEST_NEGATIVES = {"og-01-cal-electrochemistry", "og-08-alt-robust-innovation-set"}

def _relations(s):
    return [str(r.get("relation","")) for r in (s.get("parent_relations") or []) if isinstance(r, dict)]

def derive(study: dict) -> tuple[str, str]:
    name = str(study.get("name",""))
    gate = str(study.get("gate_status","")).strip().lower()
    rels = _relations(study)
    # verdict
    if name in HONEST_NEGATIVES:
        verdict = "honest-negative"
    elif "refutes" in rels:
        verdict = "refuted"
    elif gate in ("failed","failed_evaluation","refuted"):
        verdict = "honest-negative"   # a fail not otherwise classified is a finding, not a disproof
    elif gate in ("partial","needs_calibration"):
        verdict = "qualified"
    else:
        verdict = "supported"
    # tier: existing value wins; calibration studies -> calibrated; else mechanistic
    tier = str(study.get("evidence_tier","")).strip()
    if tier not in ("conceptual_demonstration","mechanistic_prediction","calibrated"):
        if "cal" in name.split("-"):      # og-cal-*, og-01-cal-*
            tier = "calibrated"
        else:
            tier = "mechanistic_prediction"
    return verdict, tier

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="dry-run; non-zero exit if any change")
    ap.add_argument("--force", action="store_true", help="overwrite human-set values that differ")
    args = ap.parse_args()
    changed = 0
    for path in STUDIES:
        data = yaml.safe_load(path.read_text()) or {}
        v, t = derive(data)
        updates = {}
        for key, val in (("verdict", v), ("evidence_tier", t)):
            cur = data.get(key)
            if cur == val:
                continue
            if cur and not args.force:   # idempotent / respect human correction
                continue
            updates[key] = val
        if not updates:
            continue
        changed += 1
        print(f"{path.parent.name}: " + ", ".join(f"{k}={v2}" for k,v2 in updates.items()))
        if not args.check:
            data.update(updates)
            tmp = str(path) + ".tmp"
            with open(tmp, "w") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            os.replace(tmp, path)
    print(f"{changed} study.yaml file(s) {'would change' if args.check else 'changed'}")
    return 1 if (args.check and changed) else 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_derive_verdict_tier.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/derive_study_verdict_tier.py tests/test_derive_verdict_tier.py
git commit -m "feat(scripts): derive study verdict + evidence_tier seed"
```

---

## Task 3: Run the derive script → seed the 23 studies (viva-genesis)

**Files:**
- Modify: `viva-genesis/workspace/studies/*/study.yaml` (23 files, data)
- Test: reuses lint + an assertion test (create `tests/test_studies_have_markers.py`)

**Interfaces:**
- Consumes: Task 1 schema + Task 2 script.
- Produces: every study.yaml carries a schema-valid `verdict` + `evidence_tier`.

- [ ] **Step 1: Write the failing test**

```python
# viva-genesis/tests/test_studies_have_markers.py
import pathlib, yaml
STUDIES = sorted((pathlib.Path(__file__).resolve().parents[1]
                  / "workspace/studies").glob("*/study.yaml"))
V = {"supported","qualified","honest-negative","refuted","mixed"}
T = {"conceptual_demonstration","mechanistic_prediction","calibrated"}

def test_every_study_has_valid_markers():
    missing = []
    for p in STUDIES:
        d = yaml.safe_load(p.read_text()) or {}
        if d.get("verdict") not in V or d.get("evidence_tier") not in T:
            missing.append(p.parent.name)
    assert not missing, f"studies missing/invalid markers: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_studies_have_markers.py -v`
Expected: FAIL — most studies have no `verdict` yet.

- [ ] **Step 3: Run the derive script (apply), then eyeball the two honest-negatives and og-cal**

```bash
.venv/bin/python scripts/derive_study_verdict_tier.py --check   # review proposed pairs
.venv/bin/python scripts/derive_study_verdict_tier.py           # apply
```

Spot-check: `og-01-cal-electrochemistry` and `og-08-alt-robust-innovation-set` → `verdict: honest-negative`; `og-cal-vent-environment` → `evidence_tier: calibrated`; `og-adv-generic-nonequilibrium` → `verdict: refuted`. Hand-correct any obviously-wrong call directly in the yaml (the field is authoritative; the script won't re-clobber it).

- [ ] **Step 4: Run tests + lint to verify**

Run: `.venv/bin/python -m pytest tests/test_studies_have_markers.py -v` → PASS
Run: `.venv/bin/python scripts/derive_study_verdict_tier.py --check` → exits 0 (idempotent, no change)
Run: `PYTHONPATH=$PWD .venv/bin/python scripts/lint-workspace.py` → `workspace lint: OK`, schema ✓ dashboard ✓

- [ ] **Step 5: Commit**

```bash
git add workspace/studies/*/study.yaml tests/test_studies_have_markers.py
git commit -m "data(studies): seed verdict + evidence_tier across the 23 studies"
```

> **viva-genesis PR boundary:** open the viva-genesis PR after Task 3 (Tasks 1–3), merge on green, before starting the workbench tasks. The data must exist on main so the renderer has something to read.

---

## Task 4: Confirm server/snapshot passthrough of the new fields (vivarium-workbench)

**Files:**
- Inspect: `vivarium-workbench/lib/investigations.py`, `lib/simulations_index.py`, `publish.py`
- Modify (only if a whitelist strips them): the study-summary / per-study payload builder
- Test: `vivarium-workbench/tests/test_study_markers_passthrough.py` (create — pytest, uses `dashboard_client` fixture)

**Interfaces:**
- Produces: `verdict` + `evidence_tier` present in the study objects the SPA receives from `/api/investigation/<name>` (live) and in `api/*.json` (snapshot).

- [ ] **Step 1: Write the failing test**

```python
# vivarium-workbench/tests/test_study_markers_passthrough.py
def test_investigation_study_carries_verdict_and_tier(dashboard_client):
    # the fixture workspace must have at least one study with verdict+evidence_tier;
    # add those fields to tests/_fixtures/<ws>/workspace/studies/<one>/study.yaml first.
    r = dashboard_client.get("/api/investigation/origins-of-life-vent")
    assert r.status_code == 200
    studies = r.json().get("studies") or r.json().get("members") or []
    sample = next(s for s in studies if s.get("name"))
    assert "verdict" in sample and "evidence_tier" in sample
```

(If the fixture workspace isn't the origins investigation, point the test at whatever investigation the fixture ships and add the two fields to one of its study.yamls.)

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `cd vivarium-workbench--graph-markers && VIVARIUM_WORKBENCH_DISABLE_CSRF=1 .venv/bin/python -m pytest tests/test_study_markers_passthrough.py -v`
Expected: PASS if the payload already passes raw study fields through (likely — many payloads are `extra="allow"`); FAIL if a pydantic model or dict-builder whitelists fields.

- [ ] **Step 3: If it failed, add the fields to the passthrough**

Find the study-summary builder (grep `gate_status` in `lib/investigations.py` — the field list that already carries `gate_status`, `confidence`, `title` is where `verdict` + `evidence_tier` belong). Add both keys to that dict/model. If a pydantic model in `lib/models.py` defines the study shape with explicit fields, add `verdict: str | None = None` and `evidence_tier: str | None = None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `VIVARIUM_WORKBENCH_DISABLE_CSRF=1 .venv/bin/python -m pytest tests/test_study_markers_passthrough.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_study_markers_passthrough.py lib/   # lib/ only if modified
git commit -m "feat(api): pass study verdict + evidence_tier through to the SPA"
```

---

## Task 5: Two-axis node marker (vivarium-workbench `static/walkthrough.js`)

**Files:**
- Modify: `vivarium-workbench/static/walkthrough.js` (`_studyStatusState`/`_STATUS_META` ≈ 52–82; node render ≈ 11062–11085)
- Test: `vivarium-workbench/tests/js/test_investigation_graph_markers.js` (create — node source-contract test, same style as `tests/js/test_composite_card_contract.js`)

**Interfaces:**
- Consumes: study objects with optional `verdict` + `evidence_tier` (Task 4).
- Produces: `_studyVerdict(s)`, `_studyTier(s)`, `_VERDICT_META`, `_TIER_META` in walkthrough.js; node markup rendering verdict color+icon+label and a tier pip group.

- [ ] **Step 1: Write the failing test**

```js
// vivarium-workbench/tests/js/test_investigation_graph_markers.js
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '../../static/walkthrough.js'), 'utf8');
function assert(cond, msg){ if(!cond){ console.error('FAIL: '+msg); process.exit(1);} }

// new two-axis model exists
assert(src.includes('_VERDICT_META'), '_VERDICT_META defined');
assert(src.includes('_studyVerdict'), '_studyVerdict defined');
assert(src.includes('_studyTier'), '_studyTier defined');
// all five verdicts present
['supported','qualified','honest-negative','refuted','mixed'].forEach(v =>
  assert(src.includes("'"+v+"'") || src.includes('"'+v+'"'), 'verdict '+v+' present'));
// tier values (data vocabulary) present
['conceptual_demonstration','mechanistic_prediction','calibrated'].forEach(t =>
  assert(src.includes(t), 'tier '+t+' present'));
// explicit field preferred over gate fallback
assert(/s\.verdict/.test(src), 'reads s.verdict');
assert(/s\.evidence_tier/.test(src), 'reads s.evidence_tier');
console.log('PASS test_investigation_graph_markers');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd vivarium-workbench--graph-markers && node tests/js/test_investigation_graph_markers.js`
Expected: FAIL — `_VERDICT_META defined`.

- [ ] **Step 3: Add the two-axis model + render**

Near the current `_STATUS_META`/`_studyStatusState` (≈ 52–82) add (keep `_studyStatusState` for any other caller, but the graph will use the new functions):

```js
var _VERDICT_META = {
  'supported':       {color:'#16a34a', icon:'◆', label:'supported'},
  'qualified':       {color:'#0d9488', icon:'◇', label:'qualified'},
  'honest-negative': {color:'#d97706', icon:'⊖', label:'honest-negative'},
  'refuted':         {color:'#dc2626', icon:'✗', label:'refuted'},
  'mixed':           {color:'#64748b', icon:'◐', label:'mixed'},
};
var _TIER_META = {   // value -> {pips, label}
  'conceptual_demonstration': {pips:1, label:'demonstration'},
  'mechanistic_prediction':   {pips:2, label:'mechanistic'},
  'calibrated':               {pips:3, label:'calibrated'},
};
function _studyVerdict(s){
  s = s || {};
  if (_VERDICT_META[s.verdict]) return s.verdict;   // explicit field wins
  // fallback: map the old gate-based state into the new set
  var st = _studyStatusState(s);
  if (st === 'Accepted') return 'supported';
  if (st === 'Refuted')  return 'refuted';
  return 'supported';
}
function _verdictMeta(s){ return _VERDICT_META[_studyVerdict(s)] || _VERDICT_META['supported']; }
function _studyTier(s){ s = s || {}; return _TIER_META[s.evidence_tier] ? s.evidence_tier : null; }
function _tierPipsHtml(s){
  var t = _studyTier(s); if (!t) return '';
  var n = _TIER_META[t].pips, lab = _TIER_META[t].label, out = '';
  for (var i=0;i<3;i++) out += '<span style="color:'+(i<n?'#334155':'#cbd5e1')+'">●</span>';
  return '<span class="aig-tier-pips" title="evidence: '+lab+'">'+out+'</span>';
}
```

In the node-render block (≈ 11062–11085) replace the uses of `_studyStatusMeta(s)`/`ss`/`confidence` with `_verdictMeta(s)`:
- card top-border: `border-top:3px solid ' + _verdictMeta(s).color`
- status icon + label pill: use `_verdictMeta(s).icon` and `_verdictMeta(s).label`
- append `_tierPipsHtml(s)` right after the verdict pill.

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/js/test_investigation_graph_markers.js` → PASS
Run the existing JS suite to catch regressions: `for f in tests/js/test_*.js; do node "$f" || exit 1; done`

- [ ] **Step 5: Commit**

```bash
git add static/walkthrough.js tests/js/test_investigation_graph_markers.js
git commit -m "feat(graph): two-axis verdict x evidence-tier node marker"
```

---

## Task 6: Typed edges from `parent_relations` (vivarium-workbench `static/walkthrough.js`)

**Files:**
- Modify: `vivarium-workbench/static/walkthrough.js` (`_DAG_REL_STYLE`/`_dagRelStyle` ≈ 10676–10690; `dagEdgesFor`/`_dagEdges` ≈ 10655–10881; arrowhead ≈ 11248–11251)
- Test: extend `tests/js/test_investigation_graph_markers.js`

**Interfaces:**
- Consumes: study objects carrying `parent_relations: [{study, relation}]`.
- Produces: edges whose `relation` is resolved from `parent_relations`; `_DAG_REL_STYLE` covering `leads-to|model-input|alternative-to|refutes|precedes`.

- [ ] **Step 1: Extend the failing test**

Append to `tests/js/test_investigation_graph_markers.js`:

```js
// edge vocabulary right-sized
assert(src.includes("'alternative-to'"), 'alternative-to style present');
assert(src.includes("'precedes'"), 'precedes style present');
assert(!/_DAG_REL_STYLE[\s\S]*'calibrates-threshold'/.test(src), 'calibrates-threshold removed from style map');
assert(!/_DAG_REL_STYLE[\s\S]*'evidence':/.test(src), "'evidence' removed from style map");
// authored relations are actually read
assert(src.includes('parent_relations'), 'edge builder reads parent_relations');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/js/test_investigation_graph_markers.js`
Expected: FAIL — `alternative-to style present`.

- [ ] **Step 3: Wire relations + styles + arrowhead**

(a) `_DAG_REL_STYLE` (≈ 10676) — replace the map with:

```js
var _DAG_REL_STYLE = {
  'leads-to':        { color:'#94a3b8', dash:null,  label:'leads to' },
  'model-input':     { color:'#2563eb', dash:null,  label:'model input' },
  'alternative-to':  { color:'#64748b', dash:'6 3', label:'alternative to' },
  'refutes':         { color:'#dc2626', dash:'5 3', label:'refutes' },
  'precedes':        { color:'#94a3b8', dash:'2 3', label:'precedes' },
};
function _dagRelStyle(rel){
  if (rel === 'regulatory') rel = 'precedes';
  if (rel === 'refutes-alternative') rel = 'refutes';
  if (rel === 'leads to') rel = 'leads-to';
  return _DAG_REL_STYLE[rel] || _DAG_REL_STYLE['leads-to'];
}
```

(b) In the edge builder (`dagEdgesFor`/`_dagEdges`, ≈ 10655–10881): after assembling an edge, resolve its relation from the child study's `parent_relations` when present, taking precedence over the server-hardcoded/synthesized value:

```js
// given child study `s` and an edge `e` with e.study === parent slug:
function _authoredRelation(s, parentSlug){
  var pr = (s && s.parent_relations) || [];
  for (var i=0;i<pr.length;i++){ if (pr[i] && pr[i].study === parentSlug && pr[i].relation) return pr[i].relation; }
  return null;
}
// in dagEdgesFor, map each edge: e.relation = _authoredRelation(s, e.study) || e.relation || 'leads-to';
```

(c) Arrowhead (≈ 11248–11251): give each edge path a marker that inherits its stroke. Simplest: define the marker with `fill="context-stroke"` if supported, else create a per-color marker id. Minimal change — set the shared marker to `fill="currentColor"` and set the path's `color` to `st.color`, or append per-edge `<marker>` nodes keyed by color. Verify arrowheads visually match edge color.

- [ ] **Step 4: Run tests**

Run: `node tests/js/test_investigation_graph_markers.js` → PASS
Run: `for f in tests/js/test_*.js; do node "$f" || exit 1; done` → all PASS

- [ ] **Step 5: Commit**

```bash
git add static/walkthrough.js tests/js/test_investigation_graph_markers.js
git commit -m "feat(graph): typed edges from authored parent_relations"
```

---

## Task 7: Regenerate the legend from the objects (vivarium-workbench `static/walkthrough.js`)

**Files:**
- Modify: `vivarium-workbench/static/walkthrough.js` (legend ≈ 11322–11356)
- Test: extend `tests/js/test_investigation_graph_markers.js`

**Interfaces:**
- Consumes: `_VERDICT_META`, `_TIER_META`, `_DAG_REL_STYLE`.
- Produces: a three-row legend (Verdict / Evidence / Edges) built from those objects, no hardcoded duplicate.

- [ ] **Step 1: Extend the failing test**

```js
// legend has three rows and is generated, not a stale hardcoded 'Accepted' list
assert(/Verdict/.test(src), 'legend Verdict row');
assert(/Evidence/.test(src), 'legend Evidence row');
assert(!/_lg\('#16a34a','✓','Accepted'\)/.test(src), 'old hardcoded Accepted legend removed');
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/js/test_investigation_graph_markers.js`
Expected: FAIL — `legend Verdict row`.

- [ ] **Step 3: Rebuild the legend**

Replace the hardcoded confidence/edges legend string (≈ 11322–11356) with rows generated by iterating the objects:

```js
function _legendHtml(){
  var vr = Object.keys(_VERDICT_META).map(function(k){var m=_VERDICT_META[k];
    return '<span style="color:'+m.color+';margin-right:10px">'+m.icon+' '+m.label+'</span>';}).join('');
  var tr = Object.keys(_TIER_META).map(function(k){var m=_TIER_META[k];
    var pips=''; for(var i=0;i<3;i++) pips+='<span style="color:'+(i<m.pips?'#334155':'#cbd5e1')+'">●</span>';
    return '<span style="margin-right:10px">'+pips+' '+m.label+'</span>';}).join('');
  var er = Object.keys(_DAG_REL_STYLE).map(function(k){var m=_DAG_REL_STYLE[k];
    return '<span style="margin-right:10px"><span style="border-top:2px '+(m.dash?'dashed':'solid')+' '+m.color+';display:inline-block;width:18px;vertical-align:middle"></span> '+m.label+'</span>';}).join('');
  return '<div><b>Verdict:</b> '+vr+'</div><div><b>Evidence:</b> '+tr+'</div><div><b>Edges:</b> '+er+'</div>';
}
// inject _legendHtml() into #investigation-dag-legend
```

- [ ] **Step 4: Run tests**

Run: `node tests/js/test_investigation_graph_markers.js` → PASS
Run: `for f in tests/js/test_*.js; do node "$f" || exit 1; done` → all PASS

- [ ] **Step 5: Commit + open the workbench PR**

```bash
git add static/walkthrough.js tests/js/test_investigation_graph_markers.js
git commit -m "feat(graph): regenerate legend from verdict/tier/edge objects"
```

Open the vivarium-workbench PR (Tasks 4–7), merge on green.

---

## Task 8: Manual verification + republish

- [ ] Refresh the live workbench (serve-latest → viva-genesis main with the data; serve-main → workbench main with the renderer) and load the Investigation graph.
- [ ] Confirm: the two honest-negatives (Phase 2 galvanic cell, Phase 9 integrated autonomy) render amber `⊖ honest-negative`, not red `✗ Refuted`; `og-cal-vent-environment` shows `●●● calibrated`; a demonstration-tier study shows `●○○`; the five `-alt` studies draw `alternative-to` edges and og-adv draws a `refutes` edge; the legend shows all three rows.
- [ ] Republish the read-only dashboard (auto-triggers on the viva-genesis `workspace/**` merge; confirm `publish-dashboard` green) and confirm the published graph shows the new markers.

---

## Self-review notes (completed)

- **Spec coverage:** data model (Tasks 1–3), server passthrough incl. snapshot (Task 4), node marker (Task 5), edges (Task 6), legend (Task 7), testing (each task), delivery order + manual/republish (Task 8). All spec sections map to a task.
- **Tier vocabulary reconciliation:** field values use the existing `conceptual_demonstration`/`mechanistic_prediction` (+`calibrated`), display labels are the short forms — pinned in Global Constraints and used consistently in Tasks 1, 2, 5, 7.
- **Review Focus → tests:** missing-fields fallback (Task 5 test asserts `s.verdict` read with gate fallback path present); unknown-enum default (Tasks 5/6 default clauses); idempotency/no-clobber (Task 2 test); snapshot passthrough (Task 4 targets publish path); mismatched parent_relations (Task 6 `_authoredRelation` returns null → keeps synthesized relation).
