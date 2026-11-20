"""Unit tests for the process-level run_cycle memo (lib/cycle_cache)."""
from vivarium_workbench.lib import cycle_cache
from vivarium_workbench.lib.cycle_cache import cached_run_cycle


class _FakeDiscover:
    """A stand-in for discovery_engine.discover with a counting, pure run_cycle."""

    def __init__(self):
        self.calls = 0

    def run_cycle(self, inv_spec, studies, **kwargs):
        self.calls += 1
        # a deterministic function of the inputs (purity) + a mutable payload
        return {"n": len(studies), "kw": sorted(kwargs), "items": [1, 2, 3]}


def setup_function(_):
    cycle_cache.clear_cache()


def test_memoizes_identical_calls():
    d = _FakeDiscover()
    inv = {"name": "x"}
    studies = {"a": {"id": "a"}, "b": {"id": "b"}}
    r1 = cached_run_cycle(d, inv, studies)
    r2 = cached_run_cycle(d, inv, studies)
    assert r1 == r2 == {"n": 2, "kw": [], "items": [1, 2, 3]}
    assert d.calls == 1  # second call served from the memo


def test_different_inputs_recompute():
    d = _FakeDiscover()
    cached_run_cycle(d, {"name": "x"}, {"a": {"id": "a"}})
    cached_run_cycle(d, {"name": "x"}, {"a": {"id": "a2"}})  # a member changed
    cached_run_cycle(d, {"name": "y"}, {"a": {"id": "a"}})   # the investigation changed
    assert d.calls == 3


def test_kwargs_are_part_of_the_key():
    d = _FakeDiscover()
    cached_run_cycle(d, {}, {"a": {}}, viz_state={"v": 1})
    cached_run_cycle(d, {}, {"a": {}}, viz_state={"v": 2})
    assert d.calls == 2


def test_returns_independent_deep_copies():
    d = _FakeDiscover()
    r1 = cached_run_cycle(d, {}, {"a": {}})
    r1["items"].append(99)          # mutate the caller's copy
    r2 = cached_run_cycle(d, {}, {"a": {}})
    assert r2["items"] == [1, 2, 3]  # cache + other callers unaffected
    assert d.calls == 1


def test_unserializable_input_falls_back_without_caching():
    d = _FakeDiscover()
    unser = object()  # json.dumps(default=str) -> stable repr? no: address-based
    # a value that cannot be hashed stably still must not raise and must not cache
    cached_run_cycle(d, {"obj": unser}, {"a": {}})
    cached_run_cycle(d, {"obj": object()}, {"a": {}})
    assert d.calls == 2  # distinct objects -> distinct keys (or bypass) -> recompute, never a crash
