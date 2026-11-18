"""Ratchet: the workbench's calls to the SMS-specific viva-api surfaces may shrink, never grow.

docs/backend-adapters.md moves the workbench onto viva-core (``/viva/v1``) and compose-api only. Until that
is done, the SMS-specific surfaces (``/api/v1/*``, ``/core/v1/*``, the interim ``/viva/v1/compose`` and the
legacy-fallback halves of the build methods) are still called from the modules listed below. This test pins
that list:

* a NEW module calling one of these methods, or a listed module calling one it did not call before, fails --
  add the call through the backend adapter instead, or argue for it here on purpose;
* a listed call that is GONE also fails, so the list is shrunk in the same PR that removes the call and the
  ratchet never goes slack.

Only ``lib/sms_api_client.py`` may spell an SMS route; anything else that does (a ``client._get("/api/v1/...")``
that bypasses the methods) is pinned by ``ROUTE_LITERALS`` the same way.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "vivarium_workbench"
CLIENT = PKG / "lib" / "sms_api_client.py"

#: ``SmsApiClient`` methods that can reach an SMS-specific route. Those marked (fallback) go to
#: ``/viva/v1/environments`` first and to ``/core/v1/simulator*`` only when the backend lacks the
#: environments capabilities (W2); they leave this set when those fallbacks are dropped (step 4).
SMS_METHODS = frozenset({
    # /core/v1/simulator* (fallback)
    "latest_simulator", "register_simulator", "register_branch_head", "simulator_status",
    "list_branch_builds", "list_simulators", "simulator_commit", "upload_simulator",
    # /api/v1/simulations*
    "list_build_simulations", "download_workspace", "simulation_status", "get_simulation",
    "cancel_simulation", "simulation_chain_progress", "simulation_trace", "run_simulation", "download_data",
    # /api/v1/.../analys*
    "run_analysis", "analysis_status", "list_analyses",
    # /compose/v1, rewritten to the interim /viva/v1/compose
    "compose_submit", "compose_status", "compose_status_batch", "download_compose_results",
})

#: Receivers that are NOT an ``SmsApiClient`` although they share a method name (compose-api's own client).
NOT_SMS_RECEIVERS = frozenset({"session"})

#: Today's callers. Shrink this as steps 1-6 land; never grow it.
ALLOWED: dict[str, frozenset[str]] = {
    "lib.comparison_pinning": frozenset({"list_branch_builds", "list_simulators", "simulator_status"}),
    "lib.composite_test_run_views": frozenset({"run_simulation"}),
    "lib.remote_analysis_figures": frozenset({"get_simulation", "list_analyses"}),
    "lib.remote_build_source": frozenset({"download_workspace", "list_simulators"}),
    "lib.remote_pinned": frozenset({"list_branch_builds"}),
    "lib.remote_reconcile": frozenset({"simulation_status"}),
    "lib.remote_run": frozenset({"compose_status_batch", "compose_submit", "download_compose_results"}),
    "lib.remote_run_jobs": frozenset({"download_data", "run_simulation", "simulation_status", "simulator_status",
                                      "upload_simulator"}),
    "lib.remote_run_landing": frozenset({"download_data"}),
    "lib.remote_run_views": frozenset({"analysis_status", "cancel_simulation", "download_data", "get_simulation",
                                       "run_analysis", "run_simulation", "simulation_chain_progress",
                                       "simulation_status", "simulator_commit", "simulator_status",
                                       "upload_simulator"}),
    # The viva-core adapter still lands results through the interim compose route; viva-core 0.1.10's
    # /composites/{id}/results replaces it.
    "lib.remote_run_viva_v1": frozenset({"download_compose_results"}),
    "lib.remote_simulations": frozenset({"list_build_simulations", "list_simulators", "simulation_status"}),
    "lib.remote_trace": frozenset({"compose_status", "simulation_trace"}),
    "lib.run_jobs": frozenset({"compose_status_batch"}),
    "lib.simulations_index": frozenset({"download_data"}),
    "lib.source_build_views": frozenset({"register_branch_head"}),
}

#: SMS route literals outside the client: module -> the routes it spells.
ROUTE_LITERALS: dict[str, frozenset[str]] = {
    # The config discovery a card-run dispatch reads to pick a build's config file.
    "lib.composite_test_run_views": frozenset({"/api/v1/simulations/discovery"}),
}

_ROUTE = re.compile(r"^/(api/v1|core/v1|compose/v1|env-worker/v1)(/|$)")


def _modules():
    for path in sorted(PKG.rglob("*.py")):
        if path == CLIENT:
            continue
        name = ".".join(path.relative_to(PKG).with_suffix("").parts)
        yield name, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _receiver(func: ast.Attribute) -> str | None:
    return func.value.id if isinstance(func.value, ast.Name) else None


def _sms_calls() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for name, tree in _modules():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in SMS_METHODS and _receiver(node.func) not in NOT_SMS_RECEIVERS):
                found.setdefault(name, set()).add(node.func.attr)
    return found


def _route_literals() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for name, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _ROUTE.match(node.value):
                found.setdefault(name, set()).add(node.value)
    return found


def _diff(found: dict[str, set[str]], allowed: dict[str, frozenset[str]]) -> tuple[list[str], list[str]]:
    new = [f"{m}: {sorted(found[m] - allowed.get(m, frozenset()))}"
           for m in sorted(found) if found[m] - allowed.get(m, frozenset())]
    gone = [f"{m}: {sorted(allowed[m] - found.get(m, set()))}"
            for m in sorted(allowed) if allowed[m] - found.get(m, set())]
    return new, gone


def test_no_new_sms_method_calls():
    new, _ = _diff(_sms_calls(), ALLOWED)
    assert not new, (
        "new calls to SMS-specific SmsApiClient methods (docs/backend-adapters.md): go through viva-core "
        "(/viva/v1) or the backend adapter instead:\n  " + "\n  ".join(new))


def test_removed_sms_method_calls_are_dropped_from_the_ratchet():
    _, gone = _diff(_sms_calls(), ALLOWED)
    assert not gone, "these calls are gone; remove them from ALLOWED so the ratchet stays tight:\n  " + "\n  ".join(gone)


def test_no_new_sms_route_literals():
    new, gone = _diff(_route_literals(), ROUTE_LITERALS)
    assert not new, ("SMS routes spelled outside lib/sms_api_client.py; add a client method (or use viva-core):\n  "
                     + "\n  ".join(new))
    assert not gone, "these route literals are gone; remove them from ROUTE_LITERALS:\n  " + "\n  ".join(gone)


def test_every_listed_method_exists_on_the_client():
    """A renamed or deleted client method must leave SMS_METHODS too, or the ratchet silently stops seeing it."""
    tree = ast.parse(CLIENT.read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert SMS_METHODS <= defined, sorted(SMS_METHODS - defined)
