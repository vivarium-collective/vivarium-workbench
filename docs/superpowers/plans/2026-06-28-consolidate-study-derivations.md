# Consolidate Study Derivations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the study derivation rules (3-track conclusion verdicts, verdict, insight, key metrics) live in one canonical Python module that the report, study page, and investigation graph all consume — deleting the two hand-synced JS copies.

**Architecture:** Extract the `_derive_*` family from `single_study_report.py` into a new pure `lib/study_derivations.py` (imported back by the report). The server attaches a computed `derived` block to the study-page payload and to each investigation `studies[]` entry (extending the existing `computed_gate_verdict` pattern). `study-detail.js` and `walkthrough.js` read the precomputed `derived.*` and their derivation copies are removed.

**Tech Stack:** Python 3.11 / pytest; FastAPI; vanilla browser JS + `node --check`.

## Global Constraints

- **Parity-preserving:** `study_derivations` reproduces today's rules EXACTLY (the code is moved, not rewritten). The report's output must not change.
- **Additive payloads:** the `derived` block is a new key; existing keys (`computed_gate_verdict`, etc.) are untouched — backward-compatible.
- **JS reads defensively:** `(window._study.derived || {}).conclusion_verdicts` / `(s.derived || {}).conclusion_verdicts`, falling back to an empty all-PENDING/GAP shape (NOT recomputing) when absent.
- **Reference-check before deleting JS helpers:** delete a helper (`_GATE_RESULT_NORM`, `_normGateResult`) ONLY if it has no remaining references in that file (grep first). `_runWithOutcomes`/`_normOutcome`/`_testStatusToResult` in walkthrough.js are used by other rendering (e.g. line ~7372) — do NOT delete them.
- The canonical 3-track rules (verbatim): `biological_validation` ← `pipeline_gate.gate_evaluator.result` or `gate_status` (normalized PASS/FAIL/PARTIAL/PENDING); `regression_compatibility` ← runs (PASS all completed / FAIL any errored / PARTIAL mixed / PENDING none); `explanatory_gain` ← findings (PASS if any `tier=='interpretation'` or `mechanism_origin`; PARTIAL if findings but none qualify; GAP if none).
- Run tests with the venv: `/Users/eranagmon/code/venv/bin/python -m pytest`; JS via `node --check`.

---

### Task 1: Extract `lib/study_derivations.py` (canonical, pure) + report imports it

**Files:**
- Create: `vivarium_dashboard/lib/study_derivations.py`
- Modify: `vivarium_dashboard/lib/single_study_report.py` (replace the local `_derive_*`/`_GATE_*`/`_norm_*` defs with imports; ~lines 431–555)
- Test: `tests/test_study_derivations.py`

**Interfaces:**
- Produces (public, pure): `conclusion_verdicts(spec) -> {biological_validation,regression_compatibility,explanatory_gain: {result, basis}}`; `verdict(spec) -> str`; `insight(spec) -> str`; `key_metrics(spec) -> list[dict]`; `latest_outcomes(spec) -> dict`; `norm_gate_result(val) -> str`; `derived_block(spec) -> {conclusion_verdicts, verdict, insight, key_metrics}`; constants `GATE_TO_VERDICT`, `GATE_RESULT_NORM`, `RUN_ERRORED`, `RUN_COMPLETED`. Consumed by Tasks 2–4.

- [ ] **Step 1: Write the failing parity tests**

```python
# tests/test_study_derivations.py
from vivarium_dashboard.lib import study_derivations as D


def test_conclusion_verdicts_passed_gate_completed_runs_interp_finding():
    spec = {
        "pipeline_gate": {"gate_evaluator": {"result": "passed"}},
        "runs": [{"status": "completed"}, {"status": "complete"}],
        "findings": [{"tier": "interpretation", "statement": "X dominates"}],
        "conclusion_verdicts": {"biological_validation": {"basis": "b1"}},
    }
    cv = D.conclusion_verdicts(spec)
    assert cv["biological_validation"] == {"result": "PASS", "basis": "b1"}
    assert cv["regression_compatibility"]["result"] == "PASS"
    assert cv["explanatory_gain"]["result"] == "PASS"


def test_regression_fail_when_a_run_errored():
    spec = {"runs": [{"status": "completed"}, {"status": "errored"}]}
    assert D.conclusion_verdicts(spec)["regression_compatibility"]["result"] == "FAIL"


def test_regression_partial_when_mixed_and_pending_when_none():
    assert D.conclusion_verdicts({"runs": [{"status": "completed"}, {"status": "queued"}]})["regression_compatibility"]["result"] == "PARTIAL"
    assert D.conclusion_verdicts({})["regression_compatibility"]["result"] == "PENDING"


def test_explanatory_gap_then_partial_then_pass():
    assert D.conclusion_verdicts({})["explanatory_gain"]["result"] == "GAP"
    assert D.conclusion_verdicts({"findings": [{"statement": "plain"}]})["explanatory_gain"]["result"] == "PARTIAL"
    assert D.conclusion_verdicts({"findings": [{"mechanism_origin": "y"}]})["explanatory_gain"]["result"] == "PASS"


def test_bio_failed_and_pending_normalization():
    assert D.conclusion_verdicts({"gate_status": "failed"})["biological_validation"]["result"] == "FAIL"
    assert D.conclusion_verdicts({"gate_status": "needs_calibration"})["biological_validation"]["result"] == "PARTIAL"
    assert D.conclusion_verdicts({})["biological_validation"]["result"] == "PENDING"


def test_verdict_insight_key_metrics():
    assert D.verdict({"gate_status": "passed"}) == "passing"
    assert D.verdict({}) == ""
    assert D.insight({"findings": [{"summary": "the insight"}]}) == "the insight"
    assert D.insight({}) == ""
    km = D.key_metrics({"runs": [{"outcomes": {"t1": {"result": "PASS", "observed": 1.2}}}]})
    assert km == [{"label": "t1", "value": 1.2, "status": "pass"}]


def test_derived_block_has_four_keys():
    b = D.derived_block({"gate_status": "passed"})
    assert set(b) == {"conclusion_verdicts", "verdict", "insight", "key_metrics"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_study_derivations.py -q`
Expected: FAIL — `No module named 'vivarium_dashboard.lib.study_derivations'`.

- [ ] **Step 3: Create the module (move the canonical logic verbatim, rename to public)**

```python
# vivarium_dashboard/lib/study_derivations.py
"""Canonical study derivations — the SINGLE source of the rules that the report,
the study page, and the investigation graph all consume. Pure (no I/O). Moved
out of single_study_report.py so the rules are defined once instead of being
hand-kept-identical in Python + two JS files."""
from __future__ import annotations

GATE_TO_VERDICT = {
    "passed": "passing", "failed": "failing-bio",
    "needs_calibration": "calibrating", "blocked": "blocked",
    "not_started": "not-started",
}
GATE_RESULT_NORM = {
    "pass": "PASS", "passed": "PASS", "ok": "PASS",
    "fail": "FAIL", "failed": "FAIL",
    "partial": "PARTIAL", "mixed": "PARTIAL", "needs_calibration": "PARTIAL",
}
RUN_ERRORED = {"error", "errored", "failed", "crashed", "fail"}
RUN_COMPLETED = {"completed", "complete", "success", "succeeded", "ok", "done", "finished"}


def norm_gate_result(val) -> str:
    return GATE_RESULT_NORM.get(str(val or "").strip().lower(), "PENDING")


def verdict(spec: dict) -> str:
    ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator") or {}
    return GATE_TO_VERDICT.get(ge.get("result") or spec.get("gate_status"), "")


def latest_outcomes(spec: dict) -> dict:
    for r in reversed(spec.get("runs") or []):
        if isinstance(r, dict) and r.get("outcomes"):
            return r["outcomes"]
    return {}


def key_metrics(spec: dict) -> list[dict]:
    """Behavior-test outcomes as metric chips (PASS/FAIL + the observed value)."""
    metrics = []
    for name, o in latest_outcomes(spec).items():
        if not isinstance(o, dict):
            continue
        res = str(o.get("result", "")).upper()
        observed = o.get("observed")
        metrics.append({
            "label": name,
            "value": observed if observed is not None else res,
            "status": "pass" if res == "PASS" else ("fail" if res == "FAIL" else "warn"),
        })
    return metrics


def insight(spec: dict) -> str:
    """Headline insight: the first finding's statement/summary."""
    for f in (spec.get("findings") or []):
        if isinstance(f, dict):
            s = f.get("statement") or f.get("summary")
            if s:
                return s
    return ""


def conclusion_verdicts(spec: dict) -> dict:
    """Three verdict-track results computed from canonical fields (read-only).
    biological_validation ← gate_evaluator.result / gate_status;
    regression_compatibility ← run statuses; explanatory_gain ← finding tiers.
    The authored `basis` free-text is carried through per track."""
    authored = spec.get("conclusion_verdicts") or {}
    ge = (spec.get("pipeline_gate") or {}).get("gate_evaluator") or {}
    bio = norm_gate_result(ge.get("result") or spec.get("gate_status"))

    runs = [r for r in (spec.get("runs") or []) if isinstance(r, dict)]
    if not runs:
        reg = "PENDING"
    else:
        statuses = [str(r.get("status", "")).strip().lower() for r in runs]
        if any(s in RUN_ERRORED for s in statuses):
            reg = "FAIL"
        elif all(s in RUN_COMPLETED for s in statuses):
            reg = "PASS"
        else:
            reg = "PARTIAL"

    findings = [f for f in (spec.get("findings") or []) if isinstance(f, dict)]
    if not findings:
        exp = "GAP"
    elif any((f.get("tier") == "interpretation") or f.get("mechanism_origin") for f in findings):
        exp = "PASS"
    else:
        exp = "PARTIAL"

    def _basis(track):
        t = authored.get(track)
        return (t.get("basis", "") if isinstance(t, dict) else "")

    return {
        "biological_validation":    {"result": bio, "basis": _basis("biological_validation")},
        "regression_compatibility": {"result": reg, "basis": _basis("regression_compatibility")},
        "explanatory_gain":         {"result": exp, "basis": _basis("explanatory_gain")},
    }


def derived_block(spec: dict) -> dict:
    """The full derived study-content block embedded into payloads/surfaces."""
    return {
        "conclusion_verdicts": conclusion_verdicts(spec),
        "verdict": verdict(spec),
        "insight": insight(spec),
        "key_metrics": key_metrics(spec),
    }
```

- [ ] **Step 4: Point `single_study_report.py` at the module (delete its local defs)**

In `vivarium_dashboard/lib/single_study_report.py`, delete the local definitions of `_GATE_TO_VERDICT`, `_derive_verdict`, `_latest_outcomes`, `_derive_key_metrics`, `_derive_insight`, `_GATE_RESULT_NORM`, `_RUN_ERRORED`, `_RUN_COMPLETED`, `_norm_gate_result`, `_derive_conclusion_verdicts` (the block at ~431–555; KEEP `_TRACK_COLORS` and the `_render_*` functions). Add an import near the top:

```python
from vivarium_dashboard.lib import study_derivations as _D
```

Then point the remaining call sites at the module — replace each call: `_derive_conclusion_verdicts(spec)` → `_D.conclusion_verdicts(spec)`, `_derive_verdict(spec)` → `_D.verdict(spec)`, `_derive_key_metrics(spec)` → `_D.key_metrics(spec)`, `_derive_insight(spec)` → `_D.insight(spec)`. (Grep `single_study_report.py` for `_derive_verdict`/`_derive_insight`/`_derive_key_metrics`/`_derive_conclusion_verdicts` and rewrite each call.)

- [ ] **Step 5: Run tests to verify they pass + report unchanged**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_study_derivations.py -q && /Users/eranagmon/code/venv/bin/python -c "import vivarium_dashboard.lib.single_study_report"`
Expected: parity tests PASS (7 passed); the report module imports cleanly (no NameError from a missed call site). If the repo has a single-study-report test, run it too: `/Users/eranagmon/code/venv/bin/python -m pytest tests/ -q -k "single_study or study_report"` → still green.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/lib/study_derivations.py vivarium_dashboard/lib/single_study_report.py tests/test_study_derivations.py
git commit -m "refactor(derivations): extract canonical study_derivations.py; report imports it"
```

---

### Task 2: Embed the `derived` block in both payloads

**Files:**
- Modify: `vivarium_dashboard/lib/report_views.py` (`build_iset_detail`, the `studies_out.append({...})` with `computed_gate_verdict`, ~line 640)
- Modify: `vivarium_dashboard/lib/study_spec.py` (`load_study_detail_spec` — the `/api/study/{slug}` body builder) — see Step 4
- Test: `tests/test_derived_block_embed.py`

**Interfaces:**
- Consumes: `study_derivations.derived_block(spec)` (Task 1).
- Produces: each investigation `studies[]` entry and the `/api/study/{slug}` payload carry `"derived": {conclusion_verdicts, verdict, insight, key_metrics}`. Consumed by Tasks 3–4.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_derived_block_embed.py
import yaml
from pathlib import Path
from vivarium_dashboard.lib.report_views import build_iset_detail


def _ws(tmp_path: Path) -> Path:
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    inv = tmp_path / "investigations" / "inv"; inv.mkdir(parents=True)
    inv.joinpath("investigation.yaml").write_text(yaml.safe_dump({"name": "inv", "studies": ["s1"]}))
    s1 = tmp_path / "studies" / "s1"; s1.mkdir(parents=True)
    s1.joinpath("study.yaml").write_text(yaml.safe_dump(
        {"schema_version": 4, "name": "s1", "gate_status": "passed",
         "runs": [{"status": "completed"}], "findings": [{"tier": "interpretation", "statement": "X"}]}))
    return tmp_path


def test_build_iset_detail_attaches_derived_per_study(tmp_path):
    detail = build_iset_detail(_ws(tmp_path), "inv")
    s = next(x for x in detail["studies"] if x["name"] == "s1")
    assert "derived" in s
    assert set(s["derived"]) == {"conclusion_verdicts", "verdict", "insight", "key_metrics"}
    assert s["derived"]["conclusion_verdicts"]["biological_validation"]["result"] == "PASS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_derived_block_embed.py -q`
Expected: FAIL — `KeyError: 'derived'`.

- [ ] **Step 3: Attach `derived` in `build_iset_detail`**

In `vivarium_dashboard/lib/report_views.py`, add the import near the top with the other `from vivarium_dashboard.lib import …` imports:

```python
from vivarium_dashboard.lib import study_derivations as _study_derivations
```

In `build_iset_detail`, in the `studies_out.append({...})` dict (the one with `"computed_gate_verdict": ...`), add a line right after `computed_gate_verdict`:

```python
            "derived": _study_derivations.derived_block(study_spec),
```

(`study_spec` is the full loaded study spec already in scope in that loop.)

- [ ] **Step 4: Attach `derived` to the study-page payload**

The `/api/study/{slug}` route (`study_detail_route`, app.py:1256) returns `lib.study_spec.load_study_detail_spec(...)` — the single builder that already computes `computed_gate_verdict` and the other lifecycle-derived keys, and whose output becomes `window._study` (fetched by `_bootstrapStudy` in study-detail.js). Add `derived` there. In `vivarium_dashboard/lib/study_spec.py`, in `load_study_detail_spec`, add the import:

```python
from vivarium_dashboard.lib import study_derivations as _study_derivations
```

and, just before the function returns the assembled `spec` dict, add:

```python
    spec["derived"] = _study_derivations.derived_block(spec)
```

(`StudyDetail` is a pass-through `extra="allow"` model, so the new key flows through the route untouched.) Append a test to `tests/test_derived_block_embed.py` that GETs `/api/study/s1` via FastAPI `TestClient` (mirror the client fixture in `tests/test_finding_route.py`) and asserts `r.json()["derived"]["conclusion_verdicts"]["biological_validation"]["result"]` is present.

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_derived_block_embed.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/lib/report_views.py vivarium_dashboard/api/app.py tests/test_derived_block_embed.py
git commit -m "feat(derivations): embed computed derived block in study + investigation payloads"
```

---

### Task 3: `study-detail.js` reads `derived`; delete its derivation copy

**Files:**
- Modify: `vivarium_dashboard/static/study-detail.js` (the block at ~1293–1345)
- Test: `tests/test_derivations_js_dedup.py` (static assertions)

**Interfaces:**
- Consumes: `window._study.derived.conclusion_verdicts` (Task 2).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_derivations_js_dedup.py
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SD = (ROOT / "vivarium_dashboard/static/study-detail.js")
WT = (ROOT / "vivarium_dashboard/static/walkthrough.js")


def test_study_detail_js_reads_derived_not_recompute():
    js = SD.read_text()
    assert "window._study.derived" in js or "_study.derived" in js
    assert "function _deriveConclusionVerdicts" not in js  # copy removed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_derivations_js_dedup.py::test_study_detail_js_reads_derived_not_recompute -q`
Expected: FAIL — `_deriveConclusionVerdicts` still defined.

- [ ] **Step 3: Replace the derivation with a read**

In `vivarium_dashboard/static/study-detail.js`, in `_populateConclusionVerdictBadges` (~line 1334), replace the line that computes the verdicts:

```javascript
    var cv = _deriveConclusionVerdicts(window._study || {});
```

with a read of the precomputed block (empty-state fallback, no recompute):

```javascript
    var cv = ((window._study || {}).derived || {}).conclusion_verdicts || {
      biological_validation: { result: 'PENDING' },
      regression_compatibility: { result: 'PENDING' },
      explanatory_gain: { result: 'GAP' }
    };
```

Then DELETE the now-unused `function _deriveConclusionVerdicts(s) { … }` (~1307–1333). Grep the file for `_normGateResult` and `_GATE_RESULT_NORM`: if each has no remaining reference after the deletion, delete its definition too (~1297–1306); if still referenced elsewhere, leave it. Do not change `_populateConclusionVerdictBadges`'s rendering of `cv` — only its source.

- [ ] **Step 4: Run test + syntax check**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_derivations_js_dedup.py::test_study_detail_js_reads_derived_not_recompute -q && node --check vivarium_dashboard/static/study-detail.js`
Expected: PASS; `node --check` exit 0.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/study-detail.js tests/test_derivations_js_dedup.py
git commit -m "refactor(derivations): study-detail.js reads precomputed derived; drop its copy"
```

---

### Task 4: `walkthrough.js` reads `derived`; delete its derivation copy

**Files:**
- Modify: `vivarium_dashboard/static/walkthrough.js` (the block at ~6371–6630)
- Test: `tests/test_derivations_js_dedup.py` (extend)

**Interfaces:**
- Consumes: `s.derived.conclusion_verdicts` from `d.studies[]` (Task 2).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_derivations_js_dedup.py`:

```python
def test_walkthrough_js_reads_derived_not_recompute():
    js = WT.read_text()
    assert ".derived" in js and "conclusion_verdicts" in js
    assert "function _deriveConclusionVerdicts" not in js  # copy removed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_derivations_js_dedup.py::test_walkthrough_js_reads_derived_not_recompute -q`
Expected: FAIL — `_deriveConclusionVerdicts` still defined in walkthrough.js.

- [ ] **Step 3: Replace the derivation with a read**

In `vivarium_dashboard/static/walkthrough.js`, find the call site (~line 6627):

```javascript
    var cv = _deriveConclusionVerdicts(s);
```

replace with:

```javascript
    var cv = (s.derived || {}).conclusion_verdicts || {
      biological_validation: { result: 'PENDING' },
      regression_compatibility: { result: 'PENDING' },
      explanatory_gain: { result: 'GAP' }
    };
```

Then DELETE the `function _deriveConclusionVerdicts(s) { … }` definition (~6598–6626). Grep walkthrough.js for `_normGateResult` and `_GATE_RESULT_NORM`: delete each definition ONLY if it has no remaining reference after the deletion; if still referenced, leave it. **Do NOT delete `_runWithOutcomes`, `_normOutcome`, or `_testStatusToResult`** — they are used elsewhere (e.g. ~line 7372) for outcome chips, not for the verdict derivation.

- [ ] **Step 4: Run test + syntax check**

Run: `/Users/eranagmon/code/venv/bin/python -m pytest tests/test_derivations_js_dedup.py -q && node --check vivarium_dashboard/static/walkthrough.js`
Expected: both new tests PASS; `node --check` exit 0.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/static/walkthrough.js tests/test_derivations_js_dedup.py
git commit -m "refactor(derivations): walkthrough.js reads precomputed derived; drop its copy"
```

---

## Self-Review

**Spec coverage:**
- Canonical `study_derivations.py` (conclusion_verdicts/verdict/insight/key_metrics + normalizers + `derived_block`) + report imports it → Task 1. ✓
- Server embeds `derived` in the investigation payload (`build_iset_detail`) + the study-page payload (`/api/study/{slug}`) → Task 2. ✓
- `study-detail.js` reads + deletes its copy → Task 3. ✓
- `walkthrough.js` reads + deletes its copy → Task 4. ✓
- Parity tests (the canonical rules pinned) → Task 1. Additive payload, defensive JS reads, reference-checked deletions → Global Constraints + Task 3/4 steps. ✓
- Out of scope (card confidence badge; presentation split; chain_derivation alignment) → not touched. ✓

**Placeholder scan:** No TBD/TODO; complete code for the module + embed; JS steps give exact replace strings + the reference-check rule. The one underspecified anchor (the exact `/api/study/{slug}` body-assembly point) is handled by Task 2 Step 4's instruction to locate the body builder + add the key + a TestClient assertion — concrete, not a placeholder. ✓

**Type consistency:** `derived_block` returns `{conclusion_verdicts, verdict, insight, key_metrics}` (Task 1); Task 2 embeds exactly that under `"derived"`; Tasks 3–4 read `(…).derived.conclusion_verdicts` with the same track keys (`biological_validation`/`regression_compatibility`/`explanatory_gain` → `{result}`) the renderers already expect. The empty-state fallback shape matches. ✓
