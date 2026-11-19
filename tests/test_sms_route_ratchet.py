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

What counts as a use: any reference to the method by name -- a call, a bound method passed on
(``status_of or client.compose_status``), or ``getattr(client, "compose_status")``. ``SMS_METHODS`` itself
is checked against the client: it must be exactly the public methods whose bodies reach an SMS route,
directly or through other client methods and module helpers, so a new SMS method cannot slip past.
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
    # /viva/v1/capabilities first, /core/v1/capabilities on a 404 (fallback)
    "capabilities",
    # /api/v1/simulations*
    "list_build_simulations", "download_workspace", "simulation_status", "get_simulation",
    "cancel_simulation", "simulation_chain_progress", "simulation_trace", "run_simulation", "download_data",
    # /api/v1/.../analys*
    "run_analysis", "analysis_status", "list_analyses",
    # /compose/v1, rewritten to the interim /viva/v1/compose
    "compose_submit", "compose_status", "compose_status_batch", "download_compose_results",
})

#: (module, receiver) pairs that are NOT an ``SmsApiClient`` although they share a method name. Pinned to the
#: module, so naming some other variable ``session`` does not hide an SMS call.
NOT_SMS_RECEIVERS = frozenset({
    ("lib.remote_run_compose", "session"),   # compose-api's own client (compose_api_client.ComposeSession)
    ("lib.ai_views", "ai_tools"),            # the AI chat's own capabilities() (a module function)
    # ServerCapabilities.capabilities, a frozenset field; only ``client.capabilities()`` here is the SMS call
    ("lib.server_capabilities", "self"),
    ("lib.server_capabilities", "caps"),
})

#: Today's callers. Shrink this as steps 1-6 land; never grow it.
ALLOWED: dict[str, frozenset[str]] = {
    "lib.comparison_pinning": frozenset({"list_branch_builds", "list_simulators", "simulator_status"}),
    "lib.composite_test_run_views": frozenset({"run_simulation"}),
    "lib.remote_analysis_figures": frozenset({"get_simulation", "list_analyses"}),
    "lib.remote_build_source": frozenset({"download_workspace", "list_simulators"}),
    "lib.remote_pinned": frozenset({"list_branch_builds"}),
    "lib.remote_reconcile": frozenset({"simulation_status"}),
    # compose_status is passed as a bound method: ``(status_of or client.compose_status)(sim_id)``.
    "lib.remote_run": frozenset({"compose_status", "compose_status_batch", "compose_submit",
                                 "download_compose_results"}),
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
    # Reads the deployment's capability list; the /core/v1 half goes when no site predates /viva/v1.
    "lib.server_capabilities": frozenset({"capabilities"}),
    "lib.source_build_views": frozenset({"register_branch_head"}),
}

#: SMS route literals outside the client: module -> the routes it spells.
ROUTE_LITERALS: dict[str, frozenset[str]] = {
    # The config discovery a card-run dispatch reads to pick a build's config file.
    "lib.composite_test_run_views": frozenset({"/api/v1/simulations/discovery"}),
}

#: The SMS-specific prefixes. ``/viva/v1/compose`` is the interim home of ``/compose/v1``; the rest of
#: ``/viva/v1`` (and the hosted-worker routes) is viva-core, which the design keeps.
_ROUTE = re.compile(r"^/(api/v1|core/v1|compose/v1|viva/v1/compose)(/|$)")


def _modules():
    for path in sorted(PKG.rglob("*.py")):
        if path == CLIENT:
            continue
        name = ".".join(path.relative_to(PKG).with_suffix("").parts)
        yield name, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _receiver(node: ast.Attribute) -> str | None:
    return node.value.id if isinstance(node.value, ast.Name) else None


def _sms_calls() -> dict[str, set[str]]:
    """Every reference to an SMS method by name: ``x.method`` (called or not) and ``getattr(x, "method")``."""
    found: dict[str, set[str]] = {}
    for name, tree in _modules():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr in SMS_METHODS
                    and (name, _receiver(node)) not in NOT_SMS_RECEIVERS):
                found.setdefault(name, set()).add(node.attr)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("getattr", "hasattr")
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in SMS_METHODS):
                found.setdefault(name, set()).add(node.args[1].value)
    return found


def _client_sms_methods() -> set[str]:
    """The public ``SmsApiClient`` methods that reach an SMS route: a route literal in their body, or a
    reference (by name) to a client method, module-level helper, or module/class constant of
    ``sms_api_client`` that holds or reaches one (``_COMPOSE_PREFIX + "/..."`` counts)."""
    tree = ast.parse(CLIENT.read_text(encoding="utf-8"))
    # Transport every request goes through, so not a route for other methods to inherit: the path
    # rewriter (it spells /viva/v1/compose to map /compose/v1 onto it) and the capability lookup it
    # consults (whose /core/v1 fallback makes ``capabilities`` itself an SMS method, listed above).
    transport = {"resolve_path", "_has_successor", "_server_capabilities"}
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SmsApiClient")
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name not in transport}
    helpers = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name not in transport}

    def constants(body: list) -> dict[str, ast.AST]:
        out: dict[str, ast.AST] = {}
        for n in body:
            targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
            for t in targets:
                if isinstance(t, ast.Name) and getattr(n, "value", None) is not None:
                    out[t.id] = n
        return out
    module_consts, class_consts = constants(tree.body), constants(cls.body)
    nodes = {**{f"m:{k}": v for k, v in methods.items()}, **{f"f:{k}": v for k, v in helpers.items()},
             **{f"c:{k}": v for k, v in module_consts.items()}, **{f"a:{k}": v for k, v in class_consts.items()}}

    def edges(fn: ast.AST) -> set[str]:
        out = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Attribute) and n.attr in methods:
                out.add(f"m:{n.attr}")
            elif isinstance(n, ast.Attribute) and n.attr in class_consts:
                out.add(f"a:{n.attr}")
            elif isinstance(n, ast.Name) and n.id in helpers:
                out.add(f"f:{n.id}")
            elif isinstance(n, ast.Name) and n.id in module_consts:
                out.add(f"c:{n.id}")
        return out

    reach = {k for k, fn in nodes.items()
             if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and _ROUTE.match(n.value)
                    for n in ast.walk(fn))}
    graph = {k: edges(fn) for k, fn in nodes.items()}
    grew = True
    while grew:
        grew = False
        for k in nodes:
            if k not in reach and graph[k] & reach:
                reach.add(k)
                grew = True
    return {k[2:] for k in reach if k.startswith("m:") and not k[2:].startswith("_")}


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


def test_sms_methods_match_the_client():
    """SMS_METHODS is exactly the client's public methods that reach an SMS route: a new one (or a renamed
    one) must be listed, and a method that stops reaching one must leave the list."""
    derived = _client_sms_methods()
    assert derived == SMS_METHODS, {"missing from SMS_METHODS": sorted(derived - SMS_METHODS),
                                    "listed but not SMS": sorted(SMS_METHODS - derived)}
