"""Fixture composite generators for ws_increase_demo.

hint_test is a minimal @composite_generator with default_n_steps=42 so the
/api/composites endpoint test can assert the field is surfaced.

side_effect_probe has a generator BODY with an observable side effect: when
its body runs it writes a sentinel file whose path is taken from the
``VWB_TEST_DISCOVER_SENTINEL`` env var. A read-only composite listing must
never execute generator bodies (issue #1574 — GET /api/composites ran a full
ParCa just to count a generator's processes), so the regression test asserts
that discovering/listing composites does NOT create the sentinel.
"""
import os

from process_bigraph.composite_generator import composite_generator


@composite_generator(name="hint_test", description="", parameters={},
                     default_n_steps=42)
def hint_test(core=None):
    return {}


@composite_generator(name="side_effect_probe", description="", parameters={})
def side_effect_probe(core=None):
    # Simulates a generator (like v2ecoli's parca_prep) that does real,
    # expensive work with on-disk side effects inside its body. Discovering /
    # listing composites must NOT call this.
    sentinel = os.environ.get("VWB_TEST_DISCOVER_SENTINEL")
    if sentinel:
        with open(sentinel, "w") as fh:
            fh.write("generator body ran\n")
    return {}
