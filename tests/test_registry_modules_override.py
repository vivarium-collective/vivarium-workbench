"""Unit tests for the per-workspace catalog override
(``dashboard.registry.modules``) — the pure resolver/builder helpers.

The override REPLACES pbg's default catalog (unlike ``include``, which only
filters it), so a workspace can surface a package pbg doesn't ship (e.g.
``viva-munk``). These tests cover string-vs-dict resolution, the derived
process-registry allow-list, and the guard that leaves classic behavior
(no block / ``include`` only) unchanged.
"""
from __future__ import annotations

from vivarium_dashboard.lib.registry import (
    _registry_modules_override,
    _registry_include_pkgs,
)
from vivarium_dashboard.lib.catalog import _build_override_catalog


DEFAULT_CATALOG = [
    {
        "name": "pbg-bioreactordesign",
        "package": "pbg_bioreactordesign",
        "description": "BiRD bioreactor wrapper",
        "source": "https://github.com/vivarium-collective/pbg-bioreactordesign.git",
        "tags": ["bioreactor"],
    },
    {"name": "spatio-flux", "package": "spatio_flux", "description": "x", "tags": []},
]


# --- guards: classic behavior unchanged ------------------------------------

def test_no_registry_block_means_no_override_and_no_filter():
    ws = {"name": "foo", "package_path": "foo"}
    assert _registry_modules_override(ws) is None
    assert _registry_include_pkgs(ws) is None


def test_include_only_is_classic_filter_no_override():
    ws = {"name": "foo", "dashboard": {"registry": {"include": ["a", "b"]}}}
    assert _registry_modules_override(ws) is None
    assert _registry_include_pkgs(ws) == {"a", "b"}


def test_empty_modules_list_treated_as_unset():
    ws = {"name": "foo", "dashboard": {"registry": {"modules": []}}}
    assert _registry_modules_override(ws) is None


# --- override resolution ----------------------------------------------------

def test_modules_override_returns_list():
    ws = {"dashboard": {"registry": {"modules": ["pbg-bioreactordesign"]}}}
    assert _registry_modules_override(ws) == ["pbg-bioreactordesign"]


def test_derived_include_adds_self_and_declared_packages():
    ws = {
        "name": "v2ecoli",
        "package_path": "v2ecoli",
        "dashboard": {
            "registry": {
                "modules": [
                    "pbg-bioreactordesign",
                    {"name": "viva-munk", "package": "viva_munk"},
                ]
            }
        },
    }
    # No explicit include → derive from modules (+ workspace-self).
    assert _registry_include_pkgs(ws) == {
        "v2ecoli",
        "pbg_bioreactordesign",
        "viva_munk",
    }


def test_explicit_include_wins_over_derived():
    ws = {
        "name": "v2ecoli",
        "package_path": "v2ecoli",
        "dashboard": {
            "registry": {
                "include": ["only_this"],
                "modules": ["pbg-bioreactordesign"],
            }
        },
    }
    assert _registry_include_pkgs(ws) == {"only_this"}


# --- catalog build (string vs dict vs stub) --------------------------------

def test_string_entry_inherits_default_metadata():
    built = _build_override_catalog(["pbg-bioreactordesign"], DEFAULT_CATALOG)
    assert len(built) == 1
    m = built[0]
    assert m["name"] == "pbg-bioreactordesign"
    assert m["description"] == "BiRD bioreactor wrapper"
    assert m["package"] == "pbg_bioreactordesign"
    assert "override_stub" not in m and "override_custom" not in m


def test_unknown_string_entry_becomes_minimal_stub():
    built = _build_override_catalog(["does-not-exist"], DEFAULT_CATALOG)
    assert len(built) == 1
    m = built[0]
    assert m["name"] == "does-not-exist"
    assert m["package"] == "does_not_exist"
    assert m["override_stub"] is True
    assert m["description"]  # non-empty note so the row renders


def test_dict_entry_is_custom_with_filled_defaults():
    built = _build_override_catalog(
        [{"name": "viva-munk", "package": "viva_munk", "source": "g",
          "description": "physics", "category": "visualization"}],
        DEFAULT_CATALOG,
    )
    m = built[0]
    assert m["name"] == "viva-munk"
    assert m["package"] == "viva_munk"
    assert m["override_custom"] is True
    # category surfaced into tags for display convenience
    assert "visualization" in m["tags"]


def test_dict_entry_missing_package_defaults_to_snake_name():
    built = _build_override_catalog([{"name": "my-thing"}], DEFAULT_CATALOG)
    m = built[0]
    assert m["package"] == "my_thing"
    assert m["source"] == ""
    assert m["description"]  # filled fallback


def test_order_preserved_and_dupes_collapsed():
    built = _build_override_catalog(
        ["pbg-bioreactordesign", "pbg-bioreactordesign", "spatio-flux"],
        DEFAULT_CATALOG,
    )
    assert [m["name"] for m in built] == ["pbg-bioreactordesign", "spatio-flux"]
