"""Verify /api/study-* aliases hit the same handlers as /api/investigation-*.

Dispatcher structure in server.py:
- GET  routes: do_GET rewrites self.path at entry using _GET_STUDY_ALIASES so
               the rest of the dispatch chain only sees /api/investigation-*
               paths.  _GET_STUDY_ALIASES is the single source of truth;
               tests inspect it directly.
- POST routes: module-level _POST_ROUTE_MAP dict {route: method_name_str}.
               do_POST resolves route → getattr(self, method_name)(body).
               _POST_STUDY_ALIASES injects alias keys at import time so both
               old and new keys map to the same method-name string.
"""
import pytest

import vivarium_dashboard.server as srv


# ---------------------------------------------------------------------------
# POST aliases: both old and new keys must map to the same method-name string.
# ---------------------------------------------------------------------------
POST_ALIAS_PAIRS = [
    ("/api/investigation-create",             "/api/study-create"),
    ("/api/investigation-delete",             "/api/study-delete"),
    # /api/study-run-baseline is now a v3-native route (not an alias), so it
    # intentionally maps to _post_study_run_baseline, not _post_investigation_run.
    ("/api/investigation-run-one",            "/api/study-run-variant"),
    ("/api/investigation-render-viz",         "/api/study-viz-render"),
    ("/api/investigation-add-viz",            "/api/study-viz-add"),
    ("/api/investigation-run-delete",         "/api/study-run-delete"),
    ("/api/investigation-runs-clear",         "/api/study-runs-clear"),
    ("/api/investigation-composite-perturb",  "/api/study-variant-add"),
    ("/api/investigation-composite-rebuild",  "/api/study-variant-rebuild"),
    ("/api/investigation-set-observables",    "/api/study-set-observables"),
    ("/api/investigation-set-conclusions",    "/api/study-set-conclusion"),
    ("/api/investigation-set-overview",       "/api/study-set-description"),
    ("/api/investigation-comparison-add",     "/api/study-comparison-add"),
    ("/api/investigation-comparison-update",  "/api/study-comparison-update"),
    ("/api/investigation-group-add",          "/api/study-group-add"),
    ("/api/investigation-group-update",       "/api/study-group-update"),
]


@pytest.mark.parametrize("old,new", POST_ALIAS_PAIRS)
def test_post_study_alias_same_handler(old, new):
    """New /api/study-* POST key maps to the same method name as the original."""
    route_map = srv._POST_ROUTE_MAP
    if old not in route_map:
        pytest.skip(f"original route {old!r} not in POST route map; alias skipped")
    assert new in route_map, f"alias {new!r} missing from POST route map"
    assert route_map[new] == route_map[old], (
        f"{new!r} → {route_map[new]!r} differs from "
        f"{old!r} → {route_map[old]!r}"
    )


# ---------------------------------------------------------------------------
# GET aliases: _GET_STUDY_ALIASES records (old_prefix, new_prefix) pairs so
# tests can verify coverage without executing a live HTTP request.
# ---------------------------------------------------------------------------
GET_ALIAS_PAIRS = [
    ("/api/investigations",           "/api/studies"),
    ("/api/investigation-viz-html",   "/api/study-viz-html"),
    ("/api/investigation-composites", "/api/study-composites"),
    ("/api/investigation-state-tree", "/api/study-state-tree"),
    # /api/investigation/<name>  →  /api/study/<name>
    ("/api/investigation/",           "/api/study/"),
]


@pytest.mark.parametrize("old,new", GET_ALIAS_PAIRS)
def test_get_study_alias_registered(old, new):
    """Both old and new GET prefixes appear as a pair in _GET_STUDY_ALIASES."""
    registered = srv._GET_STUDY_ALIASES
    registered_olds = {o for o, _ in registered}
    registered_news = {n for _, n in registered}
    if old not in registered_olds:
        pytest.skip(f"original GET prefix {old!r} not registered; alias skipped")
    assert new in registered_news, (
        f"alias prefix {new!r} missing from _GET_STUDY_ALIASES"
    )
    # Verify the old→new mapping is a single pair (not accidentally cross-wired).
    assert (old, new) in registered, (
        f"({old!r}, {new!r}) not found as a pair in _GET_STUDY_ALIASES"
    )
