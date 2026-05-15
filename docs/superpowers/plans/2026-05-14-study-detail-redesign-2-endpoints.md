# Study Detail Redesign — Plan 2 (Server Endpoints)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update server reader/writer endpoints to consume the v3 list-baseline + flat-variants spec shape introduced in Plan 1, and add the new CRUD endpoints (`study-baseline-add/remove`, `study-variant-set-params`, `study-intervention-add/update/delete`) the redesigned UI needs.

**Architecture:** Each endpoint is implemented as a module-level `_post_*_for_test(ws_root, body) -> (response_dict, status_code)` helper plus a thin `Handler._post_*(self, body)` wrapper that calls the helper with `WORKSPACE` and serialises the JSON response. New routes register in the dispatch table at `server.py:215-235`. Persistence stays raw-YAML (`yaml.safe_dump`, `sort_keys=False`) matching the existing study-helper pattern at `server.py:476-840`. Plan 2 also rolls up the four Minors carried forward from Plan 1's final review.

**Tech Stack:** Python 3.12, `http.server.BaseHTTPRequestHandler` (existing), PyYAML, pytest. No new dependencies.

---

## File Structure

**Modified files:**

- `vivarium_dashboard/server.py` — add new routes (dispatch table at `:215-235`), add new `_post_*_for_test` helpers and `Handler._post_*` wrappers, update reader projections (`_format_baseline_source`, `_get_investigations`, `_manifest_studies_section`, `_get_investigation_composites`) for the v3 list-baseline shape, update `_post_study_run_baseline_for_test` / `_post_study_run_variant_for_test` / `_post_study_variant_add_for_test` for the new shape.
- `vivarium_dashboard/lib/spec_migration.py` — Task 0: fix stale docstring + comment.
- `tests/test_spec_migration.py` — Task 0: update idempotent-test fixture to v3 list shape.
- `tests/test_migrate_investigations_to_studies.py` — Task 0: assert `name` field on migrated baseline entry.
- `tests/test_visualization_endpoints.py` — fix the 4 currently failing tests by updating their assertions to the v3 contract.
- `tests/test_study_runs.py` — update fixture to v3 list-baseline + flat-variant shape (the deeper "not in generator registry" failure remains pre-existing and is out of scope).
- `tests/test_study_handlers.py` — extend with new-endpoint tests; update existing study-variant-add test for new `base_composite` shape.

**New test file:**

- `tests/test_study_baseline_handlers.py` — handler tests for `study-baseline-add/remove`.
- `tests/test_study_intervention_handlers.py` — handler tests for `study-intervention-add/update/delete`.

---

## Conventions

**Test command:** No `.venv` — use `python3 -m pytest …`.

**Handler convention:**

```python
def _post_study_thing_for_test(ws_root, body):
    """One-line description. Returns (response_dict, status_code)."""
    study = (body.get("study") or body.get("investigation") or body.get("name") or "").strip()
    if not study:
        return {"error": "missing study"}, 400
    sf = _study_dir(study) / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    # ... mutate spec ...
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200
```

**v3 contract (post-Plan-1):**

```yaml
schema_version: 3
name: <slug>
baseline:
  - {name: <unique>, composite: <pkg.composites.x>, params: {...}}
  # non-empty list; names unique within the list
variants:
  - {name: <unique>, base_composite: <name from baseline>, parameter_overrides: {...}}
  # optional; may be empty
interventions:
  - {name: <unique>, description: <freeform text>}
  # optional; may be empty
runs: []          # list
visualizations: [] # list
```

**Locking:** Plan 2 does NOT add locking to write endpoints. Current `_post_study_*` helpers do not lock, and adding it would expand Plan 2's scope beyond the spec. Locking is a Plan-3+ concern.

**Commit messages:** Prefix `feat(endpoints):` for new handlers, `fix(endpoints):` for reader fixes, `refactor(endpoints):` for writer reshape, `chore:` for Task 0.

**`git add`:** ALWAYS list specific files. Never `git add -A` / `git add .`.

---

## Task 0: Plan 1 Carried-Forward Minors

Roll up four cosmetic items from Plan 1's final review into a single tidy commit.

**Files:**
- Modify: `vivarium_dashboard/lib/spec_migration.py:75-131`
- Modify: `tests/test_spec_migration.py:151-153`
- Modify: `tests/test_migrate_investigations_to_studies.py:60-63`

- [ ] **Step 1: Update `migrate_v2_to_v3` docstring**

In `vivarium_dashboard/lib/spec_migration.py`, find the docstring at the top of `migrate_v2_to_v3` (around `:75-124`). Replace the existing "Transforms" bullet list and the "Two calling paths" paragraph with this expanded version that names the three reachable shapes:

```python
    """Migrate a v2 study spec to v3 in-memory.

    Three reachable input shapes get reshaped:

    1. **Legacy `composites:` list** — each entry becomes a baseline composite.
    2. **Lone `composite:` string** (CLI bare-composite path) — wrapped as a
       single baseline entry whose `name` defaults to the FQN.
    3. **"Variants-as-composites" v2 shape** — variants carrying `source:`
       split into the baseline list; variants carrying `extends:` /
       `intervention:` become v3 variants with `base_composite` +
       `parameter_overrides`.

    All three paths produce:
      - `schema_version: 3`
      - `baseline: [{name, composite, params}, ...]` (non-empty list)
      - `variants: [...]` (possibly empty; entries have `base_composite` +
        `parameter_overrides`)
      - `interventions: []` (default; preserved if already present)
      - `objective`, `parent_studies` defaults

    Specs already at `schema_version: 3` are returned unchanged (identity).
    """
```

- [ ] **Step 2: Update the stale "no schema_version" comment**

In the same file, find the comment near line 128 (the legacy-passthrough branch) and replace:

```python
    # Specs without a schema_version (legacy single-composite shape) are
    # passed through unchanged.
```

with:

```python
    # Specs without a schema_version fall through to the variants-as-composites
    # detection below; if that doesn't match, they pass through unchanged.
```

- [ ] **Step 3: Update idempotent-test fixture to v3 list shape**

In `tests/test_spec_migration.py`, find `test_migrate_v2_to_v3_idempotent` (around `:148-155`). Change the `v3_already` fixture's `baseline` from the old dict shape to the v3 list shape:

```python
def test_migrate_v2_to_v3_idempotent():
    v3_already = {
        "schema_version": 3,
        "name": "x",
        "baseline": [{"name": "x", "composite": "pkg.composites.x", "params": {}}],
        "variants": [],
    }
    out = migrate_v2_to_v3(v3_already)
    assert out is v3_already
```

- [ ] **Step 4: Assert `name` field in migration test**

In `tests/test_migrate_investigations_to_studies.py`, find `test_migration_rewrites_spec_to_v3` (around `:50-70`). After the existing assertions on `entry["composite"]` and `entry["params"]`, add:

```python
    assert entry["name"] == "main"
```

The expected name is `"main"` because the v2 fixture's source-bearing variant is named `"main"` and the migration's name-derivation rule (see `spec_migration.py` variants-as-composites branch) carries that name into the baseline entry.

- [ ] **Step 5: Run affected tests**

Run: `python3 -m pytest tests/test_spec_migration.py tests/test_migrate_investigations_to_studies.py -q`

Expected: All tests pass (no failures, no new errors).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/lib/spec_migration.py tests/test_spec_migration.py tests/test_migrate_investigations_to_studies.py
git commit -m "chore: address Plan 1 review minors (docstring, comment, fixtures)"
```

---

## Task 1: Reader — `_format_baseline_source` for v3 list

`_format_baseline_source` currently dereferences `spec["baseline"]` as a string variant name and looks up that variant's `source`. In v3, `baseline` is a list of `{name, composite, params}`. The function must summarise the list directly.

**Decision:** When there is exactly one baseline entry, format its `composite` field with the same `pkg_short:name` rule the existing function uses. When there are multiple, format the first entry's composite and append ` (+N more)`. When the list is empty / absent, return `""`.

**Files:**
- Modify: `vivarium_dashboard/server.py:1209-1228` (the `_format_baseline_source` function)
- Test: `tests/test_visualization_endpoints.py:1351-1440` (one of the three sub-cases in `test_get_investigations_includes_baseline_source_and_conclusions_excerpt` — touched in Task 2)

- [ ] **Step 1: Write the failing test first (standalone unit test for the helper)**

Add to `tests/test_visualization_endpoints.py` (place near the existing baseline_source test, before its body):

```python
def test_format_baseline_source_single_entry_short_form():
    """Single baseline entry with a `.composites.` source → pkg_short:name."""
    from vivarium_dashboard.server import _format_baseline_source
    spec = {"baseline": [
        {"name": "core", "composite": "pbg_chromosome_rep1.composites.chromosome-partition", "params": {}},
    ]}
    assert _format_baseline_source(spec) == "pbg_chromosome_rep1:chromosome-partition"


def test_format_baseline_source_opaque_composite():
    """Single baseline entry with an opaque composite ID → returned verbatim."""
    from vivarium_dashboard.server import _format_baseline_source
    spec = {"baseline": [{"name": "x", "composite": "some.opaque.path", "params": {}}]}
    assert _format_baseline_source(spec) == "some.opaque.path"


def test_format_baseline_source_multiple_entries():
    """Multiple baseline entries → first entry formatted + ' (+N more)'."""
    from vivarium_dashboard.server import _format_baseline_source
    spec = {"baseline": [
        {"name": "a", "composite": "pkg_x.composites.first", "params": {}},
        {"name": "b", "composite": "pkg_y.composites.second", "params": {}},
        {"name": "c", "composite": "pkg_z.composites.third", "params": {}},
    ]}
    assert _format_baseline_source(spec) == "pkg_x:first (+2 more)"


def test_format_baseline_source_empty_or_absent():
    """Missing or empty baseline → empty string."""
    from vivarium_dashboard.server import _format_baseline_source
    assert _format_baseline_source({}) == ""
    assert _format_baseline_source({"baseline": []}) == ""
```

- [ ] **Step 2: Verify the new tests fail**

Run: `python3 -m pytest tests/test_visualization_endpoints.py::test_format_baseline_source_single_entry_short_form tests/test_visualization_endpoints.py::test_format_baseline_source_multiple_entries -v`

Expected: FAIL — current implementation reads `spec["baseline"]` as a string and the `variants` list-lookup path doesn't apply.

- [ ] **Step 3: Rewrite `_format_baseline_source` for v3**

Replace the function body at `vivarium_dashboard/server.py:1209-1228` with:

```python
def _format_baseline_source(spec: dict) -> str:
    """Summarise a v3 study's baseline as a short label.

    - 1 entry: pkg_short:name if the composite contains '.composites.';
      otherwise the composite verbatim.
    - N entries: format the first as above, then append ' (+N-1 more)'.
    - 0 entries / missing: ''.
    """
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return ""
    first = baseline[0] if isinstance(baseline[0], dict) else None
    if first is None:
        return ""
    composite = (first.get("composite") or "").strip()
    if not composite:
        return ""
    if ".composites." in composite:
        pkg, _, rest = composite.partition(".composites.")
        label = f"{pkg}:{rest}"
    else:
        label = composite
    if len(baseline) > 1:
        return f"{label} (+{len(baseline) - 1} more)"
    return label
```

- [ ] **Step 4: Verify the four new tests pass**

Run: `python3 -m pytest tests/test_visualization_endpoints.py::test_format_baseline_source_single_entry_short_form tests/test_visualization_endpoints.py::test_format_baseline_source_opaque_composite tests/test_visualization_endpoints.py::test_format_baseline_source_multiple_entries tests/test_visualization_endpoints.py::test_format_baseline_source_empty_or_absent -v`

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_visualization_endpoints.py
git commit -m "fix(endpoints): rewrite _format_baseline_source for v3 list baseline"
```

---

## Task 2: Reader — `_get_investigations` row shape for v3

`_get_investigations` currently emits `baseline: spec.get("baseline", "")` (expecting a string) and `n_variants: len(spec.get("variants") or [])`. Under v3, `baseline` is a list; the row should expose:
- `baseline_names: [<names>]` (new — the list of `baseline[i].name`)
- `n_baseline: len(baseline)` (new — count of composites in the study)
- `n_variants: len(spec.get("variants") or [])` (same, but post-migration the count differs from v2 — see Plan 1)
- `n_interventions: len(spec.get("interventions") or [])` (new)
- `baseline_source: _format_baseline_source(spec)` — already updated in Task 1.

Drop the `baseline: <string>` field from the row (no longer meaningful in v3). The failing test `test_get_investigations_includes_v2_summary_fields` and `test_get_investigations_includes_baseline_source_and_conclusions_excerpt` are updated to match.

**Files:**
- Modify: `vivarium_dashboard/server.py:3692-3738` (the `_get_investigations` projection)
- Modify: `tests/test_visualization_endpoints.py:1279-1321` and `:1351-1440` (two failing tests)

- [ ] **Step 1: Rewrite the failing v2-summary-fields test for the v3 contract**

Replace `test_get_investigations_includes_v2_summary_fields` in `tests/test_visualization_endpoints.py` (around `:1279-1321`) with this version that writes a v3-shaped spec and asserts the new row fields:

```python
def test_get_investigations_includes_v3_summary_fields(workspace_server):
    """Row shape under v3: baseline_names list, n_baseline, n_variants,
    n_interventions, n_runs, plus the existing composite/composites fields."""
    inv = workspace_server.root / 'investigations' / 'demo'
    inv.mkdir(parents=True)
    (inv / 'spec.yaml').write_text(yaml.safe_dump({
        'schema_version': 3,
        'name': 'demo',
        'description': 'v3 summary fixture',
        'baseline': [
            {'name': 'core', 'composite': 'pkg.composites.core', 'params': {}},
        ],
        'variants': [
            {'name': 'hi', 'base_composite': 'core', 'parameter_overrides': {'k': 1}},
            {'name': 'lo', 'base_composite': 'core', 'parameter_overrides': {'k': 0.1}},
        ],
        'interventions': [
            {'name': 'heat-shock', 'description': '+10C for 5 min'},
        ],
        'runs': [
            {'run_id': 'r1', 'variant': None, 'label': 'core', 'status': 'completed', 'n_steps': 5},
            {'run_id': 'r2', 'variant': 'hi', 'label': 'hi', 'status': 'completed', 'n_steps': 5},
        ],
    }, sort_keys=False))

    with urllib.request.urlopen(workspace_server.url + '/api/investigations') as resp:
        body = json.loads(resp.read())

    rows = [r for r in body['investigations'] if r['name'] == 'demo']
    assert len(rows) == 1
    row = rows[0]
    assert row['baseline_names'] == ['core']
    assert row['n_baseline'] == 1
    assert row['n_variants'] == 2
    assert row['n_interventions'] == 1
    assert row['n_runs'] == 2
    assert row['n_simulations'] == row['n_runs']
    assert 'composite' in row
    assert 'composites' in row
```

Then delete the old `test_get_investigations_includes_v2_summary_fields` body — the line range above is the replacement, drop the original.

- [ ] **Step 2: Rewrite the baseline_source / conclusions_excerpt test for v3 list shape**

Replace `test_get_investigations_includes_baseline_source_and_conclusions_excerpt` (around `:1351-1440`) with a v3-shaped version. Keep the three sub-cases — with-baseline (long structured conclusions), no-baseline, opaque-source — but switch each fixture to v3 list-baseline:

```python
def test_get_investigations_includes_baseline_source_and_conclusions_excerpt(workspace_server):
    """row['baseline_source'] and row['conclusions_excerpt'] under v3."""
    ws = workspace_server.root / 'investigations'

    # Case A — single baseline with .composites. source + long conclusions
    a = ws / 'with-baseline'
    a.mkdir(parents=True)
    long_prose = (
        "We saw substantial divergence in growth across substrate variants. "
        "Lag phase was extended at lower substrate concentrations, while "
        "exponential phase plateaued at expected μmax values."
    )
    (a / 'spec.yaml').write_text(yaml.safe_dump({
        'schema_version': 3,
        'name': 'with-baseline',
        'baseline': [{'name': 'core',
                      'composite': 'pbg_chromosome_rep1.composites.chromosome-partition',
                      'params': {}}],
        'variants': [{'name': 'mut', 'base_composite': 'core',
                      'parameter_overrides': {}}],
        'conclusions': (
            '## Claims\n' + long_prose +
            '\n## Evidence\nplots A,B\n## Limitations\nN=3\n## Next steps\nrun N=10\n'
        ),
    }, sort_keys=False))

    # Case B — no baseline, no conclusions
    b = ws / 'no-baseline'
    b.mkdir(parents=True)
    (b / 'spec.yaml').write_text(yaml.safe_dump({
        'schema_version': 3,
        'name': 'no-baseline',
        'baseline': [],   # empty
        'variants': [],
    }, sort_keys=False))

    # Case C — opaque single composite
    c = ws / 'opaque-source'
    c.mkdir(parents=True)
    (c / 'spec.yaml').write_text(yaml.safe_dump({
        'schema_version': 3,
        'name': 'opaque-source',
        'baseline': [{'name': 'x', 'composite': 'some.opaque.path', 'params': {}}],
        'variants': [],
    }, sort_keys=False))

    with urllib.request.urlopen(workspace_server.url + '/api/investigations') as resp:
        body = json.loads(resp.read())
    by_name = {r['name']: r for r in body['investigations']}

    row_a = by_name['with-baseline']
    assert row_a['baseline_source'] == 'pbg_chromosome_rep1:chromosome-partition'
    excerpt_a = row_a['conclusions_excerpt']
    assert len(excerpt_a) <= 241  # 240 + ellipsis
    assert excerpt_a.endswith('…')
    assert '## Claims' not in excerpt_a
    assert '## Evidence' not in excerpt_a

    row_b = by_name['no-baseline']
    assert row_b['baseline_source'] == ''
    assert row_b['conclusions_excerpt'] == ''

    row_c = by_name['opaque-source']
    assert row_c['baseline_source'] == 'some.opaque.path'
    assert row_c['conclusions_excerpt'] == ''
```

- [ ] **Step 3: Verify both tests fail**

Run: `python3 -m pytest tests/test_visualization_endpoints.py::test_get_investigations_includes_v3_summary_fields tests/test_visualization_endpoints.py::test_get_investigations_includes_baseline_source_and_conclusions_excerpt -v`

Expected: FAIL — `_get_investigations` does not emit `baseline_names`, `n_baseline`, `n_interventions` yet.

- [ ] **Step 4: Update `_get_investigations` projection**

In `vivarium_dashboard/server.py`, find the row dict at `:3712-3733` and replace with:

```python
                row = {
                    "name":            spec["name"],
                    "composite":       composite_summary,
                    "composites":      composites,
                    "description":     spec.get("description", ""),
                    "topic":           spec.get("topic", ""),
                    "tags":            spec.get("tags") or [],
                    "status":          spec.get("status", "planned"),
                    "last_run":        spec.get("last_run"),
                    "n_simulations":   n_runs,
                    "baseline_names":  [b.get("name", "") for b in (spec.get("baseline") or [])
                                        if isinstance(b, dict)],
                    "n_baseline":      len(spec.get("baseline") or []),
                    "n_variants":      len(spec.get("variants") or []),
                    "n_groups":        len(spec.get("groups") or []),
                    "n_interventions": len(spec.get("interventions") or []),
                    "n_comparisons":   len(spec.get("comparisons") or []),
                    "n_runs":          n_runs,
                    "baseline_source": _format_baseline_source(spec),
                    "conclusions_excerpt": _conclusions_excerpt(spec),
                }
```

The `baseline` (string) field is dropped from the row.

- [ ] **Step 5: Run both tests**

Run: `python3 -m pytest tests/test_visualization_endpoints.py::test_get_investigations_includes_v3_summary_fields tests/test_visualization_endpoints.py::test_get_investigations_includes_baseline_source_and_conclusions_excerpt -v`

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_visualization_endpoints.py
git commit -m "fix(endpoints): _get_investigations row shape for v3 (baseline_names, n_baseline, n_interventions)"
```

---

## Task 3: Reader — `_manifest_studies_section` for v3

Same issue as Task 2: `_manifest_studies_section` reads `spec.get("baseline", "")` as a string. Update to emit `baseline_names: [...]` and `n_baseline: <count>`; drop the old string `baseline` field.

**Files:**
- Modify: `vivarium_dashboard/server.py:6150-6178` (the `_manifest_studies_section` function)
- Modify: `tests/test_visualization_endpoints.py:1882-1910` (`test_get_workspace_manifest_studies_section_lists_specs`)

- [ ] **Step 1: Rewrite the failing manifest test for v3**

Replace `test_get_workspace_manifest_studies_section_lists_specs` (around `:1882-1910`) with:

```python
def test_get_workspace_manifest_studies_section_lists_specs(workspace_server):
    inv_dir = workspace_server.root / "investigations" / "demo"
    inv_dir.mkdir(parents=True)
    (inv_dir / "spec.yaml").write_text(yaml.safe_dump({
        "schema_version": 3,
        "name": "demo",
        "topic": "metabolism",
        "status": "in-progress",
        "baseline": [
            {"name": "core", "composite": "pbg_testws.composites.demo", "params": {}},
        ],
        "variants": [],
        "interventions": [],
        "runs": [],
        "conclusions": "## Claims\nlooks promising",
    }, sort_keys=False))

    code, body = _get(workspace_server.url + "/api/workspace-manifest")
    assert code == 200, body
    studies = body["studies"]
    assert len(studies) == 1, studies
    s = studies[0]
    assert s["name"] == "demo"
    assert s["topic"] == "metabolism"
    assert s["status"] == "in-progress"
    assert s["n_variants"] == 0
    assert s["n_baseline"] == 1
    assert s["baseline_names"] == ["core"]
    assert s["n_runs"] == 0
    assert s["conclusions_len"] > 0
```

- [ ] **Step 2: Verify the test fails**

Run: `python3 -m pytest tests/test_visualization_endpoints.py::test_get_workspace_manifest_studies_section_lists_specs -v`

Expected: FAIL — `n_baseline` and `baseline_names` are absent from the manifest entry.

- [ ] **Step 3: Update `_manifest_studies_section`**

In `vivarium_dashboard/server.py`, find the entry dict at `:6162-6175` and replace with:

```python
            entry = {
                "name":             spec.get("name", d.name),
                "topic":            spec.get("topic", ""),
                "status":           spec.get("status", "draft"),
                "baseline_names":   [b.get("name", "")
                                     for b in (spec.get("baseline") or [])
                                     if isinstance(b, dict)],
                "n_baseline":       len(spec.get("baseline") or []),
                "n_variants":       len(spec.get("variants") or []),
                "n_groups":         len(spec.get("groups") or []),
                "n_interventions":  len(spec.get("interventions") or []),
                "n_runs":           n_runs,
                "n_comparisons":    len(spec.get("comparisons") or []),
                "conclusions_len":  len(spec.get("conclusions") or ""),
            }
```

The string `baseline` field is removed.

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest tests/test_visualization_endpoints.py::test_get_workspace_manifest_studies_section_lists_specs -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_visualization_endpoints.py
git commit -m "fix(endpoints): _manifest_studies_section row shape for v3"
```

---

## Task 4: Reader — `_get_investigation_composites` reads `baseline[]`

`_get_investigation_composites` currently returns `spec.get("variants") or spec.get("composites") or []`. Under v3, the **baseline list** holds the study's composites — variants are perturbations of those, not composites themselves. The endpoint must read `spec["baseline"]` and return each `{name, composite, params}` entry (renaming `composite` to `source` for the response so existing UI callers don't need to relearn the field name).

**Decision:** Response shape stays `{"composites": [...]}` (unchanged top-level key). Each item: `{"name": <baseline.name>, "source": <baseline.composite>, "params": <baseline.params or {}>}`.

**Files:**
- Modify: `vivarium_dashboard/server.py:3624-3647` (`_get_investigation_composites`)
- Modify: `tests/test_visualization_endpoints.py:143-166` (`test_get_investigation_composites_lists_entries`)

- [ ] **Step 1: Rewrite the failing composites-listing test for v3**

Replace `test_get_investigation_composites_lists_entries` (around `:143-166`) with:

```python
def test_get_investigation_composites_lists_entries(workspace_server):
    """GET /api/investigation-composites returns the v3 study baseline list."""
    inv_dir = workspace_server.root / 'investigations' / 'demo'
    inv_dir.mkdir(parents=True)
    (inv_dir / 'spec.yaml').write_text(yaml.safe_dump({
        'schema_version': 3,
        'name': 'demo',
        'baseline': [
            {'name': 'core', 'composite': 'pkg.composites.core', 'params': {'k': 1}},
            {'name': 'alt',  'composite': 'pkg.composites.alt',  'params': {}},
        ],
        'variants': [], 'runs': [],
    }, sort_keys=False))

    req = urllib.request.Request(
        workspace_server.url + '/api/investigation-composites?investigation=demo'
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    assert len(data['composites']) == 2
    assert data['composites'][0]['name'] == 'core'
    assert data['composites'][0]['source'] == 'pkg.composites.core'
    assert data['composites'][0]['params'] == {'k': 1}
    assert data['composites'][1]['name'] == 'alt'
    assert data['composites'][1]['source'] == 'pkg.composites.alt'
```

- [ ] **Step 2: Verify the test fails**

Run: `python3 -m pytest tests/test_visualization_endpoints.py::test_get_investigation_composites_lists_entries -v`

Expected: FAIL — current handler reads `spec["variants"]`, returns the empty v3 variants list.

- [ ] **Step 3: Update `_get_investigation_composites`**

Replace the function body at `vivarium_dashboard/server.py:3624-3647` with:

```python
    def _get_investigation_composites(self):
        """List a study's baseline composites for the dashboard's composites panel."""
        from vivarium_dashboard.lib.investigations import load_spec, InvestigationSpecError
        from urllib.parse import urlparse, parse_qs

        qs = parse_qs(urlparse(self.path).query)
        name = (qs.get("investigation") or [""])[0].strip()
        if not name:
            return self._json({"error": "missing investigation"}, 400)
        try:
            spec_path = _study_spec_path(name)
        except FileNotFoundError:
            return self._json({"error": "investigation not found"}, 404)
        try:
            spec = load_spec(spec_path)
        except InvestigationSpecError as e:
            return self._json({"error": str(e)}, 400)

        items = [
            {
                "name":   b.get("name", ""),
                "source": b.get("composite", ""),
                "params": b.get("params") or {},
            }
            for b in (spec.get("baseline") or [])
            if isinstance(b, dict)
        ]
        return self._json({"composites": items}, 200)
```

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest tests/test_visualization_endpoints.py::test_get_investigation_composites_lists_entries -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_visualization_endpoints.py
git commit -m "fix(endpoints): _get_investigation_composites reads v3 baseline list"
```

---

## Task 5: Writer — `_post_study_run_baseline_for_test` for v3 list

`_post_study_run_baseline_for_test` currently reads `spec.get("baseline", {}).get("composite")` — single composite. Under v3, baseline is a list; the endpoint accepts an optional `composite` field in the body naming which baseline entry to run, defaulting to `baseline[0]` if absent. Each baseline entry's `params` (excluding `n_steps`) becomes the generator overrides.

Also: the existing test fixture in `tests/test_study_runs.py` uses the old dict-baseline shape — update it to the v3 list shape.

**Files:**
- Modify: `vivarium_dashboard/server.py:681-731` (`_post_study_run_baseline_for_test`)
- Modify: `tests/test_study_runs.py:7-34` (the `_study_ws` fixture)
- Modify: `tests/test_study_runs.py:37-57` (the baseline tests — update assertions if needed)

- [ ] **Step 1: Update the `_study_ws` fixture in `tests/test_study_runs.py`**

Replace the `_study_ws` fixture body (around `:7-34`) with the v3 list-baseline + flat-variant shape:

```python
@pytest.fixture
def _study_ws(tmp_path, monkeypatch):
    """Workspace with one v3 study whose baseline is a real viva-munk composite."""
    import vivarium_dashboard.server as srv
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        'schema_version: 2\nname: viva-munk\ncreated: "2026-05-14"\n'
        'plugin_version: 0.6.1\npackage_path: multi_cell\n'
    )
    sd = ws / "studies" / "s1"
    (sd / "composites").mkdir(parents=True)
    (sd / "viz").mkdir()
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "s1", "created": "2026-05-14",
        "status": "ran", "objective": "",
        "baseline": [
            {"name": "core",
             "composite": "multi_cell.composites.chemotaxis",
             "params": {"n_steps": 2}},
        ],
        "variants": [
            {"name": "fast", "base_composite": "core",
             "parameter_overrides": {"n_steps": 3}},
        ],
        "runs": [], "visualizations": [], "comparisons": [],
        "conclusion": None, "parent_studies": [], "interventions": [],
    }))
    monkeypatch.setattr(srv, "WORKSPACE", ws)
    return ws
```

- [ ] **Step 2: Add a new failing test for the v3 baseline-run contract**

Append to `tests/test_study_runs.py`:

```python
def test_run_baseline_with_explicit_composite_404s_unknown_name(_study_ws):
    """Body's `composite` selects a baseline entry by name; unknown → 404."""
    from vivarium_dashboard.server import _post_study_run_baseline_for_test
    resp, code = _post_study_run_baseline_for_test(
        _study_ws, {"study": "s1", "composite": "no-such-name"})
    assert code == 404
    assert "composite" in resp.get("error", "").lower()


def test_run_baseline_no_baseline_400s():
    """Empty baseline list → 400 with 'no baseline' error."""
    import tempfile
    from pathlib import Path
    from vivarium_dashboard.server import _post_study_run_baseline_for_test
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "workspace.yaml").write_text(
            'schema_version: 2\nname: t\ncreated: "2026-05-14"\n'
            'plugin_version: 0.6.1\npackage_path: t\n'
        )
        sd = ws / "studies" / "empty"
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 3, "name": "empty",
            "baseline": [], "variants": [],
            "runs": [], "visualizations": [],
        }))
        resp, code = _post_study_run_baseline_for_test(ws, {"study": "empty"})
        assert code == 400
        assert "baseline" in resp.get("error", "").lower()
```

- [ ] **Step 3: Verify the new tests fail**

Run: `python3 -m pytest tests/test_study_runs.py::test_run_baseline_with_explicit_composite_404s_unknown_name tests/test_study_runs.py::test_run_baseline_no_baseline_400s -v`

Expected: FAIL — current implementation reads `spec.get("baseline").get("composite")` which would AttributeError on a list (or silently 400 with the wrong message).

- [ ] **Step 4: Update `_post_study_run_baseline_for_test` for v3 list**

Replace the function body at `vivarium_dashboard/server.py:681-731` with:

```python
def _post_study_run_baseline_for_test(ws_root, body):
    """Run a Study's baseline composite. Returns (response_dict, status_code).

    Body:
      study:     <name>  (or `name`/`investigation`)
      composite: <baseline-entry name>  (optional; default = baseline[0].name)
      steps:     <int>   (optional; overrides params.n_steps; default 5)
    """
    from vivarium_dashboard.lib import composite_runs as cr

    name = _study_name_from_body(body)
    if not name:
        return {"error": "missing study"}, 400
    study_dir = _study_dir(name)
    sf = study_dir / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return {"error": "study has no baseline composites"}, 400

    requested = (body.get("composite") or "").strip()
    if requested:
        entry = next((b for b in baseline if isinstance(b, dict) and b.get("name") == requested),
                     None)
        if entry is None:
            return {"error": f"baseline composite {requested!r} not found"}, 404
    else:
        entry = baseline[0]
    spec_id = entry.get("composite")
    if not spec_id:
        return {"error": f"baseline entry {entry.get('name')!r} has no composite"}, 400

    params = dict(entry.get("params") or {})
    params_n_steps = params.pop("n_steps", None)
    steps = int(body.get("steps") or params_n_steps or 5)
    generator_overrides = params

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text())
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

    state, err = _resolve_study_baseline_state(pkg, spec_id, generator_overrides)
    if err is not None:
        return err, 400

    full_params = dict(generator_overrides)
    if params_n_steps is not None:
        full_params["n_steps"] = params_n_steps

    db_file = str(study_dir / "runs.db")
    run_id = cr.generate_run_id(spec_id, full_params)
    label = entry.get("name") or "baseline"
    response, code = _run_composite_subprocess(
        pkg=pkg, state=state, steps=steps, db_file=db_file,
        run_id=run_id, spec_id=spec_id, label=label, sim_name=label,
        overrides=generator_overrides,
    )
    if code == 200:
        _append_study_run(study_dir, {
            "run_id": run_id, "variant": None, "label": label,
            "status": "completed", "n_steps": steps,
            "composite": entry.get("name"),
        })
    return response, code
```

- [ ] **Step 5: Run the new tests**

Run: `python3 -m pytest tests/test_study_runs.py::test_run_baseline_with_explicit_composite_404s_unknown_name tests/test_study_runs.py::test_run_baseline_no_baseline_400s -v`

Expected: 2 passed.

- [ ] **Step 6: Run the rest of `test_study_runs.py`**

Run: `python3 -m pytest tests/test_study_runs.py -q`

Expected: `test_run_baseline_persists_and_appends` and `test_run_variant_layers_overrides` continue to fail with `"composite 'multi_cell.composites.chemotaxis' not in generator registry"` — these are **pre-existing environment failures** unrelated to Plan 2 (no `multi_cell` package in the test env). All other tests in the file pass. This is the expected outcome — do not attempt to fix the LAMMPS/composite-registry issue here.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_study_runs.py
git commit -m "refactor(endpoints): _post_study_run_baseline reads v3 baseline list + optional composite arg"
```

---

## Task 6: Writer — `_post_study_run_variant_for_test` resolves `base_composite`

Under v3, a variant carries `base_composite` (a baseline name) plus flat `parameter_overrides`. The runner looks up the named baseline entry, layers the variant's `parameter_overrides` on top of the baseline's `params`, and runs the resolved composite. The old nested `intervention.parameter_overrides` path is dropped.

**Files:**
- Modify: `vivarium_dashboard/server.py:734-794` (`_post_study_run_variant_for_test`)

- [ ] **Step 1: Add a failing test for the v3 variant-run contract**

Append to `tests/test_study_runs.py`:

```python
def test_run_variant_layers_v3_overrides():
    """A v3 variant with base_composite + parameter_overrides resolves and layers."""
    import tempfile
    from pathlib import Path
    from vivarium_dashboard.server import _post_study_run_variant_for_test
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "workspace.yaml").write_text(
            'schema_version: 2\nname: t\ncreated: "2026-05-14"\n'
            'plugin_version: 0.6.1\npackage_path: nopkg\n'
        )
        sd = ws / "studies" / "s2"
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 3, "name": "s2",
            "baseline": [
                {"name": "core",
                 "composite": "nopkg.composites.missing",
                 "params": {"k": 1, "n_steps": 2}},
            ],
            "variants": [
                {"name": "fast", "base_composite": "core",
                 "parameter_overrides": {"k": 2, "n_steps": 3}},
            ],
            "runs": [], "visualizations": [], "interventions": [],
        }))
        resp, code = _post_study_run_variant_for_test(
            ws, {"study": "s2", "variant": "fast"})
        # Composite is missing in this fake pkg → expect 400 from
        # _resolve_study_baseline_state, NOT a 400 about base_composite shape.
        assert code == 400
        err = resp.get("error", "")
        assert "base_composite" not in err.lower()
        assert "no baseline" not in err.lower()


def test_run_variant_unknown_base_composite_404s():
    """Variant referencing a non-existent baseline name → 404."""
    import tempfile
    from pathlib import Path
    from vivarium_dashboard.server import _post_study_run_variant_for_test
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td)
        (ws / "workspace.yaml").write_text(
            'schema_version: 2\nname: t\ncreated: "2026-05-14"\n'
            'plugin_version: 0.6.1\npackage_path: nopkg\n'
        )
        sd = ws / "studies" / "s3"
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 3, "name": "s3",
            "baseline": [{"name": "core", "composite": "nopkg.x", "params": {}}],
            "variants": [{"name": "dangling", "base_composite": "ghost",
                          "parameter_overrides": {}}],
            "runs": [], "visualizations": [],
        }))
        resp, code = _post_study_run_variant_for_test(
            ws, {"study": "s3", "variant": "dangling"})
        assert code == 404
        assert "base_composite" in resp.get("error", "").lower()
```

- [ ] **Step 2: Verify both tests fail**

Run: `python3 -m pytest tests/test_study_runs.py::test_run_variant_layers_v3_overrides tests/test_study_runs.py::test_run_variant_unknown_base_composite_404s -v`

Expected: FAIL — current `_post_study_run_variant_for_test` reads `spec.get("baseline").get("composite")` (would AttributeError on a list) and `variant.get("intervention").get("parameter_overrides")`.

- [ ] **Step 3: Update `_post_study_run_variant_for_test` for v3**

Replace the function body at `vivarium_dashboard/server.py:734-794` with:

```python
def _post_study_run_variant_for_test(ws_root, body):
    """Run a Study variant (baseline + param overrides). Returns (response_dict, status_code).

    Body:
      study:   <name>
      variant: <variant name>
    Resolves the variant's `base_composite` against the study's `baseline[]`,
    layers `parameter_overrides` on top of that entry's `params`, and runs.
    """
    from vivarium_dashboard.lib import composite_runs as cr

    name = _study_name_from_body(body)
    variant_name = (body.get("variant") or "").strip()
    if not name or not variant_name:
        return {"error": "missing study or variant"}, 400
    study_dir = _study_dir(name)
    sf = study_dir / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    baseline = spec.get("baseline") or []
    if not isinstance(baseline, list) or not baseline:
        return {"error": "study has no baseline composites"}, 400

    variant = next((v for v in (spec.get("variants") or [])
                    if isinstance(v, dict) and v.get("name") == variant_name), None)
    if variant is None:
        return {"error": f"variant {variant_name!r} not found"}, 404

    base_name = (variant.get("base_composite") or "").strip()
    if base_name:
        entry = next((b for b in baseline
                      if isinstance(b, dict) and b.get("name") == base_name), None)
        if entry is None:
            return {"error": f"variant base_composite {base_name!r} not in baseline"}, 404
    else:
        entry = baseline[0]
    spec_id = entry.get("composite")
    if not spec_id:
        return {"error": f"baseline entry {entry.get('name')!r} has no composite"}, 400

    params = dict(entry.get("params") or {})
    overrides = variant.get("parameter_overrides") or {}
    params.update(overrides)

    params_n_steps = params.pop("n_steps", None)
    steps = int(body.get("steps") or params_n_steps or 5)
    generator_overrides = params

    ws_data = yaml.safe_load((ws_root / "workspace.yaml").read_text())
    pkg = ws_data.get("package_path") or ("pbg_" + ws_data.get("name", "").replace("-", "_"))

    state, err = _resolve_study_baseline_state(pkg, spec_id, generator_overrides)
    if err is not None:
        return err, 400

    full_params = dict(generator_overrides)
    if params_n_steps is not None:
        full_params["n_steps"] = params_n_steps

    db_file = str(study_dir / "runs.db")
    run_id = cr.generate_run_id(spec_id, full_params)
    response, code = _run_composite_subprocess(
        pkg=pkg, state=state, steps=steps, db_file=db_file,
        run_id=run_id, spec_id=spec_id, label=variant_name,
        sim_name=variant_name, overrides=generator_overrides,
    )
    if code == 200:
        _append_study_run(study_dir, {
            "run_id": run_id, "variant": variant_name, "label": variant_name,
            "status": "completed", "n_steps": steps,
            "composite": entry.get("name"),
        })
    return response, code
```

- [ ] **Step 4: Run the new tests**

Run: `python3 -m pytest tests/test_study_runs.py::test_run_variant_layers_v3_overrides tests/test_study_runs.py::test_run_variant_unknown_base_composite_404s -v`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_study_runs.py
git commit -m "refactor(endpoints): _post_study_run_variant resolves base_composite + reads flat parameter_overrides"
```

---

## Task 7: Writer — `_post_study_variant_add_for_test` writes flat v3 shape

The v3 variant shape is `{name, base_composite, parameter_overrides}` (flat). The current handler writes the old nested `{name, intervention: {description, parameter_overrides, process_overrides}}`. The endpoint must:

- accept `base_composite` (required — must be the name of an existing baseline entry)
- accept optional `parameter_overrides` (dict)
- write the flat shape, no `intervention` nesting
- 409 if a variant with that name already exists
- 400 if `base_composite` is missing
- 404 if `base_composite` does not name a baseline entry

The old `description`/`process_overrides` params are dropped from the variant writer (descriptions belong to interventions; process overrides are deferred per spec).

**Files:**
- Modify: `vivarium_dashboard/server.py:797-820` (`_post_study_variant_add_for_test`)
- Modify: `tests/test_study_handlers.py` (existing variant-add test — update for new shape; add new tests for base_composite validation)

- [ ] **Step 1: Update `_study_workspace` fixture in `tests/test_study_handlers.py` to v3 list shape**

Find the fixture at `tests/test_study_handlers.py:10-31`. Replace the `baseline` line:

```python
        "baseline": {"composite": "pkg.composites.foo", "params": {}},
```

with:

```python
        "baseline": [{"name": "core", "composite": "pkg.composites.foo", "params": {}}],
```

- [ ] **Step 2: Replace the existing variant-add test (if it asserts the old `intervention` shape) and add new failing tests**

In `tests/test_study_handlers.py`, find any existing test for `_post_study_variant_add_for_test`. Replace it (and add the validation cases) with:

```python
def test_variant_add_writes_flat_v3_shape(_study_workspace):
    """variant-add writes {name, base_composite, parameter_overrides} flat."""
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    resp, code = _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast", "base_composite": "core",
         "parameter_overrides": {"k": 1.5}},
    )
    assert code == 200
    spec = yaml.safe_load((_study_workspace / "studies" / "s1" / "study.yaml").read_text())
    assert spec["variants"] == [
        {"name": "fast", "base_composite": "core", "parameter_overrides": {"k": 1.5}},
    ]


def test_variant_add_default_empty_overrides(_study_workspace):
    """Omitting parameter_overrides yields {} in the stored variant."""
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    resp, code = _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast", "base_composite": "core"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_workspace / "studies" / "s1" / "study.yaml").read_text())
    assert spec["variants"][0]["parameter_overrides"] == {}


def test_variant_add_rejects_missing_base_composite(_study_workspace):
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    resp, code = _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast"},
    )
    assert code == 400
    assert "base_composite" in resp.get("error", "").lower()


def test_variant_add_rejects_unknown_base_composite(_study_workspace):
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    resp, code = _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast", "base_composite": "ghost"},
    )
    assert code == 404
    assert "base_composite" in resp.get("error", "").lower()


def test_variant_add_rejects_duplicate_name(_study_workspace):
    from vivarium_dashboard.server import _post_study_variant_add_for_test
    _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast", "base_composite": "core"},
    )
    resp, code = _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "fast", "base_composite": "core"},
    )
    assert code == 409
```

- [ ] **Step 3: Verify the new tests fail**

Run: `python3 -m pytest tests/test_study_handlers.py::test_variant_add_writes_flat_v3_shape tests/test_study_handlers.py::test_variant_add_rejects_unknown_base_composite -v`

Expected: FAIL — current handler writes nested `intervention` shape and doesn't validate `base_composite`.

- [ ] **Step 4: Update `_post_study_variant_add_for_test`**

Replace `vivarium_dashboard/server.py:797-820`:

```python
def _post_study_variant_add_for_test(ws_root, body):
    """Add a variant entry to study.yaml. Returns (response_dict, status_code).

    Body:
      study or investigation:  <study name>
      name:                    <variant name>
      base_composite:          <baseline entry name> (required)
      parameter_overrides:     <dict>  (optional; defaults to {})
    """
    study = (body.get("study") or body.get("investigation") or "").strip()
    variant_name = (body.get("name") or "").strip()
    base_composite = (body.get("base_composite") or "").strip()
    if not study or not variant_name:
        return {"error": "missing study or variant name"}, 400
    if not base_composite:
        return {"error": "missing base_composite"}, 400
    overrides = body.get("parameter_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        return {"error": "parameter_overrides must be an object"}, 400

    sf = _study_dir(study) / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    baseline = spec.get("baseline") or []
    baseline_names = {b.get("name") for b in baseline if isinstance(b, dict)}
    if base_composite not in baseline_names:
        return {"error": f"base_composite {base_composite!r} not in baseline"}, 404

    variants = spec.setdefault("variants", [])
    if any(v.get("name") == variant_name for v in variants if isinstance(v, dict)):
        return {"error": f"variant {variant_name!r} already exists"}, 409

    variants.append({
        "name": variant_name,
        "base_composite": base_composite,
        "parameter_overrides": overrides or {},
    })
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True, "name": variant_name}, 200
```

- [ ] **Step 5: Run the new tests**

Run: `python3 -m pytest tests/test_study_handlers.py -q`

Expected: All tests in the file pass (the 5 new variant-add tests pass; the existing `test_set_objective_updates_yaml` etc. unchanged).

- [ ] **Step 6: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_study_handlers.py
git commit -m "refactor(endpoints): _post_study_variant_add writes flat v3 shape with base_composite"
```

---

## Task 8: New — `_post_study_variant_set_params`

Replace a variant's `parameter_overrides`. The Variants tab calls this when the user edits an existing variant's parameter form.

Body: `{study, variant, parameter_overrides}`. Replaces (does not merge). Validates that `parameter_overrides` is a dict.

**Files:**
- Modify: `vivarium_dashboard/server.py` — add helper, wrapper, route
- Modify: `tests/test_study_handlers.py` — add handler tests

- [ ] **Step 1: Write failing tests**

Append to `tests/test_study_handlers.py`:

```python
def test_variant_set_params_replaces_overrides(_study_workspace):
    """Replaces parameter_overrides wholesale (not a merge)."""
    from vivarium_dashboard.server import (
        _post_study_variant_add_for_test,
        _post_study_variant_set_params_for_test,
    )
    _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "v1", "base_composite": "core",
         "parameter_overrides": {"a": 1, "b": 2}},
    )
    resp, code = _post_study_variant_set_params_for_test(
        _study_workspace,
        {"study": "s1", "variant": "v1", "parameter_overrides": {"c": 3}},
    )
    assert code == 200
    spec = yaml.safe_load((_study_workspace / "studies" / "s1" / "study.yaml").read_text())
    v = next(v for v in spec["variants"] if v["name"] == "v1")
    assert v["parameter_overrides"] == {"c": 3}


def test_variant_set_params_404_unknown_variant(_study_workspace):
    from vivarium_dashboard.server import _post_study_variant_set_params_for_test
    resp, code = _post_study_variant_set_params_for_test(
        _study_workspace,
        {"study": "s1", "variant": "ghost", "parameter_overrides": {}},
    )
    assert code == 404


def test_variant_set_params_400_non_dict(_study_workspace):
    """parameter_overrides must be an object."""
    from vivarium_dashboard.server import (
        _post_study_variant_add_for_test,
        _post_study_variant_set_params_for_test,
    )
    _post_study_variant_add_for_test(
        _study_workspace,
        {"study": "s1", "name": "v1", "base_composite": "core"},
    )
    resp, code = _post_study_variant_set_params_for_test(
        _study_workspace,
        {"study": "s1", "variant": "v1", "parameter_overrides": "not a dict"},
    )
    assert code == 400
```

- [ ] **Step 2: Verify tests fail**

Run: `python3 -m pytest tests/test_study_handlers.py::test_variant_set_params_replaces_overrides -v`

Expected: FAIL — `_post_study_variant_set_params_for_test` does not exist (ImportError).

- [ ] **Step 3: Add the helper to `server.py`**

Insert after `_post_study_variant_delete_for_test` (around `:840`):

```python
def _post_study_variant_set_params_for_test(ws_root, body):
    """Replace a variant's parameter_overrides. Returns (response_dict, status_code).

    Body:
      study:                <name>
      variant:              <variant name>
      parameter_overrides:  <dict>  (replaces; does not merge)
    """
    study = _study_name_from_body(body)
    variant_name = (body.get("variant") or "").strip()
    overrides = body.get("parameter_overrides")
    if not study or not variant_name:
        return {"error": "missing study or variant"}, 400
    if not isinstance(overrides, dict):
        return {"error": "parameter_overrides must be an object"}, 400

    sf = _study_dir(study) / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    variants = spec.get("variants") or []
    for v in variants:
        if isinstance(v, dict) and v.get("name") == variant_name:
            v["parameter_overrides"] = dict(overrides)
            spec["variants"] = variants
            sf.write_text(yaml.safe_dump(spec, sort_keys=False))
            return {"ok": True}, 200
    return {"error": f"variant {variant_name!r} not found"}, 404
```

- [ ] **Step 4: Add the Handler wrapper**

Insert after `_post_study_variant_delete` (around `:5400-5403`):

```python
    def _post_study_variant_set_params(self, body: dict):
        response, code = _post_study_variant_set_params_for_test(WORKSPACE, body)
        return self._json(response, code)
```

- [ ] **Step 5: Add the route**

In the dispatch table at `server.py:215-235`, add:

```python
    "/api/study-variant-set-params":    "_post_study_variant_set_params",
```

Place this line adjacent to the other `study-variant-*` routes.

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/test_study_handlers.py::test_variant_set_params_replaces_overrides tests/test_study_handlers.py::test_variant_set_params_404_unknown_variant tests/test_study_handlers.py::test_variant_set_params_400_non_dict -v`

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_study_handlers.py
git commit -m "feat(endpoints): study-variant-set-params replaces a variant's parameter_overrides"
```

---

## Task 9: New — `_post_study_baseline_add`

Add a composite to the study's `baseline[]`.

Body: `{study, name, composite, params?}`.

Validates: name unique within the existing baseline list; `composite` non-empty.

**Files:**
- Modify: `vivarium_dashboard/server.py` — helper, wrapper, route
- Create: `tests/test_study_baseline_handlers.py`

- [ ] **Step 1: Create the failing test file**

Write `tests/test_study_baseline_handlers.py`:

```python
"""Handler tests for v3 study baseline CRUD."""
import yaml
import pytest


@pytest.fixture
def _study_ws(tmp_path):
    """Workspace with one v3 study with a single baseline entry."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        'schema_version: 2\nname: ws\ncreated: "2026-05-14"\n'
        'plugin_version: 0.6.1\npackage_path: pkg\n'
    )
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "s1",
        "baseline": [{"name": "core", "composite": "pkg.composites.foo", "params": {}}],
        "variants": [], "runs": [], "visualizations": [], "interventions": [],
    }))
    return ws


def test_baseline_add_appends(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "alt",
         "composite": "pkg.composites.bar", "params": {"k": 1}},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["baseline"] == [
        {"name": "core", "composite": "pkg.composites.foo", "params": {}},
        {"name": "alt", "composite": "pkg.composites.bar", "params": {"k": 1}},
    ]


def test_baseline_add_default_empty_params(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "alt", "composite": "pkg.composites.bar"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    alt = next(b for b in spec["baseline"] if b["name"] == "alt")
    assert alt["params"] == {}


def test_baseline_add_rejects_missing_composite(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "alt"},
    )
    assert code == 400
    assert "composite" in resp.get("error", "").lower()


def test_baseline_add_rejects_duplicate_name(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "name": "core", "composite": "pkg.composites.other"},
    )
    assert code == 409
    assert "core" in resp.get("error", "")


def test_baseline_add_rejects_missing_name(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_add_for_test
    resp, code = _post_study_baseline_add_for_test(
        _study_ws,
        {"study": "s1", "composite": "pkg.composites.other"},
    )
    assert code == 400
```

- [ ] **Step 2: Verify the tests fail**

Run: `python3 -m pytest tests/test_study_baseline_handlers.py -v`

Expected: FAIL — `_post_study_baseline_add_for_test` does not exist.

- [ ] **Step 3: Add the helper to `server.py`**

Insert after `_post_study_variant_set_params_for_test` (added in Task 8):

```python
def _post_study_baseline_add_for_test(ws_root, body):
    """Append a composite to study.yaml.baseline[]. Returns (response_dict, status_code).

    Body:
      study:     <name>
      name:      <baseline entry name>  (unique within baseline)
      composite: <pkg.composites.x>
      params:    <dict>  (optional; defaults to {})
    """
    study = _study_name_from_body(body)
    entry_name = (body.get("name") or "").strip()
    composite = (body.get("composite") or "").strip()
    params = body.get("params")
    if not study:
        return {"error": "missing study"}, 400
    if not entry_name:
        return {"error": "missing baseline entry name"}, 400
    if not composite:
        return {"error": "missing composite"}, 400
    if params is not None and not isinstance(params, dict):
        return {"error": "params must be an object"}, 400

    sf = _study_dir(study) / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    baseline = spec.setdefault("baseline", [])
    if any(b.get("name") == entry_name for b in baseline if isinstance(b, dict)):
        return {"error": f"baseline entry {entry_name!r} already exists"}, 409
    baseline.append({"name": entry_name, "composite": composite, "params": params or {}})
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True, "name": entry_name}, 200
```

- [ ] **Step 4: Add the Handler wrapper**

Insert after `_post_study_variant_set_params` (Task 8):

```python
    def _post_study_baseline_add(self, body: dict):
        response, code = _post_study_baseline_add_for_test(WORKSPACE, body)
        return self._json(response, code)
```

- [ ] **Step 5: Add the route**

In the dispatch table, add (near the other `study-*` routes):

```python
    "/api/study-baseline-add":          "_post_study_baseline_add",
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/test_study_baseline_handlers.py -v`

Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_study_baseline_handlers.py
git commit -m "feat(endpoints): study-baseline-add appends to v3 baseline list"
```

---

## Task 10: New — `_post_study_baseline_remove`

Remove a composite from `baseline[]` by name. **Refuses (409) if any variant has `base_composite` pointing to that name** — caller (UI) must delete the dependent variants first. Refuses (400) if the removal would leave `baseline[]` empty.

**Files:**
- Modify: `vivarium_dashboard/server.py` — helper, wrapper, route
- Modify: `tests/test_study_baseline_handlers.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_study_baseline_handlers.py`:

```python
def test_baseline_remove_succeeds(_study_ws):
    """Removing a baseline entry that no variant references → 200."""
    from vivarium_dashboard.server import (
        _post_study_baseline_add_for_test,
        _post_study_baseline_remove_for_test,
    )
    _post_study_baseline_add_for_test(
        _study_ws, {"study": "s1", "name": "alt", "composite": "pkg.composites.bar"},
    )
    resp, code = _post_study_baseline_remove_for_test(
        _study_ws, {"study": "s1", "name": "alt"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert [b["name"] for b in spec["baseline"]] == ["core"]


def test_baseline_remove_404_unknown(_study_ws):
    from vivarium_dashboard.server import _post_study_baseline_remove_for_test
    resp, code = _post_study_baseline_remove_for_test(
        _study_ws, {"study": "s1", "name": "ghost"},
    )
    assert code == 404


def test_baseline_remove_409_when_variant_references_it(_study_ws):
    """Refuses to remove a baseline entry that variants depend on."""
    from vivarium_dashboard.server import (
        _post_study_variant_add_for_test,
        _post_study_baseline_remove_for_test,
    )
    _post_study_variant_add_for_test(
        _study_ws, {"study": "s1", "name": "fast", "base_composite": "core"},
    )
    resp, code = _post_study_baseline_remove_for_test(
        _study_ws, {"study": "s1", "name": "core"},
    )
    assert code == 409
    err = resp.get("error", "")
    assert "fast" in err  # error names the referencing variant(s)


def test_baseline_remove_400_when_would_be_empty(_study_ws):
    """Refuses to remove the last baseline entry."""
    from vivarium_dashboard.server import _post_study_baseline_remove_for_test
    resp, code = _post_study_baseline_remove_for_test(
        _study_ws, {"study": "s1", "name": "core"},
    )
    assert code == 400
    assert "empty" in resp.get("error", "").lower()
```

- [ ] **Step 2: Verify tests fail**

Run: `python3 -m pytest tests/test_study_baseline_handlers.py::test_baseline_remove_succeeds -v`

Expected: FAIL — helper does not exist.

- [ ] **Step 3: Add the helper to `server.py`**

Insert after `_post_study_baseline_add_for_test`:

```python
def _post_study_baseline_remove_for_test(ws_root, body):
    """Remove a baseline entry by name. Returns (response_dict, status_code).

    Body:
      study: <name>
      name:  <baseline entry name>

    409 if any variant has base_composite == name.
    400 if removal would leave baseline empty.
    """
    study = _study_name_from_body(body)
    entry_name = (body.get("name") or "").strip()
    if not study or not entry_name:
        return {"error": "missing study or baseline entry name"}, 400

    sf = _study_dir(study) / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    baseline = spec.get("baseline") or []
    remaining = [b for b in baseline
                 if not (isinstance(b, dict) and b.get("name") == entry_name)]
    if len(remaining) == len(baseline):
        return {"error": f"baseline entry {entry_name!r} not found"}, 404
    if not remaining:
        return {"error": "cannot leave baseline empty"}, 400

    dependents = [v.get("name") for v in (spec.get("variants") or [])
                  if isinstance(v, dict) and v.get("base_composite") == entry_name]
    if dependents:
        return {
            "error": f"variants reference {entry_name!r}: {', '.join(dependents)}",
            "dependents": dependents,
        }, 409

    spec["baseline"] = remaining
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200
```

- [ ] **Step 4: Add the Handler wrapper**

Insert after `_post_study_baseline_add` (Task 9):

```python
    def _post_study_baseline_remove(self, body: dict):
        response, code = _post_study_baseline_remove_for_test(WORKSPACE, body)
        return self._json(response, code)
```

- [ ] **Step 5: Add the route**

Add to the dispatch table:

```python
    "/api/study-baseline-remove":       "_post_study_baseline_remove",
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/test_study_baseline_handlers.py -v`

Expected: All 9 tests in the file pass.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_study_baseline_handlers.py
git commit -m "feat(endpoints): study-baseline-remove with variant-dependency + empty-list checks"
```

---

## Task 11: New — `_post_study_intervention_add`

Append `{name, description}` to `interventions[]`. 409 if a duplicate name.

**Files:**
- Modify: `vivarium_dashboard/server.py`
- Create: `tests/test_study_intervention_handlers.py`

- [ ] **Step 1: Create the failing test file**

Write `tests/test_study_intervention_handlers.py`:

```python
"""Handler tests for v3 study intervention CRUD."""
import yaml
import pytest


@pytest.fixture
def _study_ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        'schema_version: 2\nname: ws\ncreated: "2026-05-14"\n'
        'plugin_version: 0.6.1\npackage_path: pkg\n'
    )
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "name": "s1",
        "baseline": [{"name": "core", "composite": "pkg.composites.foo", "params": {}}],
        "variants": [], "runs": [], "visualizations": [],
        # NOTE: interventions key intentionally absent, to test default-create.
    }))
    return ws


def test_intervention_add_appends(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_add_for_test
    resp, code = _post_study_intervention_add_for_test(
        _study_ws,
        {"study": "s1", "name": "heat-shock", "description": "+10C for 5 min"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["interventions"] == [
        {"name": "heat-shock", "description": "+10C for 5 min"},
    ]


def test_intervention_add_default_empty_description(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_add_for_test
    resp, code = _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "x"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["interventions"][0]["description"] == ""


def test_intervention_add_rejects_missing_name(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_add_for_test
    resp, code = _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "description": "no name"},
    )
    assert code == 400


def test_intervention_add_rejects_duplicate_name(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_add_for_test
    _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "x", "description": "first"},
    )
    resp, code = _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "x", "description": "second"},
    )
    assert code == 409
```

- [ ] **Step 2: Verify tests fail**

Run: `python3 -m pytest tests/test_study_intervention_handlers.py -v`

Expected: FAIL — `_post_study_intervention_add_for_test` does not exist.

- [ ] **Step 3: Add the helper to `server.py`**

Insert after `_post_study_baseline_remove_for_test`:

```python
def _post_study_intervention_add_for_test(ws_root, body):
    """Append an intervention to study.yaml.interventions[]. Returns (response, code).

    Body:
      study:       <name>
      name:        <intervention name>  (unique within interventions)
      description: <freeform text>  (optional; defaults to "")
    """
    study = _study_name_from_body(body)
    name = (body.get("name") or "").strip()
    description = body.get("description") or ""
    if not study or not name:
        return {"error": "missing study or intervention name"}, 400

    sf = _study_dir(study) / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    interventions = spec.setdefault("interventions", [])
    if any(i.get("name") == name for i in interventions if isinstance(i, dict)):
        return {"error": f"intervention {name!r} already exists"}, 409
    interventions.append({"name": name, "description": description})
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True, "name": name}, 200
```

- [ ] **Step 4: Add the Handler wrapper**

Insert after `_post_study_baseline_remove` (Task 10):

```python
    def _post_study_intervention_add(self, body: dict):
        response, code = _post_study_intervention_add_for_test(WORKSPACE, body)
        return self._json(response, code)
```

- [ ] **Step 5: Add the route**

Add to the dispatch table:

```python
    "/api/study-intervention-add":      "_post_study_intervention_add",
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/test_study_intervention_handlers.py -v`

Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_study_intervention_handlers.py
git commit -m "feat(endpoints): study-intervention-add appends to v3 interventions list"
```

---

## Task 12: New — `_post_study_intervention_update`

Update an intervention's `description` by name.

Body: `{study, name, description}`. 404 if name not found. Replaces (not merges) — same semantics as `study-variant-set-params`.

**Files:**
- Modify: `vivarium_dashboard/server.py`
- Modify: `tests/test_study_intervention_handlers.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_study_intervention_handlers.py`:

```python
def test_intervention_update_replaces_description(_study_ws):
    from vivarium_dashboard.server import (
        _post_study_intervention_add_for_test,
        _post_study_intervention_update_for_test,
    )
    _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "x", "description": "old"},
    )
    resp, code = _post_study_intervention_update_for_test(
        _study_ws, {"study": "s1", "name": "x", "description": "new"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert spec["interventions"][0]["description"] == "new"


def test_intervention_update_404_unknown(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_update_for_test
    resp, code = _post_study_intervention_update_for_test(
        _study_ws, {"study": "s1", "name": "ghost", "description": "x"},
    )
    assert code == 404
```

- [ ] **Step 2: Verify tests fail**

Run: `python3 -m pytest tests/test_study_intervention_handlers.py::test_intervention_update_replaces_description -v`

Expected: FAIL — helper does not exist.

- [ ] **Step 3: Add the helper**

Insert after `_post_study_intervention_add_for_test`:

```python
def _post_study_intervention_update_for_test(ws_root, body):
    """Update an intervention's description. Returns (response, code)."""
    study = _study_name_from_body(body)
    name = (body.get("name") or "").strip()
    description = body.get("description") or ""
    if not study or not name:
        return {"error": "missing study or intervention name"}, 400

    sf = _study_dir(study) / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    for i in spec.get("interventions") or []:
        if isinstance(i, dict) and i.get("name") == name:
            i["description"] = description
            sf.write_text(yaml.safe_dump(spec, sort_keys=False))
            return {"ok": True}, 200
    return {"error": f"intervention {name!r} not found"}, 404
```

- [ ] **Step 4: Add the Handler wrapper**

```python
    def _post_study_intervention_update(self, body: dict):
        response, code = _post_study_intervention_update_for_test(WORKSPACE, body)
        return self._json(response, code)
```

- [ ] **Step 5: Add the route**

```python
    "/api/study-intervention-update":   "_post_study_intervention_update",
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/test_study_intervention_handlers.py -v`

Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_study_intervention_handlers.py
git commit -m "feat(endpoints): study-intervention-update replaces description by name"
```

---

## Task 13: New — `_post_study_intervention_delete`

Remove an intervention by name. 404 if name not found.

Body: `{study, name}`.

**Files:**
- Modify: `vivarium_dashboard/server.py`
- Modify: `tests/test_study_intervention_handlers.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_study_intervention_handlers.py`:

```python
def test_intervention_delete_removes(_study_ws):
    from vivarium_dashboard.server import (
        _post_study_intervention_add_for_test,
        _post_study_intervention_delete_for_test,
    )
    _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "x"},
    )
    _post_study_intervention_add_for_test(
        _study_ws, {"study": "s1", "name": "y"},
    )
    resp, code = _post_study_intervention_delete_for_test(
        _study_ws, {"study": "s1", "name": "x"},
    )
    assert code == 200
    spec = yaml.safe_load((_study_ws / "studies" / "s1" / "study.yaml").read_text())
    assert [i["name"] for i in spec["interventions"]] == ["y"]


def test_intervention_delete_404_unknown(_study_ws):
    from vivarium_dashboard.server import _post_study_intervention_delete_for_test
    resp, code = _post_study_intervention_delete_for_test(
        _study_ws, {"study": "s1", "name": "ghost"},
    )
    assert code == 404
```

- [ ] **Step 2: Verify tests fail**

Run: `python3 -m pytest tests/test_study_intervention_handlers.py::test_intervention_delete_removes -v`

Expected: FAIL — helper does not exist.

- [ ] **Step 3: Add the helper**

Insert after `_post_study_intervention_update_for_test`:

```python
def _post_study_intervention_delete_for_test(ws_root, body):
    """Remove an intervention by name. Returns (response, code)."""
    study = _study_name_from_body(body)
    name = (body.get("name") or "").strip()
    if not study or not name:
        return {"error": "missing study or intervention name"}, 400

    sf = _study_dir(study) / "study.yaml"
    if not sf.is_file():
        return {"error": "study not found"}, 404

    spec = yaml.safe_load(sf.read_text()) or {}
    interventions = spec.get("interventions") or []
    remaining = [i for i in interventions
                 if not (isinstance(i, dict) and i.get("name") == name)]
    if len(remaining) == len(interventions):
        return {"error": f"intervention {name!r} not found"}, 404
    spec["interventions"] = remaining
    sf.write_text(yaml.safe_dump(spec, sort_keys=False))
    return {"ok": True}, 200
```

- [ ] **Step 4: Add the Handler wrapper**

```python
    def _post_study_intervention_delete(self, body: dict):
        response, code = _post_study_intervention_delete_for_test(WORKSPACE, body)
        return self._json(response, code)
```

- [ ] **Step 5: Add the route**

```python
    "/api/study-intervention-delete":   "_post_study_intervention_delete",
```

- [ ] **Step 6: Run all of Plan 2's new test files**

Run: `python3 -m pytest tests/test_study_intervention_handlers.py tests/test_study_baseline_handlers.py tests/test_study_handlers.py tests/test_study_runs.py tests/test_visualization_endpoints.py -q`

Expected:
- `test_study_intervention_handlers.py` — 8 passed
- `test_study_baseline_handlers.py` — 9 passed
- `test_study_handlers.py` — all pass
- `test_study_runs.py` — pre-existing 2 failures remain (the `multi_cell.composites.chemotaxis` registry issue, unrelated to Plan 2). Plan-2's new tests pass.
- `test_visualization_endpoints.py` — the 4 previously failing tests now pass; the 1 pre-existing failure (`test_post_create_from_composite_creates_v2_spec`) remains.

- [ ] **Step 7: Commit**

```bash
git add vivarium_dashboard/server.py tests/test_study_intervention_handlers.py
git commit -m "feat(endpoints): study-intervention-delete removes by name"
```

---

## Final Verification (after Task 13)

Run the full suite:

```bash
python3 -m pytest -q 2>&1 | tail -15
```

Expected outcome:
- **Pre-existing failures remain (unchanged):**
  - `tests/test_investigations.py::test_run_investigation_iterates_runs_and_passes_state_doc` (scripts._lib missing)
  - `tests/test_investigation_run_e2e.py::test_run_baseline_investigation`
  - `tests/test_investigation_run_e2e.py::test_detail_after_run`
  - `tests/test_study_runs.py::test_run_baseline_persists_and_appends` (multi_cell composite missing)
  - `tests/test_study_runs.py::test_run_variant_layers_overrides` (multi_cell composite missing)
  - `tests/test_visualization_endpoints.py::test_post_create_from_composite_creates_v2_spec`
- **Plan-1 ripple failures fixed (the 4 previously failing endpoint reader tests now pass).**
- **No regressions** — all other tests that were passing before remain passing.

Total expected: 6 failed (the same 6 pre-existing), rest passed.

---

## Notes for Plan 3 (UI)

When Plan 3 wires the redesigned `study-detail.html`, the JS will call:

- `GET /api/investigation-composites?investigation=<name>` → Baseline tab (returns the `[{name, source, params}]` list per Task 4).
- `POST /api/study-baseline-add` (Task 9) — `+ Add composite` button.
- `POST /api/study-baseline-remove` (Task 10) — per-baseline `Remove`. Handle the 409-with-dependents response by surfacing which variants block the removal.
- `POST /api/study-variant-add` (Task 7) — `+ New variant` flow.
- `POST /api/study-variant-set-params` (Task 8) — `Edit params` form save.
- `POST /api/study-variant-delete` (existing) — `Delete` variant.
- `POST /api/study-run-baseline` (Task 5) — `Run` button per-baseline-entry. Pass `composite: <baseline.name>` in the body.
- `POST /api/study-run-variant` (Task 6) — `Run` button per-variant.
- `POST /api/study-intervention-add/update/delete` (Tasks 11-13) — Interventions tab.
- `POST /api/study-set-objective` / `study-set-conclusion` (existing) — Overview tab.
