"""Declared backend features (docs/backend-adapters.md §2.1, step P3 F0).

Features are derived, not listed:

- the facet features are the optional facets an active backend has (``listing``, ``artifacts``, ``analyses``,
  ``builds``);
- a provider declares only the UI features no facet implies (``Provider.features``);
- a run's own features are the facets of the backend that ran it (``artifacts``, ``analyses``), plus the optional
  ``runs`` methods it has (``visibility``: ``runs.change_visibility``, :data:`RUN_METHOD_FEATURES`). compose-api is a
  core backend, so its runs have ``visibility`` with providers off too.

Features only HIDE optional controls in the browser (``static/backend-features.js``). Nothing that decides where a
run goes, or whether one may be dispatched, reads them: those decisions stay with ``backends.select`` / ``active``
and the server's own refusals.

Everything here is a registry read: no profile, no record, no network call (#1106), so ``GET /api/backend/features``
and the per-row annotation of ``/api/simulations`` never block on a backend.
"""

from __future__ import annotations

from typing import Literal, Optional

#: The optional facets that are features of the process when any active backend declares them.
FACET_FEATURES: 'tuple[Literal["listing", "artifacts", "analyses", "builds"], ...]' = (
    "listing", "artifacts", "analyses", "builds")
#: The facets that are a run's own features (operations on a run its backend may or may not offer).
RUN_FEATURES = ("artifacts", "analyses")
#: A run's features that are an optional method of its backend's ``runs`` facet, not a facet: feature -> method.
RUN_METHOD_FEATURES = {"visibility": "change_visibility"}


def process_features() -> frozenset[str]:
    """What this process's active backends offer: the facets of :data:`FACET_FEATURES` some active backend declares
    (``backends.offering``), plus every provider's ``features()``. Empty with providers off or the plugin absent.
    A broken provider raises (as the registry does), so the browser reads "unknown", never "nothing"."""
    from vivarium_workbench.lib import backends
    from vivarium_workbench.lib.backends import provider as _provider

    found: set[str] = {f for f in FACET_FEATURES if backends.offering(f)}
    for p in _provider.providers():
        found.update(p.features())
    return frozenset(found)


def run_features(kind: Optional[str]) -> frozenset[str]:
    """The run features of the backend ``kind`` names: its facets among :data:`RUN_FEATURES` and its optional ``runs``
    methods among :data:`RUN_METHOD_FEATURES`. Empty for no kind or a kind no active provider serves."""
    if not kind:
        return frozenset()
    from vivarium_workbench.lib import backends

    try:
        backend = backends.by_kind(kind)
    except KeyError:
        return frozenset()
    return frozenset([f for f in RUN_FEATURES if getattr(backend, f) is not None]
                     + [f for f, m in RUN_METHOD_FEATURES.items() if callable(getattr(backend.runs, m, None))])


def kind_by_id_form(simulation_id: object) -> Optional[str]:
    """The kind of the active backend whose id form ``simulation_id`` has (``backends.for_run``), or ``None``: the
    fallback for a run with no record."""
    from vivarium_workbench.lib import backends

    owner = backends.for_run(simulation_id)
    return owner.name if owner is not None else None
