"""Unit tests for vivarium_workbench.lib.investigations."""
from pathlib import Path

import pytest

from vivarium_workbench.lib.investigations import (
    load_spec, expand_simulations, InvestigationSpecError,
)


def _write_spec(tmp_path, text):
    p = tmp_path / "spec.yaml"
    p.write_text(text)
    return p


def test_load_spec_valid(tmp_path):
    p = _write_spec(tmp_path, """
name: minimal
composite: pkg.composites.demo
simulations:
  - name: single
    kind: single
    overrides: {rate: 1.0}
    steps: 5
observables: [level]
""")
    spec = load_spec(p)
    assert spec["name"] == "minimal"
    assert spec["composite"] == "pkg.composites.demo"
    assert len(spec["simulations"]) == 1


def test_load_spec_missing_name(tmp_path):
    p = _write_spec(tmp_path, """
composite: pkg.x
simulations: []
observables: []
""")
    with pytest.raises(InvestigationSpecError, match="name"):
        load_spec(p)


def test_load_spec_missing_composite(tmp_path):
    p = _write_spec(tmp_path, """
name: x
simulations: []
observables: []
""")
    with pytest.raises(InvestigationSpecError, match="composite"):
        load_spec(p)


def test_load_spec_bad_simulation_kind(tmp_path):
    p = _write_spec(tmp_path, """
name: x
composite: pkg.x
simulations:
  - {name: s, kind: bogus, steps: 1}
observables: [a]
""")
    with pytest.raises(InvestigationSpecError, match="kind"):
        load_spec(p)


def test_load_spec_seeds_zero(tmp_path):
    p = _write_spec(tmp_path, """
name: x
composite: pkg.x
simulations:
  - {name: s, kind: seeds, n_seeds: 0, steps: 1, base_overrides: {}}
observables: [a]
""")
    with pytest.raises(InvestigationSpecError, match="n_seeds"):
        load_spec(p)


def test_expand_simulations_single():
    spec = {"simulations": [
        {"name": "s1", "kind": "single",
         "overrides": {"rate": 1.0}, "steps": 5},
    ]}
    runs = expand_simulations(spec)
    assert len(runs) == 1
    assert runs[0]["sim_name"] == "s1"
    assert runs[0]["overrides"] == {"rate": 1.0}
    assert runs[0]["steps"] == 5
    assert "run_label" in runs[0]


def test_expand_simulations_sweep_1d():
    spec = {"simulations": [
        {"name": "sw", "kind": "sweep",
         "sweep_over": {"rate": [0.1, 0.5, 1.0]},
         "base_overrides": {"unbinding": 0.01},
         "steps": 10},
    ]}
    runs = expand_simulations(spec)
    assert len(runs) == 3
    assert all(r["sim_name"] == "sw" for r in runs)
    rates = sorted(r["overrides"]["rate"] for r in runs)
    assert rates == [0.1, 0.5, 1.0]
    assert all(r["overrides"]["unbinding"] == 0.01 for r in runs)


def test_expand_simulations_sweep_2d():
    spec = {"simulations": [
        {"name": "grid", "kind": "sweep",
         "sweep_over": {"a": [1, 2], "b": [10, 20, 30]},
         "base_overrides": {}, "steps": 1},
    ]}
    runs = expand_simulations(spec)
    assert len(runs) == 6  # 2 × 3


def test_expand_simulations_seeds():
    spec = {"simulations": [
        {"name": "rep", "kind": "seeds",
         "n_seeds": 5, "base_overrides": {"rate": 0.1}, "steps": 4},
    ]}
    runs = expand_simulations(spec)
    assert len(runs) == 5
    seeds = sorted(r["overrides"]["seed"] for r in runs)
    assert seeds == [0, 1, 2, 3, 4]
    assert all(r["overrides"]["rate"] == 0.1 for r in runs)


def test_expand_simulations_mixed():
    spec = {"simulations": [
        {"name": "a", "kind": "single", "overrides": {}, "steps": 1},
        {"name": "b", "kind": "sweep", "sweep_over": {"x": [1, 2]},
         "base_overrides": {}, "steps": 1},
        {"name": "c", "kind": "seeds", "n_seeds": 3,
         "base_overrides": {}, "steps": 1},
    ]}
    runs = expand_simulations(spec)
    assert len(runs) == 1 + 2 + 3
    names = {r["sim_name"] for r in runs}
    assert names == {"a", "b", "c"}


import json
import sqlite3

from vivarium_workbench.lib.investigations import gather_results, load_overlays


def _setup_runs_db(tmp_path):
    """Create a minimal runs.db matching the SQLiteEmitter + runs_meta shape."""
    db = tmp_path / "runs.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE runs_meta (
            run_id TEXT PRIMARY KEY, spec_id TEXT, sim_name TEXT,
            label TEXT, params_json TEXT, started_at REAL,
            completed_at REAL, n_steps INTEGER, status TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            simulation_id TEXT, step INTEGER, global_time REAL, state TEXT
        )
    """)
    # one sim "single" with one run, three step rows
    conn.execute(
        "INSERT INTO runs_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("r1", "spec", "single", "single", json.dumps({"rate": 1.0}),
         0.0, 1.0, 3, "completed"),
    )
    for i in range(3):
        conn.execute(
            "INSERT INTO history (simulation_id, step, global_time, state) VALUES (?, ?, ?, ?)",
            ("r1", i, float(i), json.dumps({"level": float(i + 1)})),
        )
    conn.commit()
    conn.close()
    return db


def test_gather_results_one_sim_one_run(tmp_path):
    db = _setup_runs_db(tmp_path)
    spec = {"simulations": [{"name": "single", "kind": "single",
                              "overrides": {"rate": 1.0}, "steps": 3}]}
    results = gather_results(spec, db)
    assert "single" in results
    assert len(results["single"]["runs"]) == 1
    run = results["single"]["runs"][0]
    assert run["run_id"] == "r1"
    assert run["params"] == {"rate": 1.0}
    assert len(run["trajectory"]) == 3
    assert run["trajectory"][2]["state"] == {"level": 3.0}


def test_load_overlays_reference_range(tmp_path):
    spec = {}
    viz = {"overlays": [{"kind": "reference-range", "y_min": 1.0, "y_max": 5.0,
                          "label": "x"}]}
    payload = load_overlays(spec, viz, tmp_path, "demo")
    assert len(payload) == 1
    assert payload[0]["kind"] == "reference-range"
    assert payload[0]["y_min"] == 1.0


def test_load_overlays_experimental_points_missing_csv(tmp_path):
    spec = {}
    viz = {"overlays": [{"kind": "experimental-points",
                          "data": "data/missing.csv",
                          "x_column": "t", "y_column": "v",
                          "label": "experiments"}]}
    payload = load_overlays(spec, viz, tmp_path, "demo")
    assert len(payload) == 1
    assert payload[0]["kind"] == "warning"
    assert "missing" in payload[0]["message"]


def test_load_overlays_experimental_points_ok(tmp_path):
    inv_dir = tmp_path / "investigations" / "demo"
    inv_dir.mkdir(parents=True)
    data_dir = inv_dir / "data"
    data_dir.mkdir()
    (data_dir / "exp.csv").write_text("t,v\n0,1.0\n1,2.5\n2,3.7\n")
    spec = {}
    viz = {"overlays": [{"kind": "experimental-points",
                          "data": "data/exp.csv",
                          "x_column": "t", "y_column": "v",
                          "label": "exp"}]}
    payload = load_overlays(spec, viz, tmp_path, "demo")
    assert len(payload) == 1
    assert payload[0]["kind"] == "experimental-points"
    assert payload[0]["points"] == [
        {"x": "0", "y": "1.0"}, {"x": "1", "y": "2.5"}, {"x": "2", "y": "3.7"},
    ]


def test_load_overlays_cross_investigation_missing(tmp_path):
    spec = {}
    viz = {"overlays": [{"kind": "cross-investigation-series",
                          "investigation": "ghost", "observable": "x",
                          "label": "ghost"}]}
    payload = load_overlays(spec, viz, tmp_path, "demo")
    assert len(payload) == 1
    assert payload[0]["kind"] == "warning"


from vivarium_workbench.lib.investigations import (
    update_spec_status, acquire_run_lock, release_run_lock,
    gather_emitter_outputs, build_viz_composite,
)


def test_update_spec_status_writes_status_and_last_run(tmp_path):
    inv_dir = tmp_path / "investigations" / "demo"
    inv_dir.mkdir(parents=True)
    (inv_dir / "spec.yaml").write_text("""
name: demo
composite: pkg.x
simulations: []
observables: []
status: planned
""")
    update_spec_status(tmp_path, "demo", status="complete", last_run="2026-05-12T10:00:00")
    new_text = (inv_dir / "spec.yaml").read_text()
    assert "status: complete" in new_text
    assert "2026-05-12T10:00:00" in new_text


def test_acquire_and_release_run_lock(tmp_path):
    inv_dir = tmp_path / "investigations" / "x"
    inv_dir.mkdir(parents=True)
    assert acquire_run_lock(tmp_path, "x") is True
    # Second acquire on same investigation must fail
    assert acquire_run_lock(tmp_path, "x") is False
    release_run_lock(tmp_path, "x")
    # After release, acquire succeeds again
    assert acquire_run_lock(tmp_path, "x") is True
    release_run_lock(tmp_path, "x")


# ---------------------------------------------------------------------------
# Visualization v2 helpers
# ---------------------------------------------------------------------------


def _setup_db_with_schema(tmp_path):
    db = tmp_path / 'runs.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE runs_meta ('
                 ' run_id TEXT PRIMARY KEY, spec_id TEXT, sim_name TEXT,'
                 ' label TEXT, params_json TEXT, started_at REAL,'
                 ' completed_at REAL, n_steps INTEGER, status TEXT)')
    conn.execute('CREATE TABLE history (simulation_id TEXT, step INTEGER, '
                 'global_time REAL, state TEXT)')
    conn.execute('CREATE TABLE simulations (simulation_id TEXT PRIMARY KEY, '
                 'name TEXT, started_at TEXT, emit_schema TEXT)')
    conn.execute('INSERT INTO runs_meta VALUES (?,?,?,?,?,?,?,?,?)',
                 ('r1', 'spec', 'baseline', 'baseline',
                  json.dumps({'rate': 1.0}), 0.0, 1.0, 3, 'completed'))
    conn.execute('INSERT INTO simulations(simulation_id, started_at, emit_schema) '
                 'VALUES (?, ?, ?)',
                 ('r1', '2026-05-12', json.dumps({'level': 'float', 'time': 'float'})))
    for i in range(3):
        conn.execute('INSERT INTO history VALUES (?,?,?,?)',
                     ('r1', i, float(i),
                      json.dumps({'level': float(i + 1), 'time': float(i)})))
    conn.commit(); conn.close()
    return db


def test_gather_emitter_outputs_returns_schema(tmp_path):
    db = _setup_db_with_schema(tmp_path)
    out = gather_emitter_outputs(db)
    assert 'schemas' in out
    assert out['schemas']['r1'] == {'level': 'float', 'time': 'float'}


def test_gather_emitter_outputs_by_sim(tmp_path):
    db = _setup_db_with_schema(tmp_path)
    out = gather_emitter_outputs(db)
    assert 'baseline' in out['by_sim']
    runs = out['by_sim']['baseline']
    assert len(runs) == 1
    run = runs[0]
    assert run['run_id'] == 'r1'
    assert run['params'] == {'rate': 1.0}
    assert run['observables']['level'] == [1.0, 2.0, 3.0]
    assert run['observables']['time'] == [0.0, 1.0, 2.0]


def test_build_viz_composite_shape():
    viz_spec = {
        'name': 'levels', 'address': 'local:TimeSeriesPlot',
        'config': {'title': 'Demo'},
    }
    gathered = {
        'schemas': {'r1': {'level': 'float', 'time': 'float'}},
        'by_sim': {'baseline': [{
            'run_id': 'r1', 'params': {}, 'sim_name': 'baseline',
            'observables': {'level': [1.0, 2.0, 4.0], 'time': [0.0, 1.0, 2.0]},
        }]},
    }
    class _Stub:
        def inputs(self): return {'observable': 'list[float]', 'time': 'list[float]'}
        def outputs(self): return {'html': 'string'}
    registry = {'TimeSeriesPlot': _Stub}
    doc = build_viz_composite(viz_spec, gathered, registry)
    assert 'visualization' in doc
    assert doc['visualization']['_type'] == 'step'
    assert doc['visualization']['address'] == 'local:TimeSeriesPlot'
    assert 'outputs' in doc['visualization']
    assert doc['visualization']['outputs']['html'] == ['output_store']


def test_render_visualizations_v2_writes_html(tmp_path):
    """End-to-end: build_viz_composite + Composite.run(1) writes html to viz/."""
    from vivarium_workbench.lib.investigations import render_visualizations

    inv_dir = tmp_path / "investigations" / "demo"
    inv_dir.mkdir(parents=True)
    _setup_db_with_schema(inv_dir)  # writes investigations/demo/runs.db

    class _Stub:
        @classmethod
        def is_visualization(cls): return True
        def inputs(self): return {'observable': 'list[float]', 'time': 'list[float]'}
        def outputs(self): return {'html': 'string'}
        def update(self, state):
            return {'html': '<p>obs=' + str(state.get('observable')) + '</p>'}

    registry = {'TimeSeriesPlot': _Stub}
    spec = {
        'composite': 'pkg.composites.demo',
        'simulations': [{'name': 'baseline', 'kind': 'single',
                          'overrides': {}, 'steps': 3}],
        'observables': ['level'],
        'visualizations': [{
            'name': 'levels',
            'address': 'local:TimeSeriesPlot',
            'config': {'title': 'T', 'inputs_map': {'observable': 'level'}},
        }],
    }

    def fake_build_and_run(doc, registry_arg):
        viz_class = registry_arg[doc['visualization']['address'].split(':', 1)[1]]
        inst = viz_class.__new__(viz_class)
        state = dict(doc['inputs_store'])
        out = inst.update(state)
        return out.get('html', '')

    paths = render_visualizations(spec, inv_dir, 'demo',
                                   core_registry=registry,
                                   build_and_run=fake_build_and_run)
    assert paths
    html_path = inv_dir / 'viz' / 'levels.html'
    assert html_path.is_file()
    text = html_path.read_text()
    assert '<p>obs=' in text


# ---------------------------------------------------------------------------
# Multi-composite (composites: list) shape tests
# ---------------------------------------------------------------------------

def test_load_spec_accepts_composites_list(tmp_path):
    """Legacy ``composites:`` shape is auto-migrated to v3 on read.

    The first composite with ``source`` becomes a baseline entry; composites
    with ``extends`` become variant entries.  Plan 1 changed the v3 shape:
    ``baseline`` is now a list of ``{name, composite, params}`` mappings, and
    variants carry ``base_composite`` + ``parameter_overrides`` (no nested
    ``intervention`` wrapper).
    """
    from vivarium_workbench.lib.investigations import load_spec
    spec_path = tmp_path / 'spec.yaml'
    spec_path.write_text(
        'name: multi\n'
        'composites:\n'
        '  - {name: baseline, source: pkg.composites.foo, document: ./composites/baseline.yaml}\n'
        '  - {name: hi, extends: baseline, parameter_overrides: {rate: 2.0}, document: ./composites/hi.yaml}\n'
        'observables:\n'
        '  - {path: [chromosome, DnaA_count]}\n'
        'runs:\n'
        '  - {composite: baseline, params: {seed: 1}, steps: 10}\n'
        '  - {composite: hi, params: {seed: 1}, steps: 10}\n'
        'visualizations: []\n'
    )
    spec = load_spec(spec_path)
    # Migration has run: composites is gone; v3 list-baseline shape is present.
    # Schema may be v3 or v4 depending on whether v3→v4 migration is active.
    assert 'composites' not in spec
    assert spec.get('schema_version') in (3, 4)
    # The source-bearing composite becomes the sole baseline entry.
    assert isinstance(spec['baseline'], list)
    assert len(spec['baseline']) == 1
    assert spec['baseline'][0]['name'] == 'baseline'
    assert spec['baseline'][0]['composite'] == 'pkg.composites.foo'
    # The extends-bearing composite becomes a variant with base_composite.
    assert len(spec['variants']) == 1
    assert spec['variants'][0]['name'] == 'hi'
    assert spec['variants'][0]['base_composite'] == 'baseline'
    assert spec['variants'][0]['parameter_overrides'] == {'rate': 2.0}
    # runs list is preserved as-is.
    assert spec['runs'][0]['composite'] == 'baseline'


# test_load_spec_rejects_runs_without_composite_when_multi was deleted (Plan 1 Task 7).
# The v2 "composites" validator enforced that every run entry had a "composite"
# field; the v3 _validate_study_v3 validator treats runs as a free-form list and
# does not inspect individual run entries, so the rejection rule no longer exists.


def test_load_spec_rejects_extends_referencing_undeclared():
    from vivarium_workbench.lib.investigations import load_spec, InvestigationSpecError
    import tempfile, pathlib, yaml
    bad = {
        'name': 'x',
        'composites': [
            {'name': 'a', 'extends': 'nonexistent', 'document': './c/a.yaml'},
        ],
        'runs': [],
    }
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / 'spec.yaml'
        p.write_text(yaml.safe_dump(bad))
        try:
            load_spec(p)
        except InvestigationSpecError as e:
            assert 'nonexistent' in str(e).lower() or 'extends' in str(e).lower()
            return
    raise AssertionError('expected InvestigationSpecError')


# test_load_spec_rejects_duplicate_composite_names was deleted (Plan 1 Task 7).
# The v2 _validate_composites_list enforced no duplicate names; _validate_study_v3
# has no such check yet (flagged as a follow-up for a later plan).


def test_load_spec_legacy_single_composite_still_accepted(tmp_path):
    """During migration window, the old single-composite shape must still load."""
    from vivarium_workbench.lib.investigations import load_spec
    spec_path = tmp_path / 'spec.yaml'
    spec_path.write_text(
        'name: legacy\n'
        'composite: pkg.composites.foo\n'
        'simulations: [{name: s1, kind: single, overrides: {}, steps: 10}]\n'
        'observables: []\n'
        'visualizations: []\n'
    )
    spec = load_spec(spec_path)
    assert 'name' in spec
    # The legacy field must remain so migration can detect it
    assert spec.get('composite') == 'pkg.composites.foo' or spec.get('composites')


# ---------------------------------------------------------------------------
# inject_emitter_step tests
# ---------------------------------------------------------------------------

def test_inject_emitter_from_observables_paths():
    """Orchestrator helper: given a composite doc + spec.yaml.observables,
    rewrite (or add) the emitter step to record those paths."""
    from vivarium_workbench.lib.investigations import inject_emitter_step

    doc = {
        'state': {
            'chromosome': {
                'DnaA_count': {'_type': 'integer', '_default': 100},
                'free_DnaA': {'_type': 'float', '_default': 50.0},
            },
        },
    }
    observables = [
        {'path': ['chromosome', 'DnaA_count']},
        {'path': ['chromosome', 'free_DnaA']},
    ]
    out = inject_emitter_step(doc, observables)

    em = out['state']['emitter']
    assert em['_type'] == 'step'
    assert em['inputs']['DnaA_count'] == ['chromosome', 'DnaA_count']
    assert em['inputs']['free_DnaA'] == ['chromosome', 'free_DnaA']
    assert em['config']['emit'] == {'DnaA_count': 'integer', 'free_DnaA': 'float'}


def test_inject_emitter_skips_missing_paths():
    from vivarium_workbench.lib.investigations import inject_emitter_step

    doc = {'state': {'chromosome': {'DnaA_count': {'_type': 'integer', '_default': 100}}}}
    observables = [
        {'path': ['chromosome', 'DnaA_count']},
        {'path': ['chromosome', 'missing']},
    ]
    out = inject_emitter_step(doc, observables)
    em = out['state']['emitter']
    assert 'DnaA_count' in em['inputs']
    assert 'missing' not in em['inputs']


def test_inject_emitter_empty_observables_returns_empty_emit():
    from vivarium_workbench.lib.investigations import inject_emitter_step
    doc = {'state': {'chromosome': {'DnaA_count': {'_type': 'integer', '_default': 100}}}}
    out = inject_emitter_step(doc, [])
    em = out['state']['emitter']
    # No observables = empty inputs + empty emit schema; runtime decides what to do.
    assert em['inputs'] == {}
    assert em['config']['emit'] == {}


def test_inject_emitter_handles_emit_all_sentinel():
    """The set-observables endpoint represents 'emit entire state' as [{path: []}].
    The injector should treat this as wiring an emitter at the root."""
    from vivarium_workbench.lib.investigations import inject_emitter_step
    doc = {'state': {'chromosome': {'DnaA_count': {'_type': 'integer', '_default': 100}}}}
    out = inject_emitter_step(doc, [{'path': []}])
    em = out['state']['emitter']
    # 'state' port wires at root; emit schema is left empty (runtime serializes everything)
    assert em.get('inputs', {}).get('state') == [] or em.get('config', {}).get('emit_all') is True


# ---------------------------------------------------------------------------
# run_investigation multi-composite end-to-end stub test
# ---------------------------------------------------------------------------

def test_run_investigation_iterates_runs_and_passes_state_doc(tmp_path):
    """Stub run_one_composite to verify the orchestrator loads each composite
    document and passes it forward as state_doc."""
    from vivarium_workbench.lib.investigations import run_investigation
    import yaml as _yaml

    inv = tmp_path / 'investigations' / 'demo'
    composites = inv / 'composites'
    composites.mkdir(parents=True)
    (composites / 'baseline.yaml').write_text(_yaml.safe_dump({
        'name': 'b',
        'state': {'chromosome': {'DnaA_count': {'_type': 'integer', '_default': 100}}},
    }))
    (composites / 'high.yaml').write_text(_yaml.safe_dump({
        'name': 'h',
        'state': {'chromosome': {'DnaA_count': {'_type': 'integer', '_default': 200}}},
    }))
    (inv / 'spec.yaml').write_text(_yaml.safe_dump({
        'name': 'demo',
        'composites': [
            {'name': 'baseline', 'source': 'pkg.x',
             'document': './composites/baseline.yaml'},
            {'name': 'high', 'source': 'pkg.y',
             'document': './composites/high.yaml'},
        ],
        'observables': [{'path': ['chromosome', 'DnaA_count']}],
        'runs': [
            {'composite': 'baseline', 'params': {}, 'steps': 5},
            {'composite': 'high', 'params': {}, 'steps': 5},
        ],
        'visualizations': [],
    }, sort_keys=False))

    captured = []
    def fake_run(spec_id, overrides, steps, sim_name, run_id, state_doc=None, **kwargs):
        captured.append({'sim_name': sim_name, 'has_doc': state_doc is not None,
                         'emitter_inputs': (state_doc or {}).get('state', {}).get('emitter', {}).get('inputs', {})})
        return {'ok': True, 'run_id': run_id}

    # Minimal core_registry stub
    summary = run_investigation(
        tmp_path, 'demo',
        run_one_composite=fake_run,
        core_registry={},
        build_and_run=lambda doc, reg: '',
    )

    sim_names = [c['sim_name'] for c in captured]
    assert sim_names == ['baseline', 'high']
    assert all(c['has_doc'] for c in captured), 'state_doc must be passed'
    # Both injected emitters wired to chromosome.DnaA_count
    for c in captured:
        assert 'DnaA_count' in c['emitter_inputs']


# ---------------------------------------------------------------------------
# v2 variants-shape tests (load_spec auto-migration + baseline validation)
# ---------------------------------------------------------------------------

def test_load_spec_accepts_variants_shape(tmp_path):
    """v2 'variants-as-composites' shape is migrated to v3 on read.

    The entry with ``source`` (no ``extends``) becomes the sole baseline list
    item; ``variants`` ends up empty since there are no extends-bearing entries.
    Plan 1 changed ``baseline`` from a string to a list of
    ``{name, composite, params}`` mappings.
    """
    p = tmp_path / 'spec.yaml'
    p.write_text(
        "name: s\n"
        "baseline: a\n"
        "variants:\n"
        "  - {name: a, source: pkg.a}\n"
    )
    spec = load_spec(p)
    assert spec.get('schema_version') in (3, 4)
    assert isinstance(spec['baseline'], list)
    assert len(spec['baseline']) == 1
    assert spec['baseline'][0]['name'] == 'a'
    assert spec['baseline'][0]['composite'] == 'pkg.a'
    # The source-only entry moved to baseline; no extends entries → variants empty.
    assert spec['variants'] == []


def test_load_spec_migrates_legacy_composites_shape_on_read(tmp_path):
    p = tmp_path / 'spec.yaml'
    p.write_text(
        "name: s\n"
        "composites:\n"
        "  - {name: a, source: pkg.a}\n"
    )
    spec = load_spec(p)
    assert 'variants' in spec
    assert 'composites' not in spec
    # File on disk was rewritten.
    assert 'variants' in p.read_text()
    assert 'composites' not in p.read_text()


# test_load_spec_validates_baseline_references_a_variant was deleted (Plan 1 Task 7).
# In v2 the string ``baseline:`` field was validated to name a declared variant;
# in v3 ``baseline`` is a list of composite mappings produced by migration, so the
# "named-string baseline must match a variant" rule no longer exists.


# ---------------------------------------------------------------------------
# v2 groups: list validation (Task B7)
# ---------------------------------------------------------------------------

def test_load_spec_accepts_groups_list(tmp_path):
    """A top-level `groups:` list with valid variant refs should load cleanly."""
    p = tmp_path / 'spec.yaml'
    p.write_text(
        "name: s\n"
        "baseline: a\n"
        "variants:\n"
        "  - {name: a, source: pkg.a}\n"
        "  - {name: b, extends: a}\n"
        "groups:\n"
        "  - name: control\n"
        "    description: Unmodified baseline.\n"
        "    variants: [a]\n"
        "  - name: treated\n"
        "    description: Drug applied.\n"
        "    variants: [b]\n"
    )
    spec = load_spec(p)
    assert len(spec['groups']) == 2
    assert spec['groups'][0]['name'] == 'control'
    assert spec['groups'][0]['variants'] == ['a']
    assert spec['groups'][1]['variants'] == ['b']


# test_load_spec_rejects_group_with_unknown_variant was deleted (Plan 1 Task 7).
# test_load_spec_rejects_duplicate_group_names was deleted (Plan 1 Task 7).
# Both tests exercised v2 _validate_variants_list groups-validation.  The redesign
# drops groups from the UI; _validate_study_v3 (v3) does not validate groups at
# all, so these rejection rules no longer fire.


# ----------------------------------------------------------------------------
# F3 — normalize_dag_edges (pipeline_gate.prerequisites is canonical;
# parent_studies stays as a back-compat fallback with a DeprecationWarning).
# ----------------------------------------------------------------------------


import warnings as _warnings

from vivarium_workbench.lib.investigations import normalize_dag_edges


def test_normalize_dag_edges_reads_pipeline_gate_first():
    """When pipeline_gate.prerequisites is set, it wins — parent_studies
    is ignored entirely (so a half-migrated spec doesn't get DOUBLE-counted
    parents)."""
    spec = {
        "name": "child",
        "pipeline_gate": {
            "prerequisites": [
                {"study": "parent-A", "condition": "ran"},
                "parent-B",
            ],
        },
        "parent_studies": ["should-be-ignored"],
    }
    edges = normalize_dag_edges(spec)
    slugs = [e["study"] for e in edges]
    assert slugs == ["parent-A", "parent-B"]
    # Bare-string entry got the default condition
    assert next(e for e in edges if e["study"] == "parent-B")["condition"] == "tests-passed"
    # Explicit condition is preserved
    assert next(e for e in edges if e["study"] == "parent-A")["condition"] == "ran"


def test_normalize_dag_edges_preserves_pass_a_extras():
    """Pass A added extension fields (required_gate_status, outputs_used,
    artifact_hashes). The normalizer passes them through verbatim so
    downstream code can use them without re-reading the raw spec."""
    spec = {
        "name": "child",
        "pipeline_gate": {
            "prerequisites": [{
                "study":                "parent-A",
                "condition":            "tests-passed",
                "required_gate_status": "passed",
                "outputs_used":         ["dnaA_count"],
            }],
        },
    }
    edge = normalize_dag_edges(spec)[0]
    assert edge["required_gate_status"] == "passed"
    assert edge["outputs_used"] == ["dnaA_count"]


def test_normalize_dag_edges_falls_back_to_parent_studies_with_warning():
    """Legacy specs that only set parent_studies still work, but emit a
    DeprecationWarning naming the study so the workspace knows to migrate."""
    spec = {
        "name": "legacy-child",
        "parent_studies": ["legacy-parent"],
    }
    with _warnings.catch_warnings(record=True) as captured:
        _warnings.simplefilter("always")
        edges = normalize_dag_edges(spec)
    assert edges == [{"study": "legacy-parent", "condition": "tests-passed"}]
    msgs = [str(w.message) for w in captured if issubclass(w.category, DeprecationWarning)]
    assert msgs, "expected a DeprecationWarning when only parent_studies is set"
    assert "legacy-child" in msgs[0]
    assert "pipeline_gate.prerequisites" in msgs[0]


def test_normalize_dag_edges_empty_spec_returns_empty_list():
    """A spec with neither field returns []."""
    assert normalize_dag_edges({"name": "lonely"}) == []


def test_normalize_dag_edges_empty_prerequisites_falls_back():
    """pipeline_gate.prerequisites: [] is treated as 'not set' so the
    fallback to parent_studies kicks in. (Otherwise migration would
    require deleting parent_studies in the same edit as adding the new
    empty list — which is the wrong order if a workspace wants to do a
    'remove all dependencies' migration in two steps.)"""
    spec = {
        "name": "child",
        "pipeline_gate": {"prerequisites": []},
        "parent_studies": ["fallback-parent"],
    }
    with _warnings.catch_warnings(record=True) as captured:
        _warnings.simplefilter("always")
        edges = normalize_dag_edges(spec)
    assert edges == [{"study": "fallback-parent", "condition": "tests-passed"}]
    # Still warns because we ended up using the legacy field
    assert any(issubclass(w.category, DeprecationWarning) for w in captured)


def test_normalize_dag_edges_no_warning_when_using_canonical():
    """The canonical-only case must be silent — no nagging the user when
    they've already done the right thing."""
    spec = {
        "name": "modern-child",
        "pipeline_gate": {"prerequisites": ["parent-A"]},
    }
    with _warnings.catch_warnings(record=True) as captured:
        _warnings.simplefilter("always")
        normalize_dag_edges(spec)
    assert not [w for w in captured if issubclass(w.category, DeprecationWarning)]


# ----------------------------------------------------------------------------
# F1 — effective_status (multi-axis canonical; legacy `status` fallback)
# ----------------------------------------------------------------------------


from vivarium_workbench.lib.investigations import effective_status


def test_effective_status_returns_none_when_nothing_set():
    """A spec with no status fields returns None — the caller decides whether
    to render that as 'planned', 'unknown', or empty."""
    assert effective_status({"name": "blank"}) is None


def test_effective_status_prefers_multi_axis_over_legacy():
    """When ANY multi-axis axis is set, the legacy `status` is ignored AND
    the DeprecationWarning does not fire — the workspace has already
    migrated."""
    spec = {
        "name": "modern",
        "status": "in-progress",
        "design_status": "approved",
    }
    with _warnings.catch_warnings(record=True) as captured:
        _warnings.simplefilter("always")
        result = effective_status(spec)
    assert result == "approved"
    assert not [w for w in captured if issubclass(w.category, DeprecationWarning)]


def test_effective_status_precedence_gate_wins_over_others():
    """The headline pill should reflect the most-downstream verdict —
    gate > evaluation > simulation > implementation > design."""
    spec = {
        "name": "all-axes",
        "design_status":          "approved",
        "implementation_status":  "complete",
        "simulation_status":      "ran",
        "evaluation_status":      "evaluated",
        "gate_status":            "passed",
        "expert_review_status":   "approved",
    }
    assert effective_status(spec) == "passed"


def test_effective_status_precedence_evaluation_wins_when_no_gate():
    spec = {
        "name": "no-gate",
        "design_status":         "approved",
        "implementation_status": "complete",
        "simulation_status":     "ran",
        "evaluation_status":     "evaluated",
    }
    assert effective_status(spec) == "evaluated"


def test_effective_status_falls_back_to_legacy_with_warning():
    """No multi-axis fields → legacy `status` wins, and a DeprecationWarning
    fires naming the study so the workspace knows to migrate."""
    spec = {"name": "legacy-only", "status": "in-progress"}
    with _warnings.catch_warnings(record=True) as captured:
        _warnings.simplefilter("always")
        result = effective_status(spec)
    assert result == "in-progress"
    msgs = [str(w.message) for w in captured if issubclass(w.category, DeprecationWarning)]
    assert msgs, "expected DeprecationWarning for legacy-only status"
    assert "legacy-only" in msgs[0]
    assert "in-progress" in msgs[0]
    # The remediation hint should name the multi-axis fields by name.
    assert "design_status" in msgs[0] or "multi-axis" in msgs[0]


def test_effective_status_ignores_null_multi_axis_fields():
    """A spec with `gate_status: null` (the JSON-schema permitted form for
    'unset') must not be picked as the effective status. Treats null/empty
    as 'not set'."""
    spec = {
        "name": "explicit-null",
        "gate_status": None,
        "evaluation_status": "",
        "status": "draft",
    }
    with _warnings.catch_warnings(record=True) as captured:
        _warnings.simplefilter("always")
        result = effective_status(spec)
    assert result == "draft"
    assert [w for w in captured if issubclass(w.category, DeprecationWarning)]


def _mk_study(ws, name, spec):
    """Write a study.yaml under the root studies/ layout."""
    sd = ws / "studies" / name
    sd.mkdir(parents=True, exist_ok=True)
    import yaml as _yaml
    (sd / "study.yaml").write_text(_yaml.safe_dump({"name": name, **spec}))


def test_evaluated_parent_satisfies_ran_prerequisite(tmp_path, monkeypatch):
    """An `evaluated` parent must satisfy a child's `ran`-condition prerequisite.

    Regression: `evaluated` is a later lifecycle state than `ran`
    (Simulate -> Evaluate -> Decide), so a study whose only prerequisite is an
    evaluated parent must NOT be reported as blocked. Previously
    `_condition_satisfied` accepted only ("ran", "complete"), so terminally
    `evaluated` parents falsely blocked every downstream study (e.g. the
    surrogate-modeling sm-02/sm-03 studies).
    """
    import yaml as _yaml
    from vivarium_workbench.lib.investigations_index import build_investigations

    ws = tmp_path / "ws"
    (ws).mkdir()
    (ws / "workspace.yaml").write_text(_yaml.safe_dump({
        "schema_version": 2, "name": "t",
        "layout": {"studies": "studies", "investigations": "investigations"},
    }))
    _mk_study(ws, "parent", {"status": "evaluated", "composite": "pkg.demo"})
    _mk_study(ws, "child", {
        "status": "planned",
        "composite": "pkg.demo",
        "pipeline_gate": {"prerequisites": [{"study": "parent", "condition": "ran"}]},
    })

    rows = {r["name"]: r for r in build_investigations(ws)["investigations"]}
    assert rows["child"]["blocked"] is False, rows["child"].get("blocked_by")

    # Sanity: a still-planned parent does NOT satisfy a `ran` prerequisite.
    _mk_study(ws, "parent", {"status": "planned", "composite": "pkg.demo"})
    rows = {r["name"]: r for r in build_investigations(ws)["investigations"]}
    assert rows["child"]["blocked"] is True
