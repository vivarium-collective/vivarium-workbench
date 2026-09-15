"""Tests for vivarium_workbench.lib.preflight — the pre-spend composite-run preflight.

The preflight validates a ``run_pbg --composite-id <id> --overrides {…}`` request
LOCALLY before GovCloud dispatch, turning silent wrong-but-successful failure
modes into a loud aggregated error. These tests use lightweight in-test
``@composite_generator`` composites (resolved registry-free via ``_get_spec``),
so no ParCa cache or workspace is needed.

Covered:
- preflight PASSES for a valid composite-id + overrides;
- FAILS loudly for (a) a dropped process swap, (b) a bogus emit path,
  (c) an empty variant grid;
- the load-bearing injection check catches a drop AND accepts a batch-style
  carried swap (threaded into a runner config).
"""
from __future__ import annotations

import pytest
from process_bigraph import allocate_core
from process_bigraph.composite_generator import composite_generator

from vivarium_workbench.lib.preflight import (
    PreflightError,
    expand_variant_count,
    preflight_composite_run,
)

MOD = __name__


# ---------------------------------------------------------------------------
# Fixture composites (registered at import via the decorator)
# ---------------------------------------------------------------------------

_PARAMS = {
    "injected_processes": {"default": {}},
    "variants": {"default": None},
    "bad_emit": {"default": False},
}


@composite_generator(name="pf_good", description="", parameters=_PARAMS)
def pf_good(core=None, injected_processes=None, variants=None, bad_emit=False):
    """Single-cell composite that APPLIES the requested metabolism swap and wires
    its emitter to a store that exists (unless ``bad_emit`` points it at a hole)."""
    injected_processes = injected_processes or {}
    repl = injected_processes.get("metabolism", "BasalMetabolism")
    emit_target = ["does", "not", "exist"] if bad_emit else ["level"]
    return {"state": {
        "level": 1.0,
        "metabolism": {"_type": "process", "address": f"local:{repl}", "config": {}},
        "emitter": {"_type": "step", "address": "local:RAMEmitter",
                    "inputs": {"observed": emit_target}},
    }}


@composite_generator(name="pf_agents_wrapped", description="", parameters=_PARAMS)
def pf_agents_wrapped(core=None, injected_processes=None, variants=None, bad_emit=False):
    """Agents-wrapped whole-cell shape: the cell (and the store its emitter reads)
    lives under agents/<id>/, with the emitter placed INSIDE the agent — mirrors
    ecoli_baseline's /agents/0/emitter wired to cell-relative bulk/listeners. The
    recovered emit path is bare ``level``, which resolves under the agent wrapper,
    NOT at the top level; the preflight must accept it, not flag a §2.9 miss."""
    return {"state": {
        "global_time": 0.0,
        "agents": {"0": {
            "level": 1.0,
            "emitter": {"_type": "step", "address": "local:RAMEmitter",
                        "inputs": {"observed": ["level"]}},
        }},
    }}


@composite_generator(name="pf_dropping", description="", parameters=_PARAMS)
def pf_dropping(core=None, injected_processes=None, variants=None, bad_emit=False):
    """Composite that DROPS the requested swap — mirrors the §2.2 batch-mode bug
    where ``_build_batch_document`` has no ``injected_processes`` param, so the
    metabolism process is always basal and the swap is threaded nowhere."""
    return {"state": {
        "level": 1.0,
        "metabolism": {"_type": "process", "address": "local:BasalMetabolism", "config": {}},
        "emitter": {"_type": "step", "address": "local:RAMEmitter",
                    "inputs": {"observed": ["level"]}},
    }}


@composite_generator(name="pf_batch_carried", description="", parameters=_PARAMS)
def pf_batch_carried(core=None, injected_processes=None, variants=None, bad_emit=False):
    """Batch-shaped composite that CARRIES the swap into a runner Step's config
    (the correct post-P0-1 behaviour: the swap is applied inside the worker)."""
    injected_processes = injected_processes or {}
    return {"state": {
        "runner": {
            "_type": "step",
            "address": "local:BatchBaselineRunner",
            "config": {"n_seeds": 4, "n_generations": 8,
                       "injected_processes": dict(injected_processes)},
        },
    }}


def _core():
    return allocate_core()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_preflight_passes_for_valid_request(tmp_path):
    report = preflight_composite_run(
        tmp_path, f"{MOD}.pf_good",
        overrides={
            "injected_processes": {"metabolism": "MetabolismRedux"},
            "variants": {"rate": {"value": [1.0, 2.0, 3.0]}},
        },
        core=_core(),
        n_steps=1,
        expected_variant_count=3,
    )
    assert report.passed, report.summary()
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["build"] == "pass"
    assert statuses["injection-applied"] == "pass"
    assert statuses["emit-paths"] == "pass"
    assert statuses["variant-expansion"] == "pass"


def test_preflight_no_overrides_is_clean(tmp_path):
    # No swap / no variants requested -> those checks skip, build passes.
    report = preflight_composite_run(
        tmp_path, f"{MOD}.pf_good", overrides={}, core=_core(), strict=False,
    )
    assert report.passed, report.summary()


# ---------------------------------------------------------------------------
# (a) a swap that gets dropped
# ---------------------------------------------------------------------------

def test_preflight_fails_on_dropped_swap(tmp_path):
    with pytest.raises(PreflightError) as ei:
        preflight_composite_run(
            tmp_path, f"{MOD}.pf_dropping",
            overrides={"injected_processes": {"metabolism": "MetabolismRedux"}},
            core=_core(),
        )
    failures = ei.value.failures
    assert any("injection-applied" in f for f in failures)
    assert any("silently dropped" in f or "§2.2" in f for f in failures)


def test_batch_carried_swap_is_accepted(tmp_path):
    """The load-bearing check accepts a swap threaded into a runner config
    (batch mode), not only a swapped process node (single-cell mode)."""
    report = preflight_composite_run(
        tmp_path, f"{MOD}.pf_batch_carried",
        overrides={"injected_processes": {"metabolism": "MetabolismRedux"}},
        core=_core(), n_steps=1, strict=False,
    )
    inj = next(c for c in report.checks if c.name == "injection-applied")
    assert inj.status == "pass", report.summary()
    shape = next(c for c in report.checks if c.name == "step-shape")
    assert "batch" in shape.detail


# ---------------------------------------------------------------------------
# (b) a bogus emit path
# ---------------------------------------------------------------------------

def test_preflight_fails_on_bogus_emit_path(tmp_path):
    with pytest.raises(PreflightError) as ei:
        preflight_composite_run(
            tmp_path, f"{MOD}.pf_good",
            overrides={"bad_emit": True},
            core=_core(),
        )
    failures = ei.value.failures
    assert any("emit-paths" in f for f in failures)
    assert any("§2.9" in f or "emit nothing" in f for f in failures)


def test_preflight_passes_agents_wrapped_emit_path(tmp_path):
    # Agents-wrapped whole-cell composite (ecoli_baseline shape): the emitter lives
    # inside agents/<id>/ and reads a cell-relative store, so the recovered emit
    # path resolves under the agent wrapper, not at the top level. The preflight
    # must accept it rather than raise a §2.9 "emits nothing" failure.
    report = preflight_composite_run(
        tmp_path, f"{MOD}.pf_agents_wrapped",
        overrides={},
        core=_core(),
    )
    assert not report.failures
    assert any("emit-paths" in line and "resolve to real stores" in line
               for line in report.summary().splitlines())


def test_preflight_fails_on_extra_bogus_emit_path(tmp_path):
    # A single-cell composite: an externally-supplied bogus path is a hard fail.
    with pytest.raises(PreflightError) as ei:
        preflight_composite_run(
            tmp_path, f"{MOD}.pf_good",
            overrides={},
            emit_paths=["listeners/fba_results/violacein_production_flux"],
            core=_core(),
        )
    assert any("emit-paths" in f for f in ei.value.failures)


# ---------------------------------------------------------------------------
# (c) an empty variant grid
# ---------------------------------------------------------------------------

def test_preflight_fails_on_empty_variant_grid(tmp_path):
    with pytest.raises(PreflightError) as ei:
        preflight_composite_run(
            tmp_path, f"{MOD}.pf_good",
            overrides={"variants": {"rate": {"value": []}}},
            core=_core(),
        )
    failures = ei.value.failures
    assert any("variant-expansion" in f for f in failures)
    assert any("0 branches" in f or "§3.5" in f for f in failures)


def test_preflight_fails_on_variant_count_mismatch(tmp_path):
    with pytest.raises(PreflightError) as ei:
        preflight_composite_run(
            tmp_path, f"{MOD}.pf_good",
            overrides={"variants": {"rate": {"value": [1.0, 2.0]}}},
            core=_core(),
            expected_variant_count=84,
        )
    assert any("expects 84" in f for f in ei.value.failures)


# ---------------------------------------------------------------------------
# Aggregation + build failure
# ---------------------------------------------------------------------------

def test_preflight_aggregates_all_failures(tmp_path):
    # Dropped swap + empty variant grid at once -> BOTH reported, not just the first.
    with pytest.raises(PreflightError) as ei:
        preflight_composite_run(
            tmp_path, f"{MOD}.pf_dropping",
            overrides={
                "injected_processes": {"metabolism": "MetabolismRedux"},
                "variants": {"rate": {"value": []}},
            },
            core=_core(),
        )
    failures = ei.value.failures
    assert any("injection-applied" in f for f in failures)
    assert any("variant-expansion" in f for f in failures)
    assert len(failures) >= 2


def test_preflight_fails_loudly_when_composite_missing(tmp_path):
    with pytest.raises(PreflightError) as ei:
        preflight_composite_run(
            tmp_path, f"{MOD}.does_not_exist", overrides={}, core=_core(),
        )
    assert any("build" in f for f in ei.value.failures)


def test_strict_false_returns_report_without_raising(tmp_path):
    report = preflight_composite_run(
        tmp_path, f"{MOD}.pf_dropping",
        overrides={"injected_processes": {"metabolism": "MetabolismRedux"}},
        core=_core(), strict=False,
    )
    assert not report.passed
    assert report.failures


# ---------------------------------------------------------------------------
# Unit: variant expander
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("grid,expected", [
    ({"a": {"value": [1, 2, 3]}}, 3),
    ({"a": {"value": [1, 2]}, "b": {"value": [1, 2, 3]}}, 6),
    ({"a": {"value": [1, 2, 3], "op": "zip"}, "b": {"value": [1, 2, 3], "op": "zip"}}, 3),
    ({"a": {"value": []}}, 0),
    ({}, 0),
    (None, 0),
])
def test_expand_variant_count(grid, expected):
    assert expand_variant_count(grid) == expected
