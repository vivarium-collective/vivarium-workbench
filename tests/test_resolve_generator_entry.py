"""resolve_generator_entry resolves a @composite_generator by its SHORT name
(entry.name), even though _REGISTRY is keyed by the fully-qualified module path
(entry.id). That key/name mismatch is what made editable-workspace composites
unrunnable: run_runner/composite_runs/composite_flush all looked them up by the
short spec_id and missed.
"""
from process_bigraph.composite_generator import composite_generator, _REGISTRY

from vivarium_workbench.lib.composite_lookup import resolve_generator_entry


@composite_generator(name="_test_resolve_me")
def _test_resolve_me_gen(core=None):
    return {"state": {}}


def test_registry_is_keyed_by_full_module_path_not_short_name():
    # The gap this helper bridges: the short name is never a registry key.
    assert "_test_resolve_me" not in set(_REGISTRY.keys())
    assert any(
        getattr(e, "name", None) == "_test_resolve_me" for e in _REGISTRY.values()
    )


def test_resolves_by_short_name():
    entry = resolve_generator_entry("_test_resolve_me")
    assert entry is not None
    assert entry.name == "_test_resolve_me"


def test_resolves_by_full_id_too():
    entry = resolve_generator_entry("_test_resolve_me")
    by_id = resolve_generator_entry(entry.id)
    assert by_id is not None and by_id.name == "_test_resolve_me"


def test_missing_returns_none():
    assert resolve_generator_entry("definitely_not_a_composite_xyz") is None
